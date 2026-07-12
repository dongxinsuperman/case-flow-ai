from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.execution_reset import (
    clear_quick_case_execution_artifacts,
    clear_standard_case_execution_artifacts,
    reset_standard_work_item_execution,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.deleted_tables: list[str] = []

    async def execute(self, statement: object) -> None:
        self.deleted_tables.append(statement.table.name)


@pytest.mark.asyncio
async def test_standard_clear_removes_execution_items_too() -> None:
    session = _RecordingSession()

    await clear_standard_case_execution_artifacts(session, [7])

    assert session.deleted_tables == [
        "case_repair_drafts",
        "case_bug_drafts",
        "aiphone_execution_items",
    ]


@pytest.mark.asyncio
async def test_quick_clear_removes_execution_items_too() -> None:
    session = _RecordingSession()

    await clear_quick_case_execution_artifacts(session, [7])

    assert session.deleted_tables == [
        "quick_repair_drafts",
        "quick_bug_drafts",
        "quick_execution_items",
    ]


def test_reset_preserves_execution_target() -> None:
    work_item = SimpleNamespace(
        execution_status="failed",
        coverage={"android": "failed"},
        lifecycle_state="待人工干预",
        attention_reason=None,
        execution_target="app",
        report_url="http://report.local",
        failure_type="execution_failed",
        failure_summary="failed",
        bug_url="http://bug.local",
        bug_external_id="BUG-1",
        bugs=[{"url": "http://bug.local", "id": "BUG-1"}],
        active_execution_batch_id=1,
        external_submission_id="sub-1",
        execution_started_at=None,
        execution_finished_at=None,
        updated_at=None,
    )

    reset_standard_work_item_execution(work_item, status="not_run")

    assert work_item.execution_status == "not_run"
    assert work_item.execution_target == "app"
    assert work_item.coverage == {}
    assert work_item.report_url is None
