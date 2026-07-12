from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.executions import AIPhoneDeviceListOut, CasePlatformResultOut
from app.schemas.quick import (
    QuickAIPhoneCallbackOut,
    QuickAIPhoneSubmitIn,
    QuickAIPhoneSubmitOut,
    QuickBugDraftOut,
    QuickBugSubmitIn,
    QuickBugSubmitOut,
    QuickCaseItemOut,
    QuickCaseMutationOut,
    QuickCaseUpdateIn,
    QuickClearOut,
    QuickCoverageOut,
    QuickCoverageUpdateIn,
    QuickExportOut,
    QuickFeishuLinkCheckIn,
    QuickFeishuTargetIn,
    QuickFeishuTargetOut,
    QuickImportIn,
    QuickImportOut,
    QuickRepairApplyIn,
    QuickRepairApplyOut,
    QuickRepairPreviewIn,
    QuickRepairPreviewOut,
    QuickSessionOut,
    QuickSessionPatchIn,
    QuickWorkItemUpdateIn,
    QuickWorkItemUpdateOut,
)
from app.services import quick_bug_submit, quick_executions, quick_importing, quick_repair
from app.services.quick_markdown import QuickParseError
from app.services.sources import feishu_project as fp

router = APIRouter(prefix="/quick")
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/sessions/import", response_model=QuickImportOut)
async def import_quick_markdown(payload: QuickImportIn, session: SessionDep) -> QuickImportOut:
    try:
        return await quick_importing.create_session_from_markdown(session, payload)
    except QuickParseError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions/{session_id}", response_model=QuickSessionOut)
