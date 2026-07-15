from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.case_assets import (
    CaseAsset,
    CaseBody,
    CaseBugDraft,
    CaseRepairDraft,
    CaseWorkItem,
    ImportBatch,
)
from app.models.requirements import (
    RequirementAssignee,
    RequirementGroup,
    RequirementItem,
    RequirementPool,
    User,
)
from app.services.sources import feishu_project
from app.services import executor_cancellation
from app.services.execution_reset import (
    clear_standard_case_execution_artifacts,
    reset_standard_work_item_execution,
)
from app.schemas.workbench import (
    CaseCoverageOut,
    CaseCoverageUpdateIn,
    CaseWorkbenchItemOut,
    CaseWorkItemUpdateIn,
    CaseWorkItemUpdateOut,
    HomeDashboardOut,
    HomeSummaryOut,
    PoolCardOut,
    PoolCardRoleOut,
    PoolCardSprintOut,
    RequirementCatalogOut,
    RequirementGroupAddPoolIn,
    RequirementGroupBindItemsIn,
    RequirementGroupCreateWithPoolIn,
    RequirementGroupMutationOut,
    RequirementGroupOut,
    RequirementItemAutoDiscoveryOut,
    RequirementItemOut,
    RequirementItemMutationOut,
    RequirementItemsMutationOut,
    RequirementPoolCreateItemsIn,
    RequirementPoolOut,
    RequirementPoolPageOut,
    RequirementTaskOut,
    UserOut,
)

FAILURE_TYPE_LABELS = {
    "assertion_failed": "断言失败",
    "business_failure": "业务失败",
    "execution_failed": "执行失败",
    "environment_failure": "执行失败",
    "case_step_failure": "步骤问题",
    "flaky_failure": "偶发波动",
}
VISIBLE_FAILURE_TYPE_LABELS = set(FAILURE_TYPE_LABELS.values())

# 首页“我的进行中任务”只展示这些生命周期状态的二级需求（当前=测试中）。
HOME_TASK_STATUSES = ("测试中",)
T = TypeVar("T")


def _count_when(condition: object) -> object:
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _changed_when() -> object:
    return func.coalesce(func.sum(case((CaseWorkItem.attention_reason == "变更待确认", 1), else_=0)), 0)


@dataclass(frozen=True)
class RequirementCaseCounts:
    case_count: int
    not_run: int
    running: int
    passed: int
    failed: int
    attention_changed: int


@dataclass(frozen=True)
class RequirementListFilters:
    source_space: str | None = None
    person_id: int | None = None
    sprint_id: str | None = None
    testing_only: bool = False
    keyword: str | None = None
    bound_status: str = "all"
    focus_item_id: int | None = None


async def list_users(session: AsyncSession) -> list[UserOut]:
    result = await session.execute(select(User).where(User.status == "active").order_by(User.id))
    return [UserOut.model_validate(user, from_attributes=True) for user in result.scalars().all()]


async def list_requirement_groups(session: AsyncSession) -> list[RequirementGroupOut]:
    rows = await session.execute(
        select(RequirementGroup, RequirementItem, RequirementPool)
        .outerjoin(RequirementItem, RequirementItem.group_id == RequirementGroup.id)
        .outerjoin(RequirementPool, RequirementPool.id == RequirementItem.pool_id)
        # 一级目录按创建时间倒序（最新在前）；组内二级需求按创建顺序。
        .order_by(RequirementGroup.created_at.desc(), RequirementGroup.id.desc(), RequirementItem.id)
    )
    records = rows.all()
    tester_user_ids_by_pool = await _tester_user_ids_by_pool(
        session,
        [pool for _, _, pool in records if pool is not None],
    )
    groups: dict[int, RequirementGroupOut] = {}
    for group, item, pool in records:
        if group.id not in groups:
            groups[group.id] = RequirementGroupOut(
                id=group.id,
                name=group.name,
                status=group.status,
                items=[],
            )
        if item is not None:
            groups[group.id].items.append(_requirement_item_to_out(item, pool, tester_user_ids_by_pool))
    return list(groups.values())


async def list_requirement_catalog(
    session: AsyncSession,
    filters: RequirementListFilters | None = None,
    *,
    page: int = 1,
    page_size: int = 0,
) -> RequirementCatalogOut:
    filters = filters or RequirementListFilters()
    rows = await session.execute(
        select(RequirementItem, RequirementPool, RequirementGroup)
        .join(RequirementPool, RequirementPool.id == RequirementItem.pool_id)
        .outerjoin(RequirementGroup, RequirementGroup.id == RequirementItem.group_id)
        .order_by(
            RequirementGroup.created_at.desc().nulls_last(),
            RequirementGroup.id.desc().nulls_last(),
            RequirementItem.group_id.is_(None),
            RequirementItem.id,
        )
    )
    records = rows.all()
    tester_user_ids_by_pool = await _tester_user_ids_by_pool(session, [pool for _, pool, _ in records])
    facets = _requirement_facets(
        [(item, pool) for item, pool, _group in records],
        tester_user_ids_by_pool,
        source_space=filters.source_space,
    )
    filtered = [
        (item, pool, group)
        for item, pool, group in records
        if _catalog_record_matches(item, pool, group, tester_user_ids_by_pool, filters)
    ]
    total = len(filtered)
    page, page_size, filtered = _paginate_with_focus(
        filtered,
        page=page,
        page_size=page_size,
        focus_item_id=filters.focus_item_id,
        item_id_getter=lambda rec: rec[0].id,
    )

    groups: dict[int, RequirementGroupOut] = {}
    ungrouped: list[RequirementItemOut] = []
    for item, pool, group in filtered:
        item_out = _requirement_item_to_out(item, pool, tester_user_ids_by_pool)
        if group is None:
            ungrouped.append(item_out)
            continue
        if group.id not in groups:
            groups[group.id] = RequirementGroupOut(
                id=group.id,
                name=group.name,
                status=group.status,
                items=[],
            )
        groups[group.id].items.append(item_out)

    return RequirementCatalogOut(
        groups=list(groups.values()),
        ungrouped_items=ungrouped,
        total=total,
        page=page,
        page_size=page_size,
        filter_user_ids=facets["filter_user_ids"],
        sprints=facets["sprints"],
    )


