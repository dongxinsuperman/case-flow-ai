from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.function_map_asset import (
    FunctionMapAssetContentOverwriteIn,
    FunctionMapAssetCreateIn,
    FunctionMapAssetExportOut,
    FunctionMapAssetListItemOut,
    FunctionMapAssetMetaUpdateIn,
    FunctionMapAssetOut,
    FunctionMapAssetPageOut,
    FunctionMapMountIn,
    MountTargetPageOut,
)
from app.services import function_map_asset as function_map_asset_service
from app.services import function_map_mount as function_map_mount_service

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/function-map-assets", response_model=FunctionMapAssetPageOut)
async def list_function_map_assets(
    session: SessionDep,
    target: Annotated[str | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> FunctionMapAssetPageOut:
    try:
        return await function_map_asset_service.list_assets(
            session, target=target, keyword=keyword, page=page, page_size=page_size
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/function-map-assets", response_model=FunctionMapAssetOut)
async def create_function_map_asset(
    payload: FunctionMapAssetCreateIn,
    session: SessionDep,
) -> FunctionMapAssetOut:
    try:
        return await function_map_asset_service.create_asset(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/function-map-assets/{asset_id}", response_model=FunctionMapAssetOut)
async def get_function_map_asset(asset_id: int, session: SessionDep) -> FunctionMapAssetOut:
    try:
        return await function_map_asset_service.get_asset(session, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/function-map-assets/{asset_id}/export",
    response_model=FunctionMapAssetExportOut,
)
async def export_function_map_asset(
    asset_id: int,
    session: SessionDep,
) -> FunctionMapAssetExportOut:
    try:
        return await function_map_asset_service.export_asset(session, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/function-map-assets/{asset_id}", response_model=FunctionMapAssetOut)
async def update_function_map_asset_meta(
    asset_id: int,
    payload: FunctionMapAssetMetaUpdateIn,
    session: SessionDep,
) -> FunctionMapAssetOut:
    try:
        return await function_map_asset_service.update_meta(session, asset_id, payload)
    except ValueError as exc:
        status = 404 if str(exc) == function_map_asset_service.NOT_FOUND else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.put("/function-map-assets/{asset_id}/content", response_model=FunctionMapAssetOut)
async def overwrite_function_map_asset_content(
    asset_id: int,
    payload: FunctionMapAssetContentOverwriteIn,
    session: SessionDep,
) -> FunctionMapAssetOut:
    try:
        return await function_map_asset_service.overwrite_content(session, asset_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/function-map-assets/{asset_id}")
async def delete_function_map_asset(asset_id: int, session: SessionDep) -> dict[str, str]:
    try:
        await function_map_asset_service.delete_asset(session, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.get("/function-map-mount-targets", response_model=MountTargetPageOut)
async def list_function_map_mount_targets(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    keyword: Annotated[str | None, Query()] = None,
    focus_group_id: Annotated[int | None, Query(ge=1)] = None,
    focus_item_id: Annotated[int | None, Query(ge=1)] = None,
) -> MountTargetPageOut:
    return await function_map_mount_service.list_mount_targets(
        session,
        page=page,
        page_size=page_size,
        keyword=keyword,
        focus_group_id=focus_group_id,
        focus_item_id=focus_item_id,
    )


@router.get(
    "/requirement-groups/{group_id}/function-map-mounts",
    response_model=list[FunctionMapAssetListItemOut],
)
async def list_group_function_map_mounts(
    group_id: int,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        return await function_map_mount_service.list_group_mounts(session, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/requirement-groups/{group_id}/function-map-mounts",
    response_model=list[FunctionMapAssetListItemOut],
)
async def mount_function_map_to_group(
    group_id: int,
    payload: FunctionMapMountIn,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        await function_map_mount_service.mount_to_group(session, group_id, payload.asset_id)
        return await function_map_mount_service.list_group_mounts(session, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/requirement-groups/{group_id}/function-map-mounts/{asset_id}",
    response_model=list[FunctionMapAssetListItemOut],
)
async def unmount_function_map_from_group(
    group_id: int,
    asset_id: int,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        await function_map_mount_service.unmount_from_group(session, group_id, asset_id)
        return await function_map_mount_service.list_group_mounts(session, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/requirement-items/{requirement_item_id}/function-map-mounts",
    response_model=list[FunctionMapAssetListItemOut],
)
async def list_item_function_map_mounts(
    requirement_item_id: int,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        return await function_map_mount_service.list_item_mounts(session, requirement_item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/requirement-items/{requirement_item_id}/function-map-mounts",
    response_model=list[FunctionMapAssetListItemOut],
)
async def mount_function_map_to_item(
    requirement_item_id: int,
    payload: FunctionMapMountIn,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        await function_map_mount_service.mount_to_item(session, requirement_item_id, payload.asset_id)
        return await function_map_mount_service.list_item_mounts(session, requirement_item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/requirement-items/{requirement_item_id}/function-map-mounts/{asset_id}",
    response_model=list[FunctionMapAssetListItemOut],
)
async def unmount_function_map_from_item(
    requirement_item_id: int,
    asset_id: int,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        await function_map_mount_service.unmount_from_item(session, requirement_item_id, asset_id)
        return await function_map_mount_service.list_item_mounts(session, requirement_item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/quick-sessions/{quick_session_id}/function-map-mounts",
    response_model=list[FunctionMapAssetListItemOut],
)
async def list_quick_function_map_mounts(
    quick_session_id: str,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        return await function_map_mount_service.list_quick_mounts(session, quick_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/quick-sessions/{quick_session_id}/function-map-mounts",
    response_model=list[FunctionMapAssetListItemOut],
)
async def mount_function_map_to_quick(
    quick_session_id: str,
    payload: FunctionMapMountIn,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        await function_map_mount_service.mount_to_quick(session, quick_session_id, payload.asset_id)
        return await function_map_mount_service.list_quick_mounts(session, quick_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/quick-sessions/{quick_session_id}/function-map-mounts/{asset_id}",
    response_model=list[FunctionMapAssetListItemOut],
)
async def unmount_function_map_from_quick(
    quick_session_id: str,
    asset_id: int,
    session: SessionDep,
) -> list[FunctionMapAssetListItemOut]:
    try:
        await function_map_mount_service.unmount_from_quick(session, quick_session_id, asset_id)
        return await function_map_mount_service.list_quick_mounts(session, quick_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
