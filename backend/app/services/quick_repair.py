from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.quick import (
    QuickCase,
    QuickCaseBody,
    QuickCaseStep,
    QuickCaseWorkItem,
    QuickRepairDraft,
    QuickSession,
)
from app.report_readers.html import hybrid_end_key_indices, read_report
from app.schemas.quick import (
    QuickRepairApplyOut,
    QuickRepairDraftOut,
    QuickRepairPreviewIn,
    QuickRepairPreviewOut,
)
from app.services.execution_reset import (
    clear_quick_case_execution_artifacts,
    reset_quick_work_item_execution,
)
from app.services.function_map_mount import compile_quick_context
from app.services.markdown_text import collapse_inline_text
from app.services.repair_orchestrator import (
    RepairDiagnosisInput,
    run_backend_repair_diagnosis,
    selected_key_image_url,
)

FAILURE_LABELS = {
    "missing_report": "缺少报告",
    "report_unreadable": "报告不可读",
    "business_failure": "业务失败",
    "assertion_failed": "断言失败",
    "execution_failed": "执行失败",
    "flaky_failure": "偶发波动",
    "unknown_failure": "执行失败",
    "model_unavailable": "模型不可用",
    "model_failed": "模型分析失败",
}
VISIBLE_FAILURE_LABELS = {
    "business_failure": "业务失败",
    "assertion_failed": "断言失败",
    "execution_failed": "执行失败",
    "flaky_failure": "偶发波动",
    "unknown_failure": "不确定",
    "missing_report": "不确定",
    "report_unreadable": "不确定",
    "model_unavailable": "不确定",
    "model_failed": "不确定",
}
REPAIR_POLICY_VERSION = 12
DIAGNOSE_MAX_ROUNDS = 3
DIAGNOSE_MAX_IMAGES = 6
WEB_EXECUTOR_TARGETS = {"web"}


@dataclass(frozen=True)
class QuickRepairInput:
    case_id: int
    case_title: str
    path: str
    preconditions: str
    steps_text: str
    expected_result: str
    report_url: str | None
    executor_failure_reason: str = ""
    function_map: str = ""
    primary_platform: str = ""
    executor: str = "ai_phone"
    other_end_reports: tuple[dict[str, str], ...] = ()


def _executor_for_target(execution_target: str | None) -> str:
    target = str(execution_target or "").lower()
    if target == "mixed":
        return "ai_hybrid"
    return "ai_web" if target in WEB_EXECUTOR_TARGETS else "ai_phone"

_auto_inflight: dict[int, asyncio.Event] = {}


async def preview_repairs(session: AsyncSession, payload: QuickRepairPreviewIn) -> QuickRepairPreviewOut:
    case_ids = [case_id for case_id in dict.fromkeys(payload.case_ids) if case_id > 0]
    if not case_ids:
        return QuickRepairPreviewOut(items=[])
    for case_id in case_ids:
        event = _auto_inflight.get(case_id)
        if event is not None:
            try:
                await event.wait()
            except Exception:
                pass
    return await _run_preview(session, case_ids)


async def auto_diagnose_case(case_id: int) -> None:
    if case_id <= 0 or case_id in _auto_inflight:
        return
    from app.core.database import AsyncSessionLocal

    event = asyncio.Event()
    _auto_inflight[case_id] = event
    diagnosed = False
    try:
        delay = max(0, get_settings().auto_diagnose_delay_seconds)
        if delay:
            await asyncio.sleep(delay)
        async with AsyncSessionLocal() as session:
            work_item = await session.get(QuickCaseWorkItem, case_id)
            if work_item is None or work_item.execution_status != "failed" or not work_item.report_url:
                return
            await _run_preview(session, [case_id])
            diagnosed = True
    except Exception:
        pass
    finally:
        _auto_inflight.pop(case_id, None)
        event.set()

    if not diagnosed:
        return
    try:
        from app.services.quick_bug_submit import precompute_bug_draft

        async with AsyncSessionLocal() as session:
            await precompute_bug_draft(session, case_id)
    except Exception:
        pass


