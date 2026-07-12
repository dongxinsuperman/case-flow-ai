"""提交 bug 到飞书项目（issue）。

- 配置：backend/config/feishu_issue.json（每空间 issue 字段映射，私有，已 gitignore；模板 .example.json）。
- 值来源：标题/描述/各字段从「该 case 所属需求(story)的 source_payload」+ 当前用户 + 执行端 + 固定 + 模型预填。
- 建单走后端 OpenAPI（plugin_token + X-USER-KEY），不依赖 MCP。
- 关键截图走签名上传内嵌（P2，异步补图，不阻塞用户）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.case_assets import CaseAsset, CaseBody, CaseBugDraft, CaseRepairDraft, CaseWorkItem
from app.models.requirements import RequirementGroup, RequirementItem, RequirementPool, User
from app.services.function_map_mount import compile_top_level_context
from app.services.sources import feishu_project as fp
from app.services.sources import feishu_project_mcp as fpmcp

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "feishu_issue.json"
BUG_DRAFT_SCHEMA_VERSION = 4
_CHOICE_FIELD_TYPES = ("select", "multi_select")
_TEXT_FIELD_TYPES = ("text", "multi_text")
_USER_FIELD_TYPES = ("user", "multi_user")
_RELATION_FIELD_TYPES = (
    "work_item_related_select",
    "workitem_related_select",
    "work_item_related_multi_select",
    "workitem_related_multi_select",
)
_MODEL_PREFILL_FIELD_TYPES = _CHOICE_FIELD_TYPES + _TEXT_FIELD_TYPES
_FRONTEND_EDITABLE_FIELD_TYPES = _MODEL_PREFILL_FIELD_TYPES + _USER_FIELD_TYPES + _RELATION_FIELD_TYPES
_MULTI_REQUIREMENT_LINK_TYPES = ("work_item_related_multi_select", "workitem_related_multi_select")
_SEMANTIC_SUBMITTER = "submitter"
_SEMANTIC_ASSIGNEE = "assignee"
_SEMANTIC_WATCHER = "watcher"
_SEMANTIC_SPRINT = "sprint"
_SEMANTIC_LINKED_REQUIREMENT = "linked_requirement"
_DYNAMIC_CURRENT_USER_LABEL = "右上角当前用户"
BUG_IMAGE_UPLOAD_LIMIT = 9
BUG_IMAGE_UPLOAD_MAX_BYTES = 6 * 1024 * 1024
_IMAGE_MARKDOWN_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)(?:<!--[^>]*-->)?")

logger = logging.getLogger(__name__)


class BugSubmitError(RuntimeError):
    pass


@dataclass
class SpaceIssueConfig:
    project_key: str
    work_item_type: str
    template_id: int
    title_field: str
    description_field: str
    description_include: list[str]
    attachment_field: str | None
    link_requirement_field: str | None
    link_requirement_field_type: str
    # field_key -> {"source": ..., 其它参数}。只配“我们手里的值往哪个字段填”，
    # 选项/名字/必填/顺序/默认值全从飞书 meta 拉，不在这里配。
    field_sources: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_issue_config(path: Path | None = None) -> dict[str, SpaceIssueConfig]:
    cfg_path = path or CONFIG_PATH
    if not cfg_path.exists():
        return {}
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    result: dict[str, SpaceIssueConfig] = {}
    for sp in raw.get("spaces") or []:
        key = str(sp.get("project_key") or "").strip()
        if not key:
            continue
        result[key] = SpaceIssueConfig(
            project_key=key,
            work_item_type=str(sp.get("work_item_type") or "issue"),
            template_id=int(sp.get("template_id") or 0),
            title_field=str(sp.get("title_field") or "name"),
            description_field=str(sp.get("description_field") or "description"),
            description_include=[str(x) for x in (sp.get("description_include") or [])],
            attachment_field=(sp.get("attachment_field") or None),
            link_requirement_field=(sp.get("link_requirement_field") or None),
            link_requirement_field_type=str(sp.get("link_requirement_field_type") or "work_item_related_select"),
            field_sources=dict(sp.get("field_sources") or {}),
        )
    return result


def _normalize_meta(meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """飞书建单 meta → 规整字段列表（只取建单可见字段，保持顺序）。"""
    out: list[dict[str, Any]] = []
    for f in meta:
        if f.get("is_visibility") != 1:
            continue
        out.append({
            "field_key": f.get("field_key"),
            "label": f.get("field_name") or f.get("label") or f.get("field_key"),
            "type": f.get("field_type_key"),
            "required": f.get("is_required") == 1,
            "options": [
                {"name": o.get("label"), "id": o.get("value")}
                for o in (f.get("options") or [])
                if o.get("label") is not None
            ],
            "default": (f.get("default_value") or {}).get("value"),
        })
    return out


async def _pull_meta(ctx: _Ctx) -> list[dict[str, Any]]:
    client = fp.FeishuProjectClient()
    if not client.enabled:
        return []
    async with httpx.AsyncClient(timeout=25) as http:
        token = await client._plugin_token(http)
        raw = await client.get_create_meta(http, token, ctx.cfg.project_key, ctx.cfg.work_item_type)
    return _normalize_meta(raw)


@dataclass
class _Ctx:
    case: CaseAsset
    body: CaseBody
    work_item: CaseWorkItem
    pool: RequirementPool
    group: RequirementGroup | None
    user: User | None
    users: list[User]
    cfg: SpaceIssueConfig
    diagnosis: dict[str, Any]


async def _gather(
    session: AsyncSession,
    case_id: int,
    user_id: int | None = None,
    require_user: bool = False,
) -> _Ctx:
    case = await session.get(CaseAsset, case_id)
    body = await session.get(CaseBody, case_id)
    work_item = await session.get(CaseWorkItem, case_id)
    if case is None or body is None or work_item is None:
        raise BugSubmitError("case 不存在")
    item = await session.get(RequirementItem, case.source_requirement_item_id) if case.source_requirement_item_id else None
    if item is None:
        raise BugSubmitError("该 case 未挂到二级需求，无法定位来源需求")
    pool = await session.get(RequirementPool, item.pool_id)
    group = await session.get(RequirementGroup, item.group_id) if item.group_id else None
    if pool is None or not pool.source_space:
        raise BugSubmitError("来源需求缺少飞书项目信息（source_space），无法提交 bug")
    configs = load_issue_config()
    cfg = configs.get(pool.source_space)
    if cfg is None or not cfg.template_id:
        raise BugSubmitError(f"空间 {pool.source_space} 未配置 issue 提交（backend/config/feishu_issue.json）")
    # 用户只在“提交”时需要（报告人=当前用户）；预填/草稿生成不依赖用户。
    user = await session.get(User, user_id) if user_id else None
    if require_user and user is None:
        raise BugSubmitError("当前用户不存在")
    user_rows = await session.execute(
        select(User)
        .where(User.feishu_user_key.is_not(None))
        .order_by(User.display_name.asc())
    )
    users = list(user_rows.scalars().all())
    draft = await session.scalar(
        select(CaseRepairDraft).where(CaseRepairDraft.case_id == case_id).order_by(CaseRepairDraft.id.desc())
    )
    diagnosis = (draft.diagnosis_snapshot or {}) if draft else {}
    return _Ctx(case, body, work_item, pool, group, user, users, cfg, diagnosis)


def _build_description(ctx: _Ctx) -> str:
    """按 description_include 配置的顺序拼描述（想调顺序只改配置即可）。"""
    case, body, diag, wi = ctx.case, ctx.body, ctx.diagnosis, ctx.work_item

    def _section(key: str) -> str | None:
        if key == "diagnosis":
            if not diag.get("reason"):
                return None
            block = f"**失败原因**\n{diag.get('reason')}"
            if diag.get("evidence"):
                block += f"\n\n**证据**\n{diag.get('evidence')}"
            return block
        if key == "path":
            path = _path(case)
            group_name = ctx.group.name if ctx.group else "未进入目录"
            return f"**所属**：{group_name} / {ctx.case.suite_title}\n**层级**：{path or '无'}"
        if key == "preconditions":
            return f"**前置条件**\n{body.preconditions or '无'}"
        if key == "steps":
            return f"**操作步骤**\n{body.steps_text or '无'}"
        if key == "expected":
            return f"**预期结果**\n{body.expected_result or '无'}"
        if key == "report_link":
            return f"执行报告：{wi.report_url}" if wi.report_url else None
        return None

    parts = [s for key in ctx.cfg.description_include if (s := _section(key))]
    return "\n\n".join(parts)


def _path(case: CaseAsset) -> str:
    nodes = getattr(case, "path_nodes", None)
    if isinstance(nodes, list):
        return " / ".join(
            str(node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text") or "")
            for node in nodes
            if isinstance(node, dict)
            and (node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text"))
        )
    return ""


def _model_field_keys(cfg: SpaceIssueConfig) -> list[str]:
    return [k for k, v in cfg.field_sources.items() if v.get("source") == "model"]


def _llm_client():
    settings = get_settings()
    api_key = settings.llm_api_key or os.environ.get("ARK_API_KEY") or os.environ.get("CASE_FLOW_LLM_API_KEY")
    if not api_key:
        return None, settings
    try:
        from openai import OpenAI
    except Exception:
        return None, settings
    return OpenAI(base_url=settings.llm_base_url, api_key=api_key), settings


async def _polish_title(ctx: _Ctx) -> str:
    """把“用例意图标题”润色成“缺陷标题”：验证什么/因为什么/错在哪。无模型则回退原标题。"""
    client, settings = _llm_client()
    if client is None:
        return ctx.case.raw_title
    payload = {
        "case_title": ctx.case.raw_title,
        "path": _path(ctx.case),
        "expected": ctx.body.expected_result,
        "reason": ctx.diagnosis.get("reason", ""),
        "evidence": ctx.diagnosis.get("evidence", ""),
    }
    prompt = (
        "把“测试用例意图标题”改写成一行“缺陷(bug)标题”。用例描述的是“要验证什么”，"
        "bug 标题要直接描述“出了什么问题”：在哪个页面/操作下出现了什么异常。"
        "**不要过度细化**——很多问题本身复杂、无法精确描述，描述到“能定位是什么问题”的程度就停，别堆砌细节变成累赘。"
        "一行、35字以内、不要“验证/测试”字眼、不带内部ID、不加书名号或【】前缀。只输出标题文本本身。"
        "\n示例：章节列表页全部Tab下场景行卡片标题与需求不符。"
        "\n输入：" + json.dumps(payload, ensure_ascii=False)
    )
    try:
        max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=settings.llm_model, messages=[{"role": "user", "content": prompt}],
                temperature=0.4, max_tokens=max_tokens,
            )
        )
        title = (resp.choices[0].message.content or "").strip().strip("「」\"' \n")
        return title.splitlines()[0][:60] if title else ctx.case.raw_title
    except Exception:
        return ctx.case.raw_title


async def _prefill_choices(
    session: AsyncSession,
    ctx: _Ctx,
    meta_by_key: dict[str, dict[str, Any]],
    target_keys: list[str],
) -> dict[str, str | list[str]]:
    """模型预填给定字段（配置的 model 字段 + 查不到准确信息的空缺项）。

    select/multi_select 必须从飞书 meta 选项里挑；text/multi_text 允许模型生成简短文本。
    失败/无 key 时留空，交给用户或飞书默认，不自动选第一个。
    """
    model_keys = [k for k in target_keys if k in meta_by_key]
    if not model_keys:
        return {}
    specs: dict[str, dict[str, Any]] = {}
    for k in model_keys:
        m = meta_by_key[k]
        typ = m.get("type")
        names = [o["name"] for o in m.get("options") or []]
        if typ in _CHOICE_FIELD_TYPES:
            if not names:
                continue
            specs[k] = {"type": typ, "multi": typ == "multi_select", "options": names}
        elif typ in _TEXT_FIELD_TYPES:
            specs[k] = {"type": typ, "multi": False, "options": []}

    settings = get_settings()
    api_key = settings.llm_api_key or os.environ.get("ARK_API_KEY") or os.environ.get("CASE_FLOW_LLM_API_KEY")
    if not api_key or not specs:
        return {}
    try:
        from openai import OpenAI
    except Exception:
        return {}

    function_map = ""
    requirement_item_id = getattr(ctx.case, "source_requirement_item_id", None)
    if requirement_item_id:
        function_map = (
            await compile_top_level_context(session, [requirement_item_id], "mixed")
        ).context
    payload = {
        "case_title": ctx.case.raw_title,
        "path": _path(ctx.case),
        "preconditions": ctx.body.preconditions,
        "steps": ctx.body.steps_text,
        "expected": ctx.body.expected_result,
        "diagnosis_reason": ctx.diagnosis.get("reason", ""),
        "function_map": function_map,
        "fields": specs,
    }
    prompt = (
        "你在给一个【已存在并执行失败的自动化测试用例】创建 bug。请据 case 内容与失败原因，"
        "为每个字段填最合适的默认值。\n"
        "硬规则：\n"
        "1) 这个 bug 是被现有用例执行时发现的，本质属于『case 已覆盖』。缺陷标签优先选『逻辑问题（case已覆盖）』；"
        "绝不要选『测试遗漏』『需求问题（case未覆盖）』『逻辑问题（随机测试发现）』这类表示‘没有用例覆盖/随机发现’的标签。"
        "可再叠加确实贴切的现象类标签（如 UI交互问题/用户体验问题）。\n"
        "2) 业务归属等单选必须严格从该字段 options 里逐条对比、结合页面层级/标题选最贴切的一个，不要臆造、不要选第一个凑数。\n"
        "3) select 返回字符串，multi_select 返回字符串数组，值必须与 options 里的原文完全一致；"
        "text/multi_text 返回简短字符串。只输出 JSON：{field_key: 值, ...}。\n"
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        client = OpenAI(base_url=settings.llm_base_url, api_key=api_key)
        max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=settings.llm_model, messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=max_tokens,
            )
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception:
        return {}
    def _match(v: Any, names: list[str]) -> str | None:
        if not isinstance(v, str) or not v:
            return None
        if v in names:
            return v
        for n in names:  # 模糊兜底：包含关系（模型给了近似值）
            if v in n or n in v:
                return n
        return None

    out: dict[str, Any] = {}
    for k in specs:
        typ = specs[k]["type"]
        v = data.get(k)
        if typ in _TEXT_FIELD_TYPES:
            if isinstance(v, list):
                text_value = "；".join(str(x).strip() for x in v if str(x).strip())
            else:
                text_value = str(v).strip() if v not in (None, "", [], {}) else ""
            if text_value:
                out[k] = text_value
            continue
        names = specs[k]["options"]
        if specs[k]["multi"]:
            chosen: list[str] = []
            for x in (v if isinstance(v, list) else [v]):
                m = _match(x, names)
                if m and m not in chosen:
                    chosen.append(m)
            if chosen:
                out[k] = chosen
        else:
            matched = _match(v, names)
            if matched:
                out[k] = matched
    return out


def _format_value(field_type: str, raw: Any) -> Any:
    """把原始值转成飞书 create/update 需要的 field_value 形态。

    注意：单选用 {"value": id}、多选用 [{"value": id}]（实测 {"option_id"} 不落库）。
    """
    if raw in (None, "", [], {}):
        return None
    if field_type == "select":
        return {"value": str(raw)}
    if field_type == "multi_select":
        items = raw if isinstance(raw, list) else [raw]
        out = []
        for x in items:
            v = (x.get("value") or x.get("option_id")) if isinstance(x, dict) else x
            if v:
                out.append({"value": str(v)})
        return out or None
    if field_type in ("multi_user", "user"):
        return [str(x) for x in (raw if isinstance(raw, list) else [raw])]
    if field_type in ("work_item_related_multi_select", "workitem_related_multi_select"):
        return [int(x) if str(x).isdigit() else x for x in (raw if isinstance(raw, list) else [raw])]
    if field_type in ("work_item_related_select", "workitem_related_select"):
        return int(raw) if str(raw).isdigit() else raw
    return raw  # text / multi_text / link 等字符串


def _story_field_value(payload: dict[str, Any], key: str) -> Any:
    """从需求 story 的 source_payload 取某字段的“可写值”（select→option_id，多选→option_id 列表，关联→ids）。"""
    for f in payload.get("fields") or []:
        if isinstance(f, dict) and f.get("field_key") == key:
            v = f.get("field_value")
            if isinstance(v, dict):
                return v.get("value")
            if isinstance(v, list):
                out = []
                for e in v:
                    if isinstance(e, dict):
                        out.append(e.get("value") or e.get("id"))
                    else:
                        out.append(e)
                return out
            return v
    return None


def _current_month_label(now: datetime | None = None) -> str:
    dt = now or datetime.now()
    return dt.strftime("%y-%m")


def _current_month_zh_label(now: datetime | None = None) -> str:
    dt = now or datetime.now()
    return f"{dt.year % 100}年{dt.month}月"


def _requirement_role_people(ctx: _Ctx, roles: list[str]) -> list[str]:
    """从需求 role_owners + 该空间 role_map(概念→role_id) 取前端/后端/测试的人。"""
    cfg = fp.load_source_config()
    space = next((s for s in cfg.spaces if s.project_key == ctx.pool.source_space), None) if cfg else None
    if space is None:
        return []
    owners = fp._extract_role_owners(ctx.pool.source_payload or {})
    keys: list[str] = []
    for concept in roles:
        for uk in fp.user_keys_for_concept(ctx.pool.source_payload or {}, owners, space, concept):
            if uk and uk not in keys:
                keys.append(uk)
    return keys


def _disp_user(ctx: _Ctx) -> str:
    if ctx.user is None:
        return "（当前用户）"
    return getattr(ctx.user, "display_name", None) or getattr(ctx.user, "name", "") or "（当前用户）"


def _disp_roles(ctx: _Ctx, roles: list[str]) -> str:
    card = (ctx.pool.source_payload or {}).get("_card") or {}
    concept_to_label = {"frontend": "前端", "backend": "后端", "tester": "测试"}
    wanted = {concept_to_label.get(c, c) for c in roles}
    names: list[str] = []
    for role in card.get("roles") or []:
        if wanted and role.get("label") not in wanted:
            continue
        for nm in role.get("names") or []:
            if nm not in names:
                names.append(nm)
    return "、".join(names) if names else "（未指派）"


def _disp_sprint(ctx: _Ctx) -> str:
    card = (ctx.pool.source_payload or {}).get("_card") or {}
    names = [s.get("name") for s in (card.get("sprints") or []) if s.get("name")]
    return "、".join(names) if names else "（无迭代）"


def _user_options(users: list[User], current_user: User | None = None) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(user: User | None) -> None:
        key = getattr(user, "feishu_user_key", None) if user else None
        if not key or key in seen:
            return
        seen.add(str(key))
        name = getattr(user, "display_name", None) or getattr(user, "name", None) or str(key)
        options.append({"name": str(name), "id": str(key)})

    _add(current_user)
    for user in users:
        _add(user)
    return options


def _story_field_items(payload: dict[str, Any], key: str) -> list[dict[str, str]]:
    """从需求字段取可编辑候选项：保留真实 id，同时尽量带上 label/name。"""
    if not key:
        return []
    for f in payload.get("fields") or []:
        if not isinstance(f, dict) or f.get("field_key") != key:
            continue
        value = f.get("field_value") if "field_value" in f else f.get("value")
        raw_items = value if isinstance(value, list) else [value]
        items: list[dict[str, str]] = []
        for item in raw_items:
            if item in (None, "", [], {}):
                continue
            if isinstance(item, dict):
                raw_id = (
                    item.get("value")
                    or item.get("id")
                    or item.get("work_item_id")
                    or item.get("user_key")
                    or item.get("key")
                    or item.get("out_id")
                )
                raw_name = item.get("label") or item.get("name") or item.get("title") or raw_id
            else:
                raw_id = item
                raw_name = item
            if raw_id in (None, ""):
                continue
            sid = str(raw_id)
            name = str(raw_name or sid)
            if not any(existing["id"] == sid for existing in items):
                items.append({"id": sid, "name": name})
        return items
    return []


def _story_card_sprint_options(payload: dict[str, Any]) -> list[dict[str, str]]:
    card = (payload or {}).get("_card") or {}
    return [
        {"id": str(s.get("id")), "name": str(s.get("name") or s.get("id"))}
        for s in (card.get("sprints") or [])
        if s.get("id")
    ]


def _merge_options(primary: list[dict[str, str]], extra: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    name_by_id = {str(item.get("id")): str(item.get("name") or item.get("id")) for item in extra if item.get("id")}
    for item in primary + extra:
        sid = str(item.get("id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        result.append({"id": sid, "name": name_by_id.get(sid, str(item.get("name") or sid))})
    return result


def _selected_from_options(options: list[dict[str, str]], field_type: str) -> Any:
    values = [item["id"] for item in options if item.get("id")]
    if field_type in ("multi_select", "multi_user", "work_item_related_multi_select", "workitem_related_multi_select"):
        return values
    return values[0] if values else None


def _field_text(meta: dict[str, Any], src: dict[str, Any] | None = None) -> str:
    src = src or {}
    return " ".join(
        str(part or "")
        for part in [
            meta.get("field_key"),
            meta.get("label"),
            src.get("from"),
        ]
    ).lower()


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _looks_like_submitter(text: str) -> bool:
    return _has_any(text, ("报告人", "提交人", "创建人", "发现人", "提出人", "反馈人", "reporter", "submitter", "creator", "created_by"))


def _looks_like_assignee(text: str) -> bool:
    return _has_any(text, ("经办", "处理人", "负责人", "参与人", "开发", "修复人", "承接", "assignee", "operator", "owner", "handler", "developer"))


def _looks_like_watcher(text: str) -> bool:
    return _has_any(text, ("关注人", "通知人", "抄送", "watcher", "watch", "notify", "cc"))


def _looks_like_sprint(text: str) -> bool:
    return _has_any(text, ("规划迭代", "所属迭代", "迭代", "排期", "版本", "sprint", "iteration", "planning_sprint"))


def _looks_like_requirement_link(text: str) -> bool:
    return _has_any(text, ("关联需求", "迭代需求", "需求", "story", "requirement", "prd"))


def _field_semantic(cfg: SpaceIssueConfig, meta: dict[str, Any], src: dict[str, Any] | None = None) -> str | None:
    """字段语义推断：先尊重显式配置，再用字段类型 + 通用命名判断。

    这里不按空间写死；新空间字段名变化时，只要语义词接近，就走同一套规则。
    """
    src = src or {}
    explicit = src.get("semantic")
    if explicit in {
        _SEMANTIC_SUBMITTER,
        _SEMANTIC_ASSIGNEE,
        _SEMANTIC_WATCHER,
        _SEMANTIC_SPRINT,
        _SEMANTIC_LINKED_REQUIREMENT,
    }:
        return str(explicit)

    key = str(meta.get("field_key") or "")
    field_type = str(meta.get("type") or "")
    text = _field_text(meta, src)
    source = src.get("source")

    if cfg.link_requirement_field and key == cfg.link_requirement_field:
        return _SEMANTIC_LINKED_REQUIREMENT
    if source == "current_user":
        return _SEMANTIC_SUBMITTER
    if source == "requirement_roles":
        return _SEMANTIC_ASSIGNEE
    if source == "requirement_field" and _looks_like_sprint(text):
        return _SEMANTIC_SPRINT

    if field_type in _USER_FIELD_TYPES:
        if _looks_like_assignee(text):
            return _SEMANTIC_ASSIGNEE
        if _looks_like_watcher(text):
            return _SEMANTIC_WATCHER
        if _looks_like_submitter(text):
            return _SEMANTIC_SUBMITTER
    if field_type in _RELATION_FIELD_TYPES:
        if _looks_like_sprint(text):
            return _SEMANTIC_SPRINT
        if _looks_like_requirement_link(text):
            return _SEMANTIC_LINKED_REQUIREMENT
    return None


def _current_user_key(ctx: _Ctx) -> str | None:
    key = ctx.user.feishu_user_key if ctx.user and ctx.user.feishu_user_key else None
    return str(key) if key else None


def _current_user_option(ctx: _Ctx) -> list[dict[str, str]]:
    key = _current_user_key(ctx)
    if not key:
        return []
    return [{"name": _disp_user(ctx), "id": key}]


def _user_selected_value(keys: list[str], field_type: str) -> Any:
    clean = [str(key) for key in keys if key]
    if field_type == "user":
        return clean[0] if clean else None
    return clean


def _requirement_assignee_people(ctx: _Ctx, src: dict[str, Any], required: bool = False) -> list[str]:
    roles = src.get("roles") or ["frontend", "backend"]
    people = _requirement_role_people(ctx, roles)
    if not people and src.get("fallback_roles"):
        people = _requirement_role_people(ctx, src["fallback_roles"])
    current_key = _current_user_key(ctx)
    if not people and required and current_key:
        people = [current_key]
    return people


def _names_for_people(ctx: _Ctx, people: list[str]) -> dict[str, str]:
    name_map = (ctx.pool.source_payload or {}).get("_people") or {}
    for user in ctx.users:
        key = getattr(user, "feishu_user_key", None)
        if key and key not in name_map:
            name_map[str(key)] = getattr(user, "display_name", None) or getattr(user, "name", None) or str(key)
    if ctx.user and ctx.user.feishu_user_key:
        name_map.setdefault(ctx.user.feishu_user_key, _disp_user(ctx))
    return name_map


def _people_options(ctx: _Ctx, people: list[str]) -> list[dict[str, str]]:
    name_map = _names_for_people(ctx, people)
    return [{"name": str(name_map.get(key, key)), "id": str(key)} for key in people]


def _apply_relation_name_overrides(items: list[dict[str, str]], overrides: dict[str, str]) -> list[dict[str, str]]:
    if not overrides:
        return items
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        sid = str(item.get("id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        result.append({"id": sid, "name": overrides.get(sid, str(item.get("name") or sid))})
    for sid, name in overrides.items():
        if sid not in seen:
            result.append({"id": sid, "name": name})
    return result


def _stamp_draft_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in fields:
        item["schema_version"] = BUG_DRAFT_SCHEMA_VERSION
    return fields


def _stored_draft_fields_current(fields: Any) -> bool:
    if not isinstance(fields, list) or not fields:
        return False
    return all(
        isinstance(item, dict) and item.get("schema_version") == BUG_DRAFT_SCHEMA_VERSION
        for item in fields
    )


def _requirement_link_value(work_item_id: Any, field_type: str) -> Any:
    if work_item_id in (None, "", [], {}):
        return None
    value = int(work_item_id) if str(work_item_id).isdigit() else work_item_id
    return [value] if field_type in _MULTI_REQUIREMENT_LINK_TYPES else value


def _requirement_link_display(title: Any, work_item_id: Any) -> str:
    title_text = str(title or work_item_id or "").strip()
    id_text = str(work_item_id or "").strip()
    if not id_text:
        return title_text
    if title_text and id_text not in title_text:
        return f"{title_text}（{id_text}）"
    return title_text or id_text


def _requirement_field_key_for_semantic(meta: dict[str, Any], src: dict[str, Any], semantic: str | None) -> str:
    if src.get("source") == "requirement_field":
        return str(src.get("from") or meta.get("field_key") or "")
    if semantic == _SEMANTIC_SPRINT:
        return str(src.get("from") or meta.get("field_key") or "planning_sprint")
    return str(src.get("from") or meta.get("field_key") or "")


async def _relation_name_overrides(ctx: _Ctx, meta_norm: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """补充关联字段显示名。目前优先解析迭代 ID，失败不阻断建单。"""
    field_ids: dict[str, list[str]] = {}
    for meta in meta_norm:
        key = meta.get("field_key")
        src = ctx.cfg.field_sources.get(str(key)) or {}
        semantic = _field_semantic(ctx.cfg, meta, src)
        if semantic != _SEMANTIC_SPRINT:
            continue
        from_key = _requirement_field_key_for_semantic(meta, src, semantic)
        items = _merge_options(
            _story_field_items(ctx.pool.source_payload or {}, from_key),
            _story_card_sprint_options(ctx.pool.source_payload or {}),
        )
        ids = [item["id"] for item in items if item.get("id")]
        if ids:
            field_ids[from_key] = ids
    if not field_ids:
        return {}

    client = fp.FeishuProjectClient()
    if not client.enabled:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            token = await client._plugin_token(http)
            all_ids = [sid for ids in field_ids.values() for sid in ids]
            names = await client.query_sprints(http, token, ctx.cfg.project_key, all_ids)
    except Exception:
        return {}
    return {
        field_key: {sid: names[sid] for sid in ids if sid in names}
        for field_key, ids in field_ids.items()
    }


def _build_fields(
    ctx: _Ctx,
    meta_norm: list[dict[str, Any]],
    model_choices: dict[str, Any],
    relation_names: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """按飞书 meta 复刻建单字段（顺序/必填/选项/默认值），再把我们手里的值映射进去。

    - 标题/描述/附件字段单独处理，不在动态字段里。
    - 有 source 映射的：用我们的值；其余沿用飞书默认值（不凭空造）。
    - select/multi_select/text/multi_text/user/relation 按系统标准控件可编辑；附件等无控件字段只读。
    """
    cfg = ctx.cfg
    special = {cfg.title_field, cfg.description_field}
    if cfg.attachment_field:
        special.add(cfg.attachment_field)
    fields: list[dict[str, Any]] = []
    present: set[str] = set()
    relation_names = relation_names or {}
    for m in meta_norm:
        key = m["field_key"]
        typ = m["type"]
        if key in special:
            continue
        present.add(key)
        options = m["options"]
        name_by_id = {o["id"]: o["name"] for o in options}
        src = cfg.field_sources.get(key) or {}
        semantic = _field_semantic(cfg, m, src)
        if semantic == _SEMANTIC_LINKED_REQUIREMENT:
            link_type = typ or cfg.link_requirement_field_type
            fields.append({
                "field_key": key, "label": m["label"], "type": link_type, "required": m["required"],
                "editable": False, "options": [], "selected": None,
                "display": _requirement_link_display(ctx.pool.title, ctx.pool.external_key),
                "submit_value": _requirement_link_value(ctx.pool.external_key, link_type),
            })
            continue
        s = src.get("source")
        editable = typ in _FRONTEND_EDITABLE_FIELD_TYPES
        selected: Any = None
        display = ""
        submit_value: Any = None
        field_options = options
        if semantic == _SEMANTIC_SUBMITTER:
            display = _DYNAMIC_CURRENT_USER_LABEL
            editable = False
        elif semantic == _SEMANTIC_ASSIGNEE:
            people = _requirement_assignee_people(ctx, src, bool(m["required"]))
            field_options = _people_options(ctx, people)
            selected = _user_selected_value(people, typ)
            editable = typ in _USER_FIELD_TYPES
        elif semantic == _SEMANTIC_WATCHER:
            field_options = _current_user_option(ctx)
            current_key = _current_user_key(ctx)
            selected = _user_selected_value([current_key] if current_key and m["required"] else [], typ)
            editable = typ in _USER_FIELD_TYPES
        elif semantic == _SEMANTIC_SPRINT:
            from_key = _requirement_field_key_for_semantic(m, src, semantic)
            payload = ctx.pool.source_payload or {}
            field_items = _merge_options(_story_field_items(payload, from_key), _story_card_sprint_options(payload))
            field_items = _apply_relation_name_overrides(field_items, relation_names.get(from_key, {}))
            if typ in _RELATION_FIELD_TYPES:
                field_options = _merge_options(field_items, field_options if isinstance(field_options, list) else [])
                selected = _selected_from_options(field_items, typ)
                selected_ids = set(_selected_from_options(field_items, "multi_user") or [])
                display = "、".join(item["name"] for item in field_options if item["id"] in selected_ids)
            elif typ in _CHOICE_FIELD_TYPES:
                val = _story_field_value(payload, from_key)
                raw_values = val if isinstance(val, list) else [val]
                selected_values = [name_by_id.get(v, name_by_id.get(str(v))) for v in raw_values if v not in (None, "")]
                selected_values = [v for v in selected_values if v]
                selected = selected_values if typ == "multi_select" else (selected_values[0] if selected_values else None)
                display = "、".join(selected_values)
            else:
                selected = "、".join(item["name"] for item in field_items) if field_items else ""
                display = selected
        elif s == "model":
            selected = model_choices.get(key)
        elif s == "requirement_field":
            from_key = src.get("from") or key
            payload = ctx.pool.source_payload or {}
            field_items = _story_field_items(payload, from_key)
            if from_key == "planning_sprint":
                field_items = _merge_options(field_items, _story_card_sprint_options(payload))
            val = _story_field_value(payload, from_key)
            if typ in _CHOICE_FIELD_TYPES:
                raw_values = val if isinstance(val, list) else [val]
                selected_values = [name_by_id.get(v, name_by_id.get(str(v))) for v in raw_values if v not in (None, "")]
                selected_values = [v for v in selected_values if v]
                selected = selected_values if typ == "multi_select" else (selected_values[0] if selected_values else None)
                display = "、".join(selected_values)
            elif typ in _USER_FIELD_TYPES or typ in _RELATION_FIELD_TYPES:
                field_options = _merge_options(field_items, field_options if isinstance(field_options, list) else [])
                selected = _selected_from_options(field_items, typ)
                display = "、".join(item["name"] for item in field_options if item["id"] in set(_selected_from_options(field_items, "multi_user") or []))
            elif typ in _TEXT_FIELD_TYPES:
                selected = "、".join(map(str, val)) if isinstance(val, list) else str(val or "")
                display = selected
            else:
                submit_value = val
                display = "、".join(item["name"] for item in field_items) if field_items else (
                    "、".join(map(str, val)) if isinstance(val, list) else str(val or "")
                )
                editable = False
        elif s == "requirement_field_mapped":
            # 需求里的某 select 值 → 本字段选项（如 小组 学习A → 缺陷分组 A组）。保持可改。
            raw = _story_field_value(ctx.pool.source_payload or {}, src.get("from") or key)
            mapped = (src.get("map") or {}).get(raw)
            selected = name_by_id.get(mapped) if mapped else None
        elif s == "case_expected":
            selected = ctx.body.expected_result or ""
        elif s == "case_steps":
            selected = ctx.body.steps_text or ""
        elif s == "diagnosis_reason":
            selected = ctx.diagnosis.get("reason") or ""
        elif s == "current_month":
            selected = _current_month_label()
        elif s == "current_month_zh":
            selected = _current_month_zh_label()
        elif s == "fixed":
            vid = src.get("value")
            selected = name_by_id.get(vid, vid) if typ in ("select", "multi_select") else vid
        elif key in model_choices:
            # 没有确定来源、飞书也没默认 → 用模型从该字段选项里挑的值兜底。
            selected = model_choices.get(key)
        else:
            # 没有我们的映射 → 沿用飞书自带默认值（如 P2 / ToC / 安卓），不凭空造。
            dflt = m["default"]
            if typ == "select" and isinstance(dflt, dict):
                selected = dflt.get("label")
            elif typ == "multi_select" and isinstance(dflt, list):
                selected = [d.get("label") for d in dflt if isinstance(d, dict)]
            elif typ in ("text", "multi_text", "link") and isinstance(dflt, str):
                selected = dflt
        fields.append({
            "field_key": key, "label": m["label"], "type": typ, "required": m["required"],
            "editable": editable, "options": field_options,
            "selected": selected, "display": display, "submit_value": submit_value,
        })
    # 关联需求（飞书建单表单里没有，但我们要带上，便于归类）
    lf = cfg.link_requirement_field
    if lf and lf not in present:
        fields.append({
            "field_key": lf, "label": "关联需求", "type": cfg.link_requirement_field_type, "required": False,
            "editable": False, "options": [], "selected": None,
            "display": _requirement_link_display(ctx.pool.title, ctx.pool.external_key),
            "submit_value": _requirement_link_value(ctx.pool.external_key, cfg.link_requirement_field_type),
        })
    return fields


def _pairs_from_fields(
    fields_in: list[dict[str, Any]],
    cfg: SpaceIssueConfig | None = None,
    current_user: User | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把弹窗回传的字段组装成 field_value_pairs，并拆成两组：

    - create_pairs：建单即落库的字段（文本/人员/关联）。
    - select_pairs：单选/多选——建单时会被模板默认异步覆盖，必须建单后等模板稳定再 update。
    """
    create_pairs: list[dict[str, Any]] = []
    select_pairs: list[dict[str, Any]] = []
    for f in fields_in:
        typ = f.get("type")
        key = f.get("field_key")
        if f.get("editable"):
            sel = f.get("selected")
            if typ == "select":
                idmap = {o["name"]: o["id"] for o in f.get("options") or []}
                raw = idmap.get(sel) if sel else None
            elif typ == "multi_select":
                idmap = {o["name"]: o["id"] for o in f.get("options") or []}
                raw = [idmap[x] for x in (sel or []) if x in idmap] or None
            else:
                raw = sel or None
        else:
            raw = f.get("submit_value")
        if cfg is not None:
            src = cfg.field_sources.get(str(key or "")) or {}
            if _field_semantic(cfg, f, src) == _SEMANTIC_SUBMITTER:
                user_key = getattr(current_user, "feishu_user_key", None) if current_user else None
                raw = [str(user_key)] if user_key else None
        value = _format_value(typ, raw)
        if value in (None, "", [], {}):
            continue
        pair = {"field_key": key, "field_value": value}
        if typ in ("select", "multi_select"):
            select_pairs.append(pair)
        else:
            create_pairs.append(pair)
    return create_pairs, select_pairs


