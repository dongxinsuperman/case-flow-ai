from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.bug import BugDraftOut, BugImageUploadOut, BugSubmitIn, BugSubmitOut
from app.schemas.executions import (
    AIPhoneCallbackOut,
    AIPhoneDeviceListOut,
    AIPhoneSubmitIn,
    AIPhoneSubmitOut,
    CasePlatformResultOut,
)
from app.schemas.function_map import FunctionMapStateOut, FunctionMapUploadIn
from app.schemas.importing import (
    ImportJobStartOut,
    ImportJobStatusOut,
    ImportMarkdownIn,
    ImportMarkdownOut,
    ImportReviewCommitIn,
)
from app.schemas.repair import RepairApplyIn, RepairApplyOut, RepairPreviewIn, RepairPreviewOut
from app.schemas.workbench import (
    CaseAssetCreateIn,
    CaseAssetMutationOut,
    CaseAssetUpdateIn,
    CaseCoverageOut,
    CaseCoverageUpdateIn,
    CaseSuiteMarkdownExportOut,
    CaseSuiteMutationOut,
    CaseWorkbenchItemOut,
    CaseWorkItemUpdateIn,
    CaseWorkItemUpdateOut,
    HomeDashboardOut,
    RequirementCatalogOut,
    RequirementGroupAddPoolIn,
    RequirementGroupBindItemsIn,
    RequirementGroupCreateWithPoolIn,
    RequirementGroupMutationOut,
    RequirementGroupOut,
    RequirementItemAutoDiscoveryIn,
    RequirementItemAutoDiscoveryOut,
    RequirementItemMutationOut,
    RequirementItemsMutationOut,
    RequirementItemVersionUpdateIn,
    RequirementPoolCreateItemsIn,
    RequirementPoolPageOut,
    RequirementPoolOut,
    UserOut,
)
from app.services import (
    bug_submit,
    case_assets,
    case_repair,
    executions,
    function_map,
    import_jobs,
    importing,
    markdown_export,
    workbench,
)
from app.services.markdown_parser import ParseError
from app.services.sources import feishu_project

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserIdQuery = Annotated[int, Query()]
RequirementItemIdQuery = Annotated[int, Query()]


@router.get("/users", response_model=list[UserOut])
async def users(session: SessionDep) -> list[UserOut]:
    return await workbench.list_users(session)


@router.get("/requirements", response_model=list[RequirementGroupOut])
async def requirements(session: SessionDep) -> list[RequirementGroupOut]:
    return await workbench.list_requirement_groups(session)


@router.get("/requirement-catalog", response_model=RequirementCatalogOut)
async def requirement_catalog(
    session: SessionDep,
    source_space: Annotated[str | None, Query()] = None,
    person_id: Annotated[int | None, Query()] = None,
    sprint_id: Annotated[str | None, Query()] = None,
    testing_only: Annotated[bool, Query()] = False,
    keyword: Annotated[str | None, Query()] = None,
    focus_item_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=0, le=200)] = 0,
) -> RequirementCatalogOut:
    return await workbench.list_requirement_catalog(
        session,
        workbench.RequirementListFilters(
            source_space=source_space or None,
            person_id=person_id,
            sprint_id=sprint_id or None,
            testing_only=testing_only,
            keyword=keyword or None,
            focus_item_id=focus_item_id,
        ),
        page=page,
        page_size=page_size,
    )


@router.get("/requirement-pool", response_model=RequirementPoolPageOut)
async def requirement_pool(
    session: SessionDep,
    source_space: Annotated[str | None, Query()] = None,
    person_id: Annotated[int | None, Query()] = None,
    sprint_id: Annotated[str | None, Query()] = None,
    testing_only: Annotated[bool, Query()] = False,
    keyword: Annotated[str | None, Query()] = None,
    bound_status: Annotated[str, Query(pattern="^(all|bound|unbound)$")] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> RequirementPoolPageOut:
    return await workbench.list_requirement_pool(
        session,
        workbench.RequirementListFilters(
            source_space=source_space or None,
            person_id=person_id,
            sprint_id=sprint_id or None,
            testing_only=testing_only,
            keyword=keyword or None,
            bound_status=bound_status,
        ),
        page=page,
        page_size=page_size,
    )


@router.get("/sources/feishu-project/spaces")
async def feishu_project_spaces() -> dict:
    """配置里可拉取的空间(部门)列表，供前端做筛选。"""
    return {"spaces": feishu_project.list_configured_spaces()}


@router.post("/sources/feishu-project/pull")
async def pull_feishu_project(
    project_keys: Annotated[list[str] | None, Query()] = None,
) -> dict:
    """手动从飞书项目拉取需求入池。凭证走 env，空间/映射走 config/feishu_project.json。

    可选 project_keys：只拉指定空间；不传则拉配置里全部 spaces。
    """
    return feishu_project.start_pull_job(project_keys=project_keys)


@router.get("/sources/feishu-project/pull-jobs/{job_id}")
async def feishu_project_pull_job(job_id: str) -> dict:
    job = feishu_project.get_pull_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="飞书拉取任务不存在")
    return job