async def _run_preview(session: AsyncSession, case_ids: list[int]) -> QuickRepairPreviewOut:
    rows = await session.execute(
        select(QuickCase, QuickCaseBody, QuickCaseWorkItem, QuickSession)
        .join(QuickCaseBody, QuickCaseBody.case_id == QuickCase.id)
        .join(QuickCaseWorkItem, QuickCaseWorkItem.case_id == QuickCase.id)
        .join(QuickSession, QuickSession.session_id == QuickCase.session_id)
        .where(QuickCase.id.in_(case_ids))
    )
    case_rows = {case.id: (case, body, work_item, quick_session) for case, body, work_item, quick_session in rows.all()}
    drafts = await _load_latest_drafts(session, case_ids)
    outputs: dict[int, QuickRepairDraftOut] = {}
    pending: list[tuple[QuickCase, QuickCaseBody, QuickCaseWorkItem, QuickSession]] = []

    for case_id in case_ids:
        row = case_rows.get(case_id)
        if row is None:
            continue
        case, body, work_item, quick_session = row
        if work_item.execution_status != "failed":
            outputs[case_id] = _blocked_without_draft(case, body, "仅失败状态的 case 可以进入错误修复。")
            continue
        draft = drafts.get(case_id)
        if draft and _draft_is_reusable(draft):
            _sync_failure_diagnosis(work_item, draft.gate_snapshot, draft.report_snapshot, draft.reason)
            outputs[case_id] = _draft_to_out(case, body, work_item, draft)
            continue
        if draft:
            await session.delete(draft)
        pending.append(row)

    # gather 内并发不能共用 session 查库；先按会话把 Function Map 编译好再传入。
    fm_by_session: dict[str, str] = {}
    for row in pending:
        sid = row[3].session_id
        if sid not in fm_by_session:
            fm_by_session[sid] = (await compile_quick_context(session, sid, "mixed")).context
    prepared = await asyncio.gather(
        *(_prepare_draft_values(*row, fm_by_session[row[3].session_id]) for row in pending)
    )
    created: list[QuickRepairDraft] = []
    for values in prepared:
        draft = QuickRepairDraft(**values)
        session.add(draft)
        created.append(draft)
        row = case_rows.get(values["case_id"])
        if row:
            _case, _body, work_item, _quick_session = row
            _sync_failure_diagnosis(
                work_item,
                values.get("gate_snapshot"),
                values.get("report_snapshot"),
                values.get("reason"),
            )
    if prepared:
        await session.flush()
        for draft in created:
            row = case_rows.get(draft.case_id)
            if row:
                case, body, work_item, _quick_session = row
                outputs[draft.case_id] = _draft_to_out(case, body, work_item, draft)
    await session.commit()
    return QuickRepairPreviewOut(items=[outputs[case_id] for case_id in case_ids if case_id in outputs])


async def apply_repair_draft(
    session: AsyncSession,
    draft_id: int,
    steps_text: str | None = None,
    preconditions: str | None = None,
    expected_result: str | None = None,
) -> QuickRepairApplyOut | None:
    draft = await session.get(QuickRepairDraft, draft_id)
    if draft is None:
        return None
    if not _draft_is_repairable(draft):
        raise ValueError("当前诊断修复草稿不可使用。")
    case = await session.get(QuickCase, draft.case_id)
    body = await session.get(QuickCaseBody, draft.case_id)
    work_item = await session.get(QuickCaseWorkItem, draft.case_id)
    if case is None or body is None or work_item is None:
        return None

    diagnosis = draft.diagnosis_snapshot or {}
    final_steps = collapse_inline_text(steps_text if steps_text is not None else draft.proposed_steps)
    final_pre = collapse_inline_text(
        preconditions
        if preconditions is not None
        else diagnosis.get("proposed_preconditions") or body.preconditions
    )
    final_exp = collapse_inline_text(
        expected_result
        if expected_result is not None
        else diagnosis.get("proposed_expected") or body.expected_result
    )
    if not final_steps:
        raise ValueError("操作步骤不能为空。")

    body.preconditions = final_pre
    body.expected_result = final_exp
    body.steps_text = final_steps
    _set_core_node(case, "preconditions", final_pre)
    _set_core_node(case, "steps", final_steps)
    _set_core_node(case, "expected", final_exp)
    await _replace_steps(session, case.id, body.steps_text)
    case.updated_at = func.now()
    reset_quick_work_item_execution(work_item, status="not_run")
    await clear_quick_case_execution_artifacts(session, [case.id])
    await session.commit()
    return QuickRepairApplyOut(case_id=case.id, message="已使用 quick 错误修复候选，case 已重置为未执行。")


