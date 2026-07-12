from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.quick import QuickBugDraft, QuickCase, QuickCaseBody, QuickCaseWorkItem, QuickRepairDraft, QuickSession
from app.models.requirements import User
from app.schemas.quick import QuickFeishuTargetOut
from app.services import bug_submit as formal_bug
from app.services.function_map_mount import compile_quick_context
from app.services.sources import feishu_project as fp


class QuickBugSubmitError(RuntimeError):
    pass


def describe_external_error(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "飞书项目接口返回了网关错误或非 JSON 响应，请稍后重试；如果持续出现，请检查插件权限和空间配置。"
    return str(exc)


LINK_RE = re.compile(r"https?://[^/]+/(?P<project>[^/?#]+)/(?P<type>[^/?#]+)/detail/(?P<id>\d+)")


@dataclass
class _Target:
    url: str
    project_key: str
    work_item_type: str
    work_item_id: int
    raw: dict[str, Any]


@dataclass
class _Ctx:
    session: QuickSession
    case: QuickCase
    body: QuickCaseBody
    work_item: QuickCaseWorkItem
    user: User | None
    users: list[User]
    target: _Target
    cfg: formal_bug.SpaceIssueConfig
    diagnosis: dict[str, Any]
    reference_bug: dict[str, Any] | None = None


async def set_feishu_requirement_target(
    session: AsyncSession,
    session_id: str,
    url: str,
) -> QuickFeishuTargetOut:
    quick_session = await session.get(QuickSession, session_id)
    if quick_session is None:
        raise QuickBugSubmitError("quick session 不存在")
    target = await _resolve_target_from_url(url, require_configured_requirement=True)
    await _clear_session_bug_drafts(session, session_id)
    quick_session.feishu_requirement_url = target.url
    quick_session.feishu_target = _target_to_payload(target)
    quick_session.updated_at = func.now()
    await session.commit()
    return QuickFeishuTargetOut(
        url=target.url,
        project_key=target.project_key,
        work_item_type=target.work_item_type,
        work_item_id=str(target.work_item_id),
        title=str(target.raw.get("name") or ""),
        readable=True,
        message="飞书需求链接已保存。",
    )


async def check_feishu_link(
    session: AsyncSession,
    session_id: str,
    url: str,
    kind: str,
) -> QuickFeishuTargetOut:
    quick_session = await session.get(QuickSession, session_id)
    if quick_session is None:
        raise QuickBugSubmitError("quick session 不存在")
    clean_url = url.strip()
    if not clean_url:
        return QuickFeishuTargetOut(url="", readable=False, message="链接为空。")
    try:
        target = await _resolve_target_from_url(clean_url, require_configured_requirement=(kind == "requirement"))
    except Exception as exc:
        return QuickFeishuTargetOut(url=clean_url, readable=False, message=f"当前链接无法解析：{describe_external_error(exc)}")
    if kind == "requirement":
        quick_session.feishu_target = _target_to_payload(target)
        quick_session.updated_at = func.now()
        await session.commit()
    return QuickFeishuTargetOut(
        url=target.url,
        project_key=target.project_key,
        work_item_type=target.work_item_type,
        work_item_id=str(target.work_item_id),
        title=str(target.raw.get("name") or ""),
        readable=True,
        message="链接解析成功。",
    )


async def _clear_session_bug_drafts(session: AsyncSession, session_id: str) -> None:
    case_ids = select(QuickCase.id).where(QuickCase.session_id == session_id)
    await session.execute(delete(QuickBugDraft).where(QuickBugDraft.case_id.in_(case_ids)))


async def precompute_bug_draft(session: AsyncSession, case_id: int) -> None:
    try:
        ctx = await _gather(session, case_id, None, require_user=False)
    except QuickBugSubmitError:
        return
    content = await _generate_draft_content(session, ctx)
    await _store_bug_draft(session, case_id, content)
    await session.commit()


async def build_bug_draft(
    session: AsyncSession,
    case_id: int,
    user_id: int | None,
    reference_bug_url: str | None = None,
) -> dict[str, Any]:
    ctx = await _gather(session, case_id, user_id, require_user=True, reference_bug_url=reference_bug_url)
    fallback_title = ctx.case.raw_title
    fallback_description = _build_description(ctx)
    stored = await session.get(QuickBugDraft, case_id)
    if (
        stored is not None
        and formal_bug._stored_draft_fields_current(stored.editable_fields)
        and not reference_bug_url
    ):
        title, description, fields = stored.title, stored.description, stored.editable_fields
    else:
        try:
            content = await _generate_draft_content(session, ctx)
            if not reference_bug_url:
                await _store_bug_draft(session, case_id, content)
                await session.commit()
            title, description, fields = content["title"], content["description"], content["fields"]
        except QuickBugSubmitError:
            raise
        except Exception:
            title, description, fields = fallback_title, fallback_description, []
    return {
        "case_id": case_id,
        "space": ctx.target.project_key,
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
            if isinstance(b, dict) and b.get("url")
        ],
    }