async def quick_session_detail(session_id: str, session: SessionDep) -> QuickSessionOut:
    detail = await quick_importing.get_session_detail(session, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Quick session not found")
    return detail


@router.patch("/sessions/{session_id}", response_model=QuickSessionOut)
async def update_quick_session(
    session_id: str,
    payload: QuickSessionPatchIn,
    session: SessionDep,
) -> QuickSessionOut:
    detail = await quick_importing.update_session(session, session_id, payload)
    if detail is None:
        raise HTTPException(status_code=404, detail="Quick session not found")
    return detail


@router.post("/sessions/{session_id}/feishu-target", response_model=QuickFeishuTargetOut)
async def set_quick_feishu_target(
    session_id: str,
    payload: QuickFeishuTargetIn,
    session: SessionDep,
) -> QuickFeishuTargetOut:
    try:
        return await quick_bug_submit.set_feishu_requirement_target(session, session_id, payload.url)
    except quick_bug_submit.QuickBugSubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"读取飞书需求失败：{quick_bug_submit.describe_external_error(exc)}"
        raise HTTPException(status_code=502, detail=detail) from exc


@router.post("/sessions/{session_id}/feishu-link-check", response_model=QuickFeishuTargetOut)
async def check_quick_feishu_link(
    session_id: str,
    payload: QuickFeishuLinkCheckIn,
    session: SessionDep,
) -> QuickFeishuTargetOut:
    try:
        return await quick_bug_submit.check_feishu_link(session, session_id, payload.url, payload.kind)
    except quick_bug_submit.QuickBugSubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/export", response_model=QuickExportOut)
async def export_quick_session(
    session_id: str,
    session: SessionDep,
    clear: bool = Query(True),
) -> QuickExportOut:
    result = await quick_importing.export_session(session, session_id, clear=clear)
    if result is None:
        raise HTTPException(status_code=404, detail="Quick session not found")
    return result


@router.delete("/sessions/{session_id}", response_model=QuickClearOut)
async def clear_quick_session(session_id: str, session: SessionDep) -> QuickClearOut:
    return await quick_importing.clear_session(session, session_id)


@router.get("/cases/{case_id}", response_model=QuickCaseItemOut)
async def quick_case_detail(case_id: int, session: SessionDep) -> QuickCaseItemOut:
    item = await quick_importing.get_case(session, case_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Quick case not found")
    return item


@router.patch("/cases/{case_id}", response_model=QuickCaseMutationOut)
async def update_quick_case(
    case_id: int,
    payload: QuickCaseUpdateIn,
    session: SessionDep,
) -> QuickCaseMutationOut:
    item = await quick_importing.update_case(session, case_id, payload)
    if item is None:
        raise HTTPException(status_code=404, detail="Quick case not found")
    return item


@router.post("/case-work-items/update", response_model=QuickWorkItemUpdateOut)
async def update_quick_work_item(
    payload: QuickWorkItemUpdateIn,
    session: SessionDep,
) -> QuickWorkItemUpdateOut:
    item = await quick_importing.update_case_work_item(session, payload)
    if item is None:
        raise HTTPException(status_code=404, detail="Quick case work item not found")
    return item


@router.post("/case-work-items/coverage", response_model=QuickCoverageOut)
async def set_quick_case_coverage(
    payload: QuickCoverageUpdateIn,
    session: SessionDep,
) -> QuickCoverageOut:
    try:
        item = await quick_importing.set_case_coverage(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Quick case work item not found")
    return item


@router.get("/aiphone/devices", response_model=AIPhoneDeviceListOut)
async def quick_aiphone_devices() -> AIPhoneDeviceListOut:
    return await quick_executions.list_aiphone_devices()


@router.get("/aiweb/devices", response_model=AIPhoneDeviceListOut)
async def quick_aiweb_devices() -> AIPhoneDeviceListOut:
    return await quick_executions.list_aiweb_devices()


@router.post("/executions/aiphone/submit", response_model=QuickAIPhoneSubmitOut)
async def submit_quick_aiphone(
    payload: QuickAIPhoneSubmitIn,
    session: SessionDep,
) -> QuickAIPhoneSubmitOut:
    try:
        return await quick_executions.submit_aiphone_execution(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/executions/aiweb/submit", response_model=QuickAIPhoneSubmitOut)
async def submit_quick_aiweb(
    payload: QuickAIPhoneSubmitIn,
    session: SessionDep,
) -> QuickAIPhoneSubmitOut:
    try:
        return await quick_executions.submit_aiweb_execution(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/executions/aiapi/submit", response_model=QuickAIPhoneSubmitOut)
async def submit_quick_aiapi(
    payload: QuickAIPhoneSubmitIn,
    session: SessionDep,
) -> QuickAIPhoneSubmitOut:
    try:
        return await quick_executions.submit_aiapi_execution(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/executions/aihybrid/submit", response_model=QuickAIPhoneSubmitOut)
async def submit_quick_aihybrid(
    payload: QuickAIPhoneSubmitIn,
    session: SessionDep,
) -> QuickAIPhoneSubmitOut:
    try:
        return await quick_executions.submit_aihybrid_execution(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/aiphone/callback/{callback_token}", response_model=QuickAIPhoneCallbackOut)
async def quick_aiphone_callback(
    callback_token: str,
    payload: dict,
    session: SessionDep,
) -> QuickAIPhoneCallbackOut:
    try:
        return QuickAIPhoneCallbackOut.model_validate(
            await quick_executions.apply_aiphone_callback(session, callback_token, payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/aiweb/callback/{callback_token}", response_model=QuickAIPhoneCallbackOut)
async def quick_aiweb_callback(
    callback_token: str,
    payload: dict,
    session: SessionDep,
) -> QuickAIPhoneCallbackOut:
    try:
        return QuickAIPhoneCallbackOut.model_validate(
            await quick_executions.apply_aiweb_callback(session, callback_token, payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/aihybrid/callback/{callback_token}", response_model=QuickAIPhoneCallbackOut)
async def quick_aihybrid_callback(
    callback_token: str,
    payload: dict,
    session: SessionDep,
) -> QuickAIPhoneCallbackOut:
    try:
        return QuickAIPhoneCallbackOut.model_validate(
            await quick_executions.apply_aihybrid_callback(session, callback_token, payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/cases/{case_id}/platform-results", response_model=list[CasePlatformResultOut])
async def quick_case_platform_results(case_id: int, session: SessionDep) -> list[CasePlatformResultOut]:
    return await quick_executions.list_case_platform_results(session, case_id)


@router.post("/cases/repair-preview", response_model=QuickRepairPreviewOut)
async def preview_quick_repairs(
    payload: QuickRepairPreviewIn,
    session: SessionDep,
) -> QuickRepairPreviewOut:
    return await quick_repair.preview_repairs(session, payload)


@router.post("/cases/repair-drafts/{draft_id}/apply", response_model=QuickRepairApplyOut)
async def apply_quick_repair_draft(
    draft_id: int,
    payload: QuickRepairApplyIn,
    session: SessionDep,
) -> QuickRepairApplyOut:
    try:
        item = await quick_repair.apply_repair_draft(
            session,
            draft_id,
            payload.steps_text,
            payload.preconditions,
            payload.expected_result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Quick repair draft not found")
    return item


@router.get("/cases/{case_id}/bug-draft", response_model=QuickBugDraftOut)
async def quick_bug_draft(
    case_id: int,
    session: SessionDep,
    user_id: Annotated[int | None, Query()] = None,
    reference_bug_url: Annotated[str | None, Query()] = None,
) -> QuickBugDraftOut:
    try:
        return QuickBugDraftOut.model_validate(
            await quick_bug_submit.build_bug_draft(session, case_id, user_id, reference_bug_url)
        )
    except quick_bug_submit.QuickBugSubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"生成 quick bug 草稿失败：{exc}") from exc


@router.post("/cases/{case_id}/bug", response_model=QuickBugSubmitOut)
async def submit_quick_bug(
    case_id: int,
    payload: QuickBugSubmitIn,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    user_id: Annotated[int | None, Query()] = None,
) -> QuickBugSubmitOut:
    try:
        result = await quick_bug_submit.submit_bug(
            session,
            case_id,
            user_id,
            {
                "title": payload.title,
                "description": payload.description,
                "fields": payload.fields,
                "key_images": payload.key_images,
                "reference_bug_url": payload.reference_bug_url,
            },
            background_tasks=background_tasks,
        )
        return QuickBugSubmitOut.model_validate(result)
    except quick_bug_submit.QuickBugSubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except fp.FeishuProjectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
