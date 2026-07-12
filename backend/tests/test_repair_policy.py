import asyncio
from types import SimpleNamespace

import pytest

from app.core import database
from app.services import bug_submit, case_repair
from app.services.case_repair import (
    _failure_diagnosis_from_snapshots,
    _primary_failure_type,
    _versioned_gate,
)


def test_model_analysis_wins_over_report_keyword_hint() -> None:
    # 新策略：优先采信模型综合判断（日志+截图+functionMap），报告关键词只作兜底。
    assert _primary_failure_type("business_failure", "execution_failed") == "execution_failed"
    assert _primary_failure_type("execution_failed", "assertion_failed") == "assertion_failed"
    # 模型没给出有效分类时，回退到报告关键词。
    assert _primary_failure_type("assertion_failed", "") == "assertion_failed"


def test_gate_does_not_promote_execution_failure_to_business_by_reason_words() -> None:
    gate = _versioned_gate(
        {
            "allowed": False,
            "failure_type": "execution_failed",
            "reason": "模型认为可能属于产品业务展示异常，非用例步骤问题。",
        }
    )

    assert gate["failure_type"] == "execution_failed"


def test_repair_preview_persists_only_evidence_failure_types() -> None:
    failure_type, summary = _failure_diagnosis_from_snapshots(
        {"failure_type": "assertion_failed"},
        {"summary": "期望 A，实际 B。"},
        "模型判断可修复",
    )

    assert failure_type == "assertion_failed"
    assert summary == "期望 A，实际 B。"


def test_repair_preview_does_not_persist_process_failures_as_case_failure_type() -> None:
    failure_type, summary = _failure_diagnosis_from_snapshots(
        {"failure_type": "model_unavailable"},
        {"summary": "报告可读，但模型 Key 缺失。"},
        "不能生成修复候选",
    )

    assert failure_type is None
    assert summary is None


@pytest.mark.asyncio
async def test_auto_diagnose_releases_repair_inflight_before_bug_precompute(monkeypatch: pytest.MonkeyPatch) -> None:
    case_repair._auto_inflight.clear()
    repair_done = asyncio.Event()
    precompute_started = asyncio.Event()
    repair_inflight_released = asyncio.Event()
    finish_precompute = asyncio.Event()

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _model: object, _case_id: int) -> SimpleNamespace:
            return SimpleNamespace(execution_status="failed", report_url="http://report.local/result.html")

    async def fake_run_preview(_session: object, _case_ids: list[int]) -> object:
        repair_done.set()
        return SimpleNamespace(items=[])

    async def fake_precompute_bug_draft(_session: object, case_id: int) -> None:
        precompute_started.set()
        if case_repair._auto_inflight.get(case_id) is None:
            repair_inflight_released.set()
        await finish_precompute.wait()

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(case_repair, "get_settings", lambda: SimpleNamespace(auto_diagnose_delay_seconds=0))
    monkeypatch.setattr(case_repair, "_run_preview", fake_run_preview)
    monkeypatch.setattr(bug_submit, "precompute_bug_draft", fake_precompute_bug_draft)

    task = asyncio.create_task(case_repair.auto_diagnose_case(123))
    await asyncio.wait_for(repair_done.wait(), timeout=1)
    await asyncio.wait_for(precompute_started.wait(), timeout=1)
    await asyncio.wait_for(repair_inflight_released.wait(), timeout=1)
    assert case_repair._auto_inflight.get(123) is None

    finish_precompute.set()
    await task
    case_repair._auto_inflight.clear()
