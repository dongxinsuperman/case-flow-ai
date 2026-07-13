from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.settings import get_settings
from app.schemas.aihybrid import (
    HybridSubmissionStatusItemOut,
    HybridSubmissionStatusOut,
    HybridSubmitIn,
    HybridSubmitItemOut,
    HybridSubmitOut,
)
from app.services.ai_hybrid.reporter import write_hybrid_report
from app.services.ai_hybrid.runner import run_hybrid
from app.services.ai_hybrid.schemas import HybridInput, HybridRunResult

logger = logging.getLogger(__name__)

_submissions: dict[str, dict[str, Any]] = {}


async def accept_submission(payload: HybridSubmitIn) -> HybridSubmitOut:
    if not payload.items:
        raise ValueError("Hybrid 提交缺少 items")

    now_label = datetime.now().strftime("%Y%m%d-%H%M%S")
    submission_id = f"aihybrid-{uuid.uuid4().hex}"
    submission_name = payload.submission_name or f"AI Hybrid {now_label}"
    record = {
        "submission_id": submission_id,
        "submission_name": submission_name,
        "state": "running",
        "callback_url": payload.callback_url,
        "summary_report_url": None,
        "items": [
            {
                "case_id": item.case_id,
                "platform": "mixed",
                "state": "queued",
                "report_url": None,
                "status_reason": None,
                "run_id": None,
                "raw_item": item.model_dump(mode="json", by_alias=True),
            }
            for item in payload.items
        ],
        "raw": {
            "request": payload.model_dump(mode="json", by_alias=True),
            "callbacks": [],
        },
    }
    _submissions[submission_id] = record
    task = asyncio.create_task(_run_submission(submission_id, payload))
    task.add_done_callback(_consume_task_exception)
    return HybridSubmitOut(
        submission_id=submission_id,
        submission_name=submission_name,
        items=[HybridSubmitItemOut(case_id=item.case_id, platform="mixed", state="queued") for item in payload.items],
    )


def get_submission(submission_id: str) -> HybridSubmissionStatusOut | None:
    record = _submissions.get(submission_id)
    if record is None:
        return None
    return HybridSubmissionStatusOut(
        submission_id=record["submission_id"],
        submission_name=record.get("submission_name"),
        state=record.get("state") or "unknown",
        items=[
            HybridSubmissionStatusItemOut(
                case_id=item["case_id"],
                platform=item.get("platform") or "mixed",
                state=item.get("state") or "unknown",
                report_url=item.get("report_url"),
                status_reason=item.get("status_reason"),
            )
            for item in record.get("items") or []
        ],
        raw=record.get("raw") or {},
    )