async def _prepare_draft_values(
    case: QuickCase,
    body: QuickCaseBody,
    work_item: QuickCaseWorkItem,
    quick_session: QuickSession,
    function_map_context: str,
) -> dict[str, Any]:
    executor = _executor_for_target(work_item.execution_target)
    report = await read_report(work_item.report_url or "", executor=executor)
    gate = _hard_gate(report)
    status = "blocked"
    channel = "none"
    proposed_steps = body.steps_text
    proposed_preconditions = body.preconditions
    proposed_expected = body.expected_result
    reason = gate["reason"]
    fix_reason = ""
    evidence = ""
    report_failure_reason = _report_failure_reason(report, work_item.failure_summary or "")
    root_cause_point = ""
    key_image = None
    key_images: list[dict[str, str]] = []
    process_log: list[dict[str, Any]] = []
    model_name = None
    raw_payload: dict[str, Any] = {}
    trace: list[dict[str, Any]] = [{"stage": "report_reader", "report": _report_snapshot(report)}]

    if gate["allowed"]:
        repair_input = QuickRepairInput(
            case_id=case.id,
            case_title=case.raw_title,
            path=_path(case),
            preconditions=body.preconditions,
            steps_text=body.steps_text,
            expected_result=body.expected_result,
            report_url=work_item.report_url,
            executor_failure_reason=work_item.failure_summary or "",
            function_map=function_map_context,
            executor=executor,
        )
        model_result = await _analyze_with_model(repair_input, report)
        model_name = model_result.get("model_name")
        raw_payload = model_result
        trace.append({"stage": "model_analysis", "result": model_result})
        reason = str(model_result.get("reason") or reason)
        fix_reason = str(model_result.get("fix_reason") or "")
        evidence = str(model_result.get("evidence") or "")
        report_failure_reason = str(model_result.get("report_failure_reason") or report_failure_reason)
        root_cause_point = str(model_result.get("root_cause_point") or "")
        process_log = model_result.get("process") or []
        failure_type = _primary_failure_type(report.failure_type, model_result.get("failure_type"))
        images = report.image_urls
        key_idx = model_result.get("key_image_index")
        key_url = selected_key_image_url(images, key_idx)
        if model_result.get("available") and key_url:
            key_image = await _download_key_image(key_url, case.id)
        # hybrid：图来自多端，逐图带端标签 → 每端各留一张末图；其余执行器沿用单张。
        hybrid_ends = hybrid_end_key_indices(report) if executor == "ai_hybrid" else []
        if model_result.get("available") and hybrid_ends:
            for label, idx in hybrid_ends:
                if 0 <= idx < len(images):
                    end_img = await _download_key_image(images[idx], case.id)
                    if end_img:
                        key_images.append({"platform": label, "image": end_img})
        elif key_image:
            key_images.append({"platform": "", "image": key_image})
        if not model_result.get("available") or not model_result.get("repairable"):
            gate = {
                "allowed": False,
                "failure_type": failure_type,
                "analysis_failure_type": model_result.get("failure_type"),
                "reason": reason,
            }
        else:
            channel = str(model_result.get("repair_channel") or "steps")
            cand_steps = str(model_result.get("proposed_steps") or "").strip()
            cand_pre = str(model_result.get("proposed_preconditions") or "").strip()
            cand_exp = str(model_result.get("proposed_expected") or "").strip()
            usable = False
            if channel == "preconditions" and cand_pre and _normalize(cand_pre) != _normalize(body.preconditions):
                proposed_preconditions = cand_pre
                usable = True
            elif channel == "expected" and cand_exp and _normalize(cand_exp) != _normalize(body.expected_result):
                proposed_expected = cand_exp
                usable = True
            elif channel == "steps" and cand_steps and _normalize(cand_steps) != _normalize(body.steps_text):
                proposed_steps = cand_steps
                usable = True
            elif cand_steps and _normalize(cand_steps) != _normalize(body.steps_text):
                channel = "steps"
                proposed_steps = cand_steps
                usable = True
            if usable:
                status = "pending"
                gate = {
                    "allowed": True,
                    "failure_type": failure_type,
                    "analysis_failure_type": model_result.get("failure_type"),
                    "reason": reason,
                    "confidence": model_result.get("confidence"),
                }
            else:
                channel = "none"
                gate = {
                    "allowed": False,
                    "failure_type": failure_type,
                    "analysis_failure_type": "model_failed",
                    "reason": "模型判断可修，但没有给出可用的步骤/前置/预期候选。",
                }
                reason = gate["reason"]

    gate = _versioned_gate(gate)
    diagnosis_snapshot = {
        "reason": reason,
        "fix_reason": fix_reason,
        "evidence": evidence,
        "report_failure_reason": report_failure_reason,
        "root_cause_point": root_cause_point,
        "key_image": key_image,
        "key_images": key_images,
        "repair_channel": channel,
        "original_preconditions": body.preconditions,
        "proposed_preconditions": proposed_preconditions,
        "original_expected": body.expected_result,
        "proposed_expected": proposed_expected,
        "process": process_log,
    }
    return {
        "case_id": case.id,
        "status": status,
        "model_name": model_name,
        "original_steps": body.steps_text,
        "proposed_steps": proposed_steps,
        "reason": reason,
        "case_snapshot": {
            "title": case.raw_title,
            "path": _path(case),
            "preconditions": body.preconditions,
            "steps": body.steps_text,
            "expected_result": body.expected_result,
        },
        "report_snapshot": _report_snapshot(report),
        "gate_snapshot": gate,
        "analysis_trace": trace,
        "raw_payload": raw_payload,
        "diagnosis_snapshot": diagnosis_snapshot,
        "expires_at": datetime.now(UTC) + timedelta(days=7),
    }