async def submit_bug(
    session: AsyncSession,
    case_id: int,
    user_id: int | None,
    overrides: dict[str, Any],
    background_tasks: Any = None,
) -> dict[str, Any]:
    ctx = await _gather(
        session,
        case_id,
        user_id,
        require_user=True,
        reference_bug_url=overrides.get("reference_bug_url"),
    )
    if ctx.work_item.execution_status != "failed":
        raise QuickBugSubmitError("仅失败的 case 可以提交 bug")
    title = str(overrides.get("title") or ctx.case.raw_title).strip()
    description = str(overrides.get("description") or _build_description(ctx))
    if not title:
        raise QuickBugSubmitError("标题不能为空")

    create_pairs, select_pairs = formal_bug._pairs_from_fields(overrides.get("fields") or [], ctx.cfg, ctx.user)
    create_pairs.append({"field_key": ctx.cfg.description_field, "field_value": description})

    client = fp.FeishuProjectClient()
    if not client.enabled:
        raise QuickBugSubmitError("飞书项目凭证未配置")
    async with httpx.AsyncClient(timeout=30) as http:
        token = await client._plugin_token(http)
        new_id = await client.create_work_item(
            http,
            token,
            ctx.cfg.project_key,
            ctx.cfg.work_item_type,
            ctx.cfg.template_id,
            title,
            create_pairs,
        )
    bug_url = f"{get_settings().feishu_project_site_domain.rstrip('/')}/{ctx.target.project_key}/{ctx.cfg.work_item_type}/detail/{new_id}"
    bugs = list(ctx.work_item.bugs or [])
    bugs.append({"url": bug_url, "id": str(new_id)})
    ctx.work_item.bugs = bugs
    ctx.work_item.bug_url = bug_url
    ctx.work_item.bug_external_id = str(new_id)
    await session.commit()

    selected_images = overrides.get("key_images")
    if selected_images is None:
        image_paths = [[label, str(path)] for label, path in _key_image_local_paths(ctx.diagnosis)]
    else:
        image_paths = []
        for entry in selected_images:
            if not isinstance(entry, dict):
                continue
            local = formal_bug._media_local_path(entry.get("image"))
            if local:
                image_paths.append([str(entry.get("platform") or ""), str(local)])
    if background_tasks is not None and (select_pairs or image_paths):
        background_tasks.add_task(
            formal_bug.finalize_bug,
            ctx.cfg.project_key,
            ctx.cfg.work_item_type,
            new_id,
            select_pairs,
            ctx.cfg.description_field,
            description,
            image_paths,
        )
    return {
        "case_id": case_id,
        "bug_id": new_id,
        "bug_url": bug_url,
        "submitted_count": len(bugs),
        "message": "已从 quick session 提交 bug 到飞书项目。",
    }