async def list_requirement_pool(
    session: AsyncSession,
    filters: RequirementListFilters | None = None,
    *,
    page: int = 1,
    page_size: int = 20,
) -> RequirementPoolPageOut:
    filters = filters or RequirementListFilters()
    owner = aliased(User)
    rows = await session.execute(
        select(RequirementPool, RequirementItem, RequirementGroup, owner)
        .outerjoin(RequirementItem, RequirementItem.pool_id == RequirementPool.id)
        .outerjoin(RequirementGroup, RequirementGroup.id == RequirementItem.group_id)
        .outerjoin(owner, owner.id == RequirementPool.owner_user_id)
    )
    # 按工作项创建时间倒序（最新在前）；无来源时间的（老数据）排最后。
    records = sorted(rows.all(), key=lambda rec: _pool_created_ms(rec[0].source_payload), reverse=True)

    # 解析每个 pool 的全部 QA → user_id（“参与即匹配”筛选用）。批量一次查 User。
    src_cfg = feishu_project.load_source_config()
    space_by_key = {s.project_key: s for s in (src_cfg.spaces if src_cfg else [])}
    pool_tester_keys = {rec[0].id: _pool_tester_keys(rec[0], space_by_key) for rec in records}
    all_keys = {k for ks in pool_tester_keys.values() for k in ks}
    key_to_uid: dict[str, int] = {}
    if all_keys:
        urows = await session.execute(
            select(User.id, User.feishu_user_key).where(User.feishu_user_key.in_(all_keys))
        )
        key_to_uid = {k: uid for uid, k in urows.all()}

    outs = [
        _pool_record_to_out(pool, item, group, owner_user, pool_tester_keys, key_to_uid)
        for pool, item, group, owner_user in records
    ]
    facets = _pool_facets(outs, source_space=filters.source_space)
    filtered = [out for out in outs if _pool_out_matches(out, filters)]
    total = len(filtered)
    attachable_total = sum(1 for out in filtered if out.bound_group_id is None)
    page, page_size, items = _paginate(filtered, page=page, page_size=page_size)
    return RequirementPoolPageOut(
        items=items,
        total=total,
        attachable_total=attachable_total,
        page=page,
        page_size=page_size,
        filter_user_ids=facets["filter_user_ids"],
        sprints=facets["sprints"],
    )


def _pool_record_to_out(
    pool: RequirementPool,
    item: RequirementItem | None,
    group: RequirementGroup | None,
    owner_user: User | None,
    pool_tester_keys: dict[int, list[str]],
    key_to_uid: dict[str, int],
) -> RequirementPoolOut:
    return RequirementPoolOut(
        id=pool.id,
        external_key=pool.external_key,
        title=pool.title,
        description=pool.description,
        source_type=pool.source_type,
        status=_pool_status(pool, item),
        lifecycle_status=(
            item.lifecycle_status
            if item is not None
            else feishu_project.lifecycle_for(pool.source_space, pool.external_status, pool.source_payload)
        ),
        source_space=pool.source_space,
        owner_user_id=pool.owner_user_id,
        owner_name=owner_user.display_name if owner_user else None,
        tester_user_ids=list(dict.fromkeys(
            key_to_uid[k] for k in pool_tester_keys.get(pool.id, []) if k in key_to_uid
        )),
        card=_pool_card(pool.source_payload),
        bound_group_id=group.id if group else None,
        bound_group_name=group.name if group else None,
        bound_item_id=item.id if item else None,
        bound_item_title=item.title if item else None,
    )


def _pool_out_matches(item: RequirementPoolOut, filters: RequirementListFilters) -> bool:
    keyword = (filters.keyword or "").strip().lower()
    if filters.source_space and item.source_space != filters.source_space:
        return False
    if filters.testing_only and item.lifecycle_status != "测试中":
        return False
    if filters.person_id is not None and filters.person_id > 0:
        if filters.person_id != item.owner_user_id and filters.person_id not in (item.tester_user_ids or []):
            return False
    if filters.sprint_id:
        if not any(sp.id == filters.sprint_id for sp in (item.card.sprints if item.card else [])):
            return False
    if filters.bound_status == "bound" and item.bound_group_id is None:
        return False
    if filters.bound_status == "unbound" and item.bound_group_id is not None:
        return False
    if keyword:
        haystack = " ".join(
            str(v or "")
            for v in (
                item.external_key,
                item.title,
                item.description,
                item.bound_group_name,
                item.bound_item_title,
            )
        ).lower()
        if keyword not in haystack:
            return False
    return True


def _catalog_record_matches(
    item: RequirementItem,
    pool: RequirementPool,
    group: RequirementGroup | None,
    tester_user_ids_by_pool: dict[int, list[int]],
    filters: RequirementListFilters,
) -> bool:
    keyword = (filters.keyword or "").strip().lower()
    if filters.source_space and pool.source_space != filters.source_space:
        return False
    if filters.testing_only and item.lifecycle_status != "测试中":
        return False
    if filters.person_id is not None and filters.person_id > 0:
        if filters.person_id not in tester_user_ids_by_pool.get(pool.id, []):
            return False
    if filters.sprint_id:
        card = _pool_card(pool.source_payload)
        if not any(sp.id == filters.sprint_id for sp in (card.sprints if card else [])):
            return False
    if keyword:
        haystack = " ".join(
            str(v or "")
            for v in (
                group.name if group else "",
                item.title,
                item.version,
                pool.external_key,
            )
        ).lower()
        if keyword not in haystack:
            return False
    return True


