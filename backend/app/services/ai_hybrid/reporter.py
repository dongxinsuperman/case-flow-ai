from __future__ import annotations

import html
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import Settings
from app.services.ai_hybrid.evidence import build_evidence, collect_evidence
from app.services.ai_hybrid.schemas import HybridRunResult


async def write_hybrid_report(
    settings: Settings,
    submission_id: str,
    source_ref: str,
    result: HybridRunResult,
) -> str:
    report_dir = Path(settings.repair_image_dir) / "aihybrid_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename(submission_id)}-{_safe_filename(source_ref)}-{uuid.uuid4().hex}.html"
    # 失败时会去子报告兜底补图（异步），所以在写盘前先把证据收敛好。
    evidence = await build_evidence(result, settings)
    (report_dir / filename).write_text(build_hybrid_report(result, evidence), encoding="utf-8")
    path = f"/media/aihybrid_reports/{filename}"
    base = str(settings.public_base_url or "").rstrip("/")
    return f"{base}{path}" if base else path


def build_hybrid_report(result: HybridRunResult, evidence: dict[str, Any] | None = None) -> str:
    """单叙事报告：以 ReAct 时间线为主线，配最终结论 + 证据截图 + 任务输入 + 折叠调试。

    evidence 可预先用 build_evidence 收敛（含失败兜底补图）；不传则只含 agent 主动看过的图。
    """
    status_class = _status_class(result.status)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    tool_calls = sum(1 for row in result.reasoning_trace if str(row.get("phase")) in {"step", "tool"})
    if evidence is None:
        evidence = collect_evidence(result)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AI Hybrid 执行报告</title>
<style>
{_CSS}
</style>
</head>
<body>
<main class="single-container">
  <header class="hd">
    <div class="hd-title">
      <h1>AI Hybrid 执行报告</h1>
      <span class="sub">· ReAct Orchestrator</span>
      <span class="status-badge {status_class}">{_e(_status_label(result.status))}</span>
    </div>
    <div class="hd-meta">
      <span class="chip"><b>结论原因</b>{_e(_reason_label(result.status_reason))}</span>
      <span class="chip"><b>工具调用</b>{tool_calls} 次</span>
      <span class="chip"><b>终止方式</b>{_e(result.terminated_by or "normal")}</span>
      <span class="chip"><b>耗时</b>{_e(_fmt_elapsed(result.elapsed_ms))}</span>
      <span class="chip"><b>生成</b>{_e(generated_at)}</span>
    </div>
  </header>
  {_conclusion_section(result)}
  {_evidence_section(evidence)}
  {_timeline_section(result.reasoning_trace)}
  {_input_section(result)}
  {_debug_section(result)}
  <div class="foot">AI Hybrid 报告 · 生成于 {_e(generated_at)}</div>
  {_embedded_evidence(evidence)}