async def _gather(
    session: AsyncSession,
    case_id: int,
    user_id: int | None,
    *,
    require_user: bool,
    reference_bug_url: str | None = None,
) -> _Ctx:
    case = await session.get(QuickCase, case_id)
    body = await session.get(QuickCaseBody, case_id)
    work_item = await session.get(QuickCaseWorkItem, case_id)
    if case is None or body is None or work_item is None:
        raise QuickBugSubmitError("quick case 不存在")
    quick_session = await session.get(QuickSession, case.session_id)
    if quick_session is None:
        raise QuickBugSubmitError("quick session 不存在")
    target = await _session_target(quick_session)
    configs = formal_bug.load_issue_config()
    cfg = configs.get(target.project_key)
    if cfg is None or not cfg.template_id:
        raise QuickBugSubmitError(f"空间 {target.project_key} 未配置 issue 提交（backend/config/feishu_issue.json）")
    chosen_user_id = user_id or quick_session.current_user_id
    user = await session.get(User, chosen_user_id) if chosen_user_id else None
    if require_user and user is None:
        raise QuickBugSubmitError("请选择提交人后再提交 bug")
    if user and quick_session.current_user_id != user.id:
        quick_session.current_user_id = user.id
        quick_session.updated_at = func.now()
    user_rows = await session.execute(
        select(User)
        .where(User.feishu_user_key.is_not(None))
        .order_by(User.display_name.asc())
    )
    users = list(user_rows.scalars().all())
    draft = await session.scalar(
        select(QuickRepairDraft).where(QuickRepairDraft.case_id == case_id).order_by(QuickRepairDraft.id.desc())
    )
    diagnosis = (draft.diagnosis_snapshot or {}) if draft else {}
    reference_bug = await _safe_reference_bug(reference_bug_url) if reference_bug_url else None
    return _Ctx(quick_session, case, body, work_item, user, users, target, cfg, diagnosis, reference_bug)


async def _session_target(quick_session: QuickSession) -> _Target:
    if isinstance(quick_session.feishu_target, dict) and quick_session.feishu_target.get("raw"):
        payload = quick_session.feishu_target
        _require_complete_space_config(
            str(payload.get("project_key") or ""),
            str(payload.get("work_item_type") or "story"),
        )
        return _Target(
            url=str(payload.get("url") or quick_session.feishu_requirement_url or ""),
            project_key=str(payload.get("project_key") or ""),
            work_item_type=str(payload.get("work_item_type") or "story"),
            work_item_id=int(payload.get("work_item_id") or 0),
            raw=dict(payload.get("raw") or {}),
        )
    if not quick_session.feishu_requirement_url:
        raise QuickBugSubmitError("提交 bug 前需要先绑定飞书需求链接")
    target = await _resolve_target_from_url(
        quick_session.feishu_requirement_url,
        require_configured_requirement=True,
    )
    quick_session.feishu_target = _target_to_payload(target)
    return target


async def _resolve_target_from_url(url: str, *, require_configured_requirement: bool = False) -> _Target:
    parsed = _parse_feishu_url(url)
    space: fp.SpaceConfig | None = None
    source_cfg: fp.FeishuSourceConfig | None = None
    work_item_type = str(parsed["work_item_type"])
    if require_configured_requirement:
        source_cfg, space, _ = _require_complete_space_config(parsed["project_key"], work_item_type)
        work_item_type = space.work_item_type or source_cfg.work_item_type or work_item_type
    client = fp.FeishuProjectClient()
    if not client.enabled:
        raise QuickBugSubmitError("飞书项目凭证未配置")
    async with httpx.AsyncClient(timeout=30) as http:
        token = await client._plugin_token(http)
        raw = await client.get_work_item(
            http,
            token,
            parsed["project_key"],
            work_item_type,
            parsed["work_item_id"],
        )
        if space is not None:
            raw = dict(raw or {})
            await _enrich_requirement_people(http, client, token, parsed["project_key"], raw, space)
    raw = dict(raw or {})
    raw.setdefault("simple_name", parsed["project_key"])
    raw.setdefault("work_item_type_key", parsed["work_item_type"])
    return _Target(
        url=url.strip(),
        project_key=parsed["project_key"],
        work_item_type=parsed["work_item_type"],
        work_item_id=int(parsed["work_item_id"]),
        raw=raw,
    )


