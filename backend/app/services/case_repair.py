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
from app.models.case_assets import (
    CaseAsset,
    CaseBody,
    CaseRawNode,
    CaseRepairDraft,
    CaseStep,
    CaseWorkItem,
)
from app.report_readers.base import ReportBlock, ReportEvidence
from app.report_readers.html import hybrid_end_key_indices, read_report
from app.schemas.repair import RepairApplyOut, RepairDraftOut, RepairPreviewIn, RepairPreviewOut
from app.services.function_map_mount import compile_top_level_context
from app.services.execution_reset import (
    clear_standard_case_execution_artifacts,
    reset_standard_work_item_execution,
)
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

REPAIR_GATE_LABELS = {
    "missing_report": "缺少报告",
    "report_unreadable": "报告不可读",
    "business_failure": "业务失败",
    "assertion_failed": "断言失败",
    "execution_failed": "执行失败",
    "flaky_failure": "建议重试",
    "unknown_failure": "不确定",
    "model_unavailable": "不确定",
    "model_failed": "不确定",
}

REPAIR_POLICY_VERSION = 12
PERSISTED_FAILURE_TYPES = {
    "assertion_failed",
    "business_failure",
    "execution_failed",
    "flaky_failure",
    "environment_failure",
    "case_step_failure",
}
WEB_EXECUTOR_PLATFORMS = {"chrome", "safari", "webkit", "firefox"}


@dataclass(frozen=True)
class RepairInput:
    case_id: int
    case_title: str
    path: str
    preconditions: str
    steps_text: str
    expected_result: str
    report_url: str | None
    executor_failure_reason: str = ""
    function_map: str = ""
    # 代表端（report_url 对应的那个端）的平台，用于端名标签。
    primary_platform: str = ""
    # 代表端执行器，用于选择端专属报告读取标签和修复策略 prompt。
    executor: str = "ai_phone"
    # 多端融合：除代表端外、其它“也失败且有报告”的端 [{platform, url}]。诊断时一起喂模型，出一份修复。
    other_end_reports: tuple[dict[str, str], ...] = ()


def _end_label(platform: str | None) -> str:
    """端名标签：android→安卓端、ios→iOS端、harmony→鸿蒙端、chrome/safari/firefox 同名。未知则‘该端’。"""
    names = {
        "android": "安卓", "ios": "iOS", "harmony": "鸿蒙",
        "chrome": "Chrome", "safari": "Safari", "webkit": "Safari", "firefox": "Firefox",
    }
    p = str(platform or "").lower()
    base = names.get(p) or (str(platform) if platform else "")
    return f"{base}端" if base else "该端"


def _executor_for_platform(platform: str | None, execution_target: str | None = None) -> str:
    p = str(platform or "").lower()
    target = str(execution_target or "").lower()
    if target == "mixed" or p == "mixed":
        return "ai_hybrid"
    if target == "web" or p in WEB_EXECUTOR_PLATFORMS:
        return "ai_web"
    return "ai_phone"


# 正在后台自动诊断的 case → Event。手动点击若命中，等它跑完直接复用草稿，不重复烧 LLM。
_auto_inflight: dict[int, asyncio.Event] = {}


async def preview_repairs(session: AsyncSession, payload: RepairPreviewIn) -> RepairPreviewOut:
    case_ids = [case_id for case_id in dict.fromkeys(payload.case_ids) if case_id > 0]
    if not case_ids:
        return RepairPreviewOut(items=[])
    # 有后台自动诊断在跑，先等它（相当于“提前帮他点了一下”，用户只是等结果）。
    for cid in case_ids:
        ev = _auto_inflight.get(cid)
        if ev is not None:
            try:
                await ev.wait()
            except Exception:
                pass
    return await _run_preview(session, case_ids)


