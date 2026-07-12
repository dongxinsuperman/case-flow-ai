from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from app.core.settings import Settings, get_settings
from app.services.ai_hybrid import report_observer
from app.services.ai_hybrid.reasoner import observe, react_step
from app.services.ai_hybrid.schemas import (
    HybridDecision,
    HybridInput,
    HybridRunResult,
    HybridToolInput,
    HybridToolResult,
)
from app.services.ai_hybrid.tools import tool_registry


async def run_hybrid(inp: HybridInput, settings: Settings | None = None) -> HybridRunResult:
    """标准 ReAct 编排内核（不绑 case）。

    模型是唯一决策者：每轮看 goal + case + 工具规格 + history，返回 call_tool 或 finish。
    代码只负责跑循环、执行工具、把 observation 喂回、以及 backstop 防跑飞。
    **没有规则兜底**：LLM 拿不到决策就直接判失败（llm_unavailable），绝不偷偷降级，
    以便任何异常都能被清楚地看见和排查。
    """
    settings = settings or get_settings()
    started_at = time.monotonic()
    started_wall = datetime.now(UTC)
    max_steps = int(getattr(settings, "hybrid_max_steps", 50) or 0)
    max_wall_seconds = int(getattr(settings, "hybrid_max_wall_seconds", 1800) or 0)
    if max_steps <= 0 or max_wall_seconds <= 0:
        return _with_run_meta(
            _backstop_result([], [], "Hybrid 防失控限制已触发，未启动子工具。"),
            inp,
            started_wall,
            started_at,
        )

    registry = tool_registry()
    history: list[dict[str, Any]] = []
    child_results: list[HybridToolResult] = []
    trace: list[dict[str, Any]] = []

    # 报告索引/截图缓存只在这一次 run 内有效，run 结束即释放：不落库、也不做进程级悬挂缓存。
    with report_observer.run_cache_scope():
        for index in range(max_steps):
            if time.monotonic() - started_at >= max_wall_seconds:
                return _with_run_meta(
                    _backstop_result(child_results, trace, "Hybrid 到达防失控时长上限，已停止启动新的子工具。"),
                    inp,
                    started_wall,
                    started_at,
                )

            decision = await react_step(inp=inp, history=history, settings=settings)
            if decision is None:
                # LLM 不可用：直接判失败报错，不做任何规则兜底。
                return _with_run_meta(
                    _error_result(child_results, trace, "llm_unavailable", "Hybrid 无法获得模型决策（LLM 未配置或不可用），已中止。"),
                    inp,
                    started_wall,
                    started_at,
                )

            if decision.action == "finish":
                trace.append(_finish_trace(index + 1, decision))
                return _with_run_meta(
                    _finish_result(child_results, trace, decision),
                    inp,
                    started_wall,
                    started_at,
                )

            # action == call_tool
            tool_name = str(decision.tool or "")
            if not tool_name:
                return _with_run_meta(
                    _needs_human_result(child_results, trace, "missing_tool", "reasoner 决定调用工具，但没有给出工具名。"),
                    inp,
                    started_wall,
                    started_at,
                )
            if tool_name not in registry:
                return _with_run_meta(
                    _needs_human_result(child_results, trace, "tool_not_registered", f"reasoner 选择了未注册工具：{tool_name}"),
                    inp,
                    started_wall,
                    started_at,
                )

            result = await _run_tool(registry[tool_name], inp, settings, decision, tool_name)
            # observe 先跑：把可能的截图 base64 抽进 in-memory history；随后从要持久化的
            # result 里剥掉 base64，避免它进 child_results / trace / 最终 HTML 报告。
            observation = observe(result)
            _strip_persisted_image(result)
            child_results.append(result)
            history.append(
                {
                    "step": index + 1,
                    "thought": decision.thought,
                    "action": "call_tool",
                    "tool": tool_name,
                    "tool_input": dict(decision.tool_input or {}),
                    "observation": observation,
                }
            )
            trace.append(_step_trace(index + 1, decision, tool_name, result))

        return _with_run_meta(
            _backstop_result(child_results, trace, "Hybrid 到达最大工具调用步数，已停止启动新的子工具。"),
            inp,
            started_wall,
            started_at,
        )


async def _run_tool(
    tool: Any,
    inp: HybridInput,
    settings: Settings,
    decision: HybridDecision,
    tool_name: str,
) -> HybridToolResult:
    tool_input = dict(decision.tool_input or {})
    text = _tool_text(tool_input, fallback=inp.steps_text or inp.title or inp.goal)
    try:
        return await tool.run(
            HybridToolInput(
                tool=tool_name,
                input=text,
                function_map_context=inp.function_map_context,
                raw={**tool_input, "thought": decision.thought, "sourceRef": inp.source_ref},
            ),
            settings,
        )
    except Exception as exc:
        return HybridToolResult(
            tool=tool_name,
            status="failed",
            reason=f"tool_error: {exc}",
            raw={"error": str(exc), "tool_input": tool_input},
        )