async def _safe_reference_bug(url: str | None) -> dict[str, Any] | None:
    if not url:
        return None
    try:
        target = await _resolve_target_from_url(url)
        return target.raw
    except Exception:
        return None


def _require_complete_space_config(
    project_key: str,
    url_work_item_type: str | None = None,
) -> tuple[fp.FeishuSourceConfig, fp.SpaceConfig, formal_bug.SpaceIssueConfig]:
    source_cfg = fp.load_source_config()
    if source_cfg is None:
        raise QuickBugSubmitError("标准版飞书需求配置不存在，快速模式暂不能提交 bug。")
    space = next((item for item in source_cfg.spaces if item.project_key == project_key), None)
    if space is None:
        raise QuickBugSubmitError(f"空间 {project_key} 未接入标准版需求配置，快速模式暂不能提交 bug。")
    if url_work_item_type:
        allowed = {
            str(value)
            for value in (space.work_item_url_type, space.work_item_type, source_cfg.work_item_type)
            if value
        }
        if allowed and url_work_item_type not in allowed:
            raise QuickBugSubmitError("请粘贴该空间的需求详情页链接；当前链接类型不属于标准版需求配置。")
    issue_cfg = formal_bug.load_issue_config().get(project_key)
    if issue_cfg is None or not issue_cfg.template_id:
        raise QuickBugSubmitError(f"空间 {project_key} 未配置完整 bug 模板，快速模式暂不能提交 bug。")
    return source_cfg, space, issue_cfg


async def _enrich_requirement_people(
    http: httpx.AsyncClient,
    client: fp.FeishuProjectClient,
    token: str,
    project_key: str,
    raw: dict[str, Any],
    space: fp.SpaceConfig,
) -> None:
    owners = fp._extract_role_owners(raw)
    keys: list[str] = []
    for concept in ("tester", "frontend", "backend"):
        for user_key in fp.user_keys_for_concept(raw, owners, space, concept):
            if user_key and user_key not in keys:
                keys.append(user_key)
    if not keys:
        return
    try:
        users = await client.query_users(http, token, project_key, keys)
    except Exception:
        return
    people = dict(raw.get("_people") or {})
    for user_key, info in users.items():
        name = str(info.get("name_cn") or info.get("name") or user_key)
        if name:
            people[str(user_key)] = name
    if people:
        raw["_people"] = people


def _parse_feishu_url(url: str) -> dict[str, Any]:
    match = LINK_RE.search(str(url or "").strip())
    if not match:
        raise QuickBugSubmitError("飞书链接格式不正确，请粘贴需求详情页链接")
    return {
        "project_key": match.group("project"),
        "work_item_type": match.group("type"),
        "work_item_id": int(match.group("id")),
    }


def _target_to_payload(target: _Target) -> dict[str, Any]:
    return {
        "url": target.url,
        "project_key": target.project_key,
        "work_item_type": target.work_item_type,
        "work_item_id": target.work_item_id,
        "raw": target.raw,
    }


async def _generate_draft_content(session: AsyncSession, ctx: _Ctx) -> dict[str, Any]:
    meta_norm = await _pull_meta(ctx)
    meta_by_key = {m["field_key"]: m for m in meta_norm}
    targets = _model_target_keys(ctx, meta_norm)
    relation_names = await _relation_name_overrides(ctx, meta_norm)
    # gather 内并发不能共用 session 查库；先编译好 Function Map 再传入。
    function_map_context = (
        await compile_quick_context(session, ctx.session.session_id, "mixed")
    ).context
    model_choices, title = await asyncio.gather(
        _prefill_choices(ctx, meta_by_key, targets, function_map_context),
        _polish_title(ctx),
    )
    fields = formal_bug._stamp_draft_fields(_build_fields(ctx, meta_norm, model_choices, relation_names))
    description = _build_description(ctx)
    return {"title": title, "description": description, "fields": fields}