</main>
</body>
</html>"""


def _evidence_section(evidence: dict[str, Any]) -> str:
    """错误证据截图区：各失败端子报告的末图（=错误图），逐图带端标签 + 上下文。

    没有失败端截图（如纯数据前置失败）时不渲染本区。截图用真实 <img>，既给人看、
    也让诊断/提 bug 的 HTML 报告读取器（report_readers/html）按顺序抓成 image_urls + [截图#N]。
    """
    images = evidence.get("images") or []
    if not images:
        return ""
    cards: list[str] = []
    for img in images:
        label = _e(img.get("platform") or "该端")
        img_url = _e(img.get("image_url"))
        context = str(img.get("context") or "").strip()
        caption = f'<div class="ev-ctx">{_e(context)}</div>' if context else ""
        cards.append(
            f"""
<figure class="ev-card">
  <figcaption class="ev-cap"><span class="ev-badge">{label}</span><span class="ev-no">[截图#{_e(img.get("index"))}]</span></figcaption>
  <a href="{img_url}" target="_blank" rel="noreferrer"><img class="ev-img" src="{img_url}" loading="lazy" alt="截图#{_e(img.get("index"))}" /></a>
  {caption}
</figure>"""
        )
    return _section("错误证据截图（失败端子报告末图）", '<div class="ev-grid">' + "".join(cards) + "</div>")


def _embedded_evidence(evidence: dict[str, Any]) -> str:
    """把结构化证据内嵌进 HTML，供 report_readers/html 的 ai_hybrid 分支无损解析。"""
    payload = _json(evidence).replace("</", "<\\/")
    return f'<script type="application/json" id="hybrid-evidence">{payload}</script>'


def _conclusion_section(result: HybridRunResult) -> str:
    status_class = _status_class(result.status)
    finish = _find_finish(result.reasoning_trace)
    attribution = str(finish.get("attribution") or "").strip()
    evidence = [str(item) for item in (finish.get("evidence") or []) if str(item).strip()]
    suggestions = [str(item) for item in (finish.get("suggestions") or []) if str(item).strip()]

    extras = ""
    if attribution and attribution != result.final_summary:
        extras += f'<div class="conc-block"><span>归因</span><p>{_e(attribution)}</p></div>'
    if evidence:
        items = "".join(f"<li>{_e(item)}</li>" for item in evidence)
        extras += f'<div class="conc-block"><span>证据链</span><ul>{items}</ul></div>'
    if suggestions:
        items = "".join(f"<li>{_e(item)}</li>" for item in suggestions)
        extras += f'<div class="conc-block"><span>建议动作</span><ul>{items}</ul></div>'

    return f"""
<section class="reason {status_class}">
  <div class="reason-label">最终结论</div>
  <div class="reason-text">{_e(result.final_summary)}</div>
  {extras}
</section>"""


def _timeline_section(trace: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    for row in trace:
        phase = str(row.get("phase") or "")
        if phase == "step":
            cards.append(_step_card(row))
        elif phase == "finish":
            cards.append(_finish_card(row))
        elif phase == "backstop":
            cards.append(
                f'<article class="tl-card backstop"><div class="tl-head"><b>⛔ 触发防失控 backstop</b></div>'
                f'<p>{_e(row.get("message"))}</p></article>'
            )
        elif phase in {"plan", "tool"}:
            cards.append(_fallback_card(row))
    if not cards:
        return _section("ReAct 时间线", '<div class="empty">没有推理轨迹。</div>')
    return _section("ReAct 时间线", '<div class="timeline">' + "".join(cards) + "</div>")


def _step_card(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    reason = str(row.get("reason") or "")
    report = str(row.get("report_url") or "")
    link = f'<a href="{_e(report)}" target="_blank" rel="noreferrer">打开子报告</a>' if report else ""
    tool_input = row.get("tool_input") or {}
    input_html = f'<pre class="tl-input">{_e(_json(tool_input))}</pre>' if tool_input else ""
    return f"""
<article class="tl-card">
  <div class="tl-head">
    <span class="tl-index">{_e(row.get("index"))}</span>
    <b>call_tool → {_e(_tool_label(str(row.get("tool") or "")))}</b>
    <span class="status-badge {_status_class(status)}">{_e(_status_label(status))}</span>
  </div>
  <div class="tl-thought"><span>thought</span>{_e(row.get("thought"))}</div>
  {input_html}
  <div class="tl-obs">observation：<b>{_e(_status_label(status))}</b> {_e(reason)} {link}</div>
</article>"""


def _finish_card(row: dict[str, Any]) -> str:
    verdict = str(row.get("verdict") or "")
    return f"""
<article class="tl-card finish {_status_class(verdict)}">
  <div class="tl-head">
    <span class="tl-index">■</span>
    <b>finish → {_e(_status_label(verdict))}</b>
  </div>
  <div class="tl-thought"><span>thought</span>{_e(row.get("thought"))}</div>
</article>"""


def _fallback_card(row: dict[str, Any]) -> str:
    phase = str(row.get("phase") or "")
    if phase == "plan":
        title = f"[规则兜底] 生成计划：{row.get('step_count', 0)} 步"
        detail = f"模式 {row.get('mode') or '-'}"
    else:
        title = f"[规则兜底] 工具：{_tool_label(str(row.get('tool') or ''))}"
        detail = f"{row.get('status') or ''} {row.get('reason') or ''}"
    return f'<article class="tl-card fallback"><div class="tl-head"><b>{_e(title)}</b></div><p>{_e(detail)}</p></article>'


def _input_section(result: HybridRunResult) -> str:
    item = result.input_payload or {}
    if not item:
        return _section("任务输入", '<div class="empty">没有记录任务输入。</div>')
    lines = []
    if item.get("title") or item.get("goal"):
        lines.append(str(item.get("title") or item.get("goal") or ""))
    if item.get("preconditions"):
        lines.append(f"前置条件：{item.get('preconditions')}")
    if item.get("steps_text"):
        lines.append(f"操作步骤：{item.get('steps_text')}")
    if item.get("expected_result"):
        lines.append(f"预期结果：{item.get('expected_result')}")
    meta = [
        f"来源：{item.get('source_ref') or '-'}",
        "functionMap：已透传" if item.get("function_map_context_loaded") else "functionMap：未提供",
    ]
    body_text = "\n\n".join(line for line in lines if line) or "-"
    return _section(
        "任务输入",
        f"""
<article class="input-card">
  <p>{_e(body_text)}</p>
  <div class="tool-meta">{"".join(f"<span>{_e(value)}</span>" for value in meta)}</div>
</article>""",
    )


def _debug_section(result: HybridRunResult) -> str:
    payload = {
        "child_results": result.child_results_payload,
        "reasoning_trace": result.reasoning_trace,
    }
    return f"""
<details class="debug-details">
  <summary>调试原始数据（子工具返回 + 完整轨迹）</summary>
  <pre>{_e(_json(payload))}</pre>
</details>"""


def _find_finish(trace: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(trace):
        if str(row.get("phase") or "") == "finish":
            return row
    return {}


def _section(title: str, body: str) -> str:
    return f"""
<section class="section">
  <div class="section-title">{_e(title)}</div>
  {body}
</section>"""


def _status_label(status: str) -> str:
    return {"success": "通过", "failed": "失败", "needs_human": "需人工判断"}.get(status, status or "-")


def _status_class(status: str) -> str:
    if status == "success":
        return "success"
    if status == "needs_human":
        return "warning"
    return "failed"


def _reason_label(reason: str) -> str:
    if str(reason or "").startswith("child_submit_failed:"):
        return str(reason or "").replace("child_submit_failed: ", "子执行器提交失败：", 1)
    return {
        "needs_human": "需人工判断",
        "no_matching_executor": "未匹配到可自动执行的工具",
        "no_executable_step": "无可自动执行的步骤",
        "backstop_limit_reached": "防失控上限触发",
        "all_required_steps_passed": "必要步骤均通过",
        "dependency_not_satisfied": "依赖步骤未满足",
        "reasoner_parse_failed": "模型决策解析失败",
        "reasoner_error": "模型决策异常",
        "reasoner_unavailable": "模型决策不可用",
        "missing_verdict": "模型缺少最终判定",
        "missing_tool": "模型未指定工具",
        "tool_not_registered": "工具未注册",
    }.get(reason, reason or "-")


def _tool_label(tool: str) -> str:
    return {
        "ai_api": "AI API",
        "ai_phone": "AI Phone",
        "ai_web": "AI Web",
        "report_reader": "报告读取",
        "needs_human": "人工判断",
    }.get(tool, tool or "-")


def _fmt_elapsed(value: int | None) -> str:
    if value is None:
        return "-"
    if value < 1000:
        return f"{value} ms"
    seconds = value / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes = int(seconds // 60)
    rest = int(seconds % 60)
    return f"{minutes}m {rest}s"


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or ""))
    return safe[:80] or "aihybrid"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _e(value: Any) -> str:
    return html.escape(str(value or ""))


_CSS = """
:root {
  color-scheme: dark;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #020617;
  color: #e2e8f0;
}
* { box-sizing: border-box; }
body { margin: 0; background: #020617; color: #e2e8f0; }
.single-container { max-width: 1000px; margin: 0 auto; padding: 28px 20px 44px; }
.hd { display: flex; flex-direction: column; gap: 14px; margin-bottom: 16px; }
.hd-title { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
h1 { margin: 0; font-size: 25px; line-height: 1.25; }
.sub { color: #94a3b8; font-weight: 600; }
.hd-meta { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; min-height: 30px;
  padding: 5px 10px; border: 1px solid #334155; border-radius: 8px;
  background: #0f172a; color: #cbd5e1; font-size: 13px;
}
.chip b { color: #94a3b8; font-weight: 600; }
.status-badge {
  display: inline-flex; align-items: center; min-height: 26px; padding: 4px 10px;
  border-radius: 999px; font-size: 12px; font-weight: 700;
}
.status-badge.success { background: #064e3b; color: #6ee7b7; border: 1px solid #047857; }
.status-badge.warning { background: #713f12; color: #fde68a; border: 1px solid #a16207; }
.status-badge.failed { background: #7f1d1d; color: #fca5a5; border: 1px solid #b91c1c; }
.reason { border: 1px solid #1e293b; border-radius: 10px; background: #0a1120; padding: 16px 18px; margin-bottom: 14px; }
.reason.success { background: #052e23; border-color: #047857; }
.reason.warning { background: #3f2a05; border-color: #a16207; }
.reason.failed { background: #450a0a; border-color: #b91c1c; }
.reason-label { color: #94a3b8; font-size: 13px; font-weight: 700; margin-bottom: 6px; }
.reason-text { font-size: 16px; line-height: 1.7; color: #f8fafc; }
.conc-block { margin-top: 12px; }
.conc-block span { display: block; color: #94a3b8; font-size: 12px; font-weight: 700; margin-bottom: 5px; }
.conc-block p { margin: 0; line-height: 1.7; }
.conc-block ul { margin: 0; padding-left: 18px; line-height: 1.7; }
.section { background: #0a1120; border: 1px solid #1e293b; border-radius: 10px; padding: 18px; margin-top: 14px; }
.section-title { color: #cbd5e1; font-size: 15px; font-weight: 800; margin-bottom: 14px; }
.timeline { display: grid; gap: 12px; }
.tl-card { border: 1px solid #334155; border-radius: 10px; background: #0f172a; padding: 14px; }
.tl-card.finish { background: #0b1526; }
.tl-card.finish.success { border-color: #047857; }
.tl-card.finish.failed { border-color: #b91c1c; }
.tl-card.finish.warning { border-color: #a16207; }
.tl-card.backstop { border-color: #b91c1c; background: #2a0f0f; }
.tl-card.fallback { border-style: dashed; opacity: 0.85; }
.tl-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.tl-head b { font-size: 14px; }
.tl-index {
  display: grid; place-items: center; min-width: 24px; height: 24px; padding: 0 6px;
  border-radius: 999px; background: #1d4ed8; color: #dbeafe; font-weight: 800; font-size: 12px;
}
.tl-thought { line-height: 1.65; color: #e2e8f0; }
.tl-thought span {
  display: inline-block; margin-right: 8px; padding: 1px 7px; border-radius: 6px;
  background: #1e293b; color: #94a3b8; font-size: 11px; font-weight: 700;
}
.tl-input {
  margin: 10px 0 0; padding: 10px; border: 1px solid #1e293b; border-radius: 8px;
  background: #020617; color: #cbd5e1; font-size: 12px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-word;
}
.tl-obs { margin-top: 10px; color: #cbd5e1; font-size: 13px; }
.tl-obs b { color: #f8fafc; }
.input-card { border: 1px solid #334155; border-radius: 8px; background: #0f172a; padding: 14px; }
.input-card p { margin: 0; white-space: pre-wrap; word-break: break-word; line-height: 1.7; }
.tool-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.tool-meta span {
  border: 1px solid #334155; border-radius: 999px; background: #111827;
  padding: 4px 9px; color: #cbd5e1; font-size: 12px;
}
a { color: #38bdf8; text-decoration: none; font-weight: 700; }
.debug-details { margin-top: 14px; border: 1px solid #1e293b; border-radius: 10px; background: #0a1120; padding: 14px; }
.debug-details > summary { cursor: pointer; color: #38bdf8; font-weight: 800; }
pre {
  margin: 12px 0 0; overflow: auto; white-space: pre-wrap; word-break: break-word;
  background: #020617; color: #e2e8f0; border: 1px solid #1e293b; padding: 14px;
  border-radius: 8px; font-size: 12px; line-height: 1.6;
}
.empty { color: #94a3b8; padding: 8px 0; }
.ev-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
.ev-card { margin: 0; border: 1px solid #334155; border-radius: 10px; background: #0f172a; padding: 10px; }
.ev-cap { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ev-badge {
  display: inline-flex; align-items: center; padding: 3px 9px; border-radius: 999px;
  background: #1e3a8a; color: #dbeafe; font-size: 12px; font-weight: 700;
}
.ev-no { color: #94a3b8; font-size: 12px; font-weight: 700; }
.ev-img { width: 100%; height: auto; border-radius: 8px; border: 1px solid #1e293b; display: block; background: #020617; }
.ev-ctx { margin-top: 8px; color: #cbd5e1; font-size: 12px; line-height: 1.55; max-height: 96px; overflow: auto; }
.foot { color: #94a3b8; font-size: 12px; margin-top: 18px; text-align: center; }
@media (max-width: 640px) {
  .single-container { padding: 20px 12px 32px; }
  .section { padding: 14px; }
  h1 { font-size: 21px; }
}
"""
