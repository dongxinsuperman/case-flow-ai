from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.case_assets import AIPhoneExecutionBatch, AIPhoneExecutionItem
from app.models.quick import QuickExecutionBatch, QuickExecutionItem

CancellationMode = Literal["standard", "quick"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CancellationTarget:
    """重置前从执行项摘取的最小取消信息，不承载任何取消结果。"""

    executor: str
    submission_id: str
    external_case_id: str
    platform: str
    case_id: int


async def snapshot_standard_targets(
    session: AsyncSession,
    *,
    case_id: int,
    active_batch_id: int | None,
    external_submission_id: str | None,
) -> list[CancellationTarget]:
    return await _snapshot_targets(
        session,
        batch_model=AIPhoneExecutionBatch,
        item_model=AIPhoneExecutionItem,
        case_id=case_id,
        active_batch_id=active_batch_id,
        external_submission_id=external_submission_id,
    )


async def snapshot_quick_targets(
    session: AsyncSession,
    *,
    case_id: int,
    active_batch_id: int | None,
    external_submission_id: str | None,
) -> list[CancellationTarget]:
    return await _snapshot_targets(
        session,
        batch_model=QuickExecutionBatch,
        item_model=QuickExecutionItem,
        case_id=case_id,
        active_batch_id=active_batch_id,
        external_submission_id=external_submission_id,
    )


async def _snapshot_targets(
    session: AsyncSession,
    *,
    batch_model: type[AIPhoneExecutionBatch] | type[QuickExecutionBatch],
    item_model: type[AIPhoneExecutionItem] | type[QuickExecutionItem],
    case_id: int,
    active_batch_id: int | None,
    external_submission_id: str | None,
) -> list[CancellationTarget]:
    if active_batch_id is None and not external_submission_id:
        return []

    statement = (
        select(
            batch_model.executor,
            batch_model.submission_id,
            item_model.external_case_id,
            item_model.platform,
            item_model.case_id,
        )
        .join(item_model, item_model.batch_id == batch_model.id)
        .where(item_model.case_id == case_id)
    )
    if active_batch_id is not None:
        statement = statement.where(batch_model.id == active_batch_id)
    else:
        statement = statement.where(batch_model.submission_id == external_submission_id)

    rows = (await session.execute(statement)).all()
    targets: list[CancellationTarget] = []
    seen: set[tuple[str, str, str, str, int]] = set()
    for executor, submission_id, external_case_id, platform, item_case_id in rows:
        if not submission_id or not external_case_id or item_case_id is None:
            continue
        target = CancellationTarget(
            executor=str(executor),
            submission_id=str(submission_id),
            external_case_id=str(external_case_id),
            platform=str(platform or ""),
            case_id=int(item_case_id),
        )
        key = (
            target.executor,
            target.submission_id,
            target.external_case_id,
            target.platform,
            target.case_id,
        )
        if key not in seen:
            seen.add(key)
            targets.append(target)
    return targets


def schedule_cancellation(mode: CancellationMode, targets: list[CancellationTarget]) -> None:
    """提交后单链发送取消；不保存任务、不重试、不回写业务状态。"""
    if not targets:
        return
    task = asyncio.create_task(dispatch_cancellation(mode, targets))
    task.add_done_callback(_consume_task_exception)


async def dispatch_cancellation(mode: CancellationMode, targets: list[CancellationTarget]) -> None:
    """发送取消 hook；每个 hook 的结果只写服务端日志，绝不影响本地停止结果。"""
    unique_targets = _unique_targets(targets)
    _stop_internal_aiapi(mode, unique_targets)

    settings = get_settings()
    hybrid_case_ids: dict[str, list[str]] = {}
    external_targets: list[CancellationTarget] = []
    for target in unique_targets:
        if target.executor == "ai_hybrid":
            hybrid_case_ids.setdefault(target.submission_id, []).append(target.external_case_id)
        elif target.executor in {"ai_phone", "ai_web"}:
            external_targets.append(target)

    if not external_targets and not hybrid_case_ids:
        return

    async with httpx.AsyncClient(timeout=5) as client:
        jobs = [
            _post_case_cancel(
                client,
                settings.aiphone_base_url if target.executor == "ai_phone" else settings.aiweb_base_url,
                target,
            )
            for target in external_targets
        ]
        jobs.extend(
            _post_hybrid_cancel(client, settings.aihybrid_base_url, submission_id, case_ids)
            for submission_id, case_ids in hybrid_case_ids.items()
        )
        await asyncio.gather(*jobs)


def _stop_internal_aiapi(mode: CancellationMode, targets: list[CancellationTarget]) -> None:
    by_submission: dict[str, list[int]] = {}
    for target in targets:
        if target.executor == "ai_api":
            by_submission.setdefault(target.submission_id, []).append(target.case_id)

    for submission_id, case_ids in by_submission.items():
        try:
            unique_case_ids = list(dict.fromkeys(case_ids))
            if mode == "standard":
                from app.services import executions

                executions.stop_aiapi_execution(submission_id, unique_case_ids)
            else:
                from app.services import quick_executions

                quick_executions.stop_aiapi_execution(submission_id, unique_case_ids)
        except Exception:  # noqa: BLE001 - 取消 hook 不得改写本次本地停止结果
            logger.warning("AI API cancellation hook failed", exc_info=True)


async def _post_case_cancel(
    client: httpx.AsyncClient,
    base_url: str,
    target: CancellationTarget,
) -> None:
    url = (
        f"{base_url.rstrip('/')}/api/submissions/{quote(target.submission_id, safe='')}"
        f"/cases/{quote(target.external_case_id, safe='')}/cancel"
    )
    params = {"platform": target.platform} if target.platform else None
    try:
        response = await client.post(url, params=params)
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - 外部取消失败只保留服务端诊断
        logger.warning("executor cancellation hook failed: executor=%s", target.executor, exc_info=True)


async def _post_hybrid_cancel(
    client: httpx.AsyncClient,
    base_url: str,
    submission_id: str,
    case_ids: list[str],
) -> None:
    url = f"{base_url.rstrip('/')}/api/submissions/{quote(submission_id, safe='')}/cancel"
    try:
        response = await client.post(url, json={"caseIds": list(dict.fromkeys(case_ids))})
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - 外部取消失败只保留服务端诊断
        logger.warning("AI Hybrid cancellation hook failed", exc_info=True)


def _unique_targets(targets: list[CancellationTarget]) -> list[CancellationTarget]:
    unique: list[CancellationTarget] = []
    seen: set[tuple[str, str, str, str, int]] = set()
    for target in targets:
        key = (
            target.executor,
            target.submission_id,
            target.external_case_id,
            target.platform,
            target.case_id,
        )
        if key not in seen:
            seen.add(key)
            unique.append(target)
    return unique


def _consume_task_exception(task: asyncio.Task[object]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001 - 不让后台 hook 未处理异常污染事件循环
        logger.exception("executor cancellation dispatcher crashed")