async def auto_diagnose_case(case_id: int) -> None:
    """失败且有报告时后台自动诊断：用独立 session 跑一次 preview，把草稿/诊断写库。

    幂等：同一 case 已在跑则跳过；跑完释放，唤醒可能在等待的手动点击。
    """
    if case_id <= 0 or case_id in _auto_inflight:
        return
    from app.core.database import AsyncSessionLocal

    event = asyncio.Event()
    _auto_inflight[case_id] = event
    diagnosed = False
    try:
        # 先等执行器把报告内容渲染完整再读（截图/日志可能晚于 report_url 到达）。
        # 先占住 inflight 锁再 sleep：这期间用户点击会等这一次结果，而不是去读残缺报告。
        delay = max(0, get_settings().auto_diagnose_delay_seconds)
        if delay:
            await asyncio.sleep(delay)
        async with AsyncSessionLocal() as session:
            work_item = await session.get(CaseWorkItem, case_id)
            if work_item is None or work_item.execution_status != "failed" or not work_item.report_url:
                return  # 等待期间状态已变 / 没有报告，放弃自动诊断
            await _run_preview(session, [case_id])
            diagnosed = True
    except Exception:
        pass
    finally:
        _auto_inflight.pop(case_id, None)
        event.set()

    if not diagnosed:
        return

    # 诊断修复的等待态到这里已经释放；bug 草稿继续后台预生成，不能阻塞“诊断修复”弹窗。
    try:
        from app.services.bug_submit import precompute_bug_draft

        async with AsyncSessionLocal() as session:
            await precompute_bug_draft(session, case_id)
    except Exception:
        pass


async def _run_preview(session: AsyncSession, case_ids: list[int]) -> RepairPreviewOut:
    rows = await session.execute(
        select(CaseAsset, CaseBody, CaseWorkItem)
        .join(CaseBody, CaseBody.case_id == CaseAsset.id)
        .join(CaseWorkItem, CaseWorkItem.case_id == CaseAsset.id)
        .where(CaseAsset.id.in_(case_ids))
    )
    case_rows = {case.id: (case, body, work_item) for case, body, work_item in rows.all()}
    drafts = await _load_latest_drafts(session, case_ids)

    outputs: dict[int, RepairDraftOut] = {}
    pending_inputs: list[RepairInput] = []
    function_map_cache: dict[int, str] = {}
    for case_id in case_ids:
        row = case_rows.get(case_id)
        if row is None:
            continue
        case, body, work_item = row
        if work_item.execution_status != "failed":
            outputs[case_id] = _blocked_without_draft(case, body, "仅失败状态的 case 可以进入错误修复。")
            continue
        if (draft := drafts.get(case_id)) and _draft_is_reusable(draft):
            _sync_failure_diagnosis(work_item, draft.gate_snapshot, draft.report_snapshot, draft.reason)
            outputs[case_id] = _draft_to_out(case, body, draft)
            outputs[case_id].bug_url = work_item.bug_url
            continue
        if draft:
            await session.delete(draft)
        function_map = await _resolve_function_map(session, case, function_map_cache)
        primary_platform, other_reports = await _other_failed_end_reports(
            session, case.id, work_item.report_url
        )
        pending_inputs.append(
            _build_input(case, body, work_item, function_map, other_reports, primary_platform)
        )

    prepared = await asyncio.gather(*(_prepare_draft_values(item) for item in pending_inputs))
    created_drafts: list[CaseRepairDraft] = []
    for draft_values in prepared:
        draft = CaseRepairDraft(**draft_values)
        session.add(draft)
        created_drafts.append(draft)
        row = case_rows.get(draft_values["case_id"])
        if row:
            _case, _body, work_item = row
            _sync_failure_diagnosis(
                work_item,
                draft_values.get("gate_snapshot"),
                draft_values.get("report_snapshot"),
                draft_values.get("reason"),
            )
    if prepared:
        await session.flush()
        for draft in created_drafts:
            row = case_rows.get(draft.case_id)
            if row:
                case, body, _work_item = row
                outputs[draft.case_id] = _draft_to_out(case, body, draft)
                outputs[draft.case_id].bug_url = _work_item.bug_url
    await session.commit()

    return RepairPreviewOut(items=[outputs[case_id] for case_id in case_ids if case_id in outputs])