def _finish_result(
    child_results: list[HybridToolResult],
    trace: list[dict[str, Any]],
    decision: HybridDecision,
) -> HybridRunResult:
    if decision.verdict is None:
        return _needs_human_result(child_results, trace, "missing_verdict", "reasoner 选择 finish，但没有给出 verdict。")
    status = decision.verdict
    reason = decision.status_reason or decision.final.attribution or decision.thought or status
    summary = decision.final.attribution or decision.thought or f"Hybrid 收敛为 {status}"
    return HybridRunResult(
        status=status,
        status_reason=reason,
        final_summary=summary,
        child_results_payload=[item.model_dump(mode="json") for item in child_results],
        reasoning_trace=trace,
    )


def _needs_human_result(
    child_results: list[HybridToolResult],
    trace: list[dict[str, Any]],
    reason: str,
    summary: str,
) -> HybridRunResult:
    return HybridRunResult(
        status="needs_human",
        status_reason=reason,
        final_summary=summary,
        child_results_payload=[item.model_dump(mode="json") for item in child_results],
        reasoning_trace=trace,
    )


def _error_result(
    child_results: list[HybridToolResult],
    trace: list[dict[str, Any]],
    reason: str,
    summary: str,
) -> HybridRunResult:
    return HybridRunResult(
        status="failed",
        status_reason=reason,
        final_summary=summary,
        child_results_payload=[item.model_dump(mode="json") for item in child_results],
        reasoning_trace=trace,
    )


def _backstop_result(
    child_results: list[HybridToolResult],
    trace: list[dict[str, Any]],
    message: str,
) -> HybridRunResult:
    new_trace = list(trace)
    new_trace.append({"phase": "backstop", "message": message, "completed_tool_count": len(child_results)})
    return HybridRunResult(
        status="needs_human",
        status_reason="backstop_limit_reached",
        final_summary=message,
        child_results_payload=[item.model_dump(mode="json") for item in child_results],
        reasoning_trace=new_trace,
        terminated_by="backstop",
    )


# ---------------------------------------------------------------------------
# trace 构造（供 reporter 渲染 ReAct 时间线）
# ---------------------------------------------------------------------------
def _step_trace(index: int, decision: HybridDecision, tool_name: str, result: HybridToolResult) -> dict[str, Any]:
    return {
        "phase": "step",
        "index": index,
        "thought": decision.thought,
        "tool": tool_name,
        "tool_input": dict(decision.tool_input or {}),
        "status": result.status,
        "reason": result.reason,
        "report_url": result.report_url,
        "observation": observe(result),
    }


def _finish_trace(index: int, decision: HybridDecision) -> dict[str, Any]:
    return {
        "phase": "finish",
        "index": index,
        "thought": decision.thought,
        "verdict": decision.verdict,
        "attribution": decision.final.attribution,
        "evidence": decision.final.evidence,
        "suggestions": decision.final.suggestions,
    }


def _strip_persisted_image(result: HybridToolResult) -> None:
    """从要持久化的工具结果里剥掉截图 base64（只保留元信息），避免炸报告体积。"""
    if isinstance(result.raw, dict) and result.raw.get("image_data_uri"):
        result.raw = {
            key: value for key, value in result.raw.items() if key != "image_data_uri"
        }
        result.raw["image_attached"] = True


def _tool_text(tool_input: dict[str, Any], *, fallback: str) -> str:
    if not tool_input:
        return fallback
    if isinstance(tool_input.get("steps"), str) and tool_input.get("goal"):
        return f"{tool_input['goal']}\n\n{tool_input['steps']}"
    if isinstance(tool_input.get("steps"), str):
        return str(tool_input["steps"])
    if isinstance(tool_input.get("goal"), str):
        return str(tool_input["goal"])
    return fallback


def _with_run_meta(
    result: HybridRunResult,
    inp: HybridInput,
    started_wall: datetime,
    started_monotonic: float,
) -> HybridRunResult:
    finished_wall = datetime.now(UTC)
    if not result.input_payload:
        result.input_payload = {
            "goal": inp.goal,
            "title": inp.title,
            "preconditions": inp.preconditions,
            "steps_text": inp.steps_text,
            "expected_result": inp.expected_result,
            "source_ref": inp.source_ref,
            "function_map_context_loaded": bool(inp.function_map_context),
        }
    result.started_at = result.started_at or started_wall.isoformat()
    result.finished_at = result.finished_at or finished_wall.isoformat()
    result.elapsed_ms = (
        result.elapsed_ms if result.elapsed_ms is not None else int((time.monotonic() - started_monotonic) * 1000)
    )
    return result
