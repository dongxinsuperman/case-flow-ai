from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.case_assets import CaseWorkItem
from app.models.execution_call_log import ExecutionStrategyCallLog
from app.models.quick import QuickCaseWorkItem
from app.services import execution_call_log
from app.services import executions
from app.services import quick_executions


class _FakeSession:
    """支持 add/flush(分配主键)/get/commit/rollback 的最小假 session。"""

    def __init__(self, objects: dict | None = None) -> None:
        self.store: dict = dict(objects or {})
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self._next_id = 1000

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1
            self.store[(type(obj), obj.id)] = obj

    async def get(self, model: type, ident: object) -> object | None:
        return self.store.get((model, ident))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_create_call_log_defaults_to_compiling_and_infers_scope() -> None:
    session = _FakeSession()

    batch_log = await execution_call_log.create_call_log(
        session,
        mode="standard",
        executor="ai_phone",
        entry="executions/aiphone/submit",
        case_ids=[1, 2],
        requirement_item_id=9,
    )
    single_log = await execution_call_log.create_call_log(
        session,
        mode="quick",
        executor="ai_web",
        entry="quick/executions/aiweb/submit",
        case_ids=[7],
        quick_session_id="sess-1",
    )

    assert batch_log.status == "compiling"
    assert batch_log.scope == "batch"
    assert batch_log.call_id  # uuid hex
    assert batch_log.id is not None
    assert batch_log.requirement_item_id == 9
    assert single_log.scope == "single"
    assert single_log.quick_session_id == "sess-1"
    assert batch_log.call_id != single_log.call_id


@pytest.mark.asyncio
async def test_mark_submitted_and_failed_update_log() -> None:
    session = _FakeSession()
    log = await execution_call_log.create_call_log(
        session,
        mode="standard",
        executor="ai_phone",
        entry="executions/aiphone/submit",
        case_ids=[1],
    )

    await execution_call_log.mark_submitted(
        session, log.id, execution_batch_id=42, submission_id="sub-42"
    )
    assert log.status == "submitted"
    assert log.execution_batch_id == 42
    assert log.submission_id == "sub-42"

    await execution_call_log.mark_failed(
        session, log.id, status="submit_failed", reason="executor down"
    )
    assert log.status == "submit_failed"
    assert log.failure_reason == "executor down"


def _running_work_item() -> SimpleNamespace:
    return SimpleNamespace(
        execution_status="running",
        lifecycle_state="待验证",
        attention_reason=None,
        run_enabled=True,
        report_url="http://old/report",
        failure_type=None,
        failure_summary=None,
        active_execution_batch_id=None,
        execution_finished_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_standard_mark_submit_failed_flips_running_cases() -> None:
    running = _running_work_item()
    already_passed = SimpleNamespace(execution_status="passed", failure_type=None, failure_summary=None)
    log = ExecutionStrategyCallLog(id=1, call_id="c1", status="compiling")
    session = _FakeSession(
        {
            (CaseWorkItem, 1): running,
            (CaseWorkItem, 2): already_passed,
            (ExecutionStrategyCallLog, 1): log,
        }
    )

    await executions._mark_submit_failed(
        session,
        call_log_id=1,
        case_ids=[1, 2],
        status="submit_failed",
        reason="executor down",
        summary="提交执行器失败：executor down",
    )

    assert running.execution_status == "failed"
    assert running.lifecycle_state == "待人工干预"
    assert running.failure_type == "environment_failure"
    assert running.failure_summary == "提交执行器失败：executor down"
    assert running.active_execution_batch_id is None
    # 非 running 的 case 不被本次提交失败覆盖。
    assert already_passed.execution_status == "passed"
    assert log.status == "submit_failed"
    assert log.failure_reason == "executor down"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_quick_mark_submit_failed_flips_running_cases() -> None:
    running = _running_work_item()
    log = ExecutionStrategyCallLog(id=5, call_id="c5", status="compiling")
    session = _FakeSession(
        {
            (QuickCaseWorkItem, 3): running,
            (ExecutionStrategyCallLog, 5): log,
        }
    )

    await quick_executions._mark_submit_failed(
        session,
        call_log_id=5,
        case_ids=[3],
        status="compile_failed",
        reason="function map 编译报错",
        summary="策略编译失败：function map 编译报错",
    )

    assert running.execution_status == "failed"
    assert running.failure_type == "environment_failure"
    assert running.failure_summary == "策略编译失败：function map 编译报错"
    assert log.status == "compile_failed"
    assert log.failure_reason == "function map 编译报错"
    assert session.commits == 1