async def apply_repair_draft(
    session: AsyncSession,
    draft_id: int,
    steps_text: str | None = None,
    preconditions: str | None = None,
    expected_result: str | None = None,
) -> RepairApplyOut | None:
    draft = await session.get(CaseRepairDraft, draft_id)
    if draft is None:
        return None
    if not _draft_is_repairable(draft):
        raise ValueError("当前诊断修复草稿不可使用。")
    case = await session.get(CaseAsset, draft.case_id)
    body = await session.get(CaseBody, draft.case_id)
    work_item = await session.get(CaseWorkItem, draft.case_id)
    if case is None or body is None or work_item is None:
        return None

    # 用户可在弹窗编辑后提交；未传则用草稿候选。前置取候选/原值。
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
    await _replace_steps(session, case.id, body.steps_text)
    await _replace_raw_node(session, case, body)
    case.updated_at = func.now()
    reset_standard_work_item_execution(work_item, status="not_run")
    await clear_standard_case_execution_artifacts(session, [case.id])
    await session.commit()
    return RepairApplyOut(case_id=case.id, message="已使用错误修复候选，case 已重置为未执行。")


async def _load_latest_drafts(session: AsyncSession, case_ids: list[int]) -> dict[int, CaseRepairDraft]:
    rows = await session.execute(
        select(CaseRepairDraft)
        .where(CaseRepairDraft.case_id.in_(case_ids))
        .order_by(CaseRepairDraft.case_id, CaseRepairDraft.id.desc())
    )
    drafts: dict[int, CaseRepairDraft] = {}
    for draft in rows.scalars().all():
        drafts.setdefault(draft.case_id, draft)
    return drafts


def _build_input(
    case: CaseAsset,
    body: CaseBody,
    work_item: CaseWorkItem,
    function_map: str = "",
    other_end_reports: tuple[dict[str, str], ...] = (),
    primary_platform: str = "",
) -> RepairInput:
    return RepairInput(
        case_id=case.id,
        case_title=case.raw_title,
        path=_path(case),
        preconditions=body.preconditions,
        steps_text=body.steps_text,
        expected_result=body.expected_result,
        report_url=work_item.report_url,
        executor_failure_reason=work_item.failure_summary or "",
        function_map=function_map,
        primary_platform=primary_platform,
        executor=_executor_for_platform(primary_platform, work_item.execution_target),
        other_end_reports=other_end_reports,
    )


async def _other_failed_end_reports(
    session: AsyncSession, case_id: int, primary_url: str | None
) -> tuple[str, tuple[dict[str, str], ...]]:
    """返回 (代表端平台, 其它失败端 [{platform,url}])。代表端=report_url 对应的端，用于端名标签与多端融合诊断。"""
    from app.services import executions as executions_service

    try:
        results = await executions_service.list_case_platform_results(session, case_id)
    except Exception:
        return "", ()
    primary_platform = ""
    others: list[dict[str, str]] = []
    for result in results:
        if primary_url and result.report_url == primary_url:
            primary_platform = result.platform
        elif result.state == "failed" and result.report_url and result.report_url != primary_url:
            others.append({"platform": result.platform, "url": result.report_url})
    return primary_platform, tuple(others)


async def _resolve_function_map(
    session: AsyncSession, case: CaseAsset, cache: dict[int, str]
) -> str:
    """取该 case 所属二级需求的显式挂载编译上下文（一级继承 + 二级本级，按二级需求缓存）。"""
    requirement_item_id = getattr(case, "source_requirement_item_id", None)
    if not requirement_item_id:
        return ""
    if requirement_item_id not in cache:
        cache[requirement_item_id] = (
            await compile_top_level_context(session, [requirement_item_id], "mixed")
        ).context
    return cache[requirement_item_id]


