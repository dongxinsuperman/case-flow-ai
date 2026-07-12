"""Function Map 挂载管理服务（检查点 2：一级目录）。

只做挂载关系的增删查，挂载的都是资产库里的结构化资产（按引用）。
检查点 5 起，还负责把显式挂载编译成执行时的顶层 functionMapContext。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.function_map_asset import FunctionMapAsset
from app.models.function_map_mount import (
    FunctionMapGroupMount,
    FunctionMapItemMount,
    FunctionMapQuickMount,
)
from app.models.quick import QuickSession
from app.models.requirements import RequirementGroup, RequirementItem
from app.schemas.function_map_asset import (
    FunctionMapAssetListItemOut,
    MountTargetGroupOut,
    MountTargetItemOut,
    MountTargetPageOut,
)

GROUP_NOT_FOUND = "一级目录不存在"
ITEM_NOT_FOUND = "二级需求不存在"
QUICK_SESSION_NOT_FOUND = "quick session 不存在"
ASSET_NOT_FOUND = "Function Map 资产不存在"

# 执行器适用端 target → 允许带入的 Map 适用端集合（mixed=Hybrid，任一即可）。
_TARGET_ALLOWED: dict[str, set[str]] = {
    "app": {"app"},
    "web": {"web"},
    "api": {"api"},
    "mixed": {"app", "web", "api"},
}

_SOURCE_GROUP = "一级目录显式挂载"
_SOURCE_ITEM = "二级需求显式挂载"
_SOURCE_QUICK = "快速会话选择"


@dataclass
class TopLevelContext:
    """顶层显式挂载编译结果。excluded_asset_ids 是适用端不匹配被排除的资产。"""

    context: str = ""
    injected_asset_ids: list[int] = field(default_factory=list)
    excluded_asset_ids: list[int] = field(default_factory=list)


def _format_asset_block(asset: FunctionMapAsset, source: str) -> str:
    """按方案「拼接格式」给每份资产加边界：标题 + 资产 ID + 来源 + 正文。"""
    return (
        f"--- FUNCTION MAP: {asset.title} ---\n"
        f"资产 ID: {asset.id}\n"
        f"来源: {source}\n\n"
        f"{(asset.content or '').strip()}"
    )


async def compile_top_level_context(
    session: AsyncSession,
    requirement_item_ids: Iterable[int],
    executor_target: str,
) -> TopLevelContext:
    """把当前执行容器（二级需求 + 其一级目录）的显式挂载资产，按执行器适用端过滤、去重，
    带资产边界（标题/资产 ID/来源）拼接成顶层 functionMapContext。

    只处理显式挂载（顶层公共上下文），不做自动发现（item 级，检查点 6）。同一资产在一级目录
    和二级需求重叠时只带一次（来源记为一级目录继承）。无匹配挂载时返回空，不回落任何全局兜底。
    """
    item_ids = {int(i) for i in requirement_item_ids if i}
    if not item_ids:
        return TopLevelContext()

    rows = await session.execute(
        select(RequirementItem.id, RequirementItem.group_id).where(RequirementItem.id.in_(item_ids))
    )
    group_ids = {gid for _item_id, gid in rows.all() if gid is not None}

    assets: dict[int, FunctionMapAsset] = {}
    sources: dict[int, str] = {}
    if group_ids:
        group_rows = await session.execute(
            select(FunctionMapAsset)
            .join(FunctionMapGroupMount, FunctionMapGroupMount.asset_id == FunctionMapAsset.id)
            .where(FunctionMapGroupMount.group_id.in_(group_ids))
        )
        for asset in group_rows.scalars().all():
            assets[asset.id] = asset
            sources[asset.id] = _SOURCE_GROUP
    item_rows = await session.execute(
        select(FunctionMapAsset)
        .join(FunctionMapItemMount, FunctionMapItemMount.asset_id == FunctionMapAsset.id)
        .where(FunctionMapItemMount.requirement_item_id.in_(item_ids))
    )
    for asset in item_rows.scalars().all():
        if asset.id not in assets:  # 一级 + 二级重叠只带一次，来源保留一级目录继承
            assets[asset.id] = asset
            sources[asset.id] = _SOURCE_ITEM

    return _assemble_context(assets, sources, executor_target)


def _assemble_context(
    assets: dict[int, FunctionMapAsset],
    sources: dict[int, str],
    executor_target: str,
) -> TopLevelContext:
    """公共装配：按执行器适用端过滤、按 asset id 稳定排序、带资产边界拼接。"""
    allowed = _TARGET_ALLOWED.get(executor_target, set())
    injected: list[int] = []
    excluded: list[int] = []
    blocks: list[str] = []
    for asset_id in sorted(assets):
        asset = assets[asset_id]
        if allowed & set(asset.targets or []):
            injected.append(asset_id)
            blocks.append(_format_asset_block(asset, sources[asset_id]))
        else:
            excluded.append(asset_id)
    return TopLevelContext(
        context="\n\n".join(blocks),
        injected_asset_ids=injected,
        excluded_asset_ids=excluded,
    )


async def compile_quick_context(
    session: AsyncSession,
    quick_session_id: str,
    executor_target: str,
) -> TopLevelContext:
    """快速会话的顶层上下文：按该会话从资产库选中的资产编译。规则与标准模式一致
    （适用端过滤、去重、带资产边界）；快速会话本身就是执行容器，无一级/二级层级。"""
    sid = str(quick_session_id or "").strip()
    if not sid:
        return TopLevelContext()
    rows = await session.execute(
        select(FunctionMapAsset)
        .join(FunctionMapQuickMount, FunctionMapQuickMount.asset_id == FunctionMapAsset.id)
        .where(FunctionMapQuickMount.quick_session_id == sid)
        .order_by(FunctionMapQuickMount.created_at.desc(), FunctionMapAsset.id.desc())
    )
    assets: dict[int, FunctionMapAsset] = {}
    sources: dict[int, str] = {}
    for asset in rows.scalars().all():
        assets[asset.id] = asset
        sources[asset.id] = _SOURCE_QUICK
    return _assemble_context(assets, sources, executor_target)

# 挂载列表只用摘要字段，正文按需在详情/眼睛里单独取，避免列表搬全文
_ASSET_SUMMARY_COLUMNS = (
    FunctionMapAsset.id,
    FunctionMapAsset.title,
    FunctionMapAsset.description,
    FunctionMapAsset.targets,
    FunctionMapAsset.updated_at,
)


def _focus_top_level_index(
    top_level: list[tuple[str, object]],
    items: list[RequirementItem],
    focus_group_id: int | None,
    focus_item_id: int | None,
) -> int | None:
    """在顶层列表中定位 focus 目标所属顶层容器的下标，找不到返回 None。"""
    target_group_id: int | None = None
    target_item_id: int | None = None
    if focus_item_id:
        found = next((it for it in items if it.id == focus_item_id), None)
        if found is None:
            return None
        if found.group_id is None:
            target_item_id = found.id
        else:
            target_group_id = found.group_id
    elif focus_group_id:
        target_group_id = focus_group_id
    else:
        return None

    for idx, (kind, obj) in enumerate(top_level):
        if kind == "group" and target_group_id is not None and obj.id == target_group_id:  # type: ignore[attr-defined]
            return idx
        if kind == "item" and target_item_id is not None and obj.id == target_item_id:  # type: ignore[attr-defined]
            return idx
    return None


async def list_mount_targets(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    keyword: str | None = None,
    focus_group_id: int | None = None,
    focus_item_id: int | None = None,
) -> MountTargetPageOut:
    """挂载目标按顶层分页：一级目录（含空目录，组内二级需求全带）+ 未进入目录的二级需求作为同级顶层项。

    传入 focus_group_id / focus_item_id 时，忽略 page，改为返回目标所在顶层容器所在的那一页
    （二级需求以其一级目录为顶层容器；未进入目录的二级需求以自身为顶层容器），用于深链精准定位。
    """
    group_rows = await session.execute(
        select(RequirementGroup).order_by(
            RequirementGroup.created_at.desc(), RequirementGroup.id.desc()
        )
    )
    groups = list(group_rows.scalars().all())
    item_rows = await session.execute(select(RequirementItem).order_by(RequirementItem.id))
    items = list(item_rows.scalars().all())

    children: dict[int, list[MountTargetItemOut]] = {}
    ungrouped: list[MountTargetItemOut] = []
    for item in items:
        out = MountTargetItemOut(id=item.id, title=item.title, version=item.version)
        if item.group_id is None:
            ungrouped.append(out)
        else:
            children.setdefault(item.group_id, []).append(out)

    keyword_norm = str(keyword or "").strip().lower()

    def group_matches(group: RequirementGroup) -> bool:
        if not keyword_norm:
            return True
        if keyword_norm in group.name.lower():
            return True
        return any(keyword_norm in child.title.lower() for child in children.get(group.id, []))

    def item_matches(item: MountTargetItemOut) -> bool:
        return not keyword_norm or keyword_norm in item.title.lower()

    matched_groups = [group for group in groups if group_matches(group)]
    matched_ungrouped = [item for item in ungrouped if item_matches(item)]

    # 顶层顺序：一级目录在前，未归属二级需求在后
    top_level: list[tuple[str, object]] = [("group", group) for group in matched_groups]
    top_level.extend(("item", item) for item in matched_ungrouped)
    total = len(top_level)

    size = max(1, min(200, int(page_size or 50)))
    page = max(1, int(page or 1))

    focus_idx = _focus_top_level_index(top_level, items, focus_group_id, focus_item_id)
    if focus_idx is not None:
        page = focus_idx // size + 1

    start = (page - 1) * size
    page_slice = top_level[start : start + size]

    out_groups: list[MountTargetGroupOut] = []
    out_ungrouped: list[MountTargetItemOut] = []
    for kind, obj in page_slice:
        if kind == "group":
            group = obj  # type: ignore[assignment]
            out_groups.append(
                MountTargetGroupOut(id=group.id, name=group.name, items=children.get(group.id, []))
            )
        else:
            out_ungrouped.append(obj)  # type: ignore[arg-type]

    return MountTargetPageOut(
        groups=out_groups,
        ungrouped_items=out_ungrouped,
        total=total,
        page=page,
        page_size=size,
    )


def _asset_to_item(asset: FunctionMapAsset) -> FunctionMapAssetListItemOut:
    return FunctionMapAssetListItemOut(
        id=asset.id,
        title=asset.title,
        description=asset.description,
        targets=list(asset.targets or []),
        updated_at=asset.updated_at,
        reference_count=0,
    )


async def list_group_mounts(
    session: AsyncSession,
    group_id: int,
) -> list[FunctionMapAssetListItemOut]:
    group = await session.get(RequirementGroup, group_id)
    if group is None:
        raise ValueError(GROUP_NOT_FOUND)
    rows = await session.execute(
        select(FunctionMapAsset)
        .options(load_only(*_ASSET_SUMMARY_COLUMNS))
        .join(FunctionMapGroupMount, FunctionMapGroupMount.asset_id == FunctionMapAsset.id)
        .where(FunctionMapGroupMount.group_id == group_id)
        .order_by(FunctionMapGroupMount.created_at.desc(), FunctionMapAsset.id.desc())
    )
    return [_asset_to_item(asset) for asset in rows.scalars().all()]


async def mount_to_group(session: AsyncSession, group_id: int, asset_id: int) -> None:
    group = await session.get(RequirementGroup, group_id)
    if group is None:
        raise ValueError(GROUP_NOT_FOUND)
    asset = await session.get(FunctionMapAsset, asset_id)
    if asset is None:
        raise ValueError(ASSET_NOT_FOUND)
    existing = await session.execute(
        select(FunctionMapGroupMount.id).where(
            FunctionMapGroupMount.group_id == group_id,
            FunctionMapGroupMount.asset_id == asset_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return  # 幂等：已挂载不重复
    session.add(FunctionMapGroupMount(group_id=group_id, asset_id=asset_id))
    await session.commit()


async def unmount_from_group(session: AsyncSession, group_id: int, asset_id: int) -> None:
    await session.execute(
        delete(FunctionMapGroupMount).where(
            FunctionMapGroupMount.group_id == group_id,
            FunctionMapGroupMount.asset_id == asset_id,
        )
    )
    await session.commit()


async def list_item_mounts(
    session: AsyncSession,
    requirement_item_id: int,
) -> list[FunctionMapAssetListItemOut]:
    item = await session.get(RequirementItem, requirement_item_id)
    if item is None:
        raise ValueError(ITEM_NOT_FOUND)
    rows = await session.execute(
        select(FunctionMapAsset)
        .options(load_only(*_ASSET_SUMMARY_COLUMNS))
        .join(FunctionMapItemMount, FunctionMapItemMount.asset_id == FunctionMapAsset.id)
        .where(FunctionMapItemMount.requirement_item_id == requirement_item_id)
        .order_by(FunctionMapItemMount.created_at.desc(), FunctionMapAsset.id.desc())
    )
    return [_asset_to_item(asset) for asset in rows.scalars().all()]


async def mount_to_item(session: AsyncSession, requirement_item_id: int, asset_id: int) -> None:
    item = await session.get(RequirementItem, requirement_item_id)
    if item is None:
        raise ValueError(ITEM_NOT_FOUND)
    asset = await session.get(FunctionMapAsset, asset_id)
    if asset is None:
        raise ValueError(ASSET_NOT_FOUND)
    existing = await session.execute(
        select(FunctionMapItemMount.id).where(
            FunctionMapItemMount.requirement_item_id == requirement_item_id,
            FunctionMapItemMount.asset_id == asset_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    session.add(FunctionMapItemMount(requirement_item_id=requirement_item_id, asset_id=asset_id))
    await session.commit()


async def unmount_from_item(session: AsyncSession, requirement_item_id: int, asset_id: int) -> None:
    await session.execute(
        delete(FunctionMapItemMount).where(
            FunctionMapItemMount.requirement_item_id == requirement_item_id,
            FunctionMapItemMount.asset_id == asset_id,
        )
    )
    await session.commit()


async def list_quick_mounts(
    session: AsyncSession,
    quick_session_id: str,
) -> list[FunctionMapAssetListItemOut]:
    quick_session = await session.get(QuickSession, quick_session_id)
    if quick_session is None:
        raise ValueError(QUICK_SESSION_NOT_FOUND)
    rows = await session.execute(
        select(FunctionMapAsset)
        .options(load_only(*_ASSET_SUMMARY_COLUMNS))
        .join(FunctionMapQuickMount, FunctionMapQuickMount.asset_id == FunctionMapAsset.id)
        .where(FunctionMapQuickMount.quick_session_id == quick_session_id)
        .order_by(FunctionMapQuickMount.created_at.desc(), FunctionMapAsset.id.desc())
    )
    return [_asset_to_item(asset) for asset in rows.scalars().all()]


async def mount_to_quick(session: AsyncSession, quick_session_id: str, asset_id: int) -> None:
    quick_session = await session.get(QuickSession, quick_session_id)
    if quick_session is None:
        raise ValueError(QUICK_SESSION_NOT_FOUND)
    asset = await session.get(FunctionMapAsset, asset_id)
    if asset is None:
        raise ValueError(ASSET_NOT_FOUND)
    existing = await session.execute(
        select(FunctionMapQuickMount.id).where(
            FunctionMapQuickMount.quick_session_id == quick_session_id,
            FunctionMapQuickMount.asset_id == asset_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    session.add(FunctionMapQuickMount(quick_session_id=quick_session_id, asset_id=asset_id))
    await session.commit()


async def unmount_from_quick(session: AsyncSession, quick_session_id: str, asset_id: int) -> None:
    await session.execute(
        delete(FunctionMapQuickMount).where(
            FunctionMapQuickMount.quick_session_id == quick_session_id,
            FunctionMapQuickMount.asset_id == asset_id,
        )
    )
    await session.commit()