def _model_target_keys(ctx: _Ctx, meta_norm: list[dict[str, Any]]) -> list[str]:
    cfg = ctx.cfg
    model_keys = set(formal_bug._model_field_keys(cfg))
    special = {cfg.title_field, cfg.description_field}
    if cfg.attachment_field:
        special.add(cfg.attachment_field)
    targets: list[str] = []
    for meta in meta_norm:
        key = meta["field_key"]
        if key in special or meta["type"] not in formal_bug._MODEL_PREFILL_FIELD_TYPES:
            continue
        if key in cfg.field_sources:
            if key in model_keys:
                targets.append(key)
            continue
        if not meta.get("default") and meta["required"]:
            targets.append(key)
    return targets


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
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=max_tokens,
            )
        )
        title = (resp.choices[0].message.content or "").strip().strip("「」\"' \n")
        return title.splitlines()[0][:60] if title else ctx.case.raw_title
    except Exception:
        return ctx.case.raw_title


async def _prefill_choices(
    ctx: _Ctx,
    meta_by_key: dict[str, dict[str, Any]],
    target_keys: list[str],
    function_map_context: str,
) -> dict[str, str | list[str]]:
    model_keys = [key for key in target_keys if key in meta_by_key]
    if not model_keys:
        return {}
    specs: dict[str, dict[str, Any]] = {}
    for key in model_keys:
        meta = meta_by_key[key]
        typ = meta.get("type")
        names = [option["name"] for option in meta.get("options") or []]
        if typ in formal_bug._CHOICE_FIELD_TYPES:
            if names:
                specs[key] = {"type": typ, "multi": typ == "multi_select", "options": names}
        elif typ in formal_bug._TEXT_FIELD_TYPES:
            specs[key] = {"type": typ, "multi": False, "options": []}

    client, settings = _llm_client()
    if client is None or not specs:
        return {}

    payload = {
        "case_title": ctx.case.raw_title,
        "path": _path(ctx.case),
        "preconditions": ctx.body.preconditions,
        "steps": ctx.body.steps_text,
        "expected": ctx.body.expected_result,
        "diagnosis_reason": ctx.diagnosis.get("reason", ""),
        "function_context": function_map_context,
        "fields": specs,
    }
    prompt = (
        "你在给一个【quick session 里已执行失败的测试用例】创建 bug。请据 case 内容、失败原因和本 session function 上下文，"
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
        max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=max_tokens,
            )
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception:
        return {}

    def _match(value: Any, names: list[str]) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        if value in names:
            return value
        for name in names:
            if value in name or name in value:
                return name
        return None

    out: dict[str, Any] = {}
    for key in specs:
        typ = specs[key]["type"]
        value = data.get(key)
        if typ in formal_bug._TEXT_FIELD_TYPES:
            if isinstance(value, list):
                text_value = "；".join(str(item).strip() for item in value if str(item).strip())
            else:
                text_value = str(value).strip() if value not in (None, "", [], {}) else ""
            if text_value:
                out[key] = text_value
            continue
        names = specs[key]["options"]
        if specs[key]["multi"]:
            chosen: list[str] = []
            for item in (value if isinstance(value, list) else [value]):
                matched = _match(item, names)
                if matched and matched not in chosen:
                    chosen.append(matched)
            if chosen:
                out[key] = chosen
        else:
            matched = _match(value, names)
            if matched:
                out[key] = matched
    return out


async def _pull_meta(ctx: _Ctx) -> list[dict[str, Any]]:
    client = fp.FeishuProjectClient()
    if not client.enabled:
        return []
    async with httpx.AsyncClient(timeout=25) as http:
        token = await client._plugin_token(http)
        raw = await client.get_create_meta(http, token, ctx.cfg.project_key, ctx.cfg.work_item_type)
    return formal_bug._normalize_meta(raw)


