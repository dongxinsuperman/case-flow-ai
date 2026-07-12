from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

HybridStatus = Literal["success", "failed", "needs_human"]
# 标准 ReAct：动作只有两个。重试/换工具/换输入都是再来一次 call_tool；
# "需人工" 是 finish 且 verdict=needs_human。
HybridDecisionAction = Literal["call_tool", "finish"]


class HybridInput(BaseModel):
    goal: str = ""
    title: str = ""
    preconditions: str = ""
    steps_text: str = ""
    expected_result: str = ""
    function_map_context: str = ""
    source_ref: str = ""


class HybridToolInput(BaseModel):
    tool: str
    input: str
    function_map_context: str = ""
    attempt: int = 1
    raw: dict[str, Any] = Field(default_factory=dict)


class HybridToolResult(BaseModel):
    tool: str
    status: HybridStatus
    reason: str | None = None
    report_url: str | None = None
    summary_report_url: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class HybridRunResult(BaseModel):
    status: HybridStatus
    status_reason: str
    final_summary: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_ms: int | None = None
    child_results_payload: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_trace: list[dict[str, Any]] = Field(default_factory=list)
    terminated_by: str | None = None


class HybridDecisionFinal(BaseModel):
    attribution: str = ""
    evidence: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class HybridDecision(BaseModel):
    thought: str = ""
    action: HybridDecisionAction
    tool: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    verdict: HybridStatus | None = None
    final: HybridDecisionFinal = Field(default_factory=HybridDecisionFinal)
    status_reason: str | None = None