def _model_target_keys(ctx: _Ctx, meta_norm: list[dict[str, Any]]) -> list[str]:
    """要交给模型预填的字段：① 配置成 source=model 的；② 既没配数据源、飞书又没默认值的
    select/multi_select/text/multi_text 空缺项（查不到准确信息→模型分析默认值）。"""
    cfg = ctx.cfg
    model_keys = set(_model_field_keys(cfg))
    special = {cfg.title_field, cfg.description_field}
    if cfg.attachment_field:
        special.add(cfg.attachment_field)
    targets: list[str] = []
    for m in meta_norm:
        key = m["field_key"]
        if key in special or m["type"] not in _MODEL_PREFILL_FIELD_TYPES:
            continue
        if key in cfg.field_sources:
            if key in model_keys:
                targets.append(key)
            continue  # 有其它数据源，不交给模型
        # 没配、飞书也没默认：只对“必填”空缺交给模型分析；选填空缺留空，不瞎猜。
        if not m.get("default") and m["required"]:
            targets.append(key)
    return targets


async def _generate_draft_content(session: AsyncSession, ctx: _Ctx) -> dict[str, Any]:
    """拉飞书 meta + 模型预填 + 标题润色 + 拼描述，得到完整字段草稿。"""
    meta_norm = await _pull_meta(ctx)
    meta_by_key = {m["field_key"]: m for m in meta_norm}
    targets = _model_target_keys(ctx, meta_norm)
    model_choices, title, relation_names = await asyncio.gather(
        _prefill_choices(session, ctx, meta_by_key, targets),
        _polish_title(ctx),
        _relation_name_overrides(ctx, meta_norm),
    )
    description = _build_description(ctx)
    fields = _stamp_draft_fields(_build_fields(ctx, meta_norm, model_choices, relation_names))
    return {"title": title, "description": description, "fields": fields}


