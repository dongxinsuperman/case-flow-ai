"""Function Map 资产库服务：创建、查看、删除、导出、导入覆盖。

首版只做资产本身的增删查改与筛选/搜索，不接执行、不接挂载。挂载关系与引用统计
在后续检查点接入后再填充 reference_count。
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.function_map_asset import FunctionMapAsset
from app.models.function_map_mount import FunctionMapGroupMount, FunctionMapItemMount
from app.models.requirements import RequirementGroup, RequirementItem
from app.schemas.function_map_asset import (
    CANONICAL_TARGETS,
    FunctionMapAssetContentOverwriteIn,
    FunctionMapAssetCreateIn,
    FunctionMapAssetExportOut,
    FunctionMapAssetListItemOut,
    FunctionMapAssetMetaUpdateIn,
    FunctionMapAssetOut,
    FunctionMapAssetPageOut,
    FunctionMapMountRefOut,
)

NOT_FOUND = "Function Map 资产不存在"


async def _title_taken(session: AsyncSession, title: str, *, exclude_id: int | None = None) -> bool:
    """标题全局唯一校验。真库靠 WHERE 过滤，这里再按 title 复核一遍以兼容测试假 session。"""
    rows = await session.execute(select(FunctionMapAsset).where(FunctionMapAsset.title == title))
    for asset in rows.scalars().all():
        if asset.title == title and (exclude_id is None or asset.id != exclude_id):
            return True
    return False


def _to_out(
    asset: FunctionMapAsset,
    *,
    reference_count: int = 0,
    mounts: list[FunctionMapMountRefOut] | None = None,
) -> FunctionMapAssetOut:
    return FunctionMapAssetOut(
        id=asset.id,
        title=asset.title,
        description=asset.description,
        content=asset.content,
        targets=list(asset.targets or []),
        source_type=asset.source_type,
        source_filename=asset.source_filename,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        reference_count=reference_count,
        mounts=mounts or [],
    )


def _to_list_item(asset: FunctionMapAsset, *, reference_count: int = 0) -> FunctionMapAssetListItemOut:
    return FunctionMapAssetListItemOut(
        id=asset.id,
        title=asset.title,
        description=asset.description,
        targets=list(asset.targets or []),
        updated_at=asset.updated_at,
        reference_count=reference_count,
    )


async def _mount_refs(session: AsyncSession, asset_id: int) -> list[FunctionMapMountRefOut]:
    group_rows = await session.execute(
        select(RequirementGroup.id, RequirementGroup.name)
        .join(FunctionMapGroupMount, FunctionMapGroupMount.group_id == RequirementGroup.id)
        .where(FunctionMapGroupMount.asset_id == asset_id)
        .order_by(RequirementGroup.name)
    )
    refs = [FunctionMapMountRefOut(scope="group", id=gid, name=name) for gid, name in group_rows.all()]
    item_rows = await session.execute(
        select(RequirementItem.id, RequirementItem.title)
        .join(FunctionMapItemMount, FunctionMapItemMount.requirement_item_id == RequirementItem.id)
        .where(FunctionMapItemMount.asset_id == asset_id)
        .order_by(RequirementItem.title)
    )
    refs.extend(
        FunctionMapMountRefOut(scope="item", id=item_id, name=title)
        for item_id, title in item_rows.all()
    )
    return refs


async def list_assets(
    session: AsyncSession,
    *,
    target: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> FunctionMapAssetPageOut:
    conditions = []
    target_norm = str(target or "").strip().lower()
    if target_norm:
        if target_norm not in CANONICAL_TARGETS:
            raise ValueError(f"适用端筛选只能是 app / web / api，收到：{target!r}")
        conditions.append(FunctionMapAsset.targets.any(target_norm))

    keyword_norm = str(keyword or "").strip()
    if keyword_norm:
        like = f"%{keyword_norm}%"
        conditions.append(
            or_(
                FunctionMapAsset.title.ilike(like),
                FunctionMapAsset.description.ilike(like),
            )
        )

    count_stmt = select(func.count()).select_from(FunctionMapAsset)
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
    total = int((await session.execute(count_stmt)).scalar() or 0)

    page = max(1, int(page or 1))
    size = max(1, min(200, int(page_size or 20)))

    # 列表只用摘要字段，不取正文列，避免把可能很长的 content 从库里搬出来
    stmt = select(FunctionMapAsset).options(
        load_only(
            FunctionMapAsset.id,
            FunctionMapAsset.title,
            FunctionMapAsset.description,
            FunctionMapAsset.targets,
            FunctionMapAsset.updated_at,
        )
    )
    for condition in conditions:
        stmt = stmt.where(condition)
    stmt = stmt.order_by(FunctionMapAsset.updated_at.desc(), FunctionMapAsset.id.desc())
    stmt = stmt.limit(size).offset((page - 1) * size)

    rows = await session.execute(stmt)
    assets = list(rows.scalars().all())

    counts: dict[int, int] = {}
    asset_ids = [asset.id for asset in assets]
    if asset_ids:
        for mount_model in (FunctionMapGroupMount, FunctionMapItemMount):
            count_rows = await session.execute(
                select(mount_model.asset_id, func.count())
                .where(mount_model.asset_id.in_(asset_ids))
                .group_by(mount_model.asset_id)
            )
            for asset_id, count in count_rows.all():
                counts[asset_id] = counts.get(asset_id, 0) + count

    return FunctionMapAssetPageOut(
        items=[_to_list_item(asset, reference_count=counts.get(asset.id, 0)) for asset in assets],
        total=total,
        page=page,
        page_size=size,
    )


async def get_asset(session: AsyncSession, asset_id: int) -> FunctionMapAssetOut:
    asset = await session.get(FunctionMapAsset, asset_id)
    if asset is None:
        raise ValueError(NOT_FOUND)
    mounts = await _mount_refs(session, asset_id)
    return _to_out(asset, reference_count=len(mounts), mounts=mounts)


async def export_asset(session: AsyncSession, asset_id: int) -> FunctionMapAssetExportOut:
    asset = await session.get(FunctionMapAsset, asset_id)
    if asset is None:
        raise ValueError(NOT_FOUND)
    return FunctionMapAssetExportOut(
        title=asset.title,
        description=asset.description,
        content=asset.content,
        targets=list(asset.targets or []),
    )


async def create_asset(
    session: AsyncSession,
    payload: FunctionMapAssetCreateIn,
) -> FunctionMapAssetOut:
    if await _title_taken(session, payload.title):
        raise ValueError(f"已存在同名 Function Map 资产：{payload.title}")
    asset = FunctionMapAsset(
        title=payload.title,
        description=payload.description,
        content=payload.content,
        targets=list(payload.targets),
        source_type="local_import",
        source_filename=payload.source_filename,
    )
    session.add(asset)
    try:
        await session.commit()
    except IntegrityError as exc:  # 并发竞态越过预检查时由 DB 唯一约束兜住
        await session.rollback()
        raise ValueError(f"已存在同名 Function Map 资产：{payload.title}") from exc
    await session.refresh(asset)
    return _to_out(asset)


async def update_meta(
    session: AsyncSession,
    asset_id: int,
    payload: FunctionMapAssetMetaUpdateIn,
) -> FunctionMapAssetOut:
    """在线编辑元信息：标题、解释、适用端。不动正文。"""
    asset = await session.get(FunctionMapAsset, asset_id)
    if asset is None:
        raise ValueError(NOT_FOUND)
    if await _title_taken(session, payload.title, exclude_id=asset_id):
        raise ValueError(f"已存在同名 Function Map 资产：{payload.title}")
    asset.title = payload.title
    asset.description = payload.description
    asset.targets = list(payload.targets)
    asset.updated_at = func.now()
    try:
        await session.commit()
    except IntegrityError as exc:  # 并发竞态越过预检查时由 DB 唯一约束兜住
        await session.rollback()
        raise ValueError(f"已存在同名 Function Map 资产：{payload.title}") from exc
    await session.refresh(asset)
    mounts = await _mount_refs(session, asset_id)
    return _to_out(asset, reference_count=len(mounts), mounts=mounts)


async def overwrite_content(
    session: AsyncSession,
    asset_id: int,
    payload: FunctionMapAssetContentOverwriteIn,
) -> FunctionMapAssetOut:
    """导入覆盖：只替换正文，元信息保持不变。"""
    asset = await session.get(FunctionMapAsset, asset_id)
    if asset is None:
        raise ValueError(NOT_FOUND)
    asset.content = payload.content
    if payload.source_filename:
        asset.source_filename = payload.source_filename
    asset.updated_at = func.now()
    await session.commit()
    await session.refresh(asset)
    mounts = await _mount_refs(session, asset_id)
    return _to_out(asset, reference_count=len(mounts), mounts=mounts)


async def delete_asset(session: AsyncSession, asset_id: int) -> None:
    asset = await session.get(FunctionMapAsset, asset_id)
    if asset is None:
        raise ValueError(NOT_FOUND)
    # 引用提示在前端删除确认里按 reference_count 给出；挂载表对 asset_id 设了 ON DELETE
    # CASCADE，删除资产会一并清掉它的挂载关系（不删业务对象本身）。
    await session.delete(asset)
    await session.commit()
