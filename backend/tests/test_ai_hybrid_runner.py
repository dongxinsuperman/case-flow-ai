from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.ai_hybrid import runner
from app.services.ai_hybrid.schemas import HybridDecision, HybridInput, HybridToolResult

_SETTINGS = SimpleNamespace(hybrid_max_steps=50, hybrid_max_wall_seconds=1800)


@pytest.mark.asyncio
async def test_react_loop_fails_when_llm_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(runner, "react_step", unavailable)

    result = await runner.run_hybrid(HybridInput(title="任意 case"), _SETTINGS)

    assert result.status == "failed"
    assert result.status_reason == "llm_unavailable"


@pytest.mark.asyncio
async def test_hybrid_runner_stops_before_tool_when_backstop_disabled() -> None:
    result = await runner.run_hybrid(
        HybridInput(title="调用登录 api 后返回 token"),
        SimpleNamespace(hybrid_max_steps=0, hybrid_max_wall_seconds=1800),
    )

    assert result.status == "needs_human"
    assert result.status_reason == "backstop_limit_reached"
    assert result.terminated_by == "backstop"


@pytest.mark.asyncio
async def test_react_loop_finish_is_independent_from_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decisions = [
        HybridDecision(
            action="call_tool",
            tool="ai_api",
            thought="先用 API 造数",
            tool_input={"goal": "造订单", "steps": "调用订单接口"},
        ),
        HybridDecision(
            action="finish",
            verdict="success",
            thought="失败子结果只是重复造数冲突，业务目标已有证据满足",
            final={
                "attribution": "混合目标已完成",
                "evidence": ["API 返回订单已存在"],
                "suggestions": [],
            },
        ),
    ]

    async def fake_react_step(**_kwargs: object) -> HybridDecision:
        return decisions.pop(0)

    class FakeTool:
        async def run(self, inp: object, settings: object) -> HybridToolResult:
            return HybridToolResult(tool="ai_api", status="failed", reason="duplicate_order")

    monkeypatch.setattr(runner, "react_step", fake_react_step)
    monkeypatch.setattr(runner, "tool_registry", lambda: {"ai_api": FakeTool()})

    result = await runner.run_hybrid(HybridInput(title="先接口造订单，再检查结果"), _SETTINGS)

    assert result.status == "success"
    assert result.status_reason == "混合目标已完成"
    assert result.child_results_payload[0]["status"] == "failed"
    phases = [row["phase"] for row in result.reasoning_trace]
    assert phases == ["step", "finish"]


@pytest.mark.asyncio
async def test_react_loop_requires_verdict_for_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_react_step(**_kwargs: object) -> HybridDecision:
        return HybridDecision(action="finish", thought="忘了输出 verdict")

    monkeypatch.setattr(runner, "react_step", fake_react_step)

    result = await runner.run_hybrid(HybridInput(title="检查订单"), _SETTINGS)

    assert result.status == "needs_human"
    assert result.status_reason == "missing_verdict"


@pytest.mark.asyncio
async def test_react_loop_rejects_unregistered_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_react_step(**_kwargs: object) -> HybridDecision:
        return HybridDecision(action="call_tool", tool="ghost_tool", thought="乱选工具")

    monkeypatch.setattr(runner, "react_step", fake_react_step)
    monkeypatch.setattr(runner, "tool_registry", lambda: {})

    result = await runner.run_hybrid(HybridInput(title="x"), _SETTINGS)

    assert result.status == "needs_human"
    assert result.status_reason == "tool_not_registered"


@pytest.mark.asyncio
async def test_react_loop_calls_report_reader_without_auto_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型两次用同一 mode 读同一报告时，runner 不再擅自升级 mode（单脑：由模型决定）。"""
    decisions = [
        HybridDecision(
            action="call_tool",
            tool="report_reader",
            thought="读报告",
            tool_input={"report_url": "http://local/report.html", "executor": "ai_web", "mode": "facts"},
        ),
        HybridDecision(
            action="call_tool",
            tool="report_reader",
            thought="再读一次同样的",
            tool_input={"report_url": "http://local/report.html", "executor": "ai_web", "mode": "facts"},
        ),
        HybridDecision(
            action="finish",
            verdict="needs_human",
            thought="证据仍不足",
            final={"attribution": "证据不足", "evidence": [], "suggestions": []},
        ),
    ]

    async def fake_react_step(**_kwargs: object) -> HybridDecision:
        return decisions.pop(0)

    class FakeReportReader:
        async def run(self, inp: object, settings: object) -> HybridToolResult:
            mode = inp.raw.get("mode") or "facts"
            return HybridToolResult(
                tool="report_reader",
                status="success",
                reason="read_ok",
                report_url=inp.raw["report_url"],
                raw={"observation_mode": mode, "text": "报告正文"},
            )

    monkeypatch.setattr(runner, "react_step", fake_react_step)
    monkeypatch.setattr(runner, "tool_registry", lambda: {"report_reader": FakeReportReader()})

    result = await runner.run_hybrid(HybridInput(title="检查报告"), _SETTINGS)

    modes = [item["raw"]["observation_mode"] for item in result.child_results_payload]
    assert modes == ["facts", "facts"]
    assert result.status == "needs_human"