async def _store_bug_draft(session: AsyncSession, case_id: int, content: dict[str, Any]) -> None:
    existing = await session.get(CaseBugDraft, case_id)
    if existing is None:
        session.add(CaseBugDraft(
            case_id=case_id,
            title=content["title"],
            description=content["description"],
            editable_fields=content["fields"],
        ))
    else:
        existing.title = content["title"]
        existing.description = content["description"]
        existing.editable_fields = content["fields"]
        existing.updated_at = func.now()


async def precompute_bug_draft(session: AsyncSession, case_id: int) -> None:
    """后台预生成 bug 草稿并存库。无来源/未配置/无凭证则静默跳过。"""
    try:
        ctx = await _gather(session, case_id)
    except BugSubmitError:
        return
    # bug 可多次提交：即便已提交过，也预备一份草稿供“再提一条”秒开。
    content = await _generate_draft_content(session, ctx)
    await _store_bug_draft(session, case_id, content)
    await session.commit()


async def build_bug_draft(session: AsyncSession, case_id: int, user_id: int) -> dict[str, Any]:
    ctx = await _gather(session, case_id, user_id)
    # bug 可多次提交：始终给一份可编辑草稿（用于“再提一条”），并带上已提交列表。
    # 预填失败（飞书 meta / 模型不可用、网络异常等）也要能打开弹窗手动提交，不能 500。先备好降级值。
    fallback_title = ctx.case.raw_title
    fallback_description = _build_description(ctx)
    # 优先用后台预生成的草稿，秒开；没有则现场生成并存起来（下次秒开）。
    stored = await session.get(CaseBugDraft, case_id)
    if stored is not None and _stored_draft_fields_current(stored.editable_fields):
        title, description, fields = stored.title, stored.description, stored.editable_fields
    else:
        try:
            content = await _generate_draft_content(session, ctx)
            await _store_bug_draft(session, case_id, content)
            await session.commit()
            title, description, fields = content["title"], content["description"], content["fields"]
        except BugSubmitError:
            raise
        except Exception:
            # 降级：用 case 标题 + 拼接描述、空字段，让用户能继续手动提交。
            title, description, fields = fallback_title, fallback_description, []
    return {
        "case_id": case_id,
        "space": ctx.pool.source_space,
        "title": title,
        "description": description,
        "fields": fields,
        "has_diagnosis_image": bool(ctx.diagnosis.get("key_image") or ctx.diagnosis.get("key_images")),
        "key_image": ctx.diagnosis.get("key_image"),
        "key_images": [
            {"platform": str(e.get("platform") or ""), "image": str(e.get("image") or "")}
            for e in (ctx.diagnosis.get("key_images") or [])
            if isinstance(e, dict) and e.get("image")
        ],
        "existing_bug_url": ctx.work_item.bug_url,
        "submitted_bugs": [
            {"url": str(b.get("url") or ""), "id": str(b.get("id") or "")}
            for b in (ctx.work_item.bugs or [])
            if b.get("url")
        ],
    }