async def _load_latest_drafts(session: AsyncSession, case_ids: list[int]) -> dict[int, QuickRepairDraft]:
    rows = await session.execute(
        select(QuickRepairDraft)
        .where(QuickRepairDraft.case_id.in_(case_ids))
        .order_by(QuickRepairDraft.case_id, QuickRepairDraft.id.desc())
    )
    drafts: dict[int, QuickRepairDraft] = {}
    for draft in rows.scalars().all():
        drafts.setdefault(draft.case_id, draft)
    return drafts


def _draft_to_out(
    case: QuickCase,
    body: QuickCaseBody,
    work_item: QuickCaseWorkItem,
    draft: QuickRepairDraft,
) -> QuickRepairDraftOut:
    gate = draft.gate_snapshot or {}
    report = draft.report_snapshot or {}
    failure_type = str(gate.get("failure_type") or report.get("failure_type") or "unknown_failure")
    diagnosis = draft.diagnosis_snapshot or {}
    original_pre = str(diagnosis.get("original_preconditions") or draft.case_snapshot.get("preconditions") or body.preconditions or "")
    return QuickRepairDraftOut(
        draft_id=draft.id,
        case_id=case.id,
        case_title=case.raw_title,
        path=str(draft.case_snapshot.get("path") or _path(case)),
        status=draft.status if _draft_is_repairable(draft) else "blocked",
        repairable=_draft_is_repairable(draft),
        failure_type=_visible_failure_label(failure_type),
        reason=draft.reason,
        fix_reason=str(diagnosis.get("fix_reason") or ""),
        evidence=str(diagnosis.get("evidence") or ""),
        key_image=diagnosis.get("key_image"),
        key_images=[
            {"platform": str(e.get("platform") or ""), "image": str(e.get("image") or "")}
            for e in (diagnosis.get("key_images") or [])
            if isinstance(e, dict) and e.get("image")
        ],
        repair_channel=str(diagnosis.get("repair_channel") or "none"),
        process=list(diagnosis.get("process") or []),
        original_steps=draft.original_steps or body.steps_text,
        proposed_steps=draft.proposed_steps or body.steps_text,
        original_preconditions=original_pre,
        proposed_preconditions=str(diagnosis.get("proposed_preconditions") or original_pre),
        original_expected=str(diagnosis.get("original_expected") or body.expected_result or ""),
        proposed_expected=str(diagnosis.get("proposed_expected") or body.expected_result or ""),
        report_url=str((report or {}).get("url") or work_item.report_url or ""),
        report_summary=str((report or {}).get("summary") or ""),
        bug_url=work_item.bug_url,
        model_name=draft.model_name,
        gate=gate,
        created_at=draft.created_at,
    )


