from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database
from app.core.settings import get_settings
from app.models.quick import (
    QuickBugDraft,
    QuickCase,
    QuickCaseBody,
    QuickCaseWorkItem,
    QuickExecutionBatch,
    QuickExecutionItem,
    QuickRepairDraft,
    QuickSession,
)
from app.schemas.executions import AIPhoneDeviceListOut, CasePlatformResultOut
from app.schemas.quick import QuickAIPhoneSubmitIn, QuickAIPhoneSubmitOut
from app.services import execution_call_log
from app.services.ai_api import AIAPICaseInput, AIAPIKernel
from app.services.ai_api import runtime as aiapi_runtime
from app.services.function_map_mount import compile_quick_context
from app.services.executor_platforms import (
    COVERAGE_EXEC_LANES,
    executor_callback_base_url,
    normalize_device_alias_pools,
    normalize_executor_platform,
)

TERMINAL_FAILED_STATES = {"failed", "cancelled", "expired", "timeout", "error"}
TERMINAL_PASSED_STATES = {"success", "passed", "pass"}


@dataclass(frozen=True)
class ExecutorSpec:
    key: str
    label: str
    base_url: str
    callback_slug: str
    default_platform: str
    target: str


async def list_aiphone_devices() -> AIPhoneDeviceListOut:
    from app.services.executions import list_aiphone_devices as list_formal_devices

    return await list_formal_devices()


async def list_aiweb_devices() -> AIPhoneDeviceListOut:
    from app.services.executions import list_aiweb_devices as list_formal_devices

    return await list_formal_devices()


async def submit_aiphone_execution(
    session: AsyncSession,
    payload: QuickAIPhoneSubmitIn,
) -> QuickAIPhoneSubmitOut:
    settings = get_settings()
    return await _submit_executor_execution(
        session,
        payload,
        ExecutorSpec(
            key="ai_phone",
            label="AI Phone",
            base_url=settings.aiphone_base_url,
            callback_slug="aiphone",
            default_platform="android",
            target="app",
        ),
    )


async def submit_aiweb_execution(
    session: AsyncSession,
    payload: QuickAIPhoneSubmitIn,
) -> QuickAIPhoneSubmitOut:
    settings = get_settings()
    return await _submit_executor_execution(
        session,
        payload,
        ExecutorSpec(
            key="ai_web",
            label="AI Web",
            base_url=settings.aiweb_base_url,
            callback_slug="aiweb",
            default_platform="chrome",
            target="web",
        ),
    )


async def submit_aihybrid_execution(
    session: AsyncSession,
    payload: QuickAIPhoneSubmitIn,
) -> QuickAIPhoneSubmitOut:
    settings = get_settings()
    return await _submit_executor_execution(
        session,
        payload,
        ExecutorSpec(
            key="ai_hybrid",
            label="AI Hybrid",
            base_url=settings.aihybrid_base_url,
            callback_slug="aihybrid",
            default_platform="mixed",
            target="mixed",
        ),
    )


