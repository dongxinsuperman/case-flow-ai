"""AI Hybrid ReAct 循环可观测 harness。

用途：喂一段 case（runContent），把 run_hybrid 的每一轮
`thought → action → tool_input → observation → verdict` 打成可读时间线，
让人在改 prompt / 工具 / 循环前后能"看见"agent 到底怎么走。

用法（在 backend/ 目录下）：
    # 真跑（用配置好的 LLM + 真实子执行器）
    .venv/bin/python scripts/hybrid_harness.py --content "测试标题：... 操作步骤：... 预期结果：..."

    # 只看循环形态：真模型决策 + 假工具（不碰真机/接口）
    .venv/bin/python scripts/hybrid_harness.py --file case.txt --mock

    # 从文件读 case、附带 functionMap、并 dump 原始结果 JSON
    .venv/bin/python scripts/hybrid_harness.py --file case.txt --function-map fm.txt --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ai_hybrid import runner  # noqa: E402
from app.services.ai_hybrid.schemas import (  # noqa: E402
    HybridInput,
    HybridToolInput,
    HybridToolResult,
)
from app.services.ai_hybrid.service import _hybrid_input_from_item  # noqa: E402


class _MockItem:
    """喂给 _hybrid_input_from_item 的最小 item。"""

    def __init__(self, content: str) -> None:
        self.run_content = content
        self.case_name = ""
        self.case_id = "harness-case"


class _EchoTool:
    """假工具：不真的执行，只回一个成功 observation，供离线观察循环形态。"""

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, inp: HybridToolInput, settings: Any) -> HybridToolResult:
        if self.name == "report_reader":
            mode = str(inp.raw.get("mode") or "outline").lower()
            return HybridToolResult(
                tool=self.name,
                status="success",
                reason="read_ok",
                report_url=str(inp.raw.get("report_url") or ""),
                raw={
                    "observation_mode": mode,
                    "stats": {"block_count": 6, "text_chars": 320, "image_count": 2},
                    "toc": [
                        {"i": 0, "kind": "text", "preview": "[mock] 用例标题：目标达成"},
                        {"i": 1, "kind": "image", "imgNo": 0},
                        {"i": 2, "kind": "text", "preview": "[mock] 步骤 1 通过"},
                    ],
                    "text": "[mock] 报告正文：目标已达成" if mode == "read" else None,
                },
            )
        return HybridToolResult(
            tool=self.name,
            status="success",
            reason="mock_echo",
            report_url=f"http://mock/{self.name}/report.html",
            raw={"note": "mock 工具，未真实执行", "received_input": inp.input, "tool_input": inp.raw},
        )


def _mock_registry() -> dict[str, Any]:
    return {name: _EchoTool(name) for name in ("ai_api", "ai_web", "ai_phone", "cli", "report_reader")}


def _c(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _short(value: Any, limit: int = 500) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit})"


def _print_timeline(result: Any) -> None:
    print()
    print(_c("═" * 70, "90"))
    print(_c(" AI Hybrid ReAct 时间线", "1;36"))
    print(_c("═" * 70, "90"))
    for row in result.reasoning_trace:
        phase = str(row.get("phase") or "")
        if phase == "step":
            print()
            print(_c(f"● 第 {row.get('index')} 轮  call_tool → {row.get('tool')}", "1;33"))
            if row.get("thought"):
                print(f"  {_c('thought', '90')}: {_short(row.get('thought'))}")
            if row.get("tool_input"):
                print(f"  {_c('tool_input', '90')}: {_short(row.get('tool_input'))}")
            status = str(row.get("status") or "")
            color = "32" if status == "success" else "31" if status == "failed" else "33"
            print(f"  {_c('observation', '90')}: {_c(status, color)}  {row.get('reason') or ''}")
            if row.get("report_url"):
                print(f"  {_c('report', '90')}: {row.get('report_url')}")
        elif phase == "finish":
            verdict = str(row.get("verdict") or "")
            color = "1;32" if verdict == "success" else "1;31" if verdict == "failed" else "1;33"
            print()
            print(_c(f"■ finish → {verdict}", color))
            if row.get("thought"):
                print(f"  {_c('thought', '90')}: {_short(row.get('thought'))}")
            if row.get("attribution"):
                print(f"  {_c('归因', '90')}: {_short(row.get('attribution'))}")
            if row.get("evidence"):
                print(f"  {_c('证据', '90')}: {_short(row.get('evidence'))}")
            if row.get("suggestions"):
                print(f"  {_c('建议', '90')}: {_short(row.get('suggestions'))}")
        elif phase in {"plan", "tool"}:
            print(_c(f"  · [规则兜底] {phase}: {_short(row)}", "90"))
        elif phase == "backstop":
            print()
            print(_c(f"✕ backstop: {row.get('message')}", "1;31"))
        else:
            print(_c(f"  · {phase}: {_short(row)}", "90"))

    print()
    print(_c("─" * 70, "90"))
    color = "1;32" if result.status == "success" else "1;31" if result.status == "failed" else "1;33"
    print(f"{_c('最终结论', '1')}: {_c(result.status, color)}  ({result.status_reason})")
    print(f"{_c('总结', '1')}: {_short(result.final_summary, 800)}")
    if result.terminated_by:
        print(f"{_c('终止方式', '1')}: {result.terminated_by}")
    print(f"{_c('耗时', '1')}: {result.elapsed_ms} ms | 工具调用 {len(result.child_results_payload)} 次")
    print(_c("─" * 70, "90"))


async def _amain(args: argparse.Namespace) -> None:
    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    elif args.content:
        content = args.content
    else:
        content = sys.stdin.read()
    function_map = Path(args.function_map).read_text(encoding="utf-8") if args.function_map else ""

    hybrid_input: HybridInput = _hybrid_input_from_item(_MockItem(content), function_map)

    if args.mock:
        runner.tool_registry = _mock_registry  # type: ignore[assignment]
        print(_c("[mock 模式] 工具不会真实执行，只观察循环与模型决策。", "35"))

    result = await runner.run_hybrid(hybrid_input)
    _print_timeline(result)
    if args.json:
        print()
        print(_c("── 原始结果 JSON ──", "90"))
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Hybrid ReAct 循环可观测 harness")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--content", help="直接传入 case runContent 文本")
    src.add_argument("--file", help="从文件读取 case runContent")
    parser.add_argument("--function-map", help="functionMap 文本文件（透传给子工具）")
    parser.add_argument("--mock", action="store_true", help="假工具模式：真模型决策 + 不真实执行子工具")
    parser.add_argument("--json", action="store_true", help="额外打印原始结果 JSON")
    asyncio.run(_amain(parser.parse_args()))


if __name__ == "__main__":
    main()