def _blocked_without_draft(case: QuickCase, body: QuickCaseBody, reason: str) -> QuickRepairDraftOut:
    return QuickRepairDraftOut(
        draft_id=None,
        case_id=case.id,
        case_title=case.raw_title,
        path=_path(case),
        status="blocked",
        repairable=False,
        failure_type="不确定",
        reason=reason,
        original_steps=body.steps_text,
        proposed_steps=body.steps_text,
        original_preconditions=body.preconditions,
        proposed_preconditions=body.preconditions,
        original_expected=body.expected_result,
        proposed_expected=body.expected_result,
        report_summary=reason,
        gate={"allowed": False, "reason": reason},
    )


def _hard_gate(report: Any) -> dict[str, Any]:
    if report.failure_type == "missing_report":
        return {
            "allowed": False,
            "failure_type": "missing_report",
            "reason": "当前 case 没有关联执行报告，不能生成错误修复候选。",
        }
    if not report.available:
        return {
            "allowed": False,
            "failure_type": "report_unreadable",
            "reason": report.summary or "执行报告不可读，不能生成错误修复候选。",
        }
    return {"allowed": True, "failure_type": report.failure_type, "reason": "报告可读，交由模型综合判断。"}


def _primary_failure_type(report_failure_type: str | None, analysis_failure_type: str | None) -> str:
    if analysis_failure_type in {"assertion_failed", "execution_failed", "business_failure", "flaky_failure"}:
        return str(analysis_failure_type)
    if report_failure_type in {"assertion_failed", "execution_failed", "business_failure"}:
        return str(report_failure_type)
    return str(report_failure_type) if report_failure_type in FAILURE_LABELS else "unknown_failure"


def _versioned_gate(gate: dict[str, Any]) -> dict[str, Any]:
    return {**gate, "policy_version": REPAIR_POLICY_VERSION}


def _report_snapshot(report: Any) -> dict[str, Any]:
    return {
        "available": report.available,
        "reader": report.reader,
        "url": report.url,
        "failure_type": report.failure_type,
        "summary": report.summary,
        "logs_text": report.logs_text[:20000],
        "image_urls": report.image_urls,
        "quality": report.quality or {},
        "error": report.error,
    }