async def submit_aiapi_execution(
    session: AsyncSession,
    payload: QuickAIPhoneSubmitIn,
) -> QuickAIPhoneSubmitOut:
    quick_session = await session.get(QuickSession, payload.session_id)
    if quick_session is None:
        raise ValueError("quick session 不存在")
    case_ids = [int(case_id) for case_id in payload.case_ids if int(case_id) > 0]
    if not case_ids:
        raise ValueError("请选择需要执行的 case")
    cases = await _load_execution_cases(session, quick_session.session_id, case_ids)
    if len(cases) != len(set(case_ids)):
        found_ids = {case.id for case, _body, _work_item in cases}
        missing = sorted(set(case_ids) - found_ids)
        raise ValueError(f"case 不存在或不属于当前 quick session：{missing}")
    mismatched = [case.id for case, _body, work_item in cases if work_item.execution_target != "api"]
    if mismatched:
        raise ValueError(f"AI API 只能执行 api case：{mismatched}")

    submission_id = f"local-quick-aiapi-{uuid.uuid4().hex}"
    callback_token = uuid.uuid4().hex
    now_label = datetime.now().strftime("%Y%m%d-%H%M%S")
    submission_name = payload.submission_name or f"Case Flow Quick AI API {now_label}"
    function_map_context = (
        await compile_quick_context(session, quick_session.session_id, "api")
    ).context
    submitted_items = [
        {
            "_case_id": case.id,
            "caseId": f"qcf-{case.id}",
            "caseName": _required_case_title(case),
            "runContent": _case_to_run_content(case, body),
            "platform": "api",
        }
        for case, body, _work_item in cases
    ]
    request_payload: dict[str, Any] = {
        "submissionName": submission_name,
        "items": [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in submitted_items
        ],
    }
    if function_map_context:
        request_payload["functionMapContext"] = function_map_context

    response_payload = {
        "submissionId": submission_id,
        "submissionName": submission_name,
        "items": request_payload["items"],
    }
    batch = QuickExecutionBatch(
        session_id=quick_session.session_id,
        submission_id=submission_id,
        submission_name=submission_name,
        callback_token=callback_token,
        executor="ai_api",
        status="submitted",
        raw_request=request_payload,
        raw_response=response_payload,
    )
    session.add(batch)
    await session.flush()

    for item in submitted_items:
        session.add(
            QuickExecutionItem(
                batch_id=batch.id,
                case_id=int(item["_case_id"]),
                external_case_id=str(item["caseId"]),
                platform="api",
                state="queued",
                raw_item={key: value for key, value in item.items() if not key.startswith("_")},
            )
        )

    started_at = batch.started_at or func.now()
    for case, _body, work_item in cases:
        work_item.execution_status = "running"
        work_item.lifecycle_state = "待验证"
        work_item.attention_reason = None
        work_item.case_type = "manual" if case.manual else "auto"
        work_item.run_enabled = True
        work_item.report_url = None
        work_item.failure_type = None
        work_item.failure_summary = None
        work_item.bug_url = None
        work_item.bug_external_id = None
        work_item.bugs = []
        work_item.active_execution_batch_id = batch.id
        work_item.external_submission_id = submission_id
        work_item.execution_started_at = started_at
        work_item.execution_finished_at = None
        work_item.updated_at = func.now()
    await _clear_drafts(session, case_ids)
    log = await execution_call_log.create_call_log(
        session,
        mode="quick",
        executor="ai_api",
        entry="quick/executions/aiapi/submit",
        case_ids=case_ids,
        quick_session_id=quick_session.session_id,
        trigger_user_id=quick_session.current_user_id,
        request_group_id=payload.execution_request_group_id,
        input={"target": "api", "submissionName": submission_name},
    )
    await execution_call_log.mark_submitted(
        session, log.id, execution_batch_id=batch.id, submission_id=submission_id
    )
    await session.commit()

    aiapi_runtime.start("quick", submission_id, case_ids)
    task = asyncio.create_task(_run_aiapi_batch(batch.id))
    aiapi_runtime.set_batch_task("quick", submission_id, task)
    task.add_done_callback(lambda task: _finish_aiapi_task(submission_id, task))

    return QuickAIPhoneSubmitOut(
        submission_id=submission_id,
        submission_name=submission_name,
        callback_url="",
        batch_id=batch.id,
        submitted_count=len(submitted_items),
        response=response_payload,
        call_id=log.call_id,
    )


async def _submit_executor_execution(
    session: AsyncSession,
    payload: QuickAIPhoneSubmitIn,
    spec: ExecutorSpec,
) -> QuickAIPhoneSubmitOut:
    """前台只校验 + 标记执行中 + 建调用日志后秒回；编译与提交执行器挪到后台。"""
    quick_session = await session.get(QuickSession, payload.session_id)
    if quick_session is None:
        raise ValueError("quick session 不存在")
    case_ids = [int(case_id) for case_id in payload.case_ids if int(case_id) > 0]
    if not case_ids:
        raise ValueError("请选择需要执行的 case")
    cases = await _load_execution_cases(session, quick_session.session_id, case_ids)
    if len(cases) != len(set(case_ids)):
        found_ids = {case.id for case, _body, _work_item in cases}
        missing = sorted(set(case_ids) - found_ids)
        raise ValueError(f"case 不存在或不属于当前 quick session：{missing}")
    mismatched = [case.id for case, _body, work_item in cases if work_item.execution_target != spec.target]
    if mismatched:
        raise ValueError(f"{spec.label} 只能执行 {spec.target} case：{mismatched}")

    now_label = datetime.now().strftime("%Y%m%d-%H%M%S")
    submission_name = payload.submission_name or f"Case Flow Quick {spec.label} {now_label}"

    log = await execution_call_log.create_call_log(
        session,
        mode="quick",
        executor=spec.key,
        entry=f"quick/executions/{spec.callback_slug}/submit",
        case_ids=case_ids,
        quick_session_id=quick_session.session_id,
        trigger_user_id=quick_session.current_user_id,
        request_group_id=payload.execution_request_group_id,
        input={
            "target": spec.target,
            "deviceAliasPools": payload.device_alias_pools or {},
            "cacheMode": payload.cache_mode or "off",
            "retryMax": int(payload.retry_max or 0),
            "submissionName": submission_name,
        },
    )
    call_id = log.call_id
    call_log_id = log.id

    started_at = func.now()
    for case, _body, work_item in cases:
        work_item.execution_status = "running"
        work_item.lifecycle_state = "待验证"
        work_item.attention_reason = None
        work_item.case_type = "manual" if case.manual else "auto"
        work_item.run_enabled = True
        work_item.report_url = None
        work_item.failure_type = None
        work_item.failure_summary = None
        work_item.bug_url = None
        work_item.bug_external_id = None
        work_item.bugs = []
        work_item.active_execution_batch_id = None
        work_item.external_submission_id = None
        work_item.execution_started_at = started_at
        work_item.execution_finished_at = None
        work_item.updated_at = func.now()
    await _clear_drafts(session, case_ids)
    await session.commit()

    task = asyncio.create_task(
        _run_executor_submit(
            call_log_id=call_log_id,
            session_id=quick_session.session_id,
            case_ids=case_ids,
            spec=spec,
            device_alias_pools=payload.device_alias_pools,
            submission_name=submission_name,
            cache_mode=payload.cache_mode or "off",
            retry_max=int(payload.retry_max or 0),
        )
    )
    task.add_done_callback(lambda t: t.exception())

    return QuickAIPhoneSubmitOut(
        submission_id="",
        submission_name=submission_name,
        callback_url="",
        batch_id=0,
        submitted_count=len(cases),
        response={},
        call_id=call_id,
    )