async def _store_bug_draft(session: AsyncSession, case_id: int, content: dict[str, Any]) -> None:
    existing = await session.get(QuickBugDraft, case_id)
    if existing is None:
        session.add(
            QuickBugDraft(
                case_id=case_id,
                title=content["title"],
                description=content["description"],
                editable_fields=content["fields"],
            )
        )
    else:
        existing.title = content["title"]
        existing.description = content["description"]
        existing.editable_fields = content["fields"]
        existing.updated_at = func.now()


def _build_description(ctx: _Ctx) -> str:
    parts: list[str] = []
    if ctx.diagnosis.get("reason"):
        block = f"**失败原因**\n{ctx.diagnosis.get('reason')}"
        if ctx.diagnosis.get("evidence"):
            block += f"\n\n**证据**\n{ctx.diagnosis.get('evidence')}"
        parts.append(block)
    if ctx.work_item.report_url:
        parts.append(f"执行报告：{ctx.work_item.report_url}")
    parts.extend([
        f"**预期结果**\n{ctx.body.expected_result or '无'}",
        f"**前置条件**\n{ctx.body.preconditions or '无'}",
        f"**操作步骤**\n{ctx.body.steps_text or '无'}",
        f"**层级**：{_path(ctx.case) or '无'}",
    ])
    if ctx.reference_bug:
        parts.append(_reference_bug_section(ctx.reference_bug))
    return "\n\n".join(parts)


def _reference_bug_section(raw: dict[str, Any]) -> str:
    title = str(raw.get("name") or raw.get("title") or "")
    status = raw.get("work_item_status")
    if isinstance(status, dict):
        status_text = str(status.get("name") or status.get("state_key") or "")
    else:
        status_text = str(status or "")
    return "\n".join(
        part for part in [
            "**参考 bug**",
            f"标题：{title}" if title else "",
            f"状态：{status_text}" if status_text else "",
        ] if part
    )


