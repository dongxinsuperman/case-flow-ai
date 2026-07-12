"""飞书项目（Meego）来源适配器。

职责：用插件凭证（env）调飞书项目 OpenAPI，把"需求"工作项拉进 `requirement_pool`
（只新增/更新，不因外部缺失而删除），并同步飞书用户、解析负责人(QA/前后端)。

分层：
- 配置（`FeishuSourceConfig` / `SpaceConfig`）：拉哪些空间、时间、角色映射、状态别名。
  全部从 `backend/config/feishu_project.json` 读取——换公司/换空间只改那个文件 + env 凭证。
- `map_work_item`：纯函数，原始工作项 dict → `StandardProject`，可单测、与网络无关。
- `FeishuProjectClient`：网络层（plugin_token + 工作项 filter + user/query）。**端点已真机验证**。
- `pull_into_pool`：多空间拉取 + 映射 + 用户同步 + upsert 入池（全量 payload 留存）。

真实飞书项目环境已验证端点：
- POST /open_api/authen/plugin_token            取插件 token
- POST /open_api/{project_key}/work_item/filter 列表/按 id 查单条（body: work_item_type_keys/page_num/page_size/work_item_ids）
- POST /open_api/user/query                      user_key → 姓名/头像/open_id
角色 role_id↔名 经飞书 MCP list_workitem_role_config 离线确认，写入配置文件 role_map。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.requirements import RequirementAssignee, RequirementItem, RequirementPool, User

SOURCE_TYPE = "feishu_project"

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "feishu_project.json"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class SpaceConfig:
    """单个空间（部门）的拉取配置。"""

    project_key: str
    name: str
    # 默认沿用全局 work_item_type；个别空间可覆盖为自己的工作项类型 key。
    work_item_type: str | None = None
    # URL 上使用的工作项类型别名；有些 OpenAPI 拉取要长 type_key，但页面 URL 用 api_name。
    work_item_url_type: str | None = None
    # 内部概念 → 该空间飞书 role_id 列表（tester=顶部QA身份 / frontend / backend）。
    role_map: dict[str, list[str]] = field(default_factory=dict)
    # 内部概念 → 该空间飞书自定义人员字段 key 列表。有些空间没有 role_owners，
    # 但会用 multi_user 字段承载开发/测试人员。
    user_field_map: dict[str, list[str]] = field(default_factory=dict)
    # 该空间“测试中”对应的工作流 state_key（可空，命中则标记测试中）。
    status_in_testing_state_keys: list[str] = field(default_factory=list)
    # 节点流空间里多个节点可能共享同一个 state_key（如 doing），此时按当前节点名/id 判定。
    status_in_testing_node_names: list[str] = field(default_factory=list)
    status_in_testing_node_ids: list[str] = field(default_factory=list)
    # 迭代/排期所在字段 key（如 planning_sprint）；该空间没有迭代概念则留空 None。
    sprint_field: str | None = None


@dataclass
class FeishuSourceConfig:
    """来源总配置（从 feishu_project.json 加载）。"""

    work_item_type: str = "story"
    default_sprint_field: str | None = None
    created_after: str | None = None
    page_size: int = 50
    max_items: int = 0
    spaces: list[SpaceConfig] = field(default_factory=list)

    @property
    def created_after_ms(self) -> int | None:
        if not self.created_after:
            return None
        try:
            dt = datetime.strptime(self.created_after, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return int(dt.timestamp() * 1000)


def load_source_config(path: Path | None = None) -> FeishuSourceConfig | None:
    """从 JSON 配置文件加载来源配置；文件缺失或无空间返回 None。"""
    cfg_path = path or CONFIG_PATH
    if not cfg_path.exists():
        return None
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    spaces_raw = raw.get("spaces") or []
    default_sprint_field = str(raw.get("default_sprint_field") or "").strip() or None
    spaces = [
        SpaceConfig(
            project_key=str(sp.get("project_key") or "").strip(),
            name=str(sp.get("name") or sp.get("project_key") or "").strip(),
            work_item_type=(str(sp.get("work_item_type")).strip() if sp.get("work_item_type") else None),
            work_item_url_type=(
                str(sp.get("work_item_url_type")).strip() if sp.get("work_item_url_type") else None
            ),
            role_map={k: [str(v) for v in (vals or [])] for k, vals in (sp.get("role_map") or {}).items()},
            user_field_map={
                k: [str(v) for v in (vals or [])] for k, vals in (sp.get("user_field_map") or {}).items()
            },
            status_in_testing_state_keys=[str(v) for v in (sp.get("status_in_testing_state_keys") or [])],
            status_in_testing_node_names=[str(v) for v in (sp.get("status_in_testing_node_names") or [])],
            status_in_testing_node_ids=[str(v) for v in (sp.get("status_in_testing_node_ids") or [])],
            sprint_field=(
                (str(sp.get("sprint_field") or "").strip() or None)
                if "sprint_field" in sp
                else default_sprint_field
            ),
        )
        for sp in spaces_raw
        if sp.get("project_key")
    ]
    if not spaces:
        return None
    pull = raw.get("pull") or {}
    return FeishuSourceConfig(
        work_item_type=str(raw.get("work_item_type") or "story"),
        default_sprint_field=default_sprint_field,
        created_after=(pull.get("created_after") or None),
        page_size=int(pull.get("page_size") or 50),
        max_items=int(pull.get("max_items") or 0),
        spaces=spaces,
    )


def lifecycle_for(
    source_space: str | None,
    external_status: str | None,
    payload: dict[str, Any] | None = None,
    config: FeishuSourceConfig | None = None,
) -> str:
    """按空间配置判定二级需求生命周期：状态命中该空间‘测试中’码→“测试中”，否则“其他”。

    若该空间未配置 status_in_testing_state_keys（留空），保持占位：一律“测试中”，
    避免在未配置前漏掉任务。配置后即按 state_key 精确过滤。
    """
    cfg = config or load_source_config()
    if cfg is None:
        return "测试中"
    space = next((sp for sp in cfg.spaces if sp.project_key == source_space), None)
    if space is None:
        return "测试中"
    if _matches_testing_node(payload or {}, space):
        return "测试中"
    if space.status_in_testing_state_keys:
        return "测试中" if (external_status in space.status_in_testing_state_keys) else "其他"
    if space.status_in_testing_node_names or space.status_in_testing_node_ids:
        return "其他"
    return "测试中"


def list_configured_spaces(config: FeishuSourceConfig | None = None) -> list[dict[str, str]]:
    """供前端做空间(部门)筛选用：返回 [{project_key, name}]。"""
    cfg = config or load_source_config()
    if cfg is None:
        return []
    return [{"project_key": sp.project_key, "name": sp.name} for sp in cfg.spaces]


# ---------------------------------------------------------------------------
# 纯映射
# ---------------------------------------------------------------------------
@dataclass
class StandardProject:
    external_key: str
    title: str
    state_key: str | None
    created_at_ms: int | None
    created_by: str | None
    # {role_id: [user_key,...]}
    role_owners: dict[str, list[str]]
    raw: dict[str, Any]


def _extract_role_owners(raw: dict[str, Any]) -> dict[str, list[str]]:
    """从工作项里抽出 {role_id: [user_key]}。角色可能在顶层 role_owners 或 fields 里。"""
    owners: dict[str, list[str]] = {}

    def _absorb(value: Any) -> None:
        if not isinstance(value, list):
            return
        for entry in value:
            if not isinstance(entry, dict):
                continue
            role_id = entry.get("role") or entry.get("role_key") or entry.get("role_id")
            if not role_id:
                continue
            raw_owners = entry.get("owners") or entry.get("value") or []
            keys: list[str] = []
            for o in raw_owners if isinstance(raw_owners, list) else [raw_owners]:
                if isinstance(o, dict):
                    key = o.get("user_key") or o.get("id") or o.get("out_id")
                else:
                    key = o
                if key:
                    keys.append(str(key))
            if keys:
                owners.setdefault(str(role_id), []).extend(keys)

    _absorb(raw.get("role_owners"))
    fields = raw.get("fields")
    if isinstance(fields, list):
        for f in fields:
            if isinstance(f, dict) and f.get("field_type_key") == "role_owners":
                _absorb(f.get("field_value"))
    return owners


def _extract_field_users(raw: dict[str, Any], field_keys: list[str]) -> list[str]:
    """从工作项自定义人员字段里抽出 user_key 列表。"""
    wanted = {str(k) for k in field_keys if k}
    if not wanted:
        return []
    keys: list[str] = []

    def _add(value: Any) -> None:
        if value in (None, "", [], {}):
            return
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                key = item.get("user_key") or item.get("key") or item.get("id") or item.get("out_id")
            else:
                key = item
            if key and str(key) not in keys:
                keys.append(str(key))

    for field_item in raw.get("fields") or []:
        if not isinstance(field_item, dict):
            continue
        if str(field_item.get("field_key") or "") in wanted:
            _add(field_item.get("field_value") if "field_value" in field_item else field_item.get("value"))
    return keys


def _matches_testing_node(raw: dict[str, Any], space: SpaceConfig) -> bool:
    """节点流空间里按当前节点名/id 判定测试中。"""
    names = set(space.status_in_testing_node_names or [])
    ids = set(space.status_in_testing_node_ids or [])
    if not names and not ids:
        return False
    for node in raw.get("current_nodes") or []:
        if not isinstance(node, dict):
            continue
        if ids and str(node.get("id") or "") in ids:
            return True
        if names and str(node.get("name") or "") in names:
            return True
    return False


def user_keys_for_concept(
    raw: dict[str, Any],
    role_owners: dict[str, list[str]],
    space: SpaceConfig,
    concept: str,
) -> list[str]:
    """按空间配置取某个内部概念对应的飞书 user_key，支持 role_owners + 自定义人员字段。"""
    keys: list[str] = []
    for role_id in space.role_map.get(concept, []):
        for user_key in role_owners.get(role_id, []):
            if user_key and user_key not in keys:
                keys.append(user_key)
    for user_key in _extract_field_users(raw, space.user_field_map.get(concept, [])):
        if user_key and user_key not in keys:
            keys.append(user_key)
    return keys


def map_work_item(raw: dict[str, Any]) -> StandardProject:
    """飞书工作项原始 dict → 标准结构。纯函数，无副作用、与网络无关。"""
    title = str(raw.get("name") or "").strip()
    external_key = str(raw.get("id") or "").strip()

    status = raw.get("work_item_status")
    state_key = None
    if isinstance(status, dict):
        state_key = status.get("state_key") or status.get("name")
    elif status not in (None, ""):
        state_key = str(status)

    created_at = raw.get("created_at")
    created_at_ms = int(created_at) if isinstance(created_at, (int, float)) else None
    created_by = str(raw.get("created_by")) if raw.get("created_by") else None

    return StandardProject(
        external_key=external_key,
        title=title,
        state_key=str(state_key) if state_key is not None else None,
        created_at_ms=created_at_ms,
        created_by=created_by,
        role_owners=_extract_role_owners(raw),
        raw=raw,
    )


# 卡片展示用角色：内部标签 → 概念 key（只展示这三个，标签用我们的标准）。
CARD_ROLES = (("测试", "tester"), ("前端", "frontend"), ("后端", "backend"))


def extract_sprint_ids(raw: dict[str, Any], sprint_field: str | None) -> list[str]:
    """从工作项里取迭代 id 列表（按配置的字段 key）。无字段/无值则空。"""
    if not sprint_field:
        return []
    for f in raw.get("fields") or []:
        if isinstance(f, dict) and f.get("field_key") == sprint_field:
            value = f.get("field_value")
            if isinstance(value, list):
                return [str(v) for v in value if v not in (None, "")]
            if value not in (None, "", []):
                return [str(value)]
    return []


def build_card(
    project: StandardProject,
    space: SpaceConfig,
    people: dict[str, str],
    site_domain: str,
    sprint_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把一条工作项整理成卡片展示用的紧凑结构，存进 source_payload._card。

    - number：工作项编号(auto_number)；status：当前节点中文名；created_at_ms：创建时间。
    - roles：[{label, names}]，label 用我们的标准(测试/前端/后端)，names 是人名。
    - link：飞书项目直达链接。
    """
    raw = project.raw
    # 编号用工作项 id（飞书看板/URL 里可见的那串长 ID），与跳转链接一致、可核对；
    # auto_number 那个短编号在飞书界面看不到，不用。
    number: Any = project.external_key

    nodes = raw.get("current_nodes") or []
    status = " / ".join(n.get("name") for n in nodes if isinstance(n, dict) and n.get("name")) or None

    roles: list[dict[str, Any]] = []
    for label, concept in CARD_ROLES:
        names: list[str] = []
        for uk in user_keys_for_concept(project.raw, project.role_owners, space, concept):
            nm = people.get(uk)
            if nm and nm not in names:
                names.append(nm)
        if names:
            roles.append({"label": label, "names": names})

    simple = raw.get("simple_name") or space.project_key
    wtype = raw.get("work_item_type_key") or space.work_item_url_type or space.work_item_type or "story"
    link = f"{site_domain.rstrip('/')}/{simple}/{wtype}/detail/{project.external_key}"

    sprint_names = sprint_names or {}
    sprints = [
        {"id": sid, "name": sprint_names.get(sid, sid)}
        for sid in extract_sprint_ids(raw, space.sprint_field)
    ]

    return {
        "number": number,
        "status": status,
        "created_at_ms": project.created_at_ms,
        "roles": roles,
        "sprints": sprints,
        "link": link,
    }