async def _merge_end_reports(
    item: RepairInput, primary: ReportEvidence
) -> tuple[ReportEvidence, list[tuple[str, int]] | None]:
    """多端失败时把各端报告合并成一份：统一编号的截图列表 + 分段拼接并重排 [截图#N] 的轨迹。

    返回 (合并报告, [(端标签, 该端末尾断图在合并列表里的 index)])。单端则原样返回、第二项为 None。
    这些 index 既用于首轮给“每端末尾断图”，也用于提 bug 时**每端各留一张证据图**。
    """
    if not item.other_end_reports:
        return primary, None
    segments: list[tuple[str, ReportEvidence]] = [(_end_label(item.primary_platform), primary)]
    for other in item.other_end_reports:
        try:
            rep = await read_report(
                other.get("url") or "",
                executor=_executor_for_platform(other.get("platform")),
            )
        except Exception:
            continue
        segments.append((_end_label(other.get("platform")), rep))
    if len(segments) <= 1:
        return primary, None

    combined_images: list[str] = []
    combined_blocks: list[ReportBlock] = []
    text_parts: list[str] = []
    summaries: list[str] = []
    end_key_indices: list[tuple[str, int]] = []
    for label, rep in segments:
        offset = len(combined_images)
        renumbered = re.sub(
            r"\[截图#(\d+)\]",
            lambda m: f"[截图#{int(m.group(1)) + offset}]",
            rep.logs_text or "",
        )
        text_parts.append(f"【{label}】\n{renumbered}")
        if rep.summary:
            summaries.append(f"{label}：{rep.summary}")
        if rep.image_urls:
            end_key_indices.append((label, offset + len(rep.image_urls) - 1))  # 该端末尾断图
        combined_blocks.extend(_renumber_report_blocks(label, rep, offset, len(combined_blocks)))
        combined_images.extend(rep.image_urls)

    merged = ReportEvidence(
        available=primary.available,
        reader=primary.reader,
        url=primary.url,
        failure_type=primary.failure_type,
        summary=" ｜ ".join(summaries) or primary.summary,
        logs_text="\n\n".join(text_parts),
        image_urls=combined_images,
        blocks=combined_blocks,
    )
    return merged, end_key_indices


def _renumber_report_blocks(
    label: str,
    report: ReportEvidence,
    image_offset: int,
    block_offset: int,
) -> list[ReportBlock]:
    blocks: list[ReportBlock] = []
    for block in report.blocks or []:
        kind = str(block.kind or "").strip().lower()
        if kind == "text":
            text = re.sub(
                r"\[截图#(\d+)\]",
                lambda m: f"[截图#{int(m.group(1)) + image_offset}]",
                block.text or "",
            ).strip()
            if text:
                blocks.append(
                    ReportBlock(index=block_offset + len(blocks), kind="text", text=f"【{label}】\n{text}")
                )
        elif kind == "image" and block.image_index is not None:
            image_index = block.image_index + image_offset
            url = report.image_urls[block.image_index] if 0 <= block.image_index < len(report.image_urls) else block.url
            blocks.append(
                ReportBlock(index=block_offset + len(blocks), kind="image", image_index=image_index, url=url)
            )
    return blocks