def _pool_facets(items: list[RequirementPoolOut], *, source_space: str | None) -> dict[str, list[Any]]:
    user_ids: set[int] = set()
    sprints: dict[str, PoolCardSprintOut] = {}
    for item in items:
        if source_space and item.source_space != source_space:
            continue
        if item.owner_user_id is not None:
            user_ids.add(item.owner_user_id)
        user_ids.update(item.tester_user_ids or [])
        for sprint in (item.card.sprints if item.card else []) or []:
            sprints[sprint.id] = sprint
    return {
        "filter_user_ids": sorted(user_ids),
        "sprints": sorted(sprints.values(), key=lambda sp: sp.name, reverse=True),
    }


def _requirement_facets(
    records: list[tuple[RequirementItem, RequirementPool]],
    tester_user_ids_by_pool: dict[int, list[int]],
    *,
    source_space: str | None,
) -> dict[str, list[Any]]:
    user_ids: set[int] = set()
    sprints: dict[str, PoolCardSprintOut] = {}
    for _item, pool in records:
        if source_space and pool.source_space != source_space:
            continue
        user_ids.update(tester_user_ids_by_pool.get(pool.id, []))
        card = _pool_card(pool.source_payload)
        for sprint in (card.sprints if card else []) or []:
            sprints[sprint.id] = sprint
    return {
        "filter_user_ids": sorted(user_ids),
        "sprints": sorted(sprints.values(), key=lambda sp: sp.name, reverse=True),
    }


def _paginate(items: list[T], *, page: int, page_size: int) -> tuple[int, int, list[T]]:
    page = max(1, int(page or 1))
    page_size = max(0, min(200, int(page_size or 0)))
    if page_size == 0:
        return 1, 0, items
    max_page = max(1, (len(items) + page_size - 1) // page_size)
    page = min(page, max_page)
    start = (page - 1) * page_size
    return page, page_size, items[start : start + page_size]


def _paginate_with_focus(
    items: list[T],
    *,
    page: int,
    page_size: int,
    focus_item_id: int | None,
    item_id_getter: Callable[[T], int],
) -> tuple[int, int, list[T]]:
    if page_size and focus_item_id:
        for index, item in enumerate(items):
            if item_id_getter(item) == focus_item_id:
                page = index // page_size + 1
                break
    return _paginate(items, page=page, page_size=page_size)


def _display_numbers(batch_ids: list[int], file_ords: list[int]) -> list[str]:
    """二级展示序号：单测试集=纯数字「N」；多测试集=「集号-集内序号」。
    集号按测试集出现顺序、集内序号沿用 per-file 序号——改 A 集不影响 B 集,不浮动。"""
    suite_index: dict[int, int] = {}
    for bid in batch_ids:
        if bid not in suite_index:
            suite_index[bid] = len(suite_index) + 1  # 1,2,3...
    multi = len(suite_index) > 1
    return [
        f"{suite_index[bid]}-{fo}" if multi else str(fo)
        for bid, fo in zip(batch_ids, file_ords)
    ]


def _pool_tester_keys(pool: RequirementPool, space_by_key: dict) -> list[str]:
    """该 pool 全部 QA(tester) 的飞书 user_key（从 source_payload + 空间配置解析）。"""
    space = space_by_key.get(pool.source_space or "")
    if space is None:
        return []
    owners = feishu_project._extract_role_owners(pool.source_payload or {})
    return feishu_project.user_keys_for_concept(pool.source_payload or {}, owners, space, "tester")


async def _tester_user_ids_by_pool(session: AsyncSession, pools: list[RequirementPool]) -> dict[int, list[int]]:
    """批量解析 pool 的全部 QA(tester) user_id，供项目池和目录筛选复用。"""
    if not pools:
        return {}
    src_cfg = feishu_project.load_source_config()
    space_by_key = {s.project_key: s for s in (src_cfg.spaces if src_cfg else [])}
    pool_tester_keys = {pool.id: _pool_tester_keys(pool, space_by_key) for pool in pools}
    all_keys = {key for keys in pool_tester_keys.values() for key in keys}
    key_to_uid: dict[str, int] = {}
    if all_keys:
        urows = await session.execute(
            select(User.id, User.feishu_user_key).where(User.feishu_user_key.in_(all_keys))
        )
        key_to_uid = {key: user_id for user_id, key in urows.all()}
    return {
        pool_id: list(dict.fromkeys(key_to_uid[key] for key in keys if key in key_to_uid))
        for pool_id, keys in pool_tester_keys.items()
    }


def _pool_created_ms(payload: dict | None) -> int:
    """工作项创建时间(ms)，用于排序；缺失返回 -1（排最后）。"""
    if isinstance(payload, dict):
        ms = payload.get("created_at")
        if isinstance(ms, (int, float)):
            return int(ms)
    return -1


def _pool_card(payload: dict | None) -> PoolCardOut | None:
    """从 source_payload._card 取卡片展示数据（飞书来源才有）。"""
    card = (payload or {}).get("_card") if isinstance(payload, dict) else None
    if not card:
        return None
    created_date = None
    ms = card.get("created_at_ms")
    if isinstance(ms, (int, float)):
        created_date = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return PoolCardOut(
        number=card.get("number"),
        status=card.get("status"),
        created_date=created_date,
        link=card.get("link"),
        roles=[PoolCardRoleOut(label=r.get("label"), names=r.get("names") or []) for r in card.get("roles") or []],
        sprints=[
            PoolCardSprintOut(id=str(sp.get("id")), name=str(sp.get("name")))
            for sp in card.get("sprints") or []
            if sp.get("id")
        ],
    )


def _pool_status(pool: RequirementPool, item: RequirementItem | None) -> str:
    if item is None:
        return pool.status
    return "bound" if item.group_id is not None else "created"


def _requirement_item_to_out(
    item: RequirementItem,
    pool: RequirementPool | None = None,
    tester_user_ids_by_pool: dict[int, list[int]] | None = None,
) -> RequirementItemOut:
    return RequirementItemOut(
        id=item.id,
        group_id=item.group_id,
        title=item.title,
        status=item.status,
        version=item.version,
        lifecycle_status=item.lifecycle_status,
        source_space=pool.source_space if pool else None,
        tester_user_ids=(tester_user_ids_by_pool or {}).get(pool.id, []) if pool else [],
        card=_pool_card(pool.source_payload) if pool else None,
    )


async def create_requirement_group_with_pool_items(
    session: AsyncSession,
    payload: RequirementGroupCreateWithPoolIn,
) -> RequirementGroupMutationOut:
    name = payload.name.strip()
    if not name:
        raise ValueError("一级目录名称不能为空")
    selections = _normalize_selections(payload.items)
    if not selections:
        raise ValueError("请选择至少一个飞书项目")

    existing = await session.execute(select(RequirementGroup).where(RequirementGroup.name == name))
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"一级目录已存在：{name}")

    await _ensure_pool_items_available(session, [pid for pid, _ in selections])
    group = RequirementGroup(name=name, description=payload.description)
    session.add(group)
    await session.flush()
    await _add_pool_items_to_group(session, group.id, selections)
    await session.commit()

    group_out = await _load_requirement_group(session, group.id)
    if group_out is None:
        raise ValueError("一级目录创建后读取失败")
    return RequirementGroupMutationOut(message="目录已创建并纳入飞书项目。", group=group_out)


