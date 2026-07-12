"""从一次 run 的产物里收敛「可用于总报告 / 诊断 / 提 bug」的证据。

原则（对齐用户决策）：
- **证据图 = 每个「失败子端」子报告的末图（=真错误图）**，确定性地取，不看主 agent 有没有主动看过。
  理由：主 agent 没有看图动机、常凭子执行器的文字观察就判失败；而它「看过的图」也不等于错误图。
  真正的错误证据是失败子端子报告的最后那张（与普通 ai_web/ai_phone 提 bug 取图一致）。逐端标注端。
- ReAct 轨迹文本（thought/observation）单独产出，供总报告时间线与诊断 logs_text 使用。

这份结构既被 `reporter.py` 用来渲染总报告（可视 + 内嵌 JSON），也被 `report_readers/html.py` 的 ai_hybrid 分支解析成 `ReportEvidence`。
"""

from __future__ import annotations

from typing import Any

from app.services.ai_hybrid.schemas import HybridRunResult

_PLATFORM_LABELS = {
    "android": "安卓端",
    "ios": "iOS端",
    "harmony": "鸿蒙端",
    "chrome": "Chrome端",
    "safari": "Safari端",
    "webkit": "Safari端",
    "firefox": "Firefox端",
}
_EXECUTOR_LABELS = {
    "ai_phone": "手机端",
    "ai_web": "Web端",
    "ai_api": "接口",
}


def end_label(executor: str, platform: str) -> str:
    """端标签：优先按具体平台（android→安卓端…），否则按执行器（ai_phone→手机端…）。"""
    label = _PLATFORM_LABELS.get(str(platform or "").lower())
    if label:
        return label
    return _EXECUTOR_LABELS.get(str(executor or "").lower(), "该端")


async def build_evidence(result: HybridRunResult, settings: Any) -> dict[str, Any]:
    """收敛证据包：失败时按端取「失败子端子报告的末图（错误图）」。

    只在 case 判失败时取图（成功不多花 HTTP、也没有错误图可谈）。读子报告全程复用 `read_report`。
    """
    evidence = collect_evidence(result)
    if str(result.status or "").lower() == "failed":
        await _append_error_screenshots(evidence, result, settings)
    return evidence


async def _append_error_screenshots(evidence: dict[str, Any], result: HybridRunResult, settings: Any) -> None:
    from app.report_readers.html import read_report

    markers: list[str] = []
    seen: set[str] = set()
    for child in result.child_results_payload or []:
        if str(child.get("tool") or "") not in {"ai_phone", "ai_web"}:
            continue
        if str(child.get("status") or "").lower() != "failed":  # 只取失败端的错误图
            continue
        report_url = str(child.get("report_url") or "").strip()
        if not report_url or not report_url.startswith("http") or report_url in seen:
            continue
        seen.add(report_url)
        try:
            # image_limit=None：不截断，确保取到子报告真正的最后一张（=失败截图），
            # 否则超 40 张的报告会误取第 40 张。
            report = await read_report(report_url, executor=str(child.get("tool")), image_limit=None)
        except Exception:
            continue
        if not report.image_urls:
            continue
        raw = child.get("raw") or {}
        platform = str((raw.get("execution_strategy") or {}).get("platform") or "")
        label = end_label(str(child.get("tool")), platform)
        context = (report.summary or "")[:600]
        idx = len(evidence["images"])
        evidence["images"].append(
            {
                "index": idx,
                "executor": str(child.get("tool")),
                "platform": label,
                "report_url": report_url,
                "img_no": len(report.image_urls) - 1,
                "image_url": report.image_urls[-1],  # 末图=失败截图
                "context": context,
            }
        )
        markers.append(f"【{label}】[截图#{idx}]（失败端子报告末图）" + (f" {context}" if context else ""))
    if markers:
        tail = "错误证据（各失败端子报告末图）：\n" + "\n".join(markers)
        evidence["trajectory_text"] = (evidence.get("trajectory_text") or "").rstrip() + "\n\n" + tail


def collect_evidence(result: HybridRunResult) -> dict[str, Any]:
    """收敛结论 + ReAct 轨迹文本；images 恒为空，错误图由 build_evidence 按失败端补齐。"""
    lines: list[str] = []
    for row in result.reasoning_trace or []:
        phase = str(row.get("phase") or "")
        if phase == "step":
            lines.append(_step_line(row))
        elif phase == "finish":
            verdict = str(row.get("verdict") or "")
            thought = str(row.get("thought") or "").strip()
            lines.append(f"结论 · {verdict}：{thought}".rstrip("："))
        elif phase == "backstop":
            lines.append(f"⛔ 触发防失控 backstop：{row.get('message') or ''}")

    finish = _find_finish(result.reasoning_trace or [])
    return {
        "status": result.status,
        "status_reason": result.status_reason,
        "summary": result.final_summary,
        "attribution": str(finish.get("attribution") or ""),
        "evidence": [str(x) for x in (finish.get("evidence") or []) if str(x).strip()],
        "suggestions": [str(x) for x in (finish.get("suggestions") or []) if str(x).strip()],
        "images": [],
        "trajectory_text": "\n\n".join(line for line in lines if line.strip()),
    }


def _step_line(row: dict[str, Any]) -> str:
    tool = str(row.get("tool") or "")
    status = str(row.get("status") or "")
    thought = str(row.get("thought") or "").strip()
    reason = str(row.get("reason") or "").strip()
    parts = [f"第{row.get('index')}步 · {tool}（{status}）"]
    if thought:
        parts.append(f"  思考：{thought}")
    if reason:
        parts.append(f"  观察：{reason}")
    return "\n".join(parts)


def _find_finish(trace: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(trace):
        if str(row.get("phase") or "") == "finish":
            return row
    return {}
