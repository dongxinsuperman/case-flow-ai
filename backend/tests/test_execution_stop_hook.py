from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.case_assets import CaseWorkItem
from app.models.quick import QuickCaseWorkItem
from app.schemas.quick import QuickWorkItemUpdateIn
from app.schemas.workbench import CaseWorkItemUpdateIn
from app.services import executor_cancellation, quick_importing, workbench
from app.services.executor_cancellation import CancellationTarget


class _UpdateSession:
    def __init__(self, model: object, work_item: object) -> None:
        self.model = model
        self.work_item = work_item
        self.deleted_tables: list[str] = []
        self.commits = 0

    async def get(self, model: object, _case_id: int) -> object | None:
        return self.work_item if model is self.model else None

    async def execute(self, statement: object) -> None:
        self.deleted_tables.append(statement.table.name)

    async def commit(self) -> None:
        self.commits += 1


def _running_work_item(case_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        case_id=case_id,
        execution_status="running",
        coverage={"android": "passed"},
        lifecycle_state="待验证",
        attention_reason=None,
        execution_target="app",
        run_enabled=True,
        report_url="http://report.local/current",
        failure_type=None,
        failure_summary=None,
        bug_url=None,
        bug_external_id=None,
        bugs=[],
        active_execution_batch_id=11,
        external_submission_id="sub-11",
        execution_started_at=None,
        execution_finished_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_standard_stop_snapshots_before_reset_and_schedules_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _running_work_item(7)
    session = _UpdateSession(CaseWorkItem, item)
    target = CancellationTarget("ai_phone", "sub-11", "cf-7", "android", 7)
    calls: list[tuple[str, object]] = []

    async def fake_snapshot(*_args: object, **kwargs: object) -> list[CancellationTarget]:
        assert kwargs == {
            "case_id": 7,
            "active_batch_id": 11,
            "external_submission_id": "sub-11",
        }
        assert item.execution_status == "running"
        return [target]

    def fake_schedule(mode: str, targets: list[CancellationTarget]) -> None:
        assert session.commits == 1
        calls.append((mode, targets))

    monkeypatch.setattr(executor_cancellation, "snapshot_standard_targets", fake_snapshot)
    monkeypatch.setattr(executor_cancellation, "schedule_cancellation", fake_schedule)

    result = await workbench.update_case_work_item(
        session,  # type: ignore[arg-type]
        CaseWorkItemUpdateIn(case_id=7, execution_status="not_run", run_enabled=False),
    )

    assert result is not None
    assert result.execution_status == "not_run"
    assert item.external_submission_id is None
    assert item.active_execution_batch_id is None
    assert calls == [("standard", [target])]
    assert session.deleted_tables == ["case_repair_drafts", "case_bug_drafts", "aiphone_execution_items"]


@pytest.mark.asyncio
async def test_quick_stop_snapshots_before_reset_and_schedules_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _running_work_item(17)
    session = _UpdateSession(QuickCaseWorkItem, item)
    target = CancellationTarget("ai_api", "local-quick-api", "qcf-17", "api", 17)
    calls: list[tuple[str, object]] = []

    async def fake_snapshot(*_args: object, **kwargs: object) -> list[CancellationTarget]:
        assert kwargs == {
            "case_id": 17,
            "active_batch_id": 11,
            "external_submission_id": "sub-11",
        }
        assert item.execution_status == "running"
        return [target]

    def fake_schedule(mode: str, targets: list[CancellationTarget]) -> None:
        assert session.commits == 1
        calls.append((mode, targets))

    monkeypatch.setattr(executor_cancellation, "snapshot_quick_targets", fake_snapshot)
    monkeypatch.setattr(executor_cancellation, "schedule_cancellation", fake_schedule)

    result = await quick_importing.update_case_work_item(
        session,  # type: ignore[arg-type]
        QuickWorkItemUpdateIn(case_id=17, execution_status="not_run", run_enabled=False),
    )

    assert result is not None
    assert result.execution_status == "not_run"
    assert item.external_submission_id is None
    assert item.active_execution_batch_id is None
    assert calls == [("quick", [target])]
    assert session.deleted_tables == ["quick_repair_drafts", "quick_bug_drafts", "quick_execution_items"]


@pytest.mark.asyncio
async def test_not_run_from_non_running_case_does_not_send_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _running_work_item(9)
    item.execution_status = "passed"
    session = _UpdateSession(CaseWorkItem, item)

    async def fail_snapshot(*_args: object, **_kwargs: object) -> list[CancellationTarget]:
        raise AssertionError("only running -> not_run is a stop")

    def fail_schedule(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("only running -> not_run is a stop")

    monkeypatch.setattr(executor_cancellation, "snapshot_standard_targets", fail_snapshot)
    monkeypatch.setattr(executor_cancellation, "schedule_cancellation", fail_schedule)

    result = await workbench.update_case_work_item(
        session,  # type: ignore[arg-type]
        CaseWorkItemUpdateIn(case_id=9, execution_status="not_run", run_enabled=False),
    )

    assert result is not None
    assert result.execution_status == "not_run"