async def create_requirement_items_from_pool(
    session: AsyncSession,
    payload: RequirementPoolCreateItemsIn,
) -> RequirementItemsMutationOut:
    pool_ids = _normalize_pool_ids(payload.pool_ids)
    if not pool_ids:
        raise ValueError("请选择至少一个飞书项目")
    items = await _ensure_ungrouped_requirement_items(session, pool_ids)
    await session.commit()
    return RequirementItemsMutationOut(
        message="未进入目录二级需求已就绪。",
        items=[_requirement_item_to_out(item) for item in items],
    )


async def add_pool_items_to_requirement_group(
    session: AsyncSession,
    group_id: int,
    payload: RequirementGroupAddPoolIn,
) -> RequirementGroupMutationOut:
    selections = _normalize_selections(payload.items)
    if not selections:
        raise ValueError("请选择至少一个飞书项目")

    group = await session.get(RequirementGroup, group_id)
    if group is None:
        raise ValueError("一级目录不存在")

    await _ensure_versions_free(session, group_id, [ver for _, ver in selections])
    await _attach_pool_items_to_group(session, group.id, selections)
    await session.commit()

    group_out = await _load_requirement_group(session, group.id)
    if group_out is None:
        raise ValueError("一级目录读取失败")
    return RequirementGroupMutationOut(message="飞书项目已纳入已有目录。", group=group_out)


async def bind_requirement_items_to_group(
    session: AsyncSession,
    group_id: int,
    payload: RequirementGroupBindItemsIn,
) -> RequirementGroupMutationOut:
    selections = _normalize_bind_selections(payload.items)
    if not selections:
        raise ValueError("请选择至少一个二级需求")

    group = await session.get(RequirementGroup, group_id)
    if group is None:
        raise ValueError("一级目录不存在")

    versions = [version for _, version in selections]
    await _ensure_versions_free(session, group_id, versions)

    item_ids = [item_id for item_id, _ in selections]
    rows = await session.execute(select(RequirementItem).where(RequirementItem.id.in_(item_ids)))
    items = {item.id: item for item in rows.scalars().all()}
    missing = [item_id for item_id in item_ids if item_id not in items]
    if missing:
        raise ValueError(f"二级需求不存在：{missing}")

    for item_id, version in selections:
        item = items[item_id]
        if item.group_id is not None:
            raise ValueError(f"二级需求已归属目录：{item.title}")
        item.group_id = group_id
        item.version = version

    await session.commit()
    group_out = await _load_requirement_group(session, group_id)
    if group_out is None:
        raise ValueError("一级目录读取失败")
    return RequirementGroupMutationOut(message="二级需求已纳入目录。", group=group_out)


async def unbind_requirement_item_from_group(
    session: AsyncSession,
    item_id: int,
) -> RequirementItemMutationOut:
    item = await session.get(RequirementItem, item_id)
    if item is None:
        raise ValueError("二级需求不存在")
    item.group_id = None
    item.version = None
    await session.commit()
    return RequirementItemMutationOut(message="二级需求已移出目录。", item=_requirement_item_to_out(item))


async def set_requirement_item_auto_discovery(
    session: AsyncSession,
    item_id: int,
    enabled: bool,
) -> RequirementItemAutoDiscoveryOut:
    item = await session.get(RequirementItem, item_id)
    if item is None:
        raise ValueError("二级需求不存在")
    item.auto_discovery_enabled = bool(enabled)
    await session.commit()
    return RequirementItemAutoDiscoveryOut(
        requirement_item_id=item_id,
        auto_discovery_enabled=item.auto_discovery_enabled,
    )


async def update_requirement_item_version(
    session: AsyncSession,
    item_id: int,
    version: str,
) -> RequirementGroupMutationOut:
    version = (version or "").strip()
    if not version:
        raise ValueError("版本不能为空")
    item = await session.get(RequirementItem, item_id)
    if item is None:
        raise ValueError("二级需求不存在")
    if item.group_id is None:
        raise ValueError("未进入目录二级需求没有版本，请先纳入一级目录")
    await _ensure_versions_free(session, item.group_id, [version], exclude_item_id=item_id)
    item.version = version
    await session.commit()

    group_out = await _load_requirement_group(session, item.group_id)
    if group_out is None:
        raise ValueError("一级目录读取失败")
    return RequirementGroupMutationOut(message=f"版本已更新为 {version}。", group=group_out)