async def _run_executor_submit(
    *,
    call_log_id: int,
    session_id: str,
    case_ids: list[int],
    spec: ExecutorSpec,
    device_alias_pools: dict[str, list[str]] | None,
    submission_name: str,
    cache_mode: str,
    retry_max: int,
) -> None:
    """后台任务：编译 quick Function Map、提交执行器、建 batch/items；失败回写 case + 日志。"""
    async with database.AsyncSessionLocal() as session:
        stage = "compile"
        try:
            quick_session = await session.get(QuickSession, session_id)
            if quick_session is None:
                raise ValueError("quick session 不存在")
            cases = await _load_execution_cases(session, session_id, case_ids)
            if not cases:
                raise ValueError("case 不存在或已被删除")

            settings = get_settings()
            callback_token = uuid.uuid4().hex
            callback_base_url = executor_callback_base_url(settings, spec.key, spec.label)
            callback_url = (
                f"{callback_base_url}/api/v1/quick/{spec.callback_slug}/callback/{callback_token}"
            )
            normalized_pools = normalize_device_alias_pools(device_alias_pools, spec.key)
            platforms = _platforms_from_device_alias_pools(
                normalized_pools, spec.default_platform, spec.key
            )

            submitted_items: list[dict[str, Any]] = []
            for case, body, _work_item in cases:
                external_case_id = f"qcf-{case.id}"
                item: dict[str, Any] = {
                    "_case_id": case.id,
                    "caseId": external_case_id,
                    "caseName": _required_case_title(case),
                    "runContent": _case_to_run_content(case, body),
                    "platforms": platforms,
                }
                if normalized_pools:
                    item["deviceAliasPools"] = normalized_pools
                submitted_items.append(item)

            request_payload: dict[str, Any] = {
                "submissionName": submission_name,
                "callbackUrl": callback_url,
                "cacheMode": cache_mode or "off",
                "retryMax": int(retry_max or 0),
                "items": [
                    {key: value for key, value in item.items() if not key.startswith("_")}
                    for item in submitted_items
                ],
            }
            top_context = await compile_quick_context(session, session_id, spec.target)
            if top_context.context:
                request_payload["functionMapContext"] = top_context.context
            # Hybrid 主脑逐份读取结构化 Map 正文、按 targets 分端参考；其他执行器只消费拼接上下文。
            if spec.key == "ai_hybrid" and top_context.maps:
                request_payload["functionMaps"] = top_context.maps

            stage = "submit"
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.post(
                        f"{spec.base_url.rstrip('/')}/api/submissions",
                        json=request_payload,
                    )
                    response.raise_for_status()
                    response_payload = _normalize_executor_urls(response.json(), spec.base_url)
            except httpx.HTTPStatusError as exc:
                raise ValueError(_format_executor_http_error(spec.label, exc)) from exc

            submission_id = str(response_payload.get("submissionId") or response_payload.get("id") or "")
            if not submission_id:
                raise ValueError(f"{spec.label} 未返回 submissionId")

            batch = QuickExecutionBatch(
                session_id=session_id,
                submission_id=submission_id,
                submission_name=str(response_payload.get("submissionName") or submission_name),
                callback_token=callback_token,
                executor=spec.key,
                status="submitted",
                raw_request=request_payload,
                raw_response=response_payload,
            )
            session.add(batch)
            await session.flush()

            submitted_units = _build_submitted_units(
                response_payload,
                submitted_items,
                spec.default_platform,
                spec.key,
            )
            for unit in submitted_units:
                session.add(
                    QuickExecutionItem(
                        batch_id=batch.id,
                        case_id=int(unit["case_id"]),
                        external_case_id=str(unit["external_case_id"]),
                        platform=str(unit["platform"]),
                        state=str(unit.get("state") or "queued"),
                        device_alias_pool=unit.get("device_alias_pool"),
                        raw_item=unit.get("raw_item") or {},
                    )
                )

            for _case, _body, work_item in cases:
                if work_item.execution_status == "running":
                    work_item.active_execution_batch_id = batch.id
                    work_item.external_submission_id = submission_id
                    work_item.updated_at = func.now()

            await execution_call_log.mark_submitted(
                session, call_log_id, execution_batch_id=batch.id, submission_id=submission_id
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - 提交前失败无外部回调，必须由本方自己回写
            await session.rollback()
            reason = str(exc)
            status = "compile_failed" if stage == "compile" else "submit_failed"
            prefix = "策略编译失败" if stage == "compile" else "提交执行器失败"
            await _mark_submit_failed(
                session,
                call_log_id=call_log_id,
                case_ids=case_ids,
                status=status,
                reason=reason,
                summary=f"{prefix}：{reason}",
            )


async def _mark_submit_failed(
    session: AsyncSession,
    *,
    call_log_id: int,
    case_ids: list[int],
    status: str,
    reason: str,
    summary: str,
) -> None:
    """提交前失败：把 quick case 从执行中回写为失败，并记日志。"""
    for case_id in case_ids:
        work_item = await session.get(QuickCaseWorkItem, case_id)
        if work_item is None or work_item.execution_status != "running":
            continue
        work_item.execution_status = "failed"
        work_item.lifecycle_state = "待人工干预"
        work_item.attention_reason = None
        work_item.run_enabled = True
        work_item.report_url = None
        work_item.failure_type = "environment_failure"
        work_item.failure_summary = summary
        work_item.active_execution_batch_id = None
        work_item.execution_finished_at = func.now()
        work_item.updated_at = func.now()
    await execution_call_log.mark_failed(session, call_log_id, status=status, reason=reason)
    await session.commit()


async def apply_aiphone_callback(
    session: AsyncSession,
    callback_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await _apply_executor_callback(
        session,
        "ai_phone",
        get_settings().aiphone_base_url,
        callback_token,
        payload,
    )


async def apply_aiweb_callback(
    session: AsyncSession,
    callback_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await _apply_executor_callback(
        session,
        "ai_web",
        get_settings().aiweb_base_url,
        callback_token,
        payload,
    )


async def apply_aihybrid_callback(
    session: AsyncSession,
    callback_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await _apply_executor_callback(
        session,
        "ai_hybrid",
        get_settings().aihybrid_base_url,
        callback_token,
        payload,
    )


async def _apply_executor_callback(
    session: AsyncSession,
    executor_key: str,
    base_url: str,
    callback_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload = _normalize_executor_urls(payload, base_url)
    event = str(payload.get("event") or "")
    try:
        if event == "submission.item.terminal" or payload.get("caseId") or payload.get("case_id"):
            return await _apply_item_event(
                session,
                executor_key,
                callback_token,
                payload,
                event or "submission.item.terminal",
            )
        return await _apply_submission_event(
            session,
            executor_key,
            callback_token,
            payload,
            event or "submission.terminal",
        )
    except ValueError:
        return {
            "handled": False,
            "event": event or "quick.orphan_callback",
            "submission_id": payload.get("submissionId") or payload.get("submission_id"),
            "updated_case_ids": [],
        }


async def list_case_platform_results(
    session: AsyncSession,
    case_id: int,
) -> list[CasePlatformResultOut]:
    rows = await session.execute(
        select(QuickExecutionItem)
        .where(QuickExecutionItem.case_id == case_id)
        .order_by(QuickExecutionItem.batch_id.desc(), QuickExecutionItem.id.desc())
    )
    items = list(rows.scalars().all())
    if not items:
        return []
    results: list[CasePlatformResultOut] = []
    seen: set[str] = set()
    for item in items:
        if item.platform in seen:
            continue
        seen.add(item.platform)
        results.append(
            CasePlatformResultOut(
                platform=item.platform,
                state=_aiphone_case_status(item.state),
                report_url=item.report_url,
                run_id=item.run_id,
                status_reason=item.status_reason,
            )
        )
    return results


async def _load_execution_cases(
    session: AsyncSession,
    session_id: str,
    case_ids: list[int],
) -> list[tuple[QuickCase, QuickCaseBody, QuickCaseWorkItem]]:
    rows = await session.execute(
        select(QuickCase, QuickCaseBody, QuickCaseWorkItem)
        .join(QuickCaseBody, QuickCaseBody.case_id == QuickCase.id)
        .join(QuickCaseWorkItem, QuickCaseWorkItem.case_id == QuickCase.id)
        .where(QuickCase.session_id == session_id, QuickCase.id.in_(case_ids))
    )
    by_id = {case.id: (case, body, work_item) for case, body, work_item in rows.all()}
    return [by_id[case_id] for case_id in case_ids if case_id in by_id]


async def _run_aiapi_batch(batch_id: int) -> None:
    async with database.AsyncSessionLocal() as session:
        batch = await session.get(QuickExecutionBatch, batch_id)
        if batch is None or batch.executor != "ai_api":
            return
        try:
            batch.status = "running"
            await session.commit()

            rows = await session.execute(
                select(QuickExecutionItem)
                .where(QuickExecutionItem.batch_id == batch_id)
                .order_by(QuickExecutionItem.id)
            )
            items = list(rows.scalars().all())
            for item in items:
                if aiapi_runtime.is_stopped("quick", batch.submission_id, item.case_id):
                    continue
                task = asyncio.create_task(_execute_aiapi_item(session, batch, item))
                aiapi_runtime.set_item_task("quick", batch.submission_id, item.case_id, task)
                try:
                    await task
                except asyncio.CancelledError:
                    if aiapi_runtime.is_stopped("quick", batch.submission_id, item.case_id):
                        continue
                    raise
                finally:
                    aiapi_runtime.clear_item_task("quick", batch.submission_id, item.case_id)

            counts = {
                "success": sum(1 for item in items if item.state == "success"),
                "failed": sum(1 for item in items if item.state == "failed"),
            }
            batch.status = "done"
            batch.finished_at = batch.finished_at or func.now()
            batch.raw_callback = {
                "event": "submission.terminal",
                "submissionId": batch.submission_id,
                "submissionState": "done",
                "counts": counts,
            }
            batch.summary_report_url = next((item.report_url for item in items if item.state == "failed"), None)
            await session.commit()
        except asyncio.CancelledError:
            # 停止不是执行结果：不写报告、不回写成功或失败。
            return
        finally:
            aiapi_runtime.finish("quick", batch.submission_id)


async def _execute_aiapi_item(
    session: AsyncSession,
    batch: QuickExecutionBatch,
    item: QuickExecutionItem,
) -> None:
    case = await session.get(QuickCase, item.case_id)
    body = await session.get(QuickCaseBody, item.case_id)
    work_item = await session.get(QuickCaseWorkItem, item.case_id)
    if case is None or body is None or work_item is None:
        item.state = "failed"
        item.status_reason = "case_not_found"
        item.raw_item = {**(item.raw_item or {}), "error": "case/body/work_item not found"}
        await session.commit()
        return

    from app.services.executions import (
        _aiapi_result_payload,
        _aiapi_security_config,
        _effective_item_function_map_context,
        _write_aiapi_report,
    )

    settings = get_settings()
    function_map_context = _effective_item_function_map_context(batch.raw_request, item.raw_item)
    case_input = _aiapi_case_input(case, body, function_map_context)
    kernel = AIAPIKernel(security_config=_aiapi_security_config(settings))
    result = await kernel.execute(case_input)
    if aiapi_runtime.is_stopped("quick", batch.submission_id, item.case_id):
        raise asyncio.CancelledError
    report_url = _write_aiapi_report(settings, batch.submission_id, item.case_id, result.report_html)
    item_state = "success" if result.status == "success" else "failed"
    item.state = item_state
    item.status_reason = result.status_reason
    item.run_id = f"quick-aiapi-run-{uuid.uuid4().hex}"
    item.report_url = report_url
    item.raw_item = {
        **(item.raw_item or {}),
        "runId": item.run_id,
        "state": item_state,
        "statusReason": result.status_reason,
        "reportUrl": report_url,
        "effectiveFunctionMapContext": function_map_context,
        "result": _aiapi_result_payload(result),
    }
    case_status = "passed" if result.status == "success" else "failed"
    _apply_case_execution_result(work_item, case, batch, case_status, report_url, result.status_reason)
    await session.commit()

    if case_status == "failed" and report_url:
        from app.services.quick_repair import auto_diagnose_case

        task = asyncio.create_task(auto_diagnose_case(item.case_id))
        task.add_done_callback(lambda t: t.exception())


def stop_aiapi_execution(submission_id: str, case_ids: list[int] | None = None) -> list[int]:
    """Quick AI API 内部停止入口；当前不对外暴露 HTTP 路由。"""
    return aiapi_runtime.stop("quick", submission_id, case_ids)


def _finish_aiapi_task(submission_id: str, task: asyncio.Task[object]) -> None:
    aiapi_runtime.finish("quick", submission_id)
    try:
        task.exception()
    except asyncio.CancelledError:
        return


def _aiapi_case_input(
    case: QuickCase,
    body: QuickCaseBody,
    function_map_context: str,
) -> AIAPICaseInput:
    return AIAPICaseInput(
        title=_required_case_title(case),
        preconditions=body.preconditions or "",
        steps_text=body.steps_text or "",
        expected_result=body.expected_result or "",
        function_map_context=function_map_context,
    )


def _platforms_from_device_alias_pools(
    device_alias_pools: dict[str, list[str]] | None,
    default_platform: str,
    executor_key: str,
) -> list[str]:
    if not device_alias_pools:
        return [normalize_executor_platform(default_platform, executor_key)]
    platforms: list[str] = []
    for platform, aliases in device_alias_pools.items():
        if aliases is not None and not isinstance(aliases, list):
            continue
        normalized = normalize_executor_platform(platform, executor_key)
        if normalized not in platforms:
            platforms.append(normalized)
    return platforms or [normalize_executor_platform(default_platform, executor_key)]


def _case_to_run_content(case: QuickCase, body: QuickCaseBody) -> str:
    title = _required_case_title(case)
    return "\n\n".join(
        [
            f"测试标题：{title}",
            f"前置条件：{body.preconditions or ''}",
            f"操作步骤：{body.steps_text or ''}",
            f"预期结果：{body.expected_result or ''}",
        ]
    )


def _required_case_title(case: QuickCase) -> str:
    title = str(case.raw_title or "").strip()
    if not title:
        raise ValueError("Quick Case 缺少完整测试标题，已停止执行")
    return title


def _build_submitted_units(
    response_payload: dict[str, Any],
    submitted_items: list[dict[str, Any]],
    default_platform: str,
    executor_key: str,
) -> list[dict[str, Any]]:
    case_id_by_external = {item["caseId"]: item["_case_id"] for item in submitted_items}
    response_items = response_payload.get("items") if isinstance(response_payload, dict) else None
    units: list[dict[str, Any]] = []
    if response_items:
        for item in response_items:
            external_case_id = str(item.get("caseId") or item.get("case_id") or "")
            case_id = case_id_by_external.get(external_case_id)
            if not case_id:
                continue
            units.append(
                {
                    "case_id": case_id,
                    "external_case_id": external_case_id,
                    "platform": normalize_executor_platform(
                        item.get("platform") or default_platform,
                        executor_key,
                    ),
                    "state": item.get("state") or "queued",
                    "device_alias_pool": item.get("deviceAliasPool") or item.get("device_alias_pool"),
                    "raw_item": item,
                }
            )
        return units
    for item in submitted_items:
        pools = item.get("deviceAliasPools") or {}
        for raw_platform in item.get("platforms") or [default_platform]:
            platform = normalize_executor_platform(raw_platform, executor_key)
            units.append(
                {
                    "case_id": item["_case_id"],
                    "external_case_id": item["caseId"],
                    "platform": platform,
                    "state": "queued",
                    "device_alias_pool": pools.get(platform) if isinstance(pools, dict) else None,
                    "raw_item": {
                        "caseId": item["caseId"],
                        "caseName": item.get("caseName"),
                        "platform": platform,
                    },
                }
            )
    return units


async def _apply_item_event(
    session: AsyncSession,
    executor_key: str,
    callback_token: str,
    payload: dict[str, Any],
    event: str,
) -> dict[str, Any]:
    submission_id = str(payload.get("submissionId") or payload.get("submission_id") or "")
    external_case_id = str(payload.get("caseId") or payload.get("case_id") or "")
    platform = normalize_executor_platform(payload.get("platform"), executor_key)
    if not submission_id or not external_case_id:
        raise ValueError("quick 执行器单条回调缺少身份字段")
    batch = await _find_batch(session, executor_key, callback_token, submission_id)
    if batch is None:
        raise ValueError("quick execution batch not found")
    item = await _find_execution_item(session, batch.id, external_case_id, platform)
    if item is None:
        raise ValueError("quick execution item not found")

    item_state = str(payload.get("state") or "").lower()
    case_status = _aiphone_case_status(item_state)
    status_reason = str(payload.get("statusReason") or payload.get("status_reason") or item_state or "")
    report_url = payload.get("reportUrl") or payload.get("report_url")
    device_alias_pool = payload.get("deviceAliasPool") or payload.get("device_alias_pool")
    if device_alias_pool is not None and not isinstance(device_alias_pool, list):
        device_alias_pool = [str(device_alias_pool)]

    item.state = item_state or case_status
    item.status_reason = status_reason
    item.run_id = payload.get("runId") or payload.get("run_id")
    item.report_url = report_url
    item.device_alias_pool = device_alias_pool
    item.raw_item = payload
    batch.status = "item_callback_received" if case_status == "running" else batch.status
    batch.raw_callback = payload

    updated_case_ids: list[int] = []
    diagnose_case_id: int | None = None
    if item.case_id is not None:
        work_item = await session.get(QuickCaseWorkItem, item.case_id)
        case = await session.get(QuickCase, item.case_id)
        if work_item and case:
            if (
                platform in COVERAGE_EXEC_LANES.get(batch.executor, set())
                and case_status in ("passed", "failed")
            ):
                coverage = {str(k): str(v) for k, v in (work_item.coverage or {}).items()}
                coverage[platform] = case_status
                work_item.coverage = coverage
            overall_status, failing_report, failing_reason = await _aggregate_case_items(
                session,
                batch.id,
                item.case_id,
            )
            overall_report = failing_report if overall_status == "failed" else report_url
            overall_reason = failing_reason or status_reason
            _apply_case_execution_result(
                work_item,
                case,
                batch,
                overall_status,
                overall_report,
                overall_reason,
            )
            if overall_status != "failed":
                await _clear_drafts(session, [item.case_id])
            updated_case_ids.append(item.case_id)
            if overall_status == "failed" and overall_report:
                diagnose_case_id = item.case_id
    await session.commit()
    if diagnose_case_id is not None:
        from app.services.quick_repair import auto_diagnose_case

        task = asyncio.create_task(auto_diagnose_case(diagnose_case_id))
        task.add_done_callback(lambda t: t.exception())
    return {
        "handled": bool(updated_case_ids),
        "event": event,
        "submission_id": batch.submission_id,
        "updated_case_ids": updated_case_ids,
    }


async def _apply_submission_event(
    session: AsyncSession,
    executor_key: str,
    callback_token: str,
    payload: dict[str, Any],
    event: str,
) -> dict[str, Any]:
    submission_id = str(payload.get("submissionId") or payload.get("submission_id") or "")
    if not submission_id:
        raise ValueError("quick 执行器批次回调缺少 submissionId")
    batch = await _find_batch(session, executor_key, callback_token, submission_id)
    if batch is None:
        raise ValueError("quick execution batch not found")
    batch.status = str(
        payload.get("submissionState")
        or payload.get("submission_state")
        or payload.get("state")
        or "callback_received"
    )
    batch.finished_at = batch.finished_at or func.now()
    batch.summary_report_url = payload.get("summaryReportUrl") or payload.get("summary_report_url")
    batch.raw_callback = payload
    await session.commit()
    return {
        "handled": True,
        "event": event,
        "submission_id": batch.submission_id,
        "updated_case_ids": [],
    }


async def _find_batch(
    session: AsyncSession,
    executor_key: str,
    callback_token: str,
    submission_id: str,
) -> QuickExecutionBatch | None:
    rows = await session.execute(
        select(QuickExecutionBatch)
        .where(
            QuickExecutionBatch.executor == executor_key,
            (QuickExecutionBatch.callback_token == callback_token)
            | (QuickExecutionBatch.submission_id == submission_id)
        )
        .order_by(QuickExecutionBatch.id.desc())
        .limit(1)
    )
    return rows.scalar_one_or_none()


async def _find_execution_item(
    session: AsyncSession,
    batch_id: int,
    external_case_id: str,
    platform: str,
) -> QuickExecutionItem | None:
    query = select(QuickExecutionItem).where(
        QuickExecutionItem.batch_id == batch_id,
        QuickExecutionItem.external_case_id == external_case_id,
    )
    if platform:
        query = query.where(QuickExecutionItem.platform == platform)
    rows = await session.execute(query.order_by(QuickExecutionItem.id.desc()))
    items = rows.scalars().all()
    if len(items) > 1 and not platform:
        raise ValueError("quick 多端执行单条回调缺少 platform")
    return items[0] if items else None


def _aiphone_case_status(item_state: str) -> str:
    normalized = str(item_state or "").lower()
    if normalized in TERMINAL_PASSED_STATES:
        return "passed"
    if normalized in TERMINAL_FAILED_STATES:
        return "failed"
    return "running"


def _format_executor_http_error(label: str, exc: httpx.HTTPStatusError) -> str:
    status = exc.response.status_code
    detail: Any
    try:
        detail = exc.response.json().get("detail")
    except Exception:
        detail = exc.response.text
    if isinstance(detail, dict):
        reason = detail.get("rejectReason") or detail.get("reason") or status
        message = detail.get("rejectDetail") or detail.get("message") or detail
        return f"{label} 拒绝提交：{reason}，{message}"
    if detail:
        return f"{label} 提交失败：HTTP {status}，{detail}"
    return f"{label} 提交失败：HTTP {status}"


async def _aggregate_case_items(
    session: AsyncSession,
    batch_id: int,
    case_id: int,
) -> tuple[str, str | None, str | None]:
    rows = await session.execute(
        select(QuickExecutionItem).where(
            QuickExecutionItem.batch_id == batch_id,
            QuickExecutionItem.case_id == case_id,
        )
    )
    current_items = list(rows.scalars().all())
    if current_items and any(_aiphone_case_status(item.state) == "running" for item in current_items):
        return "running", None, None
    latest = await list_case_platform_results(session, case_id)
    if not latest:
        return "running", None, None
    overall = _worst_wins([result.state for result in latest])
    if overall == "failed":
        failing = [result for result in latest if result.state == "failed"]
        representative = next((result for result in failing if result.report_url), failing[0])
        return "failed", representative.report_url, representative.status_reason
    return overall, None, None


def _worst_wins(statuses: list[str]) -> str:
    if not statuses:
        return "running"
    if any(status == "running" for status in statuses):
        return "running"
    if any(status == "failed" for status in statuses):
        return "failed"
    return "passed"


def _apply_case_execution_result(
    work_item: QuickCaseWorkItem,
    case: QuickCase,
    batch: QuickExecutionBatch,
    case_status: str,
    report_url: str | None,
    status_reason: str,
) -> None:
    work_item.execution_status = case_status
    work_item.lifecycle_state = {
        "passed": "已固化",
        "failed": "待人工干预",
    }.get(case_status, "待验证")
    work_item.attention_reason = None
    work_item.case_type = "manual" if case.manual else "auto"
    work_item.run_enabled = True
    work_item.report_url = report_url
    work_item.failure_type = "execution_failed" if case_status == "failed" else None
    work_item.failure_summary = status_reason if case_status == "failed" else None
    work_item.bug_url = None
    work_item.bug_external_id = None
    work_item.active_execution_batch_id = batch.id if case_status == "running" else None
    work_item.external_submission_id = batch.submission_id
    work_item.execution_started_at = work_item.execution_started_at or batch.started_at
    work_item.execution_finished_at = None if case_status == "running" else func.now()
    work_item.updated_at = func.now()


async def _clear_drafts(session: AsyncSession, case_ids: list[int]) -> None:
    if case_ids:
        await session.execute(delete(QuickRepairDraft).where(QuickRepairDraft.case_id.in_(case_ids)))
        await session.execute(delete(QuickBugDraft).where(QuickBugDraft.case_id.in_(case_ids)))


def _normalize_aiphone_urls(payload: dict[str, Any]) -> dict[str, Any]:
    return _normalize_executor_urls(payload, get_settings().aiphone_base_url)


def _normalize_executor_urls(payload: dict[str, Any], base_url: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    for key in ("summary_report_url", "summaryReportUrl", "report_url", "reportUrl"):
        if key in normalized:
            normalized[key] = _absolute_executor_url(normalized.get(key), base_url)
    items = []
    for item in normalized.get("items") or []:
        row = dict(item)
        for key in ("report_url", "reportUrl"):
            if key in row:
                row[key] = _absolute_executor_url(row.get(key), base_url)
        items.append(row)
    if "items" in normalized:
        normalized["items"] = items
    return normalized


def _absolute_aiphone_url(value: str | None) -> str | None:
    return _absolute_executor_url(value, get_settings().aiphone_base_url)


def _absolute_executor_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    value = str(value)
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        return f"{base_url.rstrip('/')}{value}"
    return value