def _media_local_path(media: Any) -> Path | None:
    if not media or not str(media).startswith("/media/"):
        return None
    p = Path(get_settings().repair_image_dir) / Path(str(media)).name
    return p if p.exists() else None


def _upload_image_suffix(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return suffix
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(content_type, ".png")


async def upload_bug_images(files: list[Any]) -> list[dict[str, str]]:
    if len(files) > BUG_IMAGE_UPLOAD_LIMIT:
        raise BugSubmitError(f"一次最多上传 {BUG_IMAGE_UPLOAD_LIMIT} 张图片")
    settings = get_settings()
    base_dir = Path(settings.repair_image_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    images: list[dict[str, str]] = []
    for file in files:
        content_type = str(getattr(file, "content_type", "") or "")
        if not content_type.startswith("image/"):
            raise BugSubmitError("只支持上传图片")
        raw = await file.read()
        if len(raw) > BUG_IMAGE_UPLOAD_MAX_BYTES:
            raise BugSubmitError("单张图片不能超过 6MB")
        suffix = _upload_image_suffix(str(getattr(file, "filename", "") or ""), content_type)
        filename = f"bug_{uuid.uuid4().hex[:12]}{suffix}"
        (base_dir / filename).write_bytes(raw)
        images.append({"platform": "手动补图", "image": f"/media/{filename}"})
    return images


def _key_image_local_path(diagnosis: dict[str, Any]) -> Path | None:
    return _media_local_path(diagnosis.get("key_image"))


def _key_image_local_paths(diagnosis: dict[str, Any]) -> list[tuple[str, Path]]:
    """每端的关键截图本地路径 [(端标签, Path)]，供提 bug 时全部带上。回退到单张 key_image。"""
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for entry in diagnosis.get("key_images") or []:
        if not isinstance(entry, dict):
            continue
        p = _media_local_path(entry.get("image"))
        if p and str(p) not in seen:
            seen.add(str(p))
            out.append((str(entry.get("platform") or ""), p))
    if not out:
        p = _key_image_local_path(diagnosis)
        if p:
            out.append(("", p))
    return out


def _description_without_bare_image_markdown(description: str) -> str:
    """飞书 MCP update_field 不能接收无 fileToken 的图片 markdown；转成普通文本链接。"""

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1).strip()
        url = match.group(2).strip()
        return f"{alt}：{url}" if alt else url

    return _IMAGE_MARKDOWN_RE.sub(replace, str(description or "")).strip()


def _rich_text_image_line(label: str, file_url: str, file_token: str) -> str:
    prefix = f"{label}：" if label else ""
    return f"{prefix}![]({file_url})<!--{file_token}-->"


def _rich_text_image_description(
    base_description: str,
    images: list[tuple[str, str, str]],
) -> str:
    base = _description_without_bare_image_markdown(base_description)
    lines = [_rich_text_image_line(label, url, token) for label, url, token in images]
    section = "诊断关键截图：\n" + "\n".join(lines)
    return f"{base}\n\n{section}" if base else section


async def append_rich_text_images_to_bug(
    http: httpx.AsyncClient,
    project_key: str,
    work_item_type: str,
    work_item_id: int | str,
    description_field: str,
    base_description: str,
    image_paths: list[Any],
    *,
    log_context: str,
) -> None:
    if not image_paths:
        return
    mcp = fpmcp.FeishuProjectMCPClient()
    if not mcp.enabled:
        logger.warning(
            "feishu bug image render skipped: MCP token is not configured "
            "context=%s project_key=%s work_item_type=%s work_item_id=%s image_count=%s",
            log_context,
            project_key,
            work_item_type,
            work_item_id,
            len(image_paths),
        )
        return

    uploaded: list[tuple[str, str, str]] = []
    for entry in image_paths:
        try:
            label, raw_path = entry
        except (TypeError, ValueError):
            logger.warning(
                "feishu bug image render skipped invalid image entry context=%s work_item_id=%s entry=%r",
                log_context,
                work_item_id,
                entry,
            )
            continue
        path = Path(raw_path)
        if not path.exists():
            logger.warning(
                "feishu bug image render skipped missing local file context=%s work_item_id=%s path=%s",
                log_context,
                work_item_id,
                path,
            )
            continue
        try:
            file_url, file_token = await mcp.upload_rich_text_image(
                http,
                path,
                project_key,
                work_item_id,
                work_item_type,
            )
        except Exception as exc:
            logger.warning(
                "feishu bug image render upload failed context=%s project_key=%s work_item_type=%s "
                "work_item_id=%s path=%s error=%s",
                log_context,
                project_key,
                work_item_type,
                work_item_id,
                path,
                exc,
            )
            continue
        uploaded.append((str(label or ""), file_url, file_token))

    if not uploaded:
        return
    description = _rich_text_image_description(base_description, uploaded)
    try:
        await mcp.call_tool(
            http,
            "update_field",
            {
                "project_key": project_key,
                "work_item_id": str(work_item_id),
                "fields": [{"field_key": description_field or "description", "field_value": description}],
            },
        )
    except Exception as exc:
        logger.warning(
            "feishu bug image render update_field failed context=%s project_key=%s work_item_type=%s "
            "work_item_id=%s image_count=%s error=%s",
            log_context,
            project_key,
            work_item_type,
            work_item_id,
            len(uploaded),
            exc,
        )


async def finalize_bug(
    project_key: str,
    work_item_type: str,
    work_item_id: int,
    select_pairs: list[dict[str, Any]],
    description_field: str,
    base_description: str,
    image_paths: list[list[str]],
) -> None:
    """后台异步收尾：① 等模板默认稳定后回写单选/多选字段（建单时会被模板异步覆盖）；
    ② 上传**每端**关键截图，以 MCP 富文本图片写回描述。best-effort，失败不影响已建 bug。
    image_paths: [[端标签, 本地路径], ...]。"""
    client = fp.FeishuProjectClient()
    if not client.enabled:
        return
    settle = max(0, get_settings().bug_field_settle_seconds)
    try:
        # 先等飞书模板把默认值刷完，否则我们随后写的单选/描述都会被模板默认顶掉。
        if settle:
            await asyncio.sleep(settle)
        async with httpx.AsyncClient(timeout=40) as http:
            token = await client._plugin_token(http)
            # ① 单选/多选
            if select_pairs:
                for pair in select_pairs:  # 逐个写，单个失败不影响其它
                    try:
                        await client.update_work_item(
                            http, token, project_key, work_item_type, work_item_id, [pair]
                        )
                    except Exception as exc:
                        logger.warning(
                            "feishu bug finalize select field update failed project_key=%s work_item_type=%s "
                            "work_item_id=%s field_key=%s error=%s",
                            project_key,
                            work_item_type,
                            work_item_id,
                            pair.get("field_key"),
                            exc,
                        )
                        continue
            await append_rich_text_images_to_bug(
                http,
                project_key,
                work_item_type,
                work_item_id,
                description_field,
                base_description,
                image_paths,
                log_context="standard_or_quick",
            )
    except Exception as exc:
        logger.warning(
            "feishu bug finalize failed project_key=%s work_item_type=%s work_item_id=%s error=%s",
            project_key,
            work_item_type,
            work_item_id,
            exc,
        )
        return


async def submit_bug(
    session: AsyncSession,
    case_id: int,
    user_id: int,
    overrides: dict[str, Any],
    background_tasks: Any = None,
) -> dict[str, Any]:
    ctx = await _gather(session, case_id, user_id, require_user=True)
    # bug 与执行解耦、可多次提交：不再因“已提交过”而拦截。仅限失败的 case。
    if ctx.work_item.execution_status != "failed":
        raise BugSubmitError("仅失败的 case 可以提交 bug")
    title = str(overrides.get("title") or ctx.case.raw_title).strip()
    description = str(overrides.get("description") or _build_description(ctx))
    fields_in = overrides.get("fields") or []
    if not title:
        raise BugSubmitError("标题不能为空")

    # 拆分：create_pairs 建单即落库；select_pairs 单选/多选要建单后再回写（模板会覆盖）。
    create_pairs, select_pairs = _pairs_from_fields(fields_in, ctx.cfg, ctx.user)
    create_pairs.append({"field_key": ctx.cfg.description_field, "field_value": description})

    client = fp.FeishuProjectClient()
    if not client.enabled:
        raise BugSubmitError("飞书项目凭证未配置")
    async with httpx.AsyncClient(timeout=30) as http:
        token = await client._plugin_token(http)
        new_id = await client.create_work_item(
            http, token, ctx.cfg.project_key, ctx.cfg.work_item_type, ctx.cfg.template_id, title, create_pairs
        )
    simple = (ctx.pool.source_payload or {}).get("simple_name") or ctx.cfg.project_key
    bug_url = f"{get_settings().feishu_project_site_domain.rstrip('/')}/{simple}/{ctx.cfg.work_item_type}/detail/{new_id}"
    # 追加到“已提交列表”（支持一条 case 多次提交）；单条字段保留为最近一次，向后兼容。
    bugs = list(ctx.work_item.bugs or [])
    bugs.append({"url": bug_url, "id": str(new_id)})
    ctx.work_item.bugs = bugs
    ctx.work_item.bug_url = bug_url
    ctx.work_item.bug_external_id = str(new_id)
    await session.commit()

    # 本次提交要带的证据图：overrides 未给 → 带全部（默认）；给了列表（可为空）→ 按用户在弹窗里的取舍带。
    selected_images = overrides.get("key_images")
    if selected_images is None:
        image_paths = [[label, str(p)] for label, p in _key_image_local_paths(ctx.diagnosis)]
    else:
        image_paths = []
        for entry in selected_images:
            if not isinstance(entry, dict):
                continue
            local = _media_local_path(entry.get("image"))
            if local:
                image_paths.append([str(entry.get("platform") or ""), str(local)])
    if background_tasks is not None and (select_pairs or image_paths):
        background_tasks.add_task(
            finalize_bug,
            ctx.cfg.project_key, ctx.cfg.work_item_type, new_id,
            select_pairs, ctx.cfg.description_field, description,
            image_paths,
        )
    return {
        "case_id": case_id,
        "bug_id": new_id,
        "bug_url": bug_url,
        "submitted_count": len(bugs),
        "message": "已提交 bug 到飞书项目。",
    }
