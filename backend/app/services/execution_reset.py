from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_assets import (
    AIPhoneExecutionItem,
    CaseBugDraft,
    CaseRepairDraft,
    CaseWorkItem,
)
from app.models.quick import (
    QuickBugDraft,
    QuickCaseWorkItem,
    QuickExecutionItem,
    QuickRepairDraft,
)

ExecutionStatus = Literal["not_run", "running", "passed", "failed"]


def _lifecycle_for_status(status: str) -> str:
    return {
        "passed": "已固化",
        "failed": "待人工干预",
    }.get(status, "待验证")


async def clear_standard_case_execution_artifacts(
    session: AsyncSession,
    case_ids: list[int] | set[int] | tuple[int, ...],
) -> None:
    ids = [case_id for case_id in dict.fromkeys(int(case_id) for case_id in case_ids) if case_id > 0]
    if not ids:
        return
    await session.execute(delete(CaseRepairDraft).where(CaseRepairDraft.case_id.in_(ids)))
    await session.execute(delete(CaseBugDraft).where(CaseBugDraft.case_id.in_(ids)))
    await session.execute(delete(AIPhoneExecutionItem).where(AIPhoneExecutionItem.case_id.in_(ids)))


async def clear_quick_case_execution_artifacts(
    session: AsyncSession,
    case_ids: list[int] | set[int] | tuple[int, ...],
) -> None:
    ids = [case_id for case_id in dict.fromkeys(int(case_id) for case_id in case_ids) if case_id > 0]
    if not ids:
        return
    await session.execute(delete(QuickRepairDraft).where(QuickRepairDraft.case_id.in_(ids)))
    await session.execute(delete(QuickBugDraft).where(QuickBugDraft.case_id.in_(ids)))
    await session.execute(delete(QuickExecutionItem).where(QuickExecutionItem.case_id.in_(ids)))


def reset_standard_work_item_execution(
    work_item: CaseWorkItem,
    *,
    status: ExecutionStatus = "not_run",
    lifecycle_state: str | None = None,
    attention_reason: str | None = None,
    clear_coverage: bool = True,
) -> None:
    _reset_work_item_execution(
        work_item,
        status=status,
        lifecycle_state=lifecycle_state,
        attention_reason=attention_reason,
        clear_coverage=clear_coverage,
        timestamp=func.now(),
    )


def reset_quick_work_item_execution(
    work_item: QuickCaseWorkItem,
    *,
    status: ExecutionStatus = "not_run",
    lifecycle_state: str | None = None,
    attention_reason: str | None = None,
    clear_coverage: bool = True,
) -> None:
    _reset_work_item_execution(
        work_item,
        status=status,
        lifecycle_state=lifecycle_state,
        attention_reason=attention_reason,
        clear_coverage=clear_coverage,
        timestamp=datetime.now(UTC),
    )


def _reset_work_item_execution(
    work_item: CaseWorkItem | QuickCaseWorkItem,
    *,
    status: ExecutionStatus,
    lifecycle_state: str | None,
    attention_reason: str | None,
    clear_coverage: bool,
    timestamp: object,
) -> None:
    work_item.execution_status = status
    if clear_coverage:
        work_item.coverage = {}
    work_item.lifecycle_state = lifecycle_state or _lifecycle_for_status(status)
    work_item.attention_reason = attention_reason
    work_item.report_url = None
    work_item.failure_type = "execution_failed" if status == "failed" else None
    work_item.failure_summary = None
    work_item.bug_url = None
    work_item.bug_external_id = None
    work_item.bugs = []
    work_item.active_execution_batch_id = None
    work_item.external_submission_id = None
    work_item.execution_started_at = timestamp if status == "running" else None
    work_item.execution_finished_at = None
    work_item.updated_at = timestamp