async def _prepare_draft_values(item: RepairInput) -> dict[str, Any]:
    primary = await read_report(item.report_url or "", executor=item.executor)
    report, end_key_indices = await _merge_end_reports(item, primary)
    # hybrid 单份总报告：图来自多个子端，用逐图端标签合成“每端一张末图”，复用多端诊断链路。
    if not end_key_indices and item.executor == "ai_hybrid":
        end_key_indices = hybrid_end_key_indices(report) or None
    round1_image_indices = [idx for _label, idx in end_key_indices] if end_key_indices else None
    gate = _hard_gate(report)
    status = "blocked"
    channel = "none"
    proposed_steps = item.steps_text
    proposed_preconditions = item.preconditions
    proposed_expected = item.expected_result
    reason = gate["reason"]
    fix_reason = ""
    evidence = ""
    report_failure_reason = _report_failure_reason(report, item.executor_failure_reason)
    root_cause_point = ""
    process_log: list[dict[str, Any]] = []
    key_image: str | None = None
    # 多端证据：每端各一张关键截图 [{platform, image}]，提 bug 时全部带上。
    key_images: list[dict[str, str]] = []
    model_name: str | None = None
    raw_payload: dict[str, Any] = {}
    trace: list[dict[str, Any]] = [{"stage": "report_reader", "report": _report_snapshot(report)}]

    if gate["allowed"]:
        model_result = await _analyze_with_model(item, report, round1_image_indices)
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
        # 关键失败截图：只使用模型明确指认的绝对 index（与 [截图#N] 对齐）。
        images = report.image_urls
        key_idx = model_result.get("key_image_index")
        key_url = selected_key_image_url(images, key_idx)
        if model_result.get("available") and key_url:
            key_image = await _download_key_image(key_url, item.case_id)
        # 每端各下一张关键截图（该端末尾断图）作为提 bug 的多端证据；单端则用模型指认的那张。
        if model_result.get("available"):
            if end_key_indices:
                for label, idx in end_key_indices:
                    if 0 <= idx < len(images):
                        end_img = await _download_key_image(images[idx], item.case_id)
                        if end_img:
                            key_images.append({"platform": label, "image": end_img})
            elif key_image:
                label = _end_label(item.primary_platform) if item.primary_platform else ""
                key_images.append({"platform": label, "image": key_image})

        if not model_result.get("available"):
            gate = {"allowed": False, "failure_type": failure_type,
                    "analysis_failure_type": model_result.get("failure_type"), "reason": reason}
        elif not model_result.get("repairable"):
            channel = "none"
            gate = {"allowed": False, "failure_type": failure_type,
                    "analysis_failure_type": model_result.get("failure_type"), "reason": reason}
        else:
            channel = str(model_result.get("repair_channel") or "steps")
            cand_steps = str(model_result.get("proposed_steps") or "").strip()
            cand_pre = str(model_result.get("proposed_preconditions") or "").strip()
            cand_exp = str(model_result.get("proposed_expected") or "").strip()
            usable = False
            if channel == "preconditions" and cand_pre and _normalize(cand_pre) != _normalize(item.preconditions):
                proposed_preconditions = cand_pre
                usable = True
            elif channel == "expected" and cand_exp and _normalize(cand_exp) != _normalize(item.expected_result):
                proposed_expected = cand_exp
                usable = True
            elif channel == "steps" and cand_steps and _normalize(cand_steps) != _normalize(item.steps_text):
                proposed_steps = cand_steps
                usable = True
            elif cand_steps and _normalize(cand_steps) != _normalize(item.steps_text):
                # 渠道含糊但给了不同步骤，按步骤修复处理。
                channel = "steps"
                proposed_steps = cand_steps
                usable = True
            if usable:
                status = "pending"
                gate = {"allowed": True, "failure_type": failure_type,
                        "analysis_failure_type": model_result.get("failure_type"),
                        "reason": reason, "confidence": model_result.get("confidence")}
            else:
                channel = "none"
                gate = {"allowed": False, "failure_type": failure_type,
                        "analysis_failure_type": "model_failed",
                        "reason": "模型判断可修，但没有给出可用的步骤/前置候选。"}
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
        "original_preconditions": item.preconditions,
        "proposed_preconditions": proposed_preconditions,
        "original_expected": item.expected_result,
        "proposed_expected": proposed_expected,
        "process": process_log,
    }
    return {
        "case_id": item.case_id,
        "status": status,
        "model_name": model_name,
        "original_steps": item.steps_text,
        "proposed_steps": proposed_steps,
        "reason": reason,
        "case_snapshot": {
            "title": item.case_title,
            "path": item.path,
            "preconditions": item.preconditions,
            "steps": item.steps_text,
            "expected_result": item.expected_result,
        },
        "report_snapshot": _report_snapshot(report),
        "gate_snapshot": gate,
        "analysis_trace": trace,
        "raw_payload": raw_payload,
        "diagnosis_snapshot": diagnosis_snapshot,
        "expires_at": datetime.now(UTC) + timedelta(days=7),
    }


def _hard_gate(report: ReportEvidence) -> dict[str, Any]:
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
    # 只硬挡“没报告/读不了”；业务 vs case 不再凭关键词硬判，交给模型结合日志+截图+functionMap 自主判断。
    return {
        "allowed": True,
        "failure_type": report.failure_type,
        "reason": "报告可读，交由模型综合判断。",
    }