def _normalize_selections(items: list) -> list[tuple[int, str]]:
    """校验并去重纳入项：每条必须有版本，批次内 pool/版本均不重复。"""
    seen_pool: set[int] = set()
    seen_ver: set[str] = set()
    result: list[tuple[int, str]] = []
    for it in items:
        pool_id = it.pool_id
        version = (it.version or "").strip()
        if pool_id <= 0 or pool_id in seen_pool:
            continue
        if not version:
            raise ValueError("每个纳入的需求都必须填写版本")
        if version in seen_ver:
            raise ValueError(f"本次纳入存在重复版本：{version}")
        seen_pool.add(pool_id)
        seen_ver.add(version)
        result.append((pool_id, version))
    return result


def _normalize_pool_ids(pool_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for pool_id in pool_ids:
        pool_id = int(pool_id)
        if pool_id <= 0 or pool_id in seen:
            continue
        seen.add(pool_id)
        result.append(pool_id)
    return result


def _normalize_bind_selections(items: list) -> list[tuple[int, str]]:
    seen_item: set[int] = set()
    seen_ver: set[str] = set()
    result: list[tuple[int, str]] = []
    for it in items:
        item_id = it.requirement_item_id
        version = (it.version or "").strip()
        if item_id <= 0 or item_id in seen_item:
            continue
        if not version:
            raise ValueError("每个纳入的二级需求都必须填写版本")
        if version in seen_ver:
            raise ValueError(f"本次纳入存在重复版本：{version}")
        seen_item.add(item_id)
        seen_ver.add(version)
        result.append((item_id, version))
    return result


async def _ensure_versions_free(
    session: AsyncSession,
    group_id: int,
    versions: list[str],
    exclude_item_id: int | None = None,
) -> None:
    """校验这些版本在该一级目录下未被占用（编辑时排除自身）。"""
    query = (
        select(RequirementItem.version)
        .where(RequirementItem.group_id == group_id)
        .where(RequirementItem.version.isnot(None))
    )
    if exclude_item_id is not None:
        query = query.where(RequirementItem.id != exclude_item_id)
    existing = {v for v in (await session.execute(query)).scalars().all()}
    clash = [v for v in versions if v in existing]
    if clash:
        raise ValueError(f"该一级目录下版本已存在：{'、'.join(clash)}")


async def _ensure_ungrouped_requirement_items(
    session: AsyncSession,
    pool_ids: list[int],
) -> list[RequirementItem]:
    rows = await session.execute(
        select(RequirementPool, RequirementItem, RequirementGroup)
        .outerjoin(RequirementItem, RequirementItem.pool_id == RequirementPool.id)
        .outerjoin(RequirementGroup, RequirementGroup.id == RequirementItem.group_id)
        .where(RequirementPool.id.in_(pool_ids))
    )
    records = rows.all()
    by_pool_id = {pool.id: (pool, item, group) for pool, item, group in records}
    missing = [pool_id for pool_id in pool_ids if pool_id not in by_pool_id]
    if missing:
        raise ValueError(f"飞书项目不存在：{missing}")

    blocked = [
        f"{pool.external_key} 已纳入 {group.name}"
        for pool, item, group in by_pool_id.values()
        if item is not None and group is not None
    ]
    if blocked:
        raise ValueError("；".join(blocked))

    existing_items = [item for _pool, item, _group in by_pool_id.values() if item is not None]
    missing_pool_ids = [pool_id for pool_id in pool_ids if by_pool_id[pool_id][1] is None]
    new_items = (
        await _create_requirement_items_from_pools(session, missing_pool_ids, group_id=None, versions={})
        if missing_pool_ids
        else []
    )
    return [*existing_items, *new_items]


async def _ensure_pool_items_available(session: AsyncSession, pool_ids: list[int]) -> None:
    rows = await session.execute(
        select(RequirementPool, RequirementItem, RequirementGroup)
        .outerjoin(RequirementItem, RequirementItem.pool_id == RequirementPool.id)
        .outerjoin(RequirementGroup, RequirementGroup.id == RequirementItem.group_id)
        .where(RequirementPool.id.in_(pool_ids))
    )
    items = rows.all()
    found_ids = {pool.id for pool, _, _ in items}
    missing = [pool_id for pool_id in pool_ids if pool_id not in found_ids]
    if missing:
        raise ValueError(f"飞书项目不存在：{missing}")

    used: list[str] = []
    for pool, item, group in items:
        if item is not None and group is not None:
            used.append(f"{pool.external_key} 已纳入 {group.name}")
    if used:
        raise ValueError("；".join(used))


async def _add_pool_items_to_group(
    session: AsyncSession,
    group_id: int,
    selections: list[tuple[int, str]],
) -> None:
    await _attach_pool_items_to_group(session, group_id, selections)


async def _attach_pool_items_to_group(
    session: AsyncSession,
    group_id: int,
    selections: list[tuple[int, str]],
) -> list[RequirementItem]:
    versions = {pool_id: version for pool_id, version in selections}
    pool_ids = [pid for pid, _ in selections]
    rows = await session.execute(
        select(RequirementPool, RequirementItem, RequirementGroup)
        .outerjoin(RequirementItem, RequirementItem.pool_id == RequirementPool.id)
        .outerjoin(RequirementGroup, RequirementGroup.id == RequirementItem.group_id)
        .where(RequirementPool.id.in_(pool_ids))
    )
    by_pool_id = {pool.id: (pool, item, group) for pool, item, group in rows.all()}
    missing = [pool_id for pool_id in pool_ids if pool_id not in by_pool_id]
    if missing:
        raise ValueError(f"飞书项目不存在：{missing}")

    blocked = [
        f"{pool.external_key} 已纳入 {group.name}"
        for pool, item, group in by_pool_id.values()
        if item is not None and group is not None
    ]
    if blocked:
        raise ValueError("；".join(blocked))

    attached: list[RequirementItem] = []
    missing_pool_ids: list[int] = []
    for pool_id in pool_ids:
        _pool, item, _group = by_pool_id[pool_id]
        if item is None:
            missing_pool_ids.append(pool_id)
            continue
        item.group_id = group_id
        item.version = versions[pool_id]
        attached.append(item)
    if missing_pool_ids:
        attached.extend(
            await _create_requirement_items_from_pools(
                session,
                missing_pool_ids,
                group_id=group_id,
                versions=versions,
            )
        )
    return attached


async def _create_requirement_items_from_pools(
    session: AsyncSession,
    pool_ids: list[int],
    *,
    group_id: int | None,
    versions: dict[int, str],
) -> list[RequirementItem]:
    rows = await session.execute(select(RequirementPool).where(RequirementPool.id.in_(pool_ids)))
    pools = {pool.id: pool for pool in rows.scalars().all()}
    new_items: list[tuple[RequirementItem, RequirementPool]] = []
    for pool_id in pool_ids:
        pool = pools[pool_id]
        item = RequirementItem(
            group_id=group_id,
            pool_id=pool.id,
            title=pool.title,
            description=pool.description,
            version=versions.get(pool_id),
            # 生命周期按来源空间的状态配置判定（未配置该空间状态码时占位“测试中”）。
            lifecycle_status=feishu_project.lifecycle_for(pool.source_space, pool.external_status, pool.source_payload),
        )
        session.add(item)
        new_items.append((item, pool))
        pool.status = "imported"
    await _attach_assignees_for_new_items(session, new_items)
    return [item for item, _pool in new_items]


async def _attach_assignees_for_new_items(
    session: AsyncSession,
    new_items: list[tuple[RequirementItem, RequirementPool]],
) -> None:
    # 二级需求挂测试负责人（首页“我的任务”靠它过滤）。一个需求可能有多个 QA → 全部挂上，
    # 每人首页都能看到这条二级需求。解析不到（非飞书来源）时退回 owner / 活跃用户轮流。
    await session.flush()
    active_users = (
        await session.execute(select(User.id).where(User.status == "active").order_by(User.id))
    ).scalars().all()
    base = await session.scalar(select(func.count()).select_from(RequirementAssignee)) or 0

    # 收集各 pool 的全部 QA(tester)飞书 key，一次性映射成内部 user_id。
    src_cfg = feishu_project.load_source_config()
    space_by_key = {s.project_key: s for s in (src_cfg.spaces if src_cfg else [])}
    pool_tester_keys: dict[int, list[str]] = {}
    all_keys: set[str] = set()
    for _item, pool in new_items:
        keys: list[str] = []
        space = space_by_key.get(pool.source_space or "")
        if space:
            owners = feishu_project._extract_role_owners(pool.source_payload or {})
            keys = feishu_project.user_keys_for_concept(pool.source_payload or {}, owners, space, "tester")
        pool_tester_keys[pool.id] = keys
        all_keys.update(keys)
    key_to_uid: dict[str, int] = {}
    if all_keys:
        rows = await session.execute(
            select(User.id, User.feishu_user_key).where(User.feishu_user_key.in_(all_keys))
        )
        key_to_uid = {k: uid for uid, k in rows.all()}

    rr_index = 0
    for item, pool in new_items:
        uids = list(dict.fromkeys(
            key_to_uid[k] for k in pool_tester_keys.get(pool.id, []) if k in key_to_uid
        ))
        if not uids:  # 非飞书来源 / 没解析到 QA → 退回主负责人或轮流
            if pool.owner_user_id is not None:
                uids = [pool.owner_user_id]
            elif active_users:
                uids = [active_users[(base + rr_index) % len(active_users)]]
                rr_index += 1
        for uid in uids:
            session.add(
                RequirementAssignee(requirement_item_id=item.id, user_id=uid, role="tester")
            )


async def _load_requirement_group(
    session: AsyncSession,
    group_id: int,
) -> RequirementGroupOut | None:
    rows = await session.execute(
        select(RequirementGroup, RequirementItem)
        .outerjoin(RequirementItem, RequirementItem.group_id == RequirementGroup.id)
        .where(RequirementGroup.id == group_id)
        .order_by(RequirementItem.id)
    )
    group_out: RequirementGroupOut | None = None
    for group, item in rows.all():
        if group_out is None:
            group_out = RequirementGroupOut(id=group.id, name=group.name, status=group.status, items=[])
        if item is not None:
            group_out.items.append(_requirement_item_to_out(item))
    return group_out


async def get_home_dashboard(session: AsyncSession, user_id: int) -> HomeDashboardOut:
    user = await session.get(User, user_id)
    if user is None:
        result = await session.execute(select(User).where(User.status == "active").order_by(User.id).limit(1))
        user = result.scalar_one_or_none()
    if user is None:
        raise ValueError("No active users are configured.")

    requirement_rows = await session.execute(
        select(RequirementItem, RequirementGroup)
        .outerjoin(RequirementGroup, RequirementGroup.id == RequirementItem.group_id)
        .join(RequirementAssignee, RequirementAssignee.requirement_item_id == RequirementItem.id)
        .where(RequirementAssignee.user_id == user.id)
        .where(RequirementItem.lifecycle_status.in_(HOME_TASK_STATUSES))
        .order_by(RequirementItem.id)
    )
    requirements: list[RequirementTaskOut] = []
    for requirement, group in requirement_rows.all():
        counts = await _load_counts_for_requirement(session, requirement.id)
        requirements.append(
            RequirementTaskOut(
                requirement_item_id=requirement.id,
                requirement_item_title=requirement.title,
                requirement_lifecycle_status=requirement.lifecycle_status,
                group_id=group.id if group else None,
                group_name=group.name if group else None,
                case_count=counts.case_count,
                not_run=counts.not_run,
                running=counts.running,
                passed=counts.passed,
                failed=counts.failed,
                attention_changed=counts.attention_changed,
                auto_discovery_enabled=requirement.auto_discovery_enabled,
            )
        )

    summary = HomeSummaryOut(
        requirements=len(requirements),
        case_count=sum(item.case_count for item in requirements),
        not_run=sum(item.not_run for item in requirements),
        running=sum(item.running for item in requirements),
        passed=sum(item.passed for item in requirements),
        failed=sum(item.failed for item in requirements),
        attention_changed=sum(item.attention_changed for item in requirements),
    )
    return HomeDashboardOut(
        user=UserOut.model_validate(user, from_attributes=True),
        summary=summary,
        requirements=requirements,
    )


async def _load_counts_for_requirement(
    session: AsyncSession,
    requirement_item_id: int,
) -> RequirementCaseCounts:
    result = await session.execute(
        select(
            func.count(CaseAsset.id),
            _count_when(CaseWorkItem.execution_status == "not_run"),
            _count_when(CaseWorkItem.execution_status == "running"),
            _count_when(CaseWorkItem.execution_status == "passed"),
            _count_when(CaseWorkItem.execution_status == "failed"),
            _changed_when(),
        )
        .select_from(CaseAsset)
        .join(CaseWorkItem, CaseWorkItem.case_id == CaseAsset.id)
        .where(CaseAsset.source_requirement_item_id == requirement_item_id)
    )
    row = result.one()
    return RequirementCaseCounts(*(int(value or 0) for value in row))


def _case_query(requirement_item_id: int) -> Select[tuple[CaseAsset, CaseBody, CaseWorkItem, ImportBatch]]:
    return (
        select(CaseAsset, CaseBody, CaseWorkItem, ImportBatch)
        .join(CaseBody, CaseBody.case_id == CaseAsset.id)
        .join(CaseWorkItem, CaseWorkItem.case_id == CaseAsset.id)
        .join(ImportBatch, ImportBatch.id == CaseAsset.batch_id)
        .where(CaseAsset.source_requirement_item_id == requirement_item_id)
        .order_by(
            # 先按测试集(导入批次)分组，避免多个测试集穿插；组内沿用手动序/原序号。
            ImportBatch.id,
            func.coalesce(CaseWorkItem.display_order, CaseAsset.ordinal),
            CaseAsset.ordinal,
            CaseAsset.id,
        )
    )


async def _apply_ready_flags(session: AsyncSession, outs: list[CaseWorkbenchItemOut]) -> None:
    """标记后台草稿是否已就绪：诊断草稿 / bug 预填草稿是否已生成。"""
    case_ids = [o.id for o in outs]
    if not case_ids:
        return
    diag_ids = set(
        (await session.execute(
            select(CaseRepairDraft.case_id).where(CaseRepairDraft.case_id.in_(case_ids))
        )).scalars().all()
    )
    bug_ids = set(
        (await session.execute(
            select(CaseBugDraft.case_id).where(CaseBugDraft.case_id.in_(case_ids))
        )).scalars().all()
    )
    for o in outs:
        o.diagnosis_ready = o.id in diag_ids
        o.bug_draft_ready = o.id in bug_ids


def _kick_pending_diagnoses(outs: list[CaseWorkbenchItemOut]) -> None:
    """对“失败+有报告+还没诊断草稿”的 case 补触发后台诊断（幂等，已在跑会跳过）。

    保证波浪“后台准备中”名副其实：功能上线前就失败的、或回调时没触发到的，
    打开列表时也会真正开始准备，跑完草稿就绪、波浪自动停。
    """
    from app.services.case_repair import auto_diagnose_case

    for o in outs:
        if o.execution_status == "failed" and o.report_url and not o.diagnosis_ready:
            task = asyncio.create_task(auto_diagnose_case(o.id))
            task.add_done_callback(lambda t: t.exception())


async def list_workbench_cases(session: AsyncSession, requirement_item_id: int) -> list[CaseWorkbenchItemOut]:
    rows = (await session.execute(_case_query(requirement_item_id))).all()
    outs: list[CaseWorkbenchItemOut] = []
    file_ords: list[int] = []
    batch_ids: list[int] = []
    for case_asset, body, work_item, batch in rows:
        outs.append(_case_to_out(case_asset, body, work_item, batch))
        file_ords.append(int(case_asset.ordinal or 0))
        batch_ids.append(batch.id)
    # 展示序号用二级标识，稳定不浮动：单测试集=纯数字；多测试集=「集号-集内序号」(1-3、2-15)。
    for out, dno in zip(outs, _display_numbers(batch_ids, file_ords)):
        out.display_no = dno
    await _apply_ready_flags(session, outs)
    _kick_pending_diagnoses(outs)
    return outs


async def get_case(session: AsyncSession, case_id: int) -> CaseWorkbenchItemOut | None:
    rows = await session.execute(
        select(CaseAsset, CaseBody, CaseWorkItem, ImportBatch)
        .join(CaseBody, CaseBody.case_id == CaseAsset.id)
        .join(CaseWorkItem, CaseWorkItem.case_id == CaseAsset.id)
        .join(ImportBatch, ImportBatch.id == CaseAsset.batch_id)
        .where(CaseAsset.id == case_id)
    )
    row = rows.one_or_none()
    if row is None:
        return None
    out = _case_to_out(*row)
    await _apply_ready_flags(session, [out])
    return out


async def update_case_work_item(
    session: AsyncSession,
    payload: CaseWorkItemUpdateIn,
) -> CaseWorkItemUpdateOut | None:
    item = await session.get(CaseWorkItem, payload.case_id)
    if item is None:
        return None
    cancellation_targets = []
    if payload.execution_status == "not_run" and item.execution_status == "running":
        # reset 会删 execution item 并清空 submission 关联，必须先只摘取取消目标。
        cancellation_targets = await executor_cancellation.snapshot_standard_targets(
            session,
            case_id=item.case_id,
            active_batch_id=item.active_execution_batch_id,
            external_submission_id=item.external_submission_id,
        )
    should_clear_artifacts = payload.execution_status is not None or payload.execution_target is not None
    if payload.execution_status is not None:
        reset_standard_work_item_execution(item, status=payload.execution_status)
    elif payload.execution_target is not None:
        reset_standard_work_item_execution(item, status="not_run")
    if payload.execution_target is not None:
        item.execution_target = payload.execution_target
    if payload.run_enabled is not None:
        item.run_enabled = payload.run_enabled
    if not should_clear_artifacts:
        item.updated_at = func.now()
    if should_clear_artifacts:
        await clear_standard_case_execution_artifacts(session, [payload.case_id])
    await session.commit()
    if cancellation_targets:
        executor_cancellation.schedule_cancellation("standard", cancellation_targets)
    return CaseWorkItemUpdateOut(
        case_id=item.case_id,
        execution_status=item.execution_status,
        execution_target=item.execution_target,
        run_enabled=item.run_enabled,
    )


# 覆盖标记合法泳道（app 三端 / web 三浏览器）与三态。
COVERAGE_LANES = {"android", "ios", "harmony", "chrome", "safari", "firefox"}
COVERAGE_STATES = {"none", "passed", "failed"}


async def set_case_coverage(
    session: AsyncSession,
    payload: CaseCoverageUpdateIn,
) -> CaseCoverageOut | None:
    """设置单条 case 单个泳道的覆盖标记。纯展示提醒，绝不触碰 execution_status / 报告 / bug。"""
    lane = str(payload.lane).lower()
    state = str(payload.state).lower()
    if lane not in COVERAGE_LANES:
        raise ValueError(f"非法覆盖泳道：{payload.lane}")
    if state not in COVERAGE_STATES:
        raise ValueError(f"非法覆盖状态：{payload.state}")

    work_item = await session.get(CaseWorkItem, payload.case_id)
    if work_item is None:
        return None
    coverage = {str(k): str(v) for k, v in (work_item.coverage or {}).items()}
    if state == "none":
        coverage.pop(lane, None)
    else:
        coverage[lane] = state
    work_item.coverage = coverage
    work_item.updated_at = func.now()
    await session.commit()
    return CaseCoverageOut(case_id=payload.case_id, coverage=coverage)


def _case_to_out(
    case_asset: CaseAsset,
    body: CaseBody,
    work_item: CaseWorkItem,
    batch: ImportBatch,
) -> CaseWorkbenchItemOut:
    module_name = case_asset.module_name or "模块无"
    product_feature = case_asset.product_feature or "功能点无"
    test_feature = case_asset.test_feature or "测试功能点无"
    path_nodes = _case_path_nodes(case_asset)
    return CaseWorkbenchItemOut(
        id=case_asset.id,
        batch_id=batch.id,
        ordinal=case_asset.ordinal,
        suite_title=case_asset.suite_title,
        source_name=batch.source_name,
        asset_status=case_asset.status,
        module_name=module_name,
        product_feature=product_feature,
        test_feature=test_feature,
        raw_title=case_asset.raw_title,
        clean_title=case_asset.clean_title,
        path=" / ".join(_path_node_texts(path_nodes)),
        path_nodes=path_nodes,
        scenario_tags=list(case_asset.scenario_tags or []),
        manual=case_asset.manual,
        execution_status=work_item.execution_status,
        coverage={str(k): str(v) for k, v in (getattr(work_item, "coverage", None) or {}).items()},
        lifecycle_state=work_item.lifecycle_state,
        attention_reason=_visible_attention_reason(work_item.attention_reason),
        case_type=work_item.case_type,
        execution_target=work_item.execution_target,
        tag_source=work_item.tag_source,
        tag_reason=work_item.tag_reason,
        tag_confidence=work_item.tag_confidence,
        run_enabled=work_item.run_enabled,
        report_url=work_item.report_url,
        failure_type=_failure_type_label(work_item.failure_type),
        failure_summary=work_item.failure_summary,
        bug_url=work_item.bug_url,
        bugs=[
            {"url": str(b.get("url") or ""), "id": str(b.get("id") or "")}
            for b in (getattr(work_item, "bugs", None) or [])
            if isinstance(b, dict) and b.get("url")
        ],
        external_submission_id=work_item.external_submission_id,
        execution_started_at=work_item.execution_started_at,
        execution_finished_at=work_item.execution_finished_at,
        preconditions=body.preconditions,
        steps_text=body.steps_text,
        expected_result=body.expected_result,
    )


def _case_path_nodes(case_asset: CaseAsset) -> list[dict[str, Any]]:
    nodes = getattr(case_asset, "path_nodes", None)
    return [dict(node) for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _path_node_texts(nodes: list[dict[str, Any]]) -> list[str]:
    return [
        str(node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text") or "")
        for node in nodes
        if isinstance(node, dict)
        and (node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text"))
    ]


def _failure_type_label(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if text in VISIBLE_FAILURE_TYPE_LABELS:
        return text
    return FAILURE_TYPE_LABELS.get(text)


def _visible_attention_reason(value: str | None) -> str | None:
    # attention 仅表示“变更待确认”；失败统一走 failure_type，不再用 attention 表达。
    return value if value == "变更待确认" else None