# ---------------------------------------------------------------------------
# 网络层
# ---------------------------------------------------------------------------
class FeishuProjectError(RuntimeError):
    pass


class FeishuProjectClient:
    """飞书项目 OpenAPI 客户端。"""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._token: str | None = None
        self._token_expire_at: float = 0.0

    @property
    def enabled(self) -> bool:
        s = self._settings
        return bool(s.feishu_project_plugin_id and s.feishu_project_plugin_secret)

    def _base(self) -> str:
        return self._settings.feishu_project_site_domain.rstrip("/")

    async def _plugin_token(self, client: httpx.AsyncClient) -> str:
        now = time.time()
        if self._token and now < self._token_expire_at - 60:
            return self._token
        s = self._settings
        resp = await client.post(
            f"{self._base()}/open_api/authen/plugin_token",
            json={"plugin_id": s.feishu_project_plugin_id, "plugin_secret": s.feishu_project_plugin_secret, "type": 0},
        )
        data = resp.json()
        if data.get("err_code") not in (0, None):
            raise FeishuProjectError(f"plugin_token 失败：{data.get('err_code')} {data.get('err_msg')}")
        token = (data.get("data") or {}).get("token")
        if not token:
            raise FeishuProjectError("plugin_token 未返回 token")
        self._token = token
        self._token_expire_at = now + float((data.get("data") or {}).get("expire_time") or 3600)
        return token

    def _headers(self, token: str) -> dict[str, str]:
        headers = {"X-PLUGIN-TOKEN": token, "Content-Type": "application/json"}
        if self._settings.feishu_project_user_key:
            headers["X-USER-KEY"] = self._settings.feishu_project_user_key
        return headers

    async def filter_work_items(
        self,
        client: httpx.AsyncClient,
        token: str,
        project_key: str,
        work_item_type: str,
        page_size: int = 50,
        created_after_ms: int | None = None,
        max_items: int = 0,
        work_item_ids: list[int | str] | None = None,
    ) -> list[dict[str, Any]]:
        """分页拉取某空间下某类型的工作项原始列表（创建时间倒序，新的优先）。

        created_after_ms：服务端按创建时间过滤(start..now)。max_items：拉满即停(0=不限)。
        """
        items: list[dict[str, Any]] = []
        page = 1
        base_body: dict[str, Any] = {"work_item_type_keys": [work_item_type], "page_size": page_size}
        if work_item_ids:
            base_body["work_item_ids"] = [int(item) if str(item).isdigit() else item for item in work_item_ids]
        if created_after_ms:
            base_body["created_at"] = {"start": created_after_ms, "end": int(time.time() * 1000)}
        while True:
            resp = await client.post(
                f"{self._base()}/open_api/{project_key}/work_item/filter",
                headers=self._headers(token),
                json={**base_body, "page_num": page},
            )
            payload = resp.json()
            if payload.get("err_code") not in (0, None):
                raise FeishuProjectError(
                    f"work_item 查询失败（{project_key}）：{payload.get('err_code')} {payload.get('err_msg')}"
                )
            batch = payload.get("data") or []
            items.extend(batch)
            if max_items and len(items) >= max_items:
                return items[:max_items]
            if len(batch) < page_size:
                break
            page += 1
        return items

    async def query_sprints(
        self, client: httpx.AsyncClient, token: str, project_key: str, sprint_ids: list[str]
    ) -> dict[str, str]:
        """迭代 id → 名称（名称常含排期区间，如“学习A（26.06.16~26.06.29）”）。"""
        uniq = [s for s in dict.fromkeys(sprint_ids) if s]
        result: dict[str, str] = {}
        for i in range(0, len(uniq), 50):
            chunk = uniq[i : i + 50]
            resp = await client.post(
                f"{self._base()}/open_api/{project_key}/work_item/sprint/query",
                headers=self._headers(token),
                json={"work_item_ids": [int(s) if str(s).isdigit() else s for s in chunk]},
            )
            payload = resp.json()
            if payload.get("err_code") not in (0, None):
                # 迭代解析失败不阻断主流程，留空即可。
                continue
            for d in payload.get("data") or []:
                sid = str(d.get("id") or "")
                if sid:
                    result[sid] = str(d.get("name") or sid)
        return result

    async def create_work_item(
        self,
        client: httpx.AsyncClient,
        token: str,
        project_key: str,
        work_item_type: str,
        template_id: int,
        name: str,
        field_value_pairs: list[dict[str, Any]],
    ) -> int:
        """创建工作项（如 issue），返回新工作项 id。"""
        resp = await client.post(
            f"{self._base()}/open_api/{project_key}/work_item/create",
            headers=self._headers(token),
            json={
                "work_item_type_key": work_item_type,
                "template_id": template_id,
                "name": name,
                "field_value_pairs": field_value_pairs,
            },
        )
        payload = resp.json()
        if payload.get("err_code") not in (0, None):
            raise FeishuProjectError(f"创建工作项失败：{payload.get('err_code')} {payload.get('err_msg')}")
        new_id = payload.get("data")
        if not new_id:
            raise FeishuProjectError("创建工作项未返回 id")
        return int(new_id)

    async def get_create_meta(
        self,
        client: httpx.AsyncClient,
        token: str,
        project_key: str,
        work_item_type: str,
    ) -> list[dict[str, Any]]:
        """拉某工作项类型的建单元信息（字段/顺序/必填/选项/默认值）。"""
        resp = await client.get(
            f"{self._base()}/open_api/{project_key}/work_item/{work_item_type}/meta",
            headers=self._headers(token),
        )
        payload = resp.json()
        if payload.get("err_code") not in (0, None):
            raise FeishuProjectError(f"拉建单元信息失败：{payload.get('err_code')} {payload.get('err_msg')}")
        return payload.get("data") or []

    async def get_work_item(
        self,
        client: httpx.AsyncClient,
        token: str,
        project_key: str,
        work_item_type: str,
        work_item_id: int | str,
    ) -> dict[str, Any]:
        """读取单个工作项原始信息。

        标准工作流主要按 filter 批量拉取；快速模式需要按用户贴的需求/bug 链接实时取一条。
        飞书项目插件接口下 GET detail 可能直接返回路由层 404；可靠路径是 filter + work_item_ids。
        """
        resp = await client.get(
            f"{self._base()}/open_api/{project_key}/work_item/{work_item_type}/{work_item_id}",
            headers=self._headers(token),
        )
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("err_code") in (0, None) and payload.get("data"):
            data = payload.get("data")
            return data[0] if isinstance(data, list) and data else data

        # 兜底：部分空间 detail GET 返回纯文本 404；filter + work_item_ids 是已验证接口。
        raw_items = await self.filter_work_items(
            client,
            token,
            project_key,
            work_item_type,
            page_size=20,
            max_items=20,
            work_item_ids=[work_item_id],
        )
        target = str(work_item_id)
        for item in raw_items:
            if str(item.get("id") or "") == target:
                return item
        if isinstance(payload, dict):
            raise FeishuProjectError(f"读取工作项失败：{payload.get('err_code')} {payload.get('err_msg')}")
        raise FeishuProjectError(f"读取工作项失败：HTTP {resp.status_code} {resp.text[:120]}")

    async def upload_file(
        self,
        client: httpx.AsyncClient,
        token: str,
        project_key: str,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> str | None:
        """上传附件，返回飞书侧文件 URL（失败返回 None）。"""
        headers = {k: v for k, v in self._headers(token).items() if k.lower() != "content-type"}
        resp = await client.post(
            f"{self._base()}/open_api/{project_key}/file/upload",
            headers=headers,
            files={"file": (file_name, content, mime_type)},
        )
        payload = resp.json()
        if payload.get("err_code") not in (0, None):
            return None
        data = payload.get("data")
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    async def update_work_item(
        self,
        client: httpx.AsyncClient,
        token: str,
        project_key: str,
        work_item_type: str,
        work_item_id: int,
        update_fields: list[dict[str, Any]],
    ) -> None:
        resp = await client.put(
            f"{self._base()}/open_api/{project_key}/work_item/{work_item_type}/{work_item_id}",
            headers=self._headers(token),
            json={"update_fields": update_fields},
        )
        payload = resp.json()
        if payload.get("err_code") not in (0, None):
            raise FeishuProjectError(f"更新工作项失败：{payload.get('err_code')} {payload.get('err_msg')}")

    async def query_users(
        self, client: httpx.AsyncClient, token: str, user_keys: list[str]
    ) -> dict[str, dict[str, Any]]:
        """user_key → {name_cn, email, avatar_url, out_id}。批量查询，去重。"""
        uniq = [k for k in dict.fromkeys(user_keys) if k]
        result: dict[str, dict[str, Any]] = {}
        for i in range(0, len(uniq), 50):
            chunk = uniq[i : i + 50]
            resp = await client.post(
                f"{self._base()}/open_api/user/query",
                headers=self._headers(token),
                json={"user_keys": chunk},
            )
            payload = resp.json()
            if payload.get("err_code") not in (0, None):
                raise FeishuProjectError(f"user/query 失败：{payload.get('err_code')} {payload.get('err_msg')}")
            for u in payload.get("data") or []:
                key = str(u.get("user_key") or u.get("id") or "")
                if not key:
                    # user/query 不回 user_key 时按入参顺序兜底对齐
                    continue
                result[key] = u
        # 部分租户 user/query 不回 user_key：按返回顺序与入参对齐兜底
        if not result and uniq:
            resp = await client.post(
                f"{self._base()}/open_api/user/query",
                headers=self._headers(token),
                json={"user_keys": uniq[:50]},
            )
            data = resp.json().get("data") or []
            for key, u in zip(uniq, data):
                result[key] = u
        return result


# ---------------------------------------------------------------------------
# 用户同步
# ---------------------------------------------------------------------------
async def _sync_users(
    session: AsyncSession, user_infos: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """把飞书用户 upsert 进 users 表，返回 {user_key: user_id}。以 feishu_user_key 为唯一身份。"""
    mapping: dict[str, int] = {}
    for user_key, info in user_infos.items():
        name_cn = str(info.get("name_cn") or info.get("name") or user_key)
        email = info.get("email") or None
        avatar_url = info.get("avatar_url") or None

        existing = await session.scalar(select(User).where(User.feishu_user_key == user_key))
        if existing is None:
            # name 唯一：同名不同人时追加 user_key 尾号区分。
            unique_name = name_cn
            clash = await session.scalar(select(User).where(User.name == unique_name))
            if clash is not None:
                unique_name = f"{name_cn}#{user_key[-4:]}"
            user = User(
                name=unique_name,
                display_name=name_cn,
                feishu_user_key=user_key,
                email=email,
                avatar_url=avatar_url,
                status="active",
            )
            session.add(user)
            await session.flush()
            mapping[user_key] = user.id
        else:
            existing.display_name = name_cn
            if email:
                existing.email = email
            if avatar_url:
                existing.avatar_url = avatar_url
            mapping[user_key] = existing.id
    return mapping


# ---------------------------------------------------------------------------
# 拉取任务状态（进程内，适合当前单机后端）
# ---------------------------------------------------------------------------
PullProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class FeishuPullJob:
    job_id: str
    project_keys: list[str] | None
    status: str = "pending"
    message: str = "等待开始"
    current_space: str | None = None
    fetched: int = 0
    created: int = 0
    updated: int = 0
    spaces: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project_keys": self.project_keys,
            "status": self.status,
            "message": self.message,
            "current_space": self.current_space,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "spaces": self.spaces,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_PULL_JOBS: dict[str, FeishuPullJob] = {}


def start_pull_job(project_keys: list[str] | None = None) -> dict[str, Any]:
    """启动后台飞书拉取任务，立即返回任务快照。"""
    job_id = uuid.uuid4().hex
    normalized_keys = list(dict.fromkeys(project_keys or [])) or None
    job = FeishuPullJob(job_id=job_id, project_keys=normalized_keys)
    _PULL_JOBS[job_id] = job

    import asyncio

    task = asyncio.create_task(_run_pull_job(job_id))
    task.add_done_callback(lambda t: t.exception())
    return job.snapshot()


def get_pull_job(job_id: str) -> dict[str, Any] | None:
    job = _PULL_JOBS.get(job_id)
    return job.snapshot() if job else None


async def _run_pull_job(job_id: str) -> None:
    from app.core.database import AsyncSessionLocal

    job = _PULL_JOBS[job_id]
    job.status = "running"
    job.message = "正在连接飞书项目"
    job.started_at = datetime.now(timezone.utc).isoformat()

    def _progress(event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "space_start":
            job.current_space = str(event.get("project_key") or "")
            job.message = f"正在拉取：{event.get('name') or job.current_space}"
            return
        if kind == "space_done":
            row = dict(event.get("space") or {})
            job.spaces.append(row)
            job.fetched = int(event.get("fetched_total") or job.fetched)
            job.created = int(event.get("created_total") or job.created)
            job.updated = int(event.get("updated_total") or job.updated)
            job.message = f"已处理 {len(job.spaces)} 个空间，获取 {job.fetched} 条"

    try:
        async with AsyncSessionLocal() as session:
            result = await pull_into_pool(session, project_keys=job.project_keys, progress=_progress)
        job.status = "succeeded"
        job.current_space = None
        job.fetched = int(result.get("fetched") or 0)
        job.created = int(result.get("created") or 0)
        job.updated = int(result.get("updated") or 0)
        job.spaces = list(result.get("spaces") or job.spaces)
        job.message = f"拉取完成：新增 {job.created}，更新 {job.updated}，获取 {job.fetched} 条"
    except Exception as exc:  # noqa: BLE001 - 后台任务必须吞住异常并反映到状态
        job.status = "failed"
        job.error = str(exc)
        job.message = str(exc)
    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 拉取入池
# ---------------------------------------------------------------------------
async def pull_into_pool(
    session: AsyncSession,
    config: FeishuSourceConfig | None = None,
    project_keys: list[str] | None = None,
    progress: PullProgressCallback | None = None,
) -> dict[str, Any]:
    """多空间拉取 + 映射 + 用户同步 + upsert 入池（只新增/更新，不删除）。

    project_keys 为空则拉配置里全部 spaces；否则只拉指定空间。
    """
    cfg = config or load_source_config()
    if cfg is None:
        raise FeishuProjectError(
            "未找到飞书来源配置：请复制 backend/config/feishu_project.example.json 为 "
            "backend/config/feishu_project.json 并按本公司真实空间/角色/状态填写。"
        )

    client = FeishuProjectClient()
    if not client.enabled:
        raise FeishuProjectError("飞书项目凭证未配置（CASE_FLOW_FEISHU_PROJECT_PLUGIN_ID/SECRET）")

    target_spaces = cfg.spaces
    if project_keys:
        wanted = set(project_keys)
        target_spaces = [sp for sp in cfg.spaces if sp.project_key in wanted]
        if not target_spaces:
            raise FeishuProjectError(f"指定空间不在配置内：{project_keys}")

    after_ms = cfg.created_after_ms
    created_total = 0
    updated_total = 0
    fetched_total = 0
    per_space: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30) as http:
        token = await client._plugin_token(http)
        for space in target_spaces:
            work_item_type = space.work_item_type or cfg.work_item_type
            if progress:
                progress({"kind": "space_start", "project_key": space.project_key, "name": space.name})
            try:
                raw_items = await client.filter_work_items(
                    http,
                    token,
                    space.project_key,
                    work_item_type,
                    cfg.page_size,
                    created_after_ms=after_ms,
                    max_items=cfg.max_items,
                )
            except FeishuProjectError as exc:
                # 单空间无权限/查询失败（如 10301 Check Token Perm Failed）→ 跳过，不影响其它空间。
                space_result = {
                    "project_key": space.project_key,
                    "name": space.name,
                    "fetched": 0,
                    "matched": 0,
                    "created": 0,
                    "updated": 0,
                    "skipped_no_qa": 0,
                    "error": str(exc),
                }
                per_space.append(space_result)
                if progress:
                    progress({
                        "kind": "space_done",
                        "space": space_result,
                        "fetched_total": fetched_total,
                        "created_total": created_total,
                        "updated_total": updated_total,
                    })
                continue
            fetched_total += len(raw_items)

            mapped = [m for m in (map_work_item(r) for r in raw_items) if m.external_key]

            # 卡片要展示 测试/前端/后端 的人名：把这三类 user_key 都查出名字（仅展示用）。
            tester_keys: set[str] = set()
            display_keys: set[str] = set()
            for m in mapped:
                tester_keys.update(user_keys_for_concept(m.raw, m.role_owners, space, "tester"))
                for concept in ("tester", "frontend", "backend"):
                    display_keys.update(user_keys_for_concept(m.raw, m.role_owners, space, concept))
            # 一次查全部展示用人名；但只把 tester 同步进 users 表（前后端只展示、不建用户）。
            user_infos = await client.query_users(http, token, list(display_keys)) if display_keys else {}
            key_to_name = {k: str(v.get("name_cn") or v.get("name") or k) for k, v in user_infos.items()}
            key_to_uid = await _sync_users(session, {k: user_infos[k] for k in tester_keys if k in user_infos})

            # 迭代/排期：按配置字段收集 id，批量解析名称（该空间无迭代字段则跳过）。
            sprint_names: dict[str, str] = {}
            if space.sprint_field:
                sprint_ids: set[str] = set()
                for m in mapped:
                    sprint_ids.update(extract_sprint_ids(m.raw, space.sprint_field))
                if sprint_ids:
                    sprint_names = await client.query_sprints(http, token, space.project_key, list(sprint_ids))

            # 批量加载已存在的池记录，避免逐条 select
            ext_keys = [m.external_key for m in mapped]
            existing_rows = (
                await session.execute(select(RequirementPool).where(RequirementPool.external_key.in_(ext_keys)))
            ).scalars().all() if ext_keys else []
            existing_by_key = {row.external_key: row for row in existing_rows}

            created = 0
            updated = 0
            skipped_no_qa = 0
            for m in mapped:
                # 负责人：取 tester(QA) 角色的第一个能解析到的 owner
                owner_user_id = None
                for ukey in user_keys_for_concept(m.raw, m.role_owners, space, "tester"):
                    if ukey in key_to_uid:
                        owner_user_id = key_to_uid[ukey]
                        break

                # 锚点：只有“QA 字段有绑定测试人员”的需求才入池；否则跳过（看板无测试参与）。
                if owner_user_id is None:
                    skipped_no_qa += 1
                    continue

                # 卡片展示数据（编号/状态/角色人名/迭代/链接），缓存进 payload，列表接口直接读。
                m.raw["_card"] = build_card(
                    m, space, key_to_name, client._settings.feishu_project_site_domain, sprint_names
                )
                # 该需求涉及的人 key→姓名映射，供 bug 经办人 chips 显示名字（前后端不入 User 表，只能靠它）。
                people_keys: set[str] = set()
                for concept in ("tester", "frontend", "backend"):
                    people_keys.update(user_keys_for_concept(m.raw, m.role_owners, space, concept))
                m.raw["_people"] = {
                    uk: key_to_name[uk]
                    for uk in people_keys
                    if uk in key_to_name
                }

                existing = existing_by_key.get(m.external_key)
                if existing is None:
                    existing = RequirementPool(
                        external_key=m.external_key,
                        title=m.title or m.external_key,
                        source_type=SOURCE_TYPE,
                        status="pending",
                        source_space=space.project_key,
                        external_status=m.state_key,
                        owner_user_id=owner_user_id,
                        source_payload=m.raw,
                    )
                    session.add(existing)
                    await session.flush()
                    created += 1
                else:
                    if m.title and existing.title != m.title:
                        existing.title = m.title
                    existing.source_type = SOURCE_TYPE
                    existing.source_space = space.project_key
                    existing.external_status = m.state_key
                    if owner_user_id:
                        existing.owner_user_id = owner_user_id
                    existing.source_payload = m.raw
                    updated += 1
                # 飞书工作项=二级需求。入池后立即 materialize 为未归属二级需求；已有二级需求则同步状态/标题/负责人(QA)。
                # case 不动、version 不动；换了测试就转移负责人。拉不到的不会进这里，自然保持现状。
                await _materialize_or_resync_requirement(session, existing, space, m, key_to_uid)

            created_total += created
            updated_total += updated
            space_result = {
                "project_key": space.project_key,
                "name": space.name,
                "fetched": len(raw_items),
                "matched": len(mapped),
                "created": created,
                "updated": updated,
                "skipped_no_qa": skipped_no_qa,
            }
            per_space.append(space_result)
            if progress:
                progress({
                    "kind": "space_done",
                    "space": space_result,
                    "fetched_total": fetched_total,
                    "created_total": created_total,
                    "updated_total": updated_total,
                })

    await session.commit()
    return {
        "source": SOURCE_TYPE,
        "fetched": fetched_total,
        "created": created_total,
        "updated": updated_total,
        "spaces": per_space,
    }


async def _materialize_or_resync_requirement(
    session: AsyncSession,
    pool: RequirementPool,
    space: SpaceConfig,
    mapped: "StandardProject",
    key_to_uid: dict[str, int],
) -> None:
    """确保飞书项目有对应二级需求，并与飞书对齐状态、标题、负责人(QA)。

    - case 完全不动（它们只是挂在二级需求上）。
    - version 不动（人工维护）。
    - 负责人按飞书当前 QA 全量对齐：新增缺的、移除已不在的（=换了测试就转移）。
    """
    items = (
        await session.execute(select(RequirementItem).where(RequirementItem.pool_id == pool.id))
    ).scalars().all()

    # 飞书当前 QA → 内部 user_id
    qa_uids: list[int] = []
    for uk in user_keys_for_concept(mapped.raw, mapped.role_owners, space, "tester"):
        uid = key_to_uid.get(uk)
        if uid is not None and uid not in qa_uids:
            qa_uids.append(uid)

    new_status = lifecycle_for(pool.source_space, pool.external_status, pool.source_payload)
    if not items:
        item = RequirementItem(
            group_id=None,
            pool_id=pool.id,
            title=pool.title,
            description=pool.description,
            version=None,
            lifecycle_status=new_status,
        )
        session.add(item)
        await session.flush()
        pool.status = "imported"
        for uid in qa_uids or ([pool.owner_user_id] if pool.owner_user_id is not None else []):
            session.add(RequirementAssignee(requirement_item_id=item.id, user_id=uid, role="tester"))
        return

    for item in items:
        item.lifecycle_status = new_status
        if pool.title:
            item.title = pool.title
        pool.status = "imported"
        if not qa_uids:
            continue  # 解析不到 QA 时不动负责人，避免误清
        existing_rows = (
            await session.execute(
                select(RequirementAssignee).where(RequirementAssignee.requirement_item_id == item.id)
            )
        ).scalars().all()
        existing_uids = {r.user_id for r in existing_rows}
        target = set(qa_uids)
        for row in existing_rows:  # 已不在飞书 QA 里的 → 移除（负责人转移走）
            if row.user_id not in target:
                await session.delete(row)
        for uid in qa_uids:  # 新增的 QA
            if uid not in existing_uids:
                session.add(
                    RequirementAssignee(requirement_item_id=item.id, user_id=uid, role="tester")
                )


def default_source_config() -> FeishuSourceConfig | None:
    """向后兼容入口：从配置文件加载（旧代码可能调用此名）。"""
    return load_source_config()