def _primary_failure_type(report_failure_type: str | None, analysis_failure_type: str | None) -> str:
    # 优先采信模型分析结论（结合日志+截图+functionMap）；报告关键词只作兜底。
    analysis_type = str(analysis_failure_type or "")
    if analysis_type in {"assertion_failed", "execution_failed", "business_failure", "flaky_failure"}:
        return analysis_type
    report_type = str(report_failure_type or "")
    if report_type in {"assertion_failed", "execution_failed", "business_failure"}:
        return report_type
    return report_type if report_type in FAILURE_LABELS else "unknown_failure"


def _versioned_gate(gate: dict[str, Any]) -> dict[str, Any]:
    return {**gate, "policy_version": REPAIR_POLICY_VERSION}


DIAGNOSE_MAX_ROUNDS = 3
DIAGNOSE_MAX_IMAGES = 6


def _image_data_uri(url: str) -> str | None:
    """下载报告截图并转 base64 data URI（本机图云端模型抓不到，必须内联）。"""
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=15, trust_env=False, follow_redirects=True)
        r.raise_for_status()
    except Exception:
        return None
    ct = (r.headers.get("content-type") or "").lower()
    low = url.lower().split("?")[0]
    if "png" in ct or low.endswith(".png"):
        mime = "image/png"
    elif "webp" in ct or low.endswith(".webp"):
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(r.content).decode()


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
        "confidence": 0,
        "model_name": model,
        "process": [],
    }


async def _analyze_with_model(
    item: RepairInput,
    report: ReportEvidence,
    round1_image_indices: list[int] | None = None,
) -> dict[str, Any]:
    """后台分阶段编排诊断：失败锚点、滑窗取证、根因门禁、修复生成分开执行。"""
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
            model=model, messages=messages, temperature=0.05, max_tokens=max_tokens,
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
    """轨迹型报告：失败在末尾，正文超长时保留头部(意图)+尾部(失败现场)。"""
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[:4000]
    tail = text[-(limit - 4000):]
    return f"{head}\n……（中间轨迹省略）……\n{tail}"


def _round1_payload(
    item: RepairInput,
    report: ReportEvidence,
    multi_end: bool = False,
) -> str:
    """第1轮用户消息的文本载荷：case + 轨迹文本(含 [截图#N] 标记) + functionMap。
    截图本身以 vision 形式单独发送（不放进文本）。多端失败时轨迹已分段拼接、截图统一编号。"""
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
            "这条 case 在多个端都失败了。report.trajectory_text 已把各端轨迹按【主端】【xxx端】分段拼接，"
            "[截图#N] 是跨端统一编号(与 images 对齐)，首轮已给每端的末尾断图，你也可按 index 追加要图。"
            "请综合所有端判断：步骤是各端共享的，只能给【一份】对所有端都安全的修复；"
            "并在 reason 里分别说明每个端是 case 侧可修复 还是 产品侧问题(应提 bug)。"
        )
    return json.dumps(payload, ensure_ascii=False)


def _report_failure_reason(report: ReportEvidence, executor_reason: str = "") -> str:
    executor_reason = str(executor_reason or "").strip()
    report_reason = _failure_reason_from_report(report)
    if executor_reason and _normalize(executor_reason) != _normalize(report.summary or ""):
        return executor_reason
    return report_reason


