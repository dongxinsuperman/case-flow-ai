from __future__ import annotations

from app.services.ai_hybrid.reporter import build_hybrid_report
from app.services.ai_hybrid.schemas import HybridRunResult


def test_hybrid_report_has_single_narrative_sections() -> None:
    html = build_hybrid_report(
        HybridRunResult(
            status="failed",
            status_reason="execution_failed",
            final_summary="App 登录后未进入首页",
            input_payload={
                "title": "验证登录",
                "preconditions": "已登录测试账号",
                "steps_text": "打开 App",
                "expected_result": "进入首页",
                "source_ref": "cf-1",
                "function_map_context_loaded": True,
            },
            started_at="2026-07-02T01:00:00+00:00",
            finished_at="2026-07-02T01:00:02+00:00",
            elapsed_ms=2300,
            child_results_payload=[
                {"tool": "ai_phone", "status": "failed", "reason": "execution_failed", "raw": {}},
            ],
            reasoning_trace=[
                {
                    "phase": "step",
                    "index": 1,
                    "thought": "先在 App 上验证登录后是否进入首页",
                    "tool": "ai_phone",
                    "tool_input": {"goal": "验证登录", "steps": "打开 App 登录并检查首页"},
                    "status": "failed",
                    "reason": "execution_failed",
                    "report_url": "http://local/phone.html",
                },
                {
                    "phase": "finish",
                    "index": 2,
                    "thought": "App 未进入首页，判失败",
                    "verdict": "failed",
                    "attribution": "登录后停留在登录页，未跳转首页",
                    "evidence": ["ai_phone 报告：首页元素未出现"],
                    "suggestions": ["检查登录跳转逻辑"],
                },
            ],
        )
    )

    # 主线板块
    assert "最终结论" in html
    assert "ReAct 时间线" in html
    assert "任务输入" in html
    # header 概览
    assert "2.3 s" in html
    assert "失败" in html
    # 时间线渲染了 thought / tool_input / observation
    assert "先在 App 上验证登录后是否进入首页" in html
    assert "验证登录" in html
    assert "打开 App 登录并检查首页" in html
    assert "打开子报告" in html
    # 结论区渲染 finish 的归因/证据/建议
    assert "登录后停留在登录页，未跳转首页" in html
    assert "首页元素未出现" in html
    assert "检查登录跳转逻辑" in html
    # 旧的重叠板块已移除
    assert "实际执行链" not in html
    assert "证据读取" not in html
    # 调试原文可折叠
    assert "调试原始数据" in html


def test_hybrid_report_renders_backstop() -> None:
    html = build_hybrid_report(
        HybridRunResult(
            status="needs_human",
            status_reason="backstop_limit_reached",
            final_summary="到达防失控上限",
            reasoning_trace=[{"phase": "backstop", "message": "到达最大步数"}],
            terminated_by="backstop",
        )
    )
    assert "防失控" in html
    assert "到达最大步数" in html
