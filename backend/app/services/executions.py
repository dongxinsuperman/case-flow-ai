from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database
from app.core.settings import Settings, get_settings
from app.models.case_assets import (
    AIPhoneExecutionBatch,
    AIPhoneExecutionItem,
    CaseAsset,
    CaseBody,
    CaseBugDraft,
    CaseRepairDraft,
    CaseWorkItem,
)
from app.schemas.executions import (
    AIPhoneDeviceListOut,
    AIPhoneSubmitIn,
    AIPhoneSubmitOut,
    CasePlatformResultOut,
)
from app.services import execution_call_log
from app.services import function_map_mount as function_map_mount_service
from app.services.ai_api import AIAPICaseInput, AIAPIKernel, AIAPISecurityConfig
from app.services.ai_api import runtime as aiapi_runtime
from app.services.ai_api.schemas import AIAPIExecutionResult, ScenarioExecutionResult, StepExecutionResult
from app.services.executor_platforms import (
    COVERAGE_EXEC_LANES,
    executor_callback_base_url,
    normalize_device_alias_pools,
    normalize_executor_device,
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
    settings = get_settings()
    return await _list_executor_devices(settings.aiphone_base_url)


async def list_aiweb_devices() -> AIPhoneDeviceListOut:
    settings = get_settings()
    result = await _list_executor_devices(settings.aiweb_base_url)
    return AIPhoneDeviceListOut(
        source=result.source,
        devices=[normalize_executor_device(device, "ai_web") for device in result.devices],
        error=result.error,
    )


async def _list_executor_devices(base_url: str) -> AIPhoneDeviceListOut:
    base = base_url.rstrip("/")

    # 1) 空闲且就绪的设备：平台口径 online + ready + 未占用 + agent 在线（/available 已做过滤）。
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{base}/api/devices/available")
            response.raise_for_status()
            available_payload = response.json()
    except Exception as exc:
        return AIPhoneDeviceListOut(source="unavailable", devices=[], error=str(exc))

    available = available_payload.get("devices") if isinstance(available_payload, dict) else available_payload
    available = available or []
    idle_devices = [{**device, "occupancy": "idle"} for device in available]
    idle_keys = {_device_identity(device) for device in available if _device_identity(device)}

    # 2) 全量设备里挑出“在线但被占用”的（effective_status/effectiveStatus==busy），
    #    归一成与 /available 一致的字段形状。
    #    AI Phone 返回 snake_case；AI Web 可能返回 camelCase。
    #    未就绪 / 离线的设备既不在 /available、也不是 busy，自然被排除，不展示。
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            full_response = await client.get(f"{base}/api/devices/statuses")
            full_response.raise_for_status()
            full_payload = full_response.json()
    except Exception:
        # 全量接口拿不到时降级：只展示空闲设备，不阻断单条 / 批量执行。
        return AIPhoneDeviceListOut(source="service-idle-only", devices=idle_devices)

    full = full_payload if isinstance(full_payload, list) else (full_payload.get("devices") or [])
    busy_devices = [
        _normalize_busy_device(device)
        for device in full
        if _device_identity(device)
        and _device_identity(device) not in idle_keys
        and _device_effective_status(device) == "busy"
    ]
    return AIPhoneDeviceListOut(source="service", devices=idle_devices + busy_devices)


def _device_identity(device: dict[str, Any]) -> str:
    return str(device.get("serial") or device.get("alias") or "").strip()


def _device_field(device: dict[str, Any], snake_key: str, camel_key: str, default: Any = "") -> Any:
    value = device.get(snake_key)
    if value is None:
        value = device.get(camel_key)
    return default if value is None else value


def _device_effective_status(device: dict[str, Any]) -> str:
    return str(_device_field(device, "effective_status", "effectiveStatus")).lower()


def _normalize_busy_device(device: dict[str, Any]) -> dict[str, Any]:
    lock = device.get("lock") if isinstance(device.get("lock"), dict) else {}
    return {
        "serial": device.get("serial") or device.get("alias"),
        "alias": device.get("alias") or "",
        "platform": device.get("platform"),
        "brand": device.get("brand") or "",
        "model": device.get("model") or "",
        "osVersion": _device_field(device, "os_version", "osVersion"),
        "screenWidth": _device_field(device, "screen_width", "screenWidth", 0),
        "screenHeight": _device_field(device, "screen_height", "screenHeight", 0),
        "lastSeenAt": _device_field(device, "last_seen_at", "lastSeenAt", None),
        "occupancy": "busy",
        "lockHolderType": (lock or {}).get("holder_type") or (lock or {}).get("holderType"),
    }


async def submit_aiphone_execution(session: AsyncSession, payload: AIPhoneSubmitIn) -> AIPhoneSubmitOut:
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


async def submit_aiweb_execution(session: AsyncSession, payload: AIPhoneSubmitIn) -> AIPhoneSubmitOut:
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


async def submit_aihybrid_execution(session: AsyncSession, payload: AIPhoneSubmitIn) -> AIPhoneSubmitOut:
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


async def submit_aiapi_execution(session: AsyncSession, payload: AIPhoneSubmitIn) -> AIPhoneSubmitOut:
    case_ids = [int(case_id) for case_id in payload.case_ids if int(case_id) > 0]
    if not case_ids:
        raise ValueError("请选择需要执行的 case")

    cases = await _load_execution_cases(session, case_ids)
    if len(cases) != len(set(case_ids)):
        found_ids = {case.id for case, _body, _work_item in cases}
        missing = sorted(set(case_ids) - found_ids)
        raise ValueError(f"case 不存在：{missing}")

    mismatched = [case.id for case, _body, work_item in cases if work_item.execution_target != "api"]
    if mismatched:
        raise ValueError(f"AI API 只能执行 api case：{mismatched}")

    settings = get_settings()
    submission_id = f"local-aiapi-{uuid.uuid4().hex}"
    callback_token = uuid.uuid4().hex
    now_label = datetime.now().strftime("%Y%m%d-%H%M%S")
    submission_name = payload.submission_name or f"Case Flow AI API {now_label}"
    item_ids = {
        case.source_requirement_item_id
        for case, _body, _work_item in cases
        if case.source_requirement_item_id
    }
    if len(item_ids) > 1:
        raise ValueError("一次执行只能包含同一个二级需求下的 case")
    top_context = await function_map_mount_service.compile_top_level_context(session, item_ids, "api")

    submitted_items = [
        {
            "_case_id": case.id,
            "caseId": f"cf-{case.id}",
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
    if top_context.context:
        request_payload["functionMapContext"] = top_context.context

    requirement_ids = {case.source_requirement_item_id for case, _body, _work_item in cases}
    batch = AIPhoneExecutionBatch(
        submission_id=submission_id,
        submission_name=submission_name,
        requirement_item_id=requirement_ids.pop() if len(requirement_ids) == 1 else None,
        callback_token=callback_token,
        executor="ai_api",
        status="submitted",
        raw_request=request_payload,
        raw_response={
            "submissionId": submission_id,
            "submissionName": submission_name,
            "items": request_payload["items"],
        },
    )
    session.add(batch)
    await session.flush()
    for item in submitted_items:
        session.add(
            AIPhoneExecutionItem(
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
    await _clear_repair_drafts(session, case_ids)
    log = await execution_call_log.create_call_log(
        session,
        mode="standard",
        executor="ai_api",
        entry="executions/aiapi/submit",
        case_ids=case_ids,
        requirement_item_id=next(iter(item_ids)) if len(item_ids) == 1 else None,
        trigger_user_id=payload.current_user_id,
        request_group_id=payload.execution_request_group_id,
        input={
            "target": "api",
            "submissionName": submission_name,
        },
    )
    await execution_call_log.mark_submitted(
        session, log.id, execution_batch_id=batch.id, submission_id=submission_id
    )
    await session.commit()

    aiapi_runtime.start("standard", submission_id, case_ids)
    task = asyncio.create_task(_run_aiapi_batch(batch.id))
    aiapi_runtime.set_batch_task("standard", submission_id, task)
    task.add_done_callback(lambda task: _finish_aiapi_task("standard", submission_id, task))

    return AIPhoneSubmitOut(
        submission_id=submission_id,
        submission_name=submission_name,
        callback_url="",
        batch_id=batch.id,
        submitted_count=len(submitted_items),
        response=batch.raw_response,
        call_id=log.call_id,
    )


async def _submit_executor_execution(
    session: AsyncSession,
    payload: AIPhoneSubmitIn,
    spec: ExecutorSpec,
) -> AIPhoneSubmitOut:
    """前台只做便宜的入参校验 + 标记执行中 + 建调用日志，然后秒回；
    编译 Function Map 与提交执行器挪到后台任务，失败由后台把 case 回写为失败。"""
    case_ids = [int(case_id) for case_id in payload.case_ids if int(case_id) > 0]
    if not case_ids:
        raise ValueError("请选择需要执行的 case")

    cases = await _load_execution_cases(session, case_ids)
    if len(cases) != len(set(case_ids)):
        found_ids = {case.id for case, _body, _work_item in cases}
        missing = sorted(set(case_ids) - found_ids)
        raise ValueError(f"case 不存在：{missing}")

    mismatched = [case.id for case, _body, work_item in cases if work_item.execution_target != spec.target]
    if mismatched:
        raise ValueError(f"{spec.label} 只能执行 {spec.target} case：{mismatched}")

    item_ids = {
        case.source_requirement_item_id
        for case, _body, _work_item in cases
        if case.source_requirement_item_id
    }
    if len(item_ids) > 1:
        raise ValueError("一次执行只能包含同一个二级需求下的 case")

    now_label = datetime.now().strftime("%Y%m%d-%H%M%S")
    submission_name = payload.submission_name or f"Case Flow {spec.label} {now_label}"

    log = await execution_call_log.create_call_log(
        session,
        mode="standard",
        executor=spec.key,
        entry=f"executions/{spec.callback_slug}/submit",
        case_ids=case_ids,
        requirement_item_id=next(iter(item_ids)) if len(item_ids) == 1 else None,
        trigger_user_id=payload.current_user_id,
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
        # 重新执行 = 新一轮：上一次失败结论与已提交 bug 关联作废，bug 列表也清空（飞书工单不删，仅卡片忘记）。
        work_item.bugs = []
        work_item.active_execution_batch_id = None
        work_item.external_submission_id = None
        work_item.execution_started_at = started_at
        work_item.execution_finished_at = None
        work_item.updated_at = func.now()
    await _clear_repair_drafts(session, case_ids)
    await session.commit()

    task = asyncio.create_task(
        _run_executor_submit(
            call_log_id=call_log_id,
            case_ids=case_ids,
            spec=spec,
            device_alias_pools=payload.device_alias_pools,
            submission_name=submission_name,
            cache_mode=payload.cache_mode or "off",
            retry_max=int(payload.retry_max or 0),
        )
    )
    task.add_done_callback(lambda t: t.exception())

    return AIPhoneSubmitOut(
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
    case_ids: list[int],
    spec: ExecutorSpec,
    device_alias_pools: dict[str, list[str]] | None,
    submission_name: str,
    cache_mode: str,
    retry_max: int,
) -> None:
    """后台任务：编译 Function Map、提交执行器、建 batch/items；失败则回写 case 失败 + 日志。"""
    async with database.AsyncSessionLocal() as session:
        stage = "compile"
        try:
            cases = await _load_execution_cases(session, case_ids)
            if not cases:
                raise ValueError("case 不存在或已被删除")

            settings = get_settings()
            callback_token = uuid.uuid4().hex
            callback_base_url = executor_callback_base_url(settings, spec.key, spec.label)
            callback_url = f"{callback_base_url}/api/v1/{spec.callback_slug}/callback/{callback_token}"
            normalized_pools = normalize_device_alias_pools(device_alias_pools, spec.key)
            platforms = _platforms_from_device_alias_pools(
                normalized_pools, spec.default_platform, spec.key
            )

            submitted_items: list[dict[str, Any]] = []
            for case, body, _work_item in cases:
                external_case_id = f"cf-{case.id}"
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
            item_ids = {
                case.source_requirement_item_id
                for case, _body, _work_item in cases
                if case.source_requirement_item_id
            }
            top_context = await function_map_mount_service.compile_top_level_context(
                session, item_ids, spec.target
            )
            if top_context.context:
                request_payload["functionMapContext"] = top_context.context
            # Hybrid 主脑需要结构化目录做选机/端过滤；其他执行器只消费拼接上下文。
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

            submitted_units = _build_submitted_units(
                response_payload,
                submitted_items,
                spec.default_platform,
                spec.key,
            )
            requirement_ids = {
                case.source_requirement_item_id for case, _body, _work_item in cases
            }
            batch = AIPhoneExecutionBatch(
                submission_id=submission_id,
                submission_name=str(response_payload.get("submissionName") or submission_name),
                requirement_item_id=requirement_ids.pop() if len(requirement_ids) == 1 else None,
                callback_token=callback_token,
                executor=spec.key,
                status="submitted",
                raw_request=request_payload,
                raw_response=response_payload,
            )
            session.add(batch)
            await session.flush()
            for unit in submitted_units:
                session.add(
                    AIPhoneExecutionItem(
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
                # 前台已置为 running；后台只补 batch 关联与真实 submissionId。
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
    """提交前失败（编译/提交执行器）：把这些 case 从执行中回写为失败，并记日志。"""
    for case_id in case_ids:
        work_item = await session.get(CaseWorkItem, case_id)
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


async def apply_aihybrid_child_callback(
    callback_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from app.services.ai_hybrid import child_wait

    base_url = child_wait.base_url_for(callback_token) or get_settings().aiphone_base_url
    normalized = _normalize_executor_urls(payload, base_url)
    event = str(normalized.get("event") or "")
    result = _hybrid_child_result(normalized)
    if result is None:
        return {
            "handled": True,
            "event": event or "submission.terminal",
            "submission_id": str(normalized.get("submissionId") or normalized.get("submission_id") or ""),
            "updated_case_ids": [],
        }
    handled = child_wait.resolve(callback_token, result)
    return {
        "handled": handled,
        "event": event or "submission.item.terminal",
        "submission_id": str(result.get("submissionId") or result.get("submission_id") or ""),
        "updated_case_ids": [],
    }


async def _apply_executor_callback(
    session: AsyncSession,
    executor_key: str,
    base_url: str,
    callback_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload = _normalize_executor_urls(payload, base_url)
    event = str(payload.get("event") or "")
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


async def _run_aiapi_batch(batch_id: int) -> None:
    async with database.AsyncSessionLocal() as session:
        batch = await session.get(AIPhoneExecutionBatch, batch_id)
        if batch is None or batch.executor != "ai_api":
            return
        try:
            batch.status = "running"
            await session.commit()

            rows = await session.execute(
                select(AIPhoneExecutionItem)
                .where(AIPhoneExecutionItem.batch_id == batch_id)
                .order_by(AIPhoneExecutionItem.id)
            )
            items = list(rows.scalars().all())
            for item in items:
                if aiapi_runtime.is_stopped("standard", batch.submission_id, item.case_id):
                    continue
                task = asyncio.create_task(_execute_aiapi_item(session, batch, item))
                aiapi_runtime.set_item_task("standard", batch.submission_id, item.case_id, task)
                try:
                    await task
                except asyncio.CancelledError:
                    if aiapi_runtime.is_stopped("standard", batch.submission_id, item.case_id):
                        continue
                    raise
                finally:
                    aiapi_runtime.clear_item_task("standard", batch.submission_id, item.case_id)

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
            aiapi_runtime.finish("standard", batch.submission_id)


async def _execute_aiapi_item(
    session: AsyncSession,
    batch: AIPhoneExecutionBatch,
    item: AIPhoneExecutionItem,
) -> None:
    case = await session.get(CaseAsset, item.case_id)
    body = await session.get(CaseBody, item.case_id)
    work_item = await session.get(CaseWorkItem, item.case_id)
    if case is None or body is None or work_item is None:
        item.state = "failed"
        item.status_reason = "case_not_found"
        item.raw_item = {**(item.raw_item or {}), "error": "case/body/work_item not found"}
        await session.commit()
        return

    settings = get_settings()
    function_map_context = _effective_item_function_map_context(batch.raw_request, item.raw_item)
    case_input = _aiapi_case_input(case, body, function_map_context)
    kernel = AIAPIKernel(security_config=_aiapi_security_config(settings))
    result = await kernel.execute(case_input)
    if aiapi_runtime.is_stopped("standard", batch.submission_id, item.case_id):
        raise asyncio.CancelledError
    report_url = _write_aiapi_report(settings, batch.submission_id, item.case_id, result.report_html)
    item_state = "success" if result.status == "success" else "failed"
    item.state = item_state
    item.status_reason = result.status_reason
    item.run_id = f"aiapi-run-{uuid.uuid4().hex}"
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
        from app.services.case_repair import auto_diagnose_case

        task = asyncio.create_task(auto_diagnose_case(item.case_id))
        task.add_done_callback(lambda t: t.exception())


def stop_aiapi_execution(submission_id: str, case_ids: list[int] | None = None) -> list[int]:
    """AI API 内部停止入口；当前不对外暴露 HTTP 路由。"""
    return aiapi_runtime.stop("standard", submission_id, case_ids)


def _finish_aiapi_task(
    mode: aiapi_runtime.ExecutionMode,
    submission_id: str,
    task: asyncio.Task[object],
) -> None:
    aiapi_runtime.finish(mode, submission_id)
    try:
        task.exception()
    except asyncio.CancelledError:
        return


def _aiapi_case_input(
    case: CaseAsset,
    body: CaseBody,
    function_map_context: str,
) -> AIAPICaseInput:
    return AIAPICaseInput(
        title=_required_case_title(case),
        preconditions=body.preconditions or "",
        steps_text=body.steps_text or "",
        expected_result=body.expected_result or "",
        function_map_context=function_map_context,
    )


def _effective_item_function_map_context(
    batch_payload: dict[str, Any] | None,
    item_payload: dict[str, Any] | None,
) -> str:
    batch_context = _extract_function_map_context(batch_payload)
    item_context = _extract_function_map_context(item_payload)
    return _join_function_map_contexts(batch_context, item_context)


def _extract_function_map_context(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    raw = payload.get("functionMapContext")
    if raw is None:
        raw = payload.get("function_map_context")
    return str(raw or "").strip()


def _join_function_map_contexts(*parts: str) -> str:
    texts = [str(part or "").strip() for part in parts if str(part or "").strip()]
    return "\n\n".join(texts)


def _aiapi_security_config(settings: Settings) -> AIAPISecurityConfig:
    return AIAPISecurityConfig(
        allowed_hosts=tuple(_split_csv(settings.aiapi_allowed_hosts_raw)),
        allowed_base_urls=tuple(_split_csv(settings.aiapi_allowed_base_urls_raw)),
        default_base_url=str(settings.aiapi_default_base_url or ""),
        default_headers=_parse_aiapi_headers(settings.aiapi_default_headers_raw),
        allowed_methods=frozenset(
            _split_csv(settings.aiapi_allowed_methods_raw) or ["GET", "POST", "PUT", "PATCH", "DELETE"]
        ),
        allow_private_networks=bool(settings.aiapi_allow_private_networks),
        max_timeout_seconds=int(settings.aiapi_max_timeout_seconds or 20),
        max_response_bytes=int(settings.aiapi_max_response_bytes or 0),
        follow_redirects=bool(settings.aiapi_follow_redirects),
    )


def _parse_aiapi_headers(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _write_aiapi_report(
    settings: Settings,
    submission_id: str,
    case_id: int,
    report_html: str,
) -> str:
    report_dir = Path(settings.repair_image_dir) / "aiapi_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename(submission_id)}-case-{case_id}-{uuid.uuid4().hex}.html"
    (report_dir / filename).write_text(report_html, encoding="utf-8")
    path = f"/media/aiapi_reports/{filename}"
    base = str(settings.public_base_url or "").rstrip("/")
    return f"{base}{path}" if base else path


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or ""))
    return safe[:80] or "aiapi"


def _aiapi_result_payload(result: AIAPIExecutionResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "statusReason": result.status_reason,
        "plan": result.plan.model_dump(mode="json") if result.plan else None,
        "security": _json_safe(asdict(result.security)) if result.security else None,
        "exchange": _json_safe(asdict(result.exchange)) if result.exchange else None,
        "assertions": [_json_safe(asdict(item)) for item in result.assertions],
        "scenarioResults": [_scenario_result_payload(item) for item in result.scenario_results],
        "error": result.error,
        "repairSuggestion": result.repair_suggestion,
    }


def _scenario_result_payload(result: ScenarioExecutionResult) -> dict[str, Any]:
    return {
        "scenarioId": result.scenario_id,
        "name": result.name,
        "intent": result.intent,
        "expectedOutcome": result.expected_outcome,
        "required": result.required,
        "passed": result.passed,
        "statusReason": result.status_reason,
        "security": _json_safe(asdict(result.security)) if result.security else None,
        "exchange": _json_safe(asdict(result.exchange)) if result.exchange else None,
        "assertions": [_json_safe(asdict(item)) for item in result.assertions],
        "stepResults": [_step_result_payload(item) for item in result.step_results],
        "variables": _json_safe(result.variables),
        "error": result.error,
        "repairSuggestion": result.repair_suggestion,
    }


def _step_result_payload(result: StepExecutionResult) -> dict[str, Any]:
    return {
        "stepId": result.step_id,
        "name": result.name,
        "intent": result.intent,
        "passed": result.passed,
        "statusReason": result.status_reason,
        "security": _json_safe(asdict(result.security)) if result.security else None,
        "exchange": _json_safe(asdict(result.exchange)) if result.exchange else None,
        "assertions": [_json_safe(asdict(item)) for item in result.assertions],
        "extracted": _json_safe(result.extracted),
        "variablesBefore": _json_safe(result.variables_before),
        "variablesAfter": _json_safe(result.variables_after),
        "error": result.error,
        "repairSuggestion": result.repair_suggestion,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


async def _load_execution_cases(
    session: AsyncSession,
    case_ids: list[int],
) -> list[tuple[CaseAsset, CaseBody, CaseWorkItem]]:
    rows = await session.execute(
        select(CaseAsset, CaseBody, CaseWorkItem)
        .join(CaseBody, CaseBody.case_id == CaseAsset.id)
        .join(CaseWorkItem, CaseWorkItem.case_id == CaseAsset.id)
        .where(CaseAsset.id.in_(case_ids))
    )
    by_id = {case.id: (case, body, work_item) for case, body, work_item in rows.all()}
    return [by_id[case_id] for case_id in case_ids if case_id in by_id]


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


def _case_to_run_content(case: CaseAsset, body: CaseBody) -> str:
    title = _required_case_title(case)
    return "\n\n".join(
        [
            f"测试标题：{title}",
            f"前置条件：{body.preconditions or ''}",
            f"操作步骤：{body.steps_text or ''}",
            f"预期结果：{body.expected_result or ''}",
        ]
    )


def _required_case_title(case: CaseAsset) -> str:
    title = str(case.raw_title or "").strip()
    if not title:
        raise ValueError("Case 缺少完整测试标题，已停止执行")
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
    if not submission_id:
        raise ValueError("执行器单条回调缺少 submissionId")
    if not external_case_id:
        raise ValueError("执行器单条回调缺少 caseId")

    batch = await _find_batch(session, executor_key, callback_token, submission_id)
    if batch is None:
        raise ValueError("未找到执行批次映射")

    item = await _find_execution_item(session, batch.id, external_case_id, platform)
    if item is None:
        if _batch_had_submitted_case(batch, external_case_id, platform):
            batch.raw_callback = payload
            await session.commit()
            return {
                "handled": True,
                "ignored": True,
                "ignore_reason": "case_asset_deleted",
                "event": event,
                "submission_id": batch.submission_id,
                "updated_case_ids": [],
            }
        raise ValueError("未找到执行单元映射")

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

    work_item = await session.get(CaseWorkItem, item.case_id)
    case = await session.get(CaseAsset, item.case_id)
    overall_status = case_status
    overall_report: str | None = report_url
    overall_reason = status_reason
    if work_item and case:
        # 顺带点亮“这次回调这个端”的覆盖标记（按执行器负责的泳道）。
        if platform in COVERAGE_EXEC_LANES.get(batch.executor, set()) and case_status in ("passed", "failed"):
            coverage = {str(k): str(v) for k, v in (work_item.coverage or {}).items()}
            coverage[platform] = case_status
            work_item.coverage = coverage
        # 卡片整体 = 按“每端最近一次结果(跨批次)”取最差；当前批次还有端在跑则保持执行中（等齐）。
        overall_status, failing_report, failing_reason = await _aggregate_case_items(
            session, batch.id, item.case_id
        )
        if overall_status == "failed":
            overall_report = failing_report
            overall_reason = failing_reason or status_reason
        _apply_case_execution_result(
            work_item,
            case,
            batch,
            overall_status,
            overall_report,
            overall_reason or "",
        )
        # 兄弟端成功不清失败端诊断：只在“整体不是失败”时清草稿。
        if overall_status != "failed":
            await _clear_repair_drafts(session, [item.case_id])
    await session.commit()

    # 整体落定为失败且有报告：后台自动诊断（不阻塞回调）。仅在所有端都终态后触发。
    if work_item and case and overall_status == "failed" and overall_report:
        from app.services.case_repair import auto_diagnose_case

        task = asyncio.create_task(auto_diagnose_case(item.case_id))
        task.add_done_callback(lambda t: t.exception())
    return {
        "handled": True,
        "event": event,
        "submission_id": batch.submission_id,
        "updated_case_ids": [item.case_id],
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
        raise ValueError("执行器批次回调缺少 submissionId")
    batch = await _find_batch(session, executor_key, callback_token, submission_id)
    if batch is None:
        raise ValueError("未找到执行批次映射")
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
) -> AIPhoneExecutionBatch | None:
    rows = await session.execute(
        select(AIPhoneExecutionBatch)
        .where(
            AIPhoneExecutionBatch.executor == executor_key,
            (AIPhoneExecutionBatch.callback_token == callback_token)
            | (AIPhoneExecutionBatch.submission_id == submission_id)
        )
        .order_by(AIPhoneExecutionBatch.id.desc())
        .limit(1)
    )
    return rows.scalar_one_or_none()


async def _find_execution_item(
    session: AsyncSession,
    batch_id: int,
    external_case_id: str,
    platform: str,
) -> AIPhoneExecutionItem | None:
    query = select(AIPhoneExecutionItem).where(
        AIPhoneExecutionItem.batch_id == batch_id,
        AIPhoneExecutionItem.external_case_id == external_case_id,
    )
    if platform:
        query = query.where(AIPhoneExecutionItem.platform == platform)
    rows = await session.execute(query.order_by(AIPhoneExecutionItem.id.desc()))
    items = rows.scalars().all()
    if len(items) > 1 and not platform:
        raise ValueError("多端执行单条回调缺少 platform")
    return items[0] if items else None


def _batch_had_submitted_case(batch: AIPhoneExecutionBatch, external_case_id: str, platform: str) -> bool:
    for payload in (batch.raw_request, batch.raw_response):
        if not isinstance(payload, dict):
            continue
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_case_id = str(item.get("caseId") or item.get("case_id") or "")
            if item_case_id == external_case_id and _item_includes_platform(item, platform):
                return True
    return False


def _hybrid_child_result(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("caseId") or payload.get("case_id"):
        return payload
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                return {**payload, **item}
    return None


def _item_includes_platform(item: dict[str, Any], platform: str) -> bool:
    if not platform:
        return True
    raw_platforms = item.get("platforms") or item.get("platform")
    if raw_platforms is None:
        return True
    if isinstance(raw_platforms, str):
        return raw_platforms == platform
    if isinstance(raw_platforms, list):
        return platform in {str(value) for value in raw_platforms}
    return False


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


async def list_case_platform_results(
    session: AsyncSession,
    case_id: int,
) -> list[CasePlatformResultOut]:
    """取该 case 最近一次批次里每个端的结果（每端一条）。供多端“查看报告”浮层。"""
    rows = await session.execute(
        select(AIPhoneExecutionItem)
        .where(AIPhoneExecutionItem.case_id == case_id)
        .order_by(AIPhoneExecutionItem.batch_id.desc(), AIPhoneExecutionItem.id.desc())
    )
    items = list(rows.scalars().all())
    if not items:
        return []
    # 每端取“最近一次”（跨批次）：按 batch_id/id 倒序，平台首见即最新。不能只看最新批次，
    # 否则单独重跑一个端会丢掉其它端上一次的结果（卡片整体会被错误带成功）。
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


async def _aggregate_case_items(
    session: AsyncSession,
    batch_id: int,
    case_id: int,
) -> tuple[str, str | None, str | None]:
    """整体状态聚合。返回 (overall_status, 失败端报告url, 失败端原因)。

    - ① 当前批次还有端在跑 → running（“等齐”这次提交的所有端，不提前落终态）。
    - ② 否则按“**每端最近一次结果（跨批次）**”取最差：重跑单个端不会丢掉其它端上一次的结果。
      存在失败 → failed，并给出一个有报告的失败端（供单值 report 暂用）；否则 passed。
    """
    rows = await session.execute(
        select(AIPhoneExecutionItem).where(
            AIPhoneExecutionItem.batch_id == batch_id,
            AIPhoneExecutionItem.case_id == case_id,
        )
    )
    current_items = list(rows.scalars().all())
    if current_items and any(_aiphone_case_status(it.state) == "running" for it in current_items):
        return "running", None, None
    # 每端最近一次结果（跨批次，按 platform 去重取最新批次），这才是“所有端”的真实当前态。
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
    """端状态聚合：任一端仍 running 则等齐，否则 failed 优先，最后才是 passed。"""
    if not statuses:
        return "running"
    if any(status == "running" for status in statuses):
        return "running"
    if any(status == "failed" for status in statuses):
        return "failed"
    return "passed"


def _apply_case_execution_result(
    work_item: CaseWorkItem,
    case: CaseAsset,
    batch: AIPhoneExecutionBatch,
    case_status: str,
    report_url: str | None,
    status_reason: str,
) -> None:
    work_item.execution_status = case_status
    work_item.lifecycle_state = {
        "passed": "已固化",
        "failed": "待人工干预",
    }.get(case_status, "待验证")
    # 失败默认就是“执行失败”；attention 只留给“变更待确认”，不再表示失败。
    work_item.attention_reason = None
    work_item.case_type = "manual" if case.manual else "auto"
    work_item.run_enabled = True
    work_item.report_url = report_url
    work_item.failure_type = "execution_failed" if case_status == "failed" else None
    work_item.failure_summary = status_reason if case_status == "failed" else None
    # 新一轮结果落地 = 上一次失败作废，清掉已提交 bug 关联。
    work_item.bug_url = None
    work_item.bug_external_id = None
    work_item.active_execution_batch_id = batch.id if case_status == "running" else None
    work_item.external_submission_id = batch.submission_id
    work_item.execution_started_at = work_item.execution_started_at or batch.started_at
    work_item.execution_finished_at = None if case_status == "running" else func.now()
    work_item.updated_at = func.now()


async def _clear_repair_drafts(session: AsyncSession, case_ids: list[int]) -> None:
    if case_ids:
        await session.execute(delete(CaseRepairDraft).where(CaseRepairDraft.case_id.in_(case_ids)))
        await session.execute(delete(CaseBugDraft).where(CaseBugDraft.case_id.in_(case_ids)))


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