def _failure_reason_from_report(report: ReportEvidence) -> str:
    text = report.logs_text or ""
    patterns = (
        r"assert_fail\(content=['\"](?P<value>[^'\"]{4,800})['\"]\)",
        r"(?:失败原因|失败信息|错误原因|断言失败|校验失败)[:：]\s*(?P<value>[^\n]{4,800})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group("value").strip()
    return report.summary.strip()


def _report_snapshot(report: ReportEvidence) -> dict[str, Any]:
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


def _draft_to_out(case: CaseAsset, body: CaseBody, draft: CaseRepairDraft) -> RepairDraftOut:
    gate = draft.gate_snapshot or {}
    report = draft.report_snapshot or {}
    path = str(draft.case_snapshot.get("path") or _path(case))
    failure_type = str(
        gate.get("failure_type")
        or gate.get("failureType")
        or report.get("failure_type")
        or "unknown_failure"
    )
    repairable = _draft_is_repairable(draft)
    display_gate = _display_gate(gate, repairable=repairable)
    diagnosis = draft.diagnosis_snapshot or {}
    original_pre = str(diagnosis.get("original_preconditions") or draft.case_snapshot.get("preconditions") or body.preconditions or "")
    return RepairDraftOut(
        draft_id=draft.id,
        case_id=case.id,
        case_title=case.raw_title,
        path=path,
        status=draft.status if repairable else "blocked",
        repairable=repairable,
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
        original_expected=str(diagnosis.get("original_expected") or draft.case_snapshot.get("expected_result") or ""),
        proposed_expected=str(diagnosis.get("proposed_expected") or diagnosis.get("original_expected") or ""),
        report_url=report.get("url"),
        report_summary=str(report.get("summary") or ""),
        model_name=draft.model_name,
        gate=display_gate,
        created_at=draft.created_at,
    )


def _sync_failure_diagnosis(
    work_item: CaseWorkItem,
    gate: dict[str, Any] | None,
    report: dict[str, Any] | None,
    reason: str | None = None,
) -> None:
    failure_type, failure_summary = _failure_diagnosis_from_snapshots(gate, report, reason)
    # 只有分析出明确分类（断言/业务/执行失败）才覆盖；无定论则保留既有“执行失败”默认，不清空。
    if failure_type is not None:
        work_item.failure_type = failure_type
        work_item.failure_summary = failure_summary
    work_item.updated_at = func.now()


def _failure_diagnosis_from_snapshots(
    gate: dict[str, Any] | None,
    report: dict[str, Any] | None,
    reason: str | None = None,
) -> tuple[str | None, str | None]:
    gate = gate or {}
    report = report or {}
    failure_type = str(
        gate.get("failure_type")
        or gate.get("failureType")
        or report.get("failure_type")
        or report.get("failureType")
        or ""
    )
    if failure_type not in PERSISTED_FAILURE_TYPES:
        return None, None
    summary = str(report.get("summary") or reason or "").strip() or None
    return failure_type, summary


def _draft_is_reusable(draft: CaseRepairDraft) -> bool:
    gate = draft.gate_snapshot or {}
    if gate.get("policy_version") != REPAIR_POLICY_VERSION:
        return False
    if "allowed" not in gate:
        return False
    return gate.get("failure_type") not in {"model_unavailable", "model_failed"}


def _draft_is_repairable(draft: CaseRepairDraft) -> bool:
    # status=pending 已由 _prepare_draft_values 保证“确有可用改动（步骤或前置）”，无需再比步骤。
    gate = draft.gate_snapshot or {}
    return draft.status == "pending" and gate.get("allowed") is True


def _blocked_without_draft(case: CaseAsset, body: CaseBody, reason: str) -> RepairDraftOut:
    gate = _display_gate(
        {"allowed": False, "failure_type": "unknown_failure", "reason": reason},
        repairable=False,
    )
    return RepairDraftOut(
        case_id=case.id,
        case_title=case.raw_title,
        path=_path(case),
        status="blocked",
        repairable=False,
        failure_type="不确定",
        reason=reason,
        original_steps=body.steps_text,
        proposed_steps=body.steps_text,
        gate=gate,
    )


async def _replace_steps(session: AsyncSession, case_id: int, steps_text: str) -> None:
    await session.execute(delete(CaseStep).where(CaseStep.case_id == case_id))
    session.add_all(
        CaseStep(case_id=case_id, step_order=index, step_text=step)
        for index, step in enumerate(_split_steps(steps_text), start=1)
    )


async def _replace_raw_node(session: AsyncSession, case: CaseAsset, body: CaseBody) -> None:
    existing = await session.scalar(select(CaseRawNode).where(CaseRawNode.case_id == case.id).limit(1))
    previous_payload = existing.raw_payload if existing and isinstance(existing.raw_payload, dict) else {}
    await session.execute(delete(CaseRawNode).where(CaseRawNode.case_id == case.id))
    session.add(
        CaseRawNode(
            case_id=case.id,
            raw_payload={
                "suite_title": case.suite_title,
                "path_nodes": _case_path_nodes(case),
                "core_nodes": _updated_core_nodes(previous_payload, case, body),
                "core_labels": previous_payload.get("core_labels") or _default_core_labels(),
                "module_name": case.module_name,
                "product_feature": case.product_feature,
                "test_feature": case.test_feature,
                "raw_title": case.raw_title,
                "preconditions": body.preconditions,
                "steps_text": body.steps_text,
                "expected_result": body.expected_result,
            },
        )
    )


def _updated_core_nodes(
    previous_payload: dict[str, Any],
    case: CaseAsset,
    body: CaseBody,
) -> dict[str, dict[str, Any]]:
    previous = previous_payload.get("core_nodes") if isinstance(previous_payload, dict) else {}
    if not isinstance(previous, dict):
        previous = {}
    labels = previous_payload.get("core_labels") if isinstance(previous_payload, dict) else {}
    if not isinstance(labels, dict):
        labels = {}
    values = {
        "case_title": case.raw_title,
        "preconditions": body.preconditions,
        "steps": body.steps_text,
        "expected": body.expected_result,
    }
    result: dict[str, dict[str, Any]] = {}
    for index, (role, value) in enumerate(values.items(), start=1):
        old_node = previous.get(role) if isinstance(previous.get(role), dict) else {}
        label = str(old_node.get("label") or labels.get(role) or _default_core_labels()[role])
        trimmed = bool(old_node.get("trimmed", True))
        separator = old_node.get("separator")
        if separator is None and trimmed:
            separator = "："
        display_text = collapse_inline_text(value)
        raw_text = f"{label}{separator}{display_text}" if trimmed and separator else display_text
        result[role] = {
            "level": old_node.get("level") or index,
            "label": label,
            "rawText": raw_text,
            "displayText": display_text,
            "trimmed": trimmed,
            "separator": separator,
        }
    return result


def _default_core_labels() -> dict[str, str]:
    return {
        "case_title": "测试标题",
        "preconditions": "前置条件",
        "steps": "操作步骤",
        "expected": "预期结果",
    }


def _path(case: CaseAsset) -> str:
    path_nodes = _case_path_nodes(case)
    return " / ".join(_path_node_texts(path_nodes))


def _case_path_nodes(case: CaseAsset) -> list[dict[str, Any]]:
    nodes = getattr(case, "path_nodes", None)
    return [dict(node) for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _path_node_texts(nodes: list[dict[str, Any]]) -> list[str]:
    return [
        str(node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text") or "")
        for node in nodes
        if isinstance(node, dict)
        and (node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text"))
    ]


def _visible_failure_label(failure_type: str | None) -> str:
    return VISIBLE_FAILURE_LABELS.get(str(failure_type or ""), "不确定")


def _display_gate(gate: dict[str, Any], *, repairable: bool) -> dict[str, Any]:
    failure_type = str(gate.get("failure_type") or gate.get("failureType") or "")
    label = "可修复" if repairable else REPAIR_GATE_LABELS.get(failure_type, "不确定")
    return {
        **gate,
        "canRepair": repairable,
        "label": label,
        "reason": str(gate.get("reason") or ""),
    }


def _split_steps(steps_text: str) -> list[str]:
    steps = [line.strip() for line in steps_text.splitlines() if line.strip()]
    return steps or ([steps_text.strip()] if steps_text.strip() else [])


async def _download_key_image(url: str, case_id: int) -> str | None:
    """下载关键失败截图到本地目录，返回可经 /media 访问的相对路径；失败返回 None。"""
    if not url:
        return None
    settings = get_settings()
    base_dir = Path(settings.repair_image_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    suffix = os.path.splitext(url.split("?")[0])[1].lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        suffix = ".png"
    filename = f"case{case_id}_{uuid.uuid4().hex[:8]}{suffix}"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            (base_dir / filename).write_bytes(resp.content)
    except Exception:
        return None
    return f"/media/{filename}"


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "")