async def _relation_name_overrides(ctx: _Ctx, meta_norm: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    field_ids: dict[str, list[str]] = {}
    for meta in meta_norm:
        key = meta.get("field_key")
        src = ctx.cfg.field_sources.get(str(key)) or {}
        semantic = formal_bug._field_semantic(ctx.cfg, meta, src)
        if semantic != formal_bug._SEMANTIC_SPRINT:
            continue
        from_key = formal_bug._requirement_field_key_for_semantic(meta, src, semantic)
        items = formal_bug._merge_options(
            formal_bug._story_field_items(ctx.target.raw or {}, from_key),
            formal_bug._story_card_sprint_options(ctx.target.raw or {}),
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
    cfg = ctx.cfg
    special = {cfg.title_field, cfg.description_field}
    if cfg.attachment_field:
        special.add(cfg.attachment_field)
    fields: list[dict[str, Any]] = []
    present: set[str] = set()
    relation_names = relation_names or {}
    for meta in meta_norm:
        key = meta["field_key"]
        typ = meta["type"]
        if key in special:
            continue
        present.add(key)
        options = meta["options"]
        name_by_id = {option["id"]: option["name"] for option in options}
        src = cfg.field_sources.get(key) or {}
        semantic = formal_bug._field_semantic(cfg, meta, src)
        if semantic == formal_bug._SEMANTIC_LINKED_REQUIREMENT:
            link_type = typ or cfg.link_requirement_field_type
            fields.append({
                "field_key": key,
                "label": meta["label"],
                "type": link_type,
                "required": meta["required"],
                "editable": False,
                "options": [],
                "selected": None,
                "display": formal_bug._requirement_link_display(
                    (ctx.target.raw or {}).get("name"),
                    ctx.target.work_item_id,
                ),
                "submit_value": formal_bug._requirement_link_value(ctx.target.work_item_id, link_type),
            })
            continue
        source = src.get("source")
        editable = typ in formal_bug._FRONTEND_EDITABLE_FIELD_TYPES
        selected: Any = None
        display = ""
        submit_value: Any = None
        field_options = options
        if semantic == formal_bug._SEMANTIC_SUBMITTER:
            display = formal_bug._DYNAMIC_CURRENT_USER_LABEL
            editable = False
        elif semantic == formal_bug._SEMANTIC_ASSIGNEE:
            roles = src.get("roles") or ["frontend", "backend"]
            people = _requirement_role_people(ctx, roles)
            if not people and src.get("fallback_roles"):
                people = _requirement_role_people(ctx, src["fallback_roles"])
            current_key = ctx.user.feishu_user_key if ctx.user and ctx.user.feishu_user_key else None
            if not people and meta["required"] and current_key:
                people = [current_key]
            name_map = (ctx.target.raw or {}).get("_people") or {}
            field_options = [{"name": name_map.get(k, k), "id": k} for k in people]
            selected = formal_bug._user_selected_value(people, typ)
            editable = typ in formal_bug._USER_FIELD_TYPES
        elif semantic == formal_bug._SEMANTIC_WATCHER:
            current_key = ctx.user.feishu_user_key if ctx.user and ctx.user.feishu_user_key else None
            field_options = [{"name": _disp_user(ctx), "id": current_key}] if current_key else []
            selected = formal_bug._user_selected_value([current_key] if current_key and meta["required"] else [], typ)
            editable = typ in formal_bug._USER_FIELD_TYPES
        elif semantic == formal_bug._SEMANTIC_SPRINT:
            from_key = formal_bug._requirement_field_key_for_semantic(meta, src, semantic)
            payload = ctx.target.raw or {}
            field_items = formal_bug._merge_options(
                formal_bug._story_field_items(payload, from_key),
                formal_bug._story_card_sprint_options(payload),
            )
            field_items = formal_bug._apply_relation_name_overrides(field_items, relation_names.get(from_key, {}))
            if typ in formal_bug._RELATION_FIELD_TYPES:
                field_options = formal_bug._merge_options(field_items, field_options if isinstance(field_options, list) else [])
                selected = formal_bug._selected_from_options(field_items, typ)
                selected_ids = set(formal_bug._selected_from_options(field_items, "multi_user") or [])
                display = "、".join(item["name"] for item in field_options if item["id"] in selected_ids)
            elif typ in formal_bug._CHOICE_FIELD_TYPES:
                value = formal_bug._story_field_value(payload, from_key)
                raw_values = value if isinstance(value, list) else [value]
                selected_values = [name_by_id.get(v, name_by_id.get(str(v))) for v in raw_values if v not in (None, "")]
                selected_values = [item for item in selected_values if item]
                selected = selected_values if typ == "multi_select" else (selected_values[0] if selected_values else None)
                display = "、".join(selected_values)
            else:
                selected = "、".join(item["name"] for item in field_items) if field_items else ""
                display = selected
        elif source == "model":
            selected = model_choices.get(key)
        elif source == "requirement_field":
            from_key = src.get("from") or key
            payload = ctx.target.raw or {}
            field_items = formal_bug._story_field_items(payload, from_key)
            if from_key == "planning_sprint":
                field_items = formal_bug._merge_options(field_items, formal_bug._story_card_sprint_options(payload))
            value = formal_bug._story_field_value(payload, from_key)
            if typ in formal_bug._CHOICE_FIELD_TYPES:
                raw_values = value if isinstance(value, list) else [value]
                selected_values = [name_by_id.get(v, name_by_id.get(str(v))) for v in raw_values if v not in (None, "")]
                selected_values = [item for item in selected_values if item]
                selected = selected_values if typ == "multi_select" else (selected_values[0] if selected_values else None)
                display = "、".join(selected_values)
            elif typ in formal_bug._USER_FIELD_TYPES or typ in formal_bug._RELATION_FIELD_TYPES:
                field_options = formal_bug._merge_options(field_items, field_options if isinstance(field_options, list) else [])
                selected = formal_bug._selected_from_options(field_items, typ)
                selected_ids = set(formal_bug._selected_from_options(field_items, "multi_user") or [])
                display = "、".join(item["name"] for item in field_options if item["id"] in selected_ids)
            elif typ in formal_bug._TEXT_FIELD_TYPES:
                selected = "、".join(map(str, value)) if isinstance(value, list) else str(value or "")
                display = selected
            else:
                submit_value = value
                display = "、".join(item["name"] for item in field_items) if field_items else (
                    "、".join(map(str, value)) if isinstance(value, list) else str(value or "")
                )
                editable = False
        elif source == "requirement_field_mapped":
            raw = formal_bug._story_field_value(ctx.target.raw or {}, src.get("from") or key)
            mapped = (src.get("map") or {}).get(raw)
            selected = name_by_id.get(mapped) if mapped else None
        elif source == "case_expected":
            selected = ctx.body.expected_result or ""
        elif source == "case_steps":
            selected = ctx.body.steps_text or ""
        elif source == "diagnosis_reason":
            selected = ctx.diagnosis.get("reason") or ""
        elif source == "current_month":
            selected = formal_bug._current_month_label()
        elif source == "current_month_zh":
            selected = formal_bug._current_month_zh_label()
        elif source == "fixed":
            value_id = src.get("value")
            selected = name_by_id.get(value_id, value_id) if typ in ("select", "multi_select") else value_id
        elif key in model_choices:
            selected = model_choices.get(key)
        else:
            default = meta["default"]
            if typ == "select" and isinstance(default, dict):
                selected = default.get("label")
            elif typ == "multi_select" and isinstance(default, list):
                selected = [item.get("label") for item in default if isinstance(item, dict)]
            elif typ in ("text", "multi_text", "link") and isinstance(default, str):
                selected = default
        fields.append({
            "field_key": key,
            "label": meta["label"],
            "type": typ,
            "required": meta["required"],
            "editable": editable,
            "options": field_options,
            "selected": selected,
            "display": display,
            "submit_value": submit_value,
        })
    if cfg.link_requirement_field and cfg.link_requirement_field not in present:
        fields.append({
            "field_key": cfg.link_requirement_field,
            "label": "关联需求",
            "type": cfg.link_requirement_field_type,
            "required": False,
            "editable": False,
            "options": [],
            "selected": None,
            "display": formal_bug._requirement_link_display(
                (ctx.target.raw or {}).get("name"),
                ctx.target.work_item_id,
            ),
            "submit_value": formal_bug._requirement_link_value(
                ctx.target.work_item_id,
                cfg.link_requirement_field_type,
            ),
        })
    return fields


def _requirement_role_people(ctx: _Ctx, roles: list[str]) -> list[str]:
    cfg = fp.load_source_config()
    space = next((item for item in cfg.spaces if item.project_key == ctx.target.project_key), None) if cfg else None
    if space is None:
        return []
    owners = fp._extract_role_owners(ctx.target.raw or {})
    keys: list[str] = []
    for concept in roles:
        for user_key in fp.user_keys_for_concept(ctx.target.raw or {}, owners, space, concept):
            if user_key and user_key not in keys:
                keys.append(user_key)
    return keys


def _disp_user(ctx: _Ctx) -> str:
    if ctx.user is None:
        return "（当前用户）"
    return getattr(ctx.user, "display_name", None) or getattr(ctx.user, "name", "") or "（当前用户）"


def _path(case: QuickCase) -> str:
    nodes = getattr(case, "path_nodes", None)
    if not isinstance(nodes, list):
        return ""
    return " / ".join(
        str(node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text") or "")
        for node in nodes
        if isinstance(node, dict)
        and (node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text"))
    )


def _key_image_local_paths(diagnosis: dict[str, Any]) -> list[tuple[str, Any]]:
    paths: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for entry in diagnosis.get("key_images") or []:
        if not isinstance(entry, dict):
            continue
        path = formal_bug._media_local_path(entry.get("image"))
        if path and str(path) not in seen:
            seen.add(str(path))
            paths.append((str(entry.get("platform") or ""), path))
    if not paths:
        path = formal_bug._media_local_path(diagnosis.get("key_image"))
        if path:
            paths.append(("", path))
    return paths