async def _run_submission(submission_id: str, payload: HybridSubmitIn) -> None:
    settings = get_settings()
    record = _submissions[submission_id]
    summary_report_url: str | None = None
    counts = {"success": 0, "failed": 0}

    for index, item in enumerate(payload.items):
        record_item = record["items"][index]
        record_item["state"] = "running"
        run_id = f"aihybrid-run-{uuid.uuid4().hex}"
        try:
            function_map_context = _effective_function_map_context(
                payload.function_map_context,
                item.function_map_context,
            )
            function_maps = _effective_function_maps(payload.function_maps, item.function_maps)
            hybrid_input = _hybrid_input_from_item(item, function_map_context, function_maps)
            result = await run_hybrid(
                hybrid_input,
                settings,
            )
        except Exception as exc:
            result = HybridRunResult(
                status="failed",
                status_reason="hybrid_error",
                final_summary=f"Hybrid 执行异常：{exc}",
                child_results_payload=[],
                reasoning_trace=[{"phase": "error", "error": str(exc)}],
            )

        try:
            report_url = await write_hybrid_report(settings, submission_id, item.case_id, result)
        except Exception as exc:
            logger.exception("failed to write AI Hybrid report")
            report_url = None
            result.status = "failed"
            result.status_reason = "report_write_failed"
            result.final_summary = f"Hybrid 报告生成失败：{exc}"

        terminal_state = "success" if result.status == "success" else "failed"
        counts[terminal_state] += 1
        if terminal_state == "failed" and not summary_report_url:
            summary_report_url = report_url
        if terminal_state == "success" and not summary_report_url:
            summary_report_url = report_url

        record_item.update(
            {
                "state": terminal_state,
                "report_url": report_url,
                "status_reason": "needs_human" if result.status == "needs_human" else result.status_reason,
                "run_id": run_id,
                "raw_result": result.model_dump(mode="json"),
            }
        )
        await _post_parent_callback(
            payload.callback_url,
            {
                "event": "submission.item.terminal",
                "submissionId": submission_id,
                "caseId": item.case_id,
                "platform": "mixed",
                "state": terminal_state,
                "statusReason": record_item["status_reason"],
                "reportUrl": report_url,
                "runId": run_id,
            },
            record,
        )

    record["state"] = "done"
    record["summary_report_url"] = summary_report_url
    await _post_parent_callback(
        payload.callback_url,
        {
            "event": "submission.terminal",
            "submissionId": submission_id,
            "submissionState": "done",
            "summaryReportUrl": summary_report_url,
            "counts": counts,
        },
        record,
    )


async def _post_parent_callback(url: str, payload: dict[str, Any], record: dict[str, Any]) -> None:
    if not url:
        return
    attempts = 8
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            record["raw"]["callbacks"].append(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "payload": payload,
                    "attempt": attempt,
                    "status": "delivered",
                }
            )
            return
        except Exception as exc:
            if attempt >= attempts:
                record["raw"]["callbacks"].append(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "payload": payload,
                        "attempt": attempt,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                logger.warning("AI Hybrid callback delivery failed: %s", exc)
                return
            await asyncio.sleep(min(2.0, 0.2 * attempt))


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        return


def _hybrid_input_from_item(
    item: Any, function_map_context: str, function_maps: list[dict[str, Any]]
) -> HybridInput:
    parsed = _parse_run_content(item.run_content)
    title = parsed.get("title") or item.case_name or ""
    steps = parsed.get("steps_text") or item.run_content or ""
    return HybridInput(
        goal=title or item.case_id,
        title=title,
        preconditions=parsed.get("preconditions") or "",
        steps_text=steps,
        expected_result=parsed.get("expected_result") or "",
        function_map_context=function_map_context,
        function_maps=function_maps,
        source_ref=item.case_id,
    )


def _effective_function_map_context(batch_context: str, item_context: str) -> str:
    parts = [
        str(part or "").strip()
        for part in (batch_context, item_context)
        if str(part or "").strip()
    ]
    return "\n\n".join(parts)


def _effective_function_maps(
    batch_maps: list[dict[str, Any]] | None,
    item_maps: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """合并提交级与 item 级的结构化 Map，按 asset_id 去重，提交级优先。"""
    combined: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for source in (batch_maps or [], item_maps or []):
        for entry in source:
            if not isinstance(entry, dict):
                continue
            key = entry.get("asset_id")
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            combined.append(entry)
    return combined


def _parse_run_content(text: str) -> dict[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    labels = {
        "测试标题": "title",
        "标题": "title",
        "前置条件": "preconditions",
        "操作步骤": "steps_text",
        "步骤": "steps_text",
        "预期结果": "expected_result",
        "期望结果": "expected_result",
    }
    pattern = re.compile(
        r"(?m)^\s*(测试标题|标题|前置条件|操作步骤|步骤|预期结果|期望结果)\s*[：:]\s*"
    )
    matches = list(pattern.finditer(raw))
    if not matches:
        return {"steps_text": raw}
    parsed: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = labels[match.group(1)]
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        value = raw[start:end].strip()
        if value:
            parsed[key] = value
    return parsed