async def _analyze_with_model(
    item: QuickRepairInput,
    report: Any,
    round1_image_indices: list[int] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    api_key = settings.llm_api_key or os.environ.get("ARK_API_KEY") or os.environ.get("CASE_FLOW_LLM_API_KEY")
    if not api_key:
        return _model_unavailable("报告可读，但当前没有配置模型 Key，不能生成诊断。", None)
    try:
        from openai import OpenAI
    except Exception as exc:
        return _model_unavailable(f"模型 SDK 不可用：{exc}", None)

    base_url = settings.llm_base_url or os.environ.get(
        "ARK_BASE_URL",
        os.environ.get("CASE_FLOW_LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    )
    model = settings.llm_model or os.environ.get(
        "ARK_MODEL",
        os.environ.get("CASE_FLOW_LLM_MODEL", "doubao-seed-2-0-pro-260215"),
    )
    client = OpenAI(base_url=base_url, api_key=api_key)
    max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)

    def _call(messages: list[dict[str, Any]]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.05,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    async def _call_async(messages: list[dict[str, Any]]) -> str:
        return await asyncio.to_thread(_call, messages)

    async def _image_item(url: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(_vision_image_item, url)

    diagnosis_input = RepairDiagnosisInput(
        case_id=item.case_id,
        case_title=item.case_title,
        path=item.path,
        preconditions=item.preconditions,
        steps_text=item.steps_text,
        expected_result=item.expected_result,
        report_url=item.report_url,
        executor_failure_reason=item.executor_failure_reason,
        function_map=item.function_map,
        multi_end=bool(item.other_end_reports),
    )
    return await run_backend_repair_diagnosis(
        diagnosis_input,
        report,
        model,
        _call_async,
        _image_item,
        first_round_image_indices=round1_image_indices,
        max_rounds=DIAGNOSE_MAX_ROUNDS,
        max_images=DIAGNOSE_MAX_IMAGES,
    )


def _trajectory_excerpt(text: str, limit: int = 18000) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[:4000]
    tail = text[-(limit - 4000):]
    return f"{head}\n……（中间轨迹省略）……\n{tail}"


def _round1_payload(
    item: QuickRepairInput,
    report: Any,
    multi_end: bool = False,
) -> str:
    images = report.image_urls
    payload: dict[str, Any] = {
        "case": {
            "title": item.case_title,
            "path": item.path,
            "preconditions": item.preconditions,
            "steps": item.steps_text,
            "expected_result": item.expected_result,
        },
        "report": {
            "url": report.url,
            "failure_reason": _report_failure_reason(report, item.executor_failure_reason),
            "failure_type_hint": report.failure_type,
            "summary": report.summary,
            "trajectory_text": _trajectory_excerpt(report.logs_text),
            "image_index_range": [0, len(images) - 1] if images else [],
        },
        "function_map": item.function_map or "",
    }
    if multi_end:
        payload["multi_end_note"] = (
            "这条 quick case 在多个端都失败了。report.trajectory_text 已把各端轨迹按【主端】【xxx端】分段拼接，"
            "[截图#N] 是跨端统一编号(与 images 对齐)，首轮已给每端的末尾断图，你也可按 index 追加要图。"
            "请综合所有端判断：步骤是各端共享的，只能给【一份】对所有端都安全的修复；"
            "并在 reason 里分别说明每个端是 case 侧可修复 还是 产品侧问题(应提 bug)。"
        )
    return json.dumps(payload, ensure_ascii=False)


def _report_failure_reason(report: Any, executor_reason: str = "") -> str:
    executor_reason = str(executor_reason or "").strip()
    report_reason = _failure_reason_from_report(report)
    if executor_reason and _normalize(executor_reason) != _normalize(str(getattr(report, "summary", "") or "")):
        return executor_reason
    return report_reason


def _failure_reason_from_report(report: Any) -> str:
    text = getattr(report, "logs_text", "") or ""
    patterns = (
        r"assert_fail\(content=['\"](?P<value>[^'\"]{4,800})['\"]\)",
        r"(?:失败原因|失败信息|错误原因|断言失败|校验失败)[:：]\s*(?P<value>[^\n]{4,800})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group("value").strip()
    return str(getattr(report, "summary", "") or "").strip()


def _image_data_uri(url: str) -> str | None:
    if not url:
        return None
    try:
        resp = httpx.get(url, timeout=15, trust_env=False, follow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return None
    content_type = (resp.headers.get("content-type") or "").lower()
    low = url.lower().split("?")[0]
    if "png" in content_type or low.endswith(".png"):
        mime = "image/png"
    elif "webp" in content_type or low.endswith(".webp"):
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(resp.content).decode()


def _vision_image_item(url: str) -> dict[str, Any] | None:
    uri = _image_data_uri(url)
    return {"type": "image_url", "image_url": {"url": uri}} if uri else None


def _model_unavailable(reason: str, model: str | None) -> dict[str, Any]:
    return {
        "available": False,
        "repairable": False,
        "failure_type": "model_unavailable" if model is None else "model_failed",
        "reason": reason,
        "proposed_steps": "",
        "proposed_preconditions": "",
        "proposed_expected": "",
        "confidence": 0,
        "model_name": model,
        "process": [],
    }


async def _download_key_image(url: str, case_id: int) -> str | None:
    if not url:
        return None
    settings = get_settings()
    base_dir = Path(settings.repair_image_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    suffix = os.path.splitext(url.split("?")[0])[1].lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        suffix = ".png"
    filename = f"quick_case{case_id}_{uuid.uuid4().hex[:8]}{suffix}"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            (base_dir / filename).write_bytes(resp.content)
    except Exception:
        return None
    return f"/media/{filename}"


def _draft_is_repairable(draft: QuickRepairDraft) -> bool:
    gate = draft.gate_snapshot or {}
    return draft.status == "pending" and bool(gate.get("allowed"))


def _draft_is_reusable(draft: QuickRepairDraft) -> bool:
    gate = draft.gate_snapshot or {}
    return bool(
        draft.expires_at
        and draft.expires_at > datetime.now(UTC)
        and gate.get("policy_version") == REPAIR_POLICY_VERSION
    )


def _sync_failure_diagnosis(
    work_item: QuickCaseWorkItem,
    gate: dict[str, Any] | None,
    report: dict[str, Any] | None,
    reason: str | None,
) -> None:
    gate = gate or {}
    report = report or {}
    failure_type = str(gate.get("failure_type") or report.get("failure_type") or "")
    if failure_type:
        work_item.failure_type = failure_type
    if reason:
        work_item.failure_summary = reason
    work_item.updated_at = func.now()


def _path(case: QuickCase) -> str:
    nodes = getattr(case, "path_nodes", None)
    if not isinstance(nodes, list):
        return ""
    return " / ".join(
        str(node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text") or "")
        for node in nodes
        if isinstance(node, dict)
        and (node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text"))
    )


def _visible_failure_label(value: str | None) -> str:
    text = str(value or "")
    if text in VISIBLE_FAILURE_LABELS:
        return text
    return VISIBLE_FAILURE_LABELS.get(text) or FAILURE_LABELS.get(text) or "不确定"


def _normalize(text: str) -> str:
    return "".join(str(text or "").split())


async def _replace_steps(session: AsyncSession, case_id: int, steps_text: str) -> None:
    await session.execute(delete(QuickCaseStep).where(QuickCaseStep.case_id == case_id))
    steps = [line.strip() for line in steps_text.splitlines() if line.strip()]
    if not steps and steps_text.strip():
        steps = [steps_text.strip()]
    session.add_all(
        QuickCaseStep(case_id=case_id, step_order=index, step_text=step)
        for index, step in enumerate(steps, start=1)
    )


def _set_core_node(case: QuickCase, role: str, display_text: str) -> None:
    display_text = collapse_inline_text(display_text)
    nodes = dict(case.core_nodes or {})
    node = dict(nodes.get(role) or {})
    node["displayText"] = display_text
    if node.get("trimmed"):
        label = str(node.get("label") or role)
        separator = str(node.get("separator") or "：")
        node["rawText"] = f"{label}{separator}{display_text}"
    else:
        node["rawText"] = display_text
    nodes[role] = node
    case.core_nodes = nodes