@router.post("/requirement-groups/create-with-pool", response_model=RequirementGroupMutationOut)
async def create_requirement_group_with_pool(
    payload: RequirementGroupCreateWithPoolIn,
    session: SessionDep,
) -> RequirementGroupMutationOut:
    try:
        return await workbench.create_requirement_group_with_pool_items(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/requirement-items/create-from-pool", response_model=RequirementItemsMutationOut)
async def create_requirement_items_from_pool(
    payload: RequirementPoolCreateItemsIn,
    session: SessionDep,
) -> RequirementItemsMutationOut:
    try:
        return await workbench.create_requirement_items_from_pool(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/requirement-groups/{group_id}/add-pool", response_model=RequirementGroupMutationOut)
async def add_pool_to_requirement_group(
    group_id: int,
    payload: RequirementGroupAddPoolIn,
    session: SessionDep,
) -> RequirementGroupMutationOut:
    try:
        return await workbench.add_pool_items_to_requirement_group(session, group_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/requirement-groups/{group_id}/bind-items", response_model=RequirementGroupMutationOut)
async def bind_requirement_items_to_group(
    group_id: int,
    payload: RequirementGroupBindItemsIn,
    session: SessionDep,
) -> RequirementGroupMutationOut:
    try:
        return await workbench.bind_requirement_items_to_group(session, group_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/requirement-items/{item_id}/unbind-group", response_model=RequirementItemMutationOut)
async def unbind_requirement_item_from_group(
    item_id: int,
    session: SessionDep,
) -> RequirementItemMutationOut:
    try:
        return await workbench.unbind_requirement_item_from_group(session, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/requirement-items/{item_id}/version", response_model=RequirementGroupMutationOut)
async def update_requirement_item_version(
    item_id: int,
    payload: RequirementItemVersionUpdateIn,
    session: SessionDep,
) -> RequirementGroupMutationOut:
    try:
        return await workbench.update_requirement_item_version(session, item_id, payload.version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/requirement-items/{item_id}/auto-discovery",
    response_model=RequirementItemAutoDiscoveryOut,
)
async def update_requirement_item_auto_discovery(
    item_id: int,
    payload: RequirementItemAutoDiscoveryIn,
    session: SessionDep,
) -> RequirementItemAutoDiscoveryOut:
    try:
        return await workbench.set_requirement_item_auto_discovery(session, item_id, payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/requirement-groups/{group_id}/function-map", response_model=FunctionMapStateOut)
async def get_group_function_map(group_id: int, session: SessionDep) -> FunctionMapStateOut:
    try:
        return await function_map.get_function_map(session, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/requirement-groups/{group_id}/function-map", response_model=FunctionMapStateOut)
async def upload_group_function_map(
    group_id: int,
    payload: FunctionMapUploadIn,
    session: SessionDep,
) -> FunctionMapStateOut:
    try:
        return await function_map.upload_function_map_file(session, group_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/requirement-groups/{group_id}/function-map", response_model=FunctionMapStateOut)
async def delete_group_function_map(
    group_id: int,
    session: SessionDep,
    filename: Annotated[str, Query()],
) -> FunctionMapStateOut:
    try:
        return await function_map.delete_function_map_file(session, group_id, filename)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/home", response_model=HomeDashboardOut)
async def home(
    session: SessionDep,
    user_id: UserIdQuery = 1,
) -> HomeDashboardOut:
    try:
        return await workbench.get_home_dashboard(session, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workbench-cases", response_model=list[CaseWorkbenchItemOut])
async def workbench_cases(
    session: SessionDep,
    requirement_item_id: RequirementItemIdQuery,
) -> list[CaseWorkbenchItemOut]:
    return await workbench.list_workbench_cases(session, requirement_item_id)


@router.get("/cases/{case_id}", response_model=CaseWorkbenchItemOut)
async def case_detail(case_id: int, session: SessionDep) -> CaseWorkbenchItemOut:
    item = await workbench.get_case(session, case_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return item


@router.post("/cases", response_model=CaseAssetMutationOut)
async def create_case_asset(
    payload: CaseAssetCreateIn,
    session: SessionDep,
) -> CaseAssetMutationOut:
    try:
        item = await case_assets.create_case_asset(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Case suite not found")
    return item


@router.post("/case-suites/export", response_model=CaseSuiteMarkdownExportOut)
async def export_case_suite_markdown(
    session: SessionDep,
    requirement_item_id: RequirementItemIdQuery,
    batch_id: Annotated[int, Query(gt=0)],
) -> CaseSuiteMarkdownExportOut:
    result = await markdown_export.export_case_suite_markdown(
        session,
        requirement_item_id,
        batch_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Case suite not found")
    return result


@router.delete("/case-suites", response_model=CaseSuiteMutationOut)
async def delete_case_suite(
    session: SessionDep,
    requirement_item_id: RequirementItemIdQuery,
    batch_id: Annotated[int, Query(gt=0)],
) -> CaseSuiteMutationOut:
    result = await case_assets.delete_case_suite(
        session,
        requirement_item_id,
        batch_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Case suite not found")
    return result


@router.get("/cases/{case_id}/platform-results", response_model=list[CasePlatformResultOut])
async def case_platform_results(case_id: int, session: SessionDep) -> list[CasePlatformResultOut]:
    return await executions.list_case_platform_results(session, case_id)


@router.patch("/cases/{case_id}", response_model=CaseAssetMutationOut)
async def update_case_asset(
    case_id: int,
    payload: CaseAssetUpdateIn,
    session: SessionDep,
) -> CaseAssetMutationOut:
    item = await case_assets.update_case_asset(session, case_id, payload)
    if item is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return item


@router.delete("/cases/{case_id}", response_model=CaseAssetMutationOut)
async def delete_case_asset(case_id: int, session: SessionDep) -> CaseAssetMutationOut:
    item = await case_assets.delete_case_asset(session, case_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return item


@router.post("/case-work-items/update", response_model=CaseWorkItemUpdateOut)
async def update_case_work_item(
    payload: CaseWorkItemUpdateIn,
    session: SessionDep,
) -> CaseWorkItemUpdateOut:
    item = await workbench.update_case_work_item(session, payload)
    if item is None:
        raise HTTPException(status_code=404, detail="Case work item not found")
    return item


@router.post("/case-work-items/coverage", response_model=CaseCoverageOut)
async def set_case_coverage(
    payload: CaseCoverageUpdateIn,
    session: SessionDep,
) -> CaseCoverageOut:
    try:
        item = await workbench.set_case_coverage(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Case work item not found")
    return item


@router.post("/cases/repair-preview", response_model=RepairPreviewOut)
async def preview_case_repairs(
    payload: RepairPreviewIn,
    session: SessionDep,
) -> RepairPreviewOut:
    return await case_repair.preview_repairs(session, payload)


@router.get("/cases/{case_id}/bug-draft", response_model=BugDraftOut)
async def bug_draft(case_id: int, session: SessionDep, user_id: UserIdQuery = 1) -> BugDraftOut:
    try:
        return BugDraftOut.model_validate(await bug_submit.build_bug_draft(session, case_id, user_id))
    except bug_submit.BugSubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        # 兜底：任何意外都回成 JSON 错误，避免前端拿到纯文本 500 解析失败。
        raise HTTPException(status_code=502, detail=f"生成 bug 草稿失败：{exc}") from exc


@router.post("/bug-images", response_model=BugImageUploadOut)
async def upload_bug_images(files: Annotated[list[UploadFile], File()]) -> BugImageUploadOut:
    try:
        return BugImageUploadOut(images=await bug_submit.upload_bug_images(files))
    except bug_submit.BugSubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cases/{case_id}/bug", response_model=BugSubmitOut)
async def submit_bug(
    case_id: int,
    payload: BugSubmitIn,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    user_id: UserIdQuery = 1,
) -> BugSubmitOut:
    try:
        result = await bug_submit.submit_bug(
            session, case_id, user_id,
            {
                "title": payload.title,
                "description": payload.description,
                "fields": payload.fields,
                "key_images": payload.key_images,
            },
            background_tasks=background_tasks,
        )
        return BugSubmitOut.model_validate(result)
    except bug_submit.BugSubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except feishu_project.FeishuProjectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/cases/repair-drafts/{draft_id}/apply", response_model=RepairApplyOut)
async def apply_case_repair_draft(
    draft_id: int,
    payload: RepairApplyIn,
    session: SessionDep,
) -> RepairApplyOut:
    try:
        item = await case_repair.apply_repair_draft(
            session, draft_id, payload.steps_text, payload.preconditions, payload.expected_result
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Repair draft not found")
    return item


@router.get("/aiphone/devices", response_model=AIPhoneDeviceListOut)
async def aiphone_devices() -> AIPhoneDeviceListOut:
    return await executions.list_aiphone_devices()


@router.get("/aiweb/devices", response_model=AIPhoneDeviceListOut)
async def aiweb_devices() -> AIPhoneDeviceListOut:
    return await executions.list_aiweb_devices()


@router.post("/executions/aiphone/submit", response_model=AIPhoneSubmitOut)
async def submit_aiphone_execution(
    payload: AIPhoneSubmitIn,
    session: SessionDep,
) -> AIPhoneSubmitOut:
    try:
        return await executions.submit_aiphone_execution(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/executions/aiweb/submit", response_model=AIPhoneSubmitOut)
async def submit_aiweb_execution(
    payload: AIPhoneSubmitIn,
    session: SessionDep,
) -> AIPhoneSubmitOut:
    try:
        return await executions.submit_aiweb_execution(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/executions/aiapi/submit", response_model=AIPhoneSubmitOut)
async def submit_aiapi_execution(
    payload: AIPhoneSubmitIn,
    session: SessionDep,
) -> AIPhoneSubmitOut:
    try:
        return await executions.submit_aiapi_execution(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/executions/aihybrid/submit", response_model=AIPhoneSubmitOut)
async def submit_aihybrid_execution(
    payload: AIPhoneSubmitIn,
    session: SessionDep,
) -> AIPhoneSubmitOut:
    try:
        return await executions.submit_aihybrid_execution(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/aiphone/callback/{callback_token}", response_model=AIPhoneCallbackOut)
async def aiphone_callback(
    callback_token: str,
    payload: dict,
    session: SessionDep,
) -> AIPhoneCallbackOut:
    try:
        return AIPhoneCallbackOut.model_validate(
            await executions.apply_aiphone_callback(session, callback_token, payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/aiweb/callback/{callback_token}", response_model=AIPhoneCallbackOut)
async def aiweb_callback(
    callback_token: str,
    payload: dict,
    session: SessionDep,
) -> AIPhoneCallbackOut:
    try:
        return AIPhoneCallbackOut.model_validate(
            await executions.apply_aiweb_callback(session, callback_token, payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/aihybrid/callback/{callback_token}", response_model=AIPhoneCallbackOut)
async def aihybrid_callback(
    callback_token: str,
    payload: dict,
    session: SessionDep,
) -> AIPhoneCallbackOut:
    try:
        return AIPhoneCallbackOut.model_validate(
            await executions.apply_aihybrid_callback(session, callback_token, payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/aihybrid/child-callback/{callback_token}", response_model=AIPhoneCallbackOut)
async def aihybrid_child_callback(
    callback_token: str,
    payload: dict,
) -> AIPhoneCallbackOut:
    return AIPhoneCallbackOut.model_validate(
        await executions.apply_aihybrid_child_callback(callback_token, payload)
    )


@router.post("/imports/markdown", response_model=ImportMarkdownOut)
async def import_markdown(
    payload: ImportMarkdownIn,
    session: SessionDep,
) -> ImportMarkdownOut:
    try:
        return await importing.start_import_markdown(session, payload)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/imports/markdown/commit", response_model=ImportJobStartOut)
async def commit_markdown_import_review(
    payload: ImportReviewCommitIn,
    session: SessionDep,
) -> ImportJobStartOut:
    try:
        return ImportJobStartOut.model_validate(await importing.start_commit_import_review(session, payload))
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/imports/markdown/jobs/{task_id}", response_model=ImportJobStatusOut)
async def import_markdown_job(task_id: str) -> ImportJobStatusOut:
    job = import_jobs.status(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导入任务不存在或已过期，请重新导入")
    return ImportJobStatusOut.model_validate(job)
