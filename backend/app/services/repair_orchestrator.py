from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ROOT_CAUSE_SYSTEM_PROMPT = (
    "你是测试失败诊断后台编排的【根因阶段】。"
    "本阶段只能围绕 failure_anchor 定位根因和证据，禁止输出修复步骤。"
    "需求/验收标准只能来自 case 与 function_map；轨迹里的思考/观察/自我判断只能作为执行过程证据。"
    "如果需要更多证据，输出 need_more=true，并必须明确填写 request_windows 或 request_images："
    "要文字只填 request_windows，要图片才填 request_images；不要依赖后台猜测或自动补图。"
    "只输出 JSON。"
)

REPAIR_SYSTEM_PROMPT = (
    "你是测试失败诊断后台编排的【修复阶段】。"
    "只有 root_cause_type=case_repairable 时才允许生成修复候选；否则必须 repairable=false。"
    "前置只能写冷启动/登录/环境约束，点击/滑动/输入必须写在 steps。"
    "只输出 JSON。"
)

INITIAL_TEXT_BUDGET = 60000
INITIAL_TAIL_WINDOWS = 40
INITIAL_HIT_NEIGHBORS = 3
EVIDENCE_HIT_MARKERS = (
    "assert_fail",
    "assertion",
    "assert",
    "断言",
    "失败",
    "错误",
    "异常",
    "超时",
    "未找到",
    "找不到",
    "expected",
    "actual",
    "期望",
    "实际",
    "error",
    "exception",
    "timeout",
    "failed",
    "fail",
)


@dataclass(frozen=True)
class RepairDiagnosisInput:
    case_id: int
    case_title: str
    path: str
    preconditions: str
    steps_text: str
    expected_result: str
    report_url: str | None = None
    executor_failure_reason: str = ""
    function_map: str = ""
    multi_end: bool = False


@dataclass(frozen=True)
class EvidenceWindow:
    window_id: str
    title: str
    text: str
    image_indices: tuple[int, ...]


@dataclass(frozen=True)
class InitialWindowSelection:
    windows: list[EvidenceWindow]
    tail_window_ids: list[str]
    hit_window_ids: list[str]


CallModel = Callable[[list[dict[str, Any]]], Awaitable[str]]
BuildImageItem = Callable[[str], Awaitable[dict[str, Any] | None]]


async def run_backend_repair_diagnosis(
    item: RepairDiagnosisInput,
    report: Any,
    model_name: str,
    call_model: CallModel,
    build_image_item: BuildImageItem,
    first_round_image_indices: list[int] | None = None,
    max_rounds: int = 3,
    max_images: int = 6,
) -> dict[str, Any]:
    """Backend-controlled repair diagnosis.

    The backend owns stage order and evidence routing. Model prompts are scoped to
    one stage at a time, so a root-cause guess cannot directly become a repair.
    """
    images = list(getattr(report, "image_urls", []) or [])
    failure_anchor = _report_failure_reason(report, item.executor_failure_reason)
    anchor = _anchor_payload(item, report, failure_anchor)
    windows = _report_evidence_windows(report)
    process: list[dict[str, Any]] = [{"stage": "anchor", **anchor}]

    root_result = await _run_root_cause_stage(
        item=item,
        report=report,
        anchor=anchor,
        windows=windows,
        model_name=model_name,
        call_model=call_model,
        build_image_item=build_image_item,
        first_round_image_indices=first_round_image_indices,
        max_rounds=max_rounds,
        max_images=max_images,
        process=process,
    )
    if not root_result.get("available"):
        return root_result

    root_gate = _gate_root_cause(item, report, anchor, root_result)
    if not root_gate["allowed"]:
        process.append({"stage": "backend_gate", **root_gate})
        return _blocked_result(
            item=item,
            report=report,
            model_name=model_name,
            process=process,
            failure_anchor=failure_anchor,
            root_cause_point=str(root_result.get("root_cause_point") or ""),
            reason=str(root_gate["reason"]),
            evidence=str(root_result.get("evidence") or ""),
            key_image_index=_safe_index(root_result.get("key_image_index"), len(images)),
            failure_type=str(root_gate.get("failure_type") or getattr(report, "failure_type", "") or "unknown_failure"),
            confidence=_safe_int(root_result.get("confidence")),
        )

    repair_result = await _run_repair_stage(
        item=item,
        report=report,
        anchor=anchor,
        root_result=root_result,
        model_name=model_name,
        call_model=call_model,
        process=process,
    )
    if not repair_result.get("available"):
        return repair_result

    repair_gate = _gate_repair_output(item, report, anchor, root_result, repair_result)
    if not repair_gate["allowed"]:
        process.append({"stage": "backend_gate", **repair_gate})
        return _blocked_result(
            item=item,
            report=report,
            model_name=model_name,
            process=process,
            failure_anchor=failure_anchor,
            root_cause_point=str(root_result.get("root_cause_point") or ""),
            reason=str(repair_gate["reason"]),
            evidence=str(root_result.get("evidence") or repair_result.get("evidence") or ""),
            key_image_index=_safe_index(root_result.get("key_image_index"), len(images)),
            failure_type=str(repair_gate.get("failure_type") or repair_result.get("failure_type") or "unknown_failure"),
            confidence=_safe_int(root_result.get("confidence")),
        )

    repair_result["process"] = process
    repair_result.setdefault("report_failure_reason", failure_anchor)
    repair_result.setdefault("root_cause_point", root_result.get("root_cause_point") or "")
    repair_result.setdefault("evidence", root_result.get("evidence") or "")
    return _final_result(repair_result, report, model_name, process)


async def _run_root_cause_stage(
    *,
    item: RepairDiagnosisInput,
    report: Any,
    anchor: dict[str, Any],
    windows: list[EvidenceWindow],
    model_name: str,
    call_model: CallModel,
    build_image_item: BuildImageItem,
    first_round_image_indices: list[int] | None,
    max_rounds: int,
    max_images: int,
    process: list[dict[str, Any]],
) -> dict[str, Any]:
    images = list(getattr(report, "image_urls", []) or [])
    indexed_text_mode = _uses_indexed_text_mode(report)
    if indexed_text_mode and first_round_image_indices:
        first_round = [i for i in first_round_image_indices if 0 <= i < len(images)]
    elif indexed_text_mode:
        first_round = [len(images) - 1] if images else []
    elif first_round_image_indices:
        first_round = [i for i in first_round_image_indices if 0 <= i < len(images)]
    else:
        first_round = list(range(max(0, len(images) - 2), len(images)))
    content = [
        {
            "type": "text",
            "text": json.dumps(_root_cause_initial_payload(item, report, anchor, windows), ensure_ascii=False),
        }
    ]
    shown = await _append_labeled_images(content, images, first_round, build_image_item)
    used_images: set[int] = set(shown)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": ROOT_CAUSE_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
    process.append(
        {
            "stage": "root_cause",
            "round": 1,
            "shown_images": shown,
            "note": (
                "失败锚点 + case/functionMap + 压缩报告文字 + 截图编号 + 失败末图"
                if indexed_text_mode
                else "失败锚点 + case/functionMap + 轨迹窗口目录 + 首轮失败截图"
            ),
        }
    )

    try:
        for rnd in range(1, max_rounds + 1):
            raw = await call_model(messages)
            data = _loads_json(raw)
            process[-1]["raw_decision"] = _compact_decision(data)
            force = rnd >= max_rounds or len(used_images) >= max_images
            if data.get("need_more") and not force:
                query = _need_more_query(data, anchor)
                raw_request_images = [i for i in (data.get("request_images") or []) if isinstance(i, int)]
                request_windows = [str(w) for w in (data.get("request_windows") or []) if str(w).strip()]
                req_images = _new_requested_images(raw_request_images, images, used_images, max_images)
                selected_windows = _select_requested_windows(windows, request_windows)
                process[-1].update(
                    {
                        "decision": "need_more",
                        "note": str(data.get("note") or data.get("question") or ""),
                        "raw_request_images": raw_request_images,
                        "request_images": req_images,
                        "request_windows": request_windows,
                        "selected_windows": [w.window_id for w in selected_windows],
                    }
                )
                if not selected_windows and not req_images:
                    process[-1]["decision"] = "need_more_empty"
                    process[-1]["conclusion_reason"] = "补证据请求未点名有效文字窗口或截图。"
                    return _normalize_root_result(
                        {
                            "need_more": False,
                            "root_cause_type": "insufficient_evidence",
                            "failure_type": data.get("failure_type"),
                            "root_cause_point": str(
                                data.get("question")
                                or data.get("root_cause_point")
                                or "补证据请求为空。"
                            ),
                            "reason": "模型请求补充证据，但没有点名有效文字窗口或截图。",
                            "evidence": "",
                            "key_image_index": None,
                            "confidence": 0,
                        },
                        report,
                        model_name,
                        process,
                    )
                messages.append({"role": "assistant", "content": raw})
                user_content = _evidence_window_content(query, selected_windows, req_images)
                shown_next = await _append_labeled_images(user_content, images, req_images, build_image_item)
                used_images.update(shown_next)
                messages.append({"role": "user", "content": user_content})
                process.append(
                    {
                        "stage": "root_cause",
                        "round": rnd + 1,
                        "shown_images": shown_next,
                        "shown_windows": [w.window_id for w in selected_windows],
                        "note": "后台按问题补充证据窗口",
                    }
                )
                continue
            if data.get("need_more") and force:
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "已到后台证据预算上限。请只基于已给证据输出 need_more=false 的根因 JSON；禁止输出修复步骤。",
                            }
                        ],
                    }
                )
                raw = await call_model(messages)
                data = _loads_json(raw)
                process[-1]["raw_decision"] = _compact_decision(data)
            process[-1]["decision"] = "conclude"
            process[-1]["conclusion_reason"] = str(data.get("reason") or data.get("root_cause_point") or "")
            return _normalize_root_result(data, report, model_name, process)
    except Exception as exc:
        return _model_unavailable(f"模型根因阶段失败：{exc}", model_name, process)
    return _model_unavailable("模型根因阶段未给出结论。", model_name, process)


async def _run_repair_stage(
    *,
    item: RepairDiagnosisInput,
    report: Any,
    anchor: dict[str, Any],
    root_result: dict[str, Any],
    model_name: str,
    call_model: CallModel,
    process: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "stage": "repair",
        "case": _case_payload(item),
        "anchor": anchor,
        "root_cause": {
            "root_cause_type": root_result.get("root_cause_type"),
            "root_cause_point": root_result.get("root_cause_point"),
            "evidence": root_result.get("evidence"),
            "key_image_index": root_result.get("key_image_index"),
        },
        "rules": [
            "只有 root_cause_type=case_repairable 才允许 repairable=true。",
            "修复只能围绕 root_cause_point，不能新增 case/function_map 没有要求的验证目标。",
            "前置只写冷启动/登录/环境约束；操作步骤写入 steps。",
        ],
        "output_schema": {
            "repairable": "boolean",
            "failure_type": "execution_failed|assertion_failed|business_failure|flaky_failure",
            "repair_channel": "steps|preconditions|expected|none",
            "reason": "为什么修/不修",
            "fix_reason": "repairable=true 时说明为什么这样改能修好，否则空串",
            "proposed_steps": "repair_channel=steps 时完整替换步骤，否则空串",
            "proposed_preconditions": "repair_channel=preconditions 时完整替换前置，否则空串",
            "proposed_expected": "repair_channel=expected 时完整替换预期，否则空串",
            "confidence": "0-100",
        },
    }
    messages = [
        {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        raw = await call_model(messages)
        data = _loads_json(raw)
    except Exception as exc:
        return _model_unavailable(f"模型修复阶段失败：{exc}", model_name, process)
    process.append({"stage": "repair", "raw_decision": _compact_decision(data)})
    data["report_failure_reason"] = anchor["failure_anchor"]
    data["root_cause_point"] = root_result.get("root_cause_point") or ""
    data["evidence"] = data.get("evidence") or root_result.get("evidence") or ""
    data["key_image_index"] = data.get("key_image_index", root_result.get("key_image_index"))
    return _final_result(data, report, model_name, process)


def _root_cause_initial_payload(
    item: RepairDiagnosisInput,
    report: Any,
    anchor: dict[str, Any],
    windows: list[EvidenceWindow],
) -> dict[str, Any]:
    images = list(getattr(report, "image_urls", []) or [])
    report_payload: dict[str, Any] = {
        "url": getattr(report, "url", item.report_url or ""),
        "summary": getattr(report, "summary", ""),
        "failure_type_hint": getattr(report, "failure_type", ""),
        "image_index_range": [0, len(images) - 1] if images else [],
    }
    if _uses_indexed_text_mode(report):
        report_payload.update(_indexed_initial_report_payload(report, windows, anchor))
    elif _uses_hybrid_evidence_mode(report):
        report_payload.update(_hybrid_initial_report_payload(report, windows))
    return {
        "stage": "root_cause",
        "case": _case_payload(item),
        "anchor": anchor,
        "report": report_payload,
        "rules": [
            "先围绕 anchor.failure_anchor 定位根因。",
            "本阶段禁止输出 proposed_steps/proposed_preconditions/proposed_expected。",
            "如果判断走错页面/入口，必须指出该页面/入口来自 case 或 function_map；仅轨迹思考不算需求来源。",
            "如果证据不足，输出 need_more=true，并明确给 request_windows 或 request_images；两者都空会被视为无效补证据请求。",
            "非 Hybrid 报告首轮按失败现场优先提供：failure_anchor 命中块、报告尾部文字、截图编号和少量失败末图。",
            "需要更多文字时只用 request_windows 点名 window_id；非 Hybrid blocks 报告可点名 block 或 block 区间。",
            "需要更多图片时只用 request_images 点名截图 index；后台不会因为你请求文字而自动补图。",
        ],
        "output_schema": {
            "need_more": "boolean",
            "question": "need_more=true 时说明要确认什么",
            "request_images": "需要看图片时填写 [截图index...]；只要文字时必须为空",
            "request_windows": "需要看文字时填写 [window_id 或 block_<开始>-block_<结束>...]；只要图片时可空",
            "root_cause_type": "case_repairable|product_mismatch|business_failure|flaky|insufficient_evidence",
            "root_cause_point": "围绕 failure_anchor 定位的具体点",
            "failure_type": "execution_failed|assertion_failed|business_failure|flaky_failure",
            "evidence": "关键证据，注明来自 case/function_map/动作/截图/断言，不能只引用思考",
            "key_image_index": "最关键截图 index 或 null",
            "confidence": "0-100",
        },
    }


def _anchor_payload(item: RepairDiagnosisInput, report: Any, failure_anchor: str) -> dict[str, Any]:
    requirement_text = _requirement_text(item)
    return {
        "failure_anchor": failure_anchor,
        "verification_target": item.steps_text,
        "expected_standard": item.expected_result,
        "failure_type_hint": getattr(report, "failure_type", ""),
        "requirement_terms": sorted(_extract_terms(requirement_text))[:80],
        "allowed_requirement_sources": ["case", "function_map"],
        "forbidden_requirement_sources": ["trajectory_thought", "trajectory_observation_self_judgement"],
    }


def _case_payload(item: RepairDiagnosisInput) -> dict[str, Any]:
    return {
        "title": item.case_title,
        "path": item.path,
        "preconditions": item.preconditions,
        "steps": item.steps_text,
        "expected_result": item.expected_result,
        "function_map": item.function_map or "",
        "multi_end": item.multi_end,
    }


def _gate_root_cause(
    item: RepairDiagnosisInput,
    report: Any,
    anchor: dict[str, Any],
    root_result: dict[str, Any],
) -> dict[str, Any]:
    root_type = str(root_result.get("root_cause_type") or "").strip()
    if root_type != "case_repairable":
        return {
            "allowed": False,
            "failure_type": root_result.get("failure_type") or _non_repair_failure_type(root_type, report),
            "reason": str(root_result.get("reason") or root_result.get("root_cause_point") or "后台判定不是 case 侧可修复问题。"),
        }
    unsupported = _unsupported_trajectory_terms(
        item,
        report,
        anchor["failure_anchor"],
        " ".join(
            [
                str(root_result.get("root_cause_point") or ""),
                str(root_result.get("reason") or ""),
                str(root_result.get("evidence") or ""),
            ]
        ),
    )
    if unsupported:
        return {
            "allowed": False,
            "failure_type": getattr(report, "failure_type", "") or "assertion_failed",
            "reason": (
                "根因引入了原 case/functionMap 未声明的验证目标或入口："
                f"{'、'.join(unsupported)}。这些只来自执行轨迹，不能作为需求来源。"
            ),
        }
    return {"allowed": True, "reason": "根因阶段通过后台门禁。"}


def _gate_repair_output(
    item: RepairDiagnosisInput,
    report: Any,
    anchor: dict[str, Any],
    root_result: dict[str, Any],
    repair_result: dict[str, Any],
) -> dict[str, Any]:
    if root_result.get("root_cause_type") != "case_repairable" and repair_result.get("repairable"):
        return {
            "allowed": False,
            "failure_type": repair_result.get("failure_type") or getattr(report, "failure_type", "") or "unknown_failure",
            "reason": "根因阶段未判定为 case 侧可修，修复阶段不能生成候选。",
        }
    if not repair_result.get("repairable"):
        return {
            "allowed": False,
            "failure_type": repair_result.get("failure_type") or getattr(report, "failure_type", "") or "unknown_failure",
            "reason": str(repair_result.get("reason") or "模型修复阶段判定不可修。"),
        }
    unsupported = _unsupported_trajectory_terms(
        item,
        report,
        anchor["failure_anchor"],
        " ".join(
            [
                str(repair_result.get("reason") or ""),
                str(repair_result.get("fix_reason") or ""),
                str(repair_result.get("proposed_steps") or ""),
                str(repair_result.get("proposed_preconditions") or ""),
                str(repair_result.get("proposed_expected") or ""),
            ]
        ),
    )
    if unsupported:
        return {
            "allowed": False,
            "failure_type": repair_result.get("failure_type") or getattr(report, "failure_type", "") or "unknown_failure",
            "reason": (
                "修复候选引入了原 case/functionMap 未声明的验证目标或入口："
                f"{'、'.join(unsupported)}。这些只来自执行轨迹，不能作为需求来源。"
            ),
        }
    return {"allowed": True, "reason": "修复阶段通过后台门禁。"}


def _unsupported_trajectory_terms(
    item: RepairDiagnosisInput,
    report: Any,
    failure_anchor: str,
    candidate_text: str,
) -> list[str]:
    requirement_text = _norm(_requirement_text(item))
    anchor_text = _norm(failure_anchor)
    trajectory_text = _norm(str(getattr(report, "logs_text", "") or ""))
    unsupported: list[str] = []
    for term in _extract_terms(candidate_text):
        normalized = _norm(term)
        if len(normalized) < 2:
            continue
        if normalized in requirement_text or normalized in anchor_text:
            continue
        if normalized in trajectory_text and _term_is_requirement_like(term, candidate_text):
            unsupported.append(term)
    return sorted(dict.fromkeys(unsupported))


def _term_is_requirement_like(term: str, text: str) -> bool:
    idx = text.find(term)
    if idx < 0:
        return False
    window = text[max(0, idx - 24) : idx + len(term) + 32]
    markers = (
        "必须",
        "需要",
        "目标",
        "正确",
        "不符合要求",
        "修复",
        "补充",
        "确认进入",
        "进入",
        "跳转",
        "页面",
        "入口",
        "导航",
        "后台",
        "步骤",
    )
    return any(marker in window for marker in markers)


def _extract_terms(text: str) -> set[str]:
    result: set[str] = set()
    for pattern in (r"「([^」]{2,40})」", r"“([^”]{2,40})”", r"'([^']{2,40})'", r'"([^"]{2,40})"'):
        result.update(match.strip() for match in re.findall(pattern, str(text or "")) if match.strip())
    return result


def _requirement_text(item: RepairDiagnosisInput) -> str:
    return "\n".join(
        [
            item.case_title or "",
            item.path or "",
            item.preconditions or "",
            item.steps_text or "",
            item.expected_result or "",
            item.function_map or "",
        ]
    )


def _uses_indexed_text_mode(report: Any) -> bool:
    return bool(getattr(report, "blocks", None)) and str(getattr(report, "reader", "") or "").lower() != "ai_hybrid"


def _uses_hybrid_evidence_mode(report: Any) -> bool:
    return str(getattr(report, "reader", "") or "").lower() == "ai_hybrid"


def _indexed_initial_report_payload(
    report: Any,
    windows: list[EvidenceWindow],
    anchor: dict[str, Any],
) -> dict[str, Any]:
    text_windows = [window for window in windows if window.window_id.startswith("block_")]
    selection = _select_initial_text_windows(text_windows, str(anchor.get("failure_anchor") or ""))
    selected_windows = selection.windows
    evidence_text, text_truncated = _render_initial_evidence_text(selected_windows)
    image_catalog = _image_index_catalog(list(getattr(report, "blocks", []) or []))
    selected_window_ids = [window.window_id for window in selected_windows]
    return {
        "reading_mode": "indexed_failure_tail_first",
        "evidence_strategy": "首轮先看失败现场：失败锚点命中块 + 报告尾部文字 + 失败末图；其余文字/图片按索引补充。",
        "stats": {
            "block_count": len(getattr(report, "blocks", []) or []),
            "text_window_count": len(text_windows),
            "selected_text_window_count": len(selected_windows),
            "omitted_text_window_count": max(0, len(text_windows) - len(selected_windows)),
            "image_count": len(getattr(report, "image_urls", []) or []),
            "char_budget": INITIAL_TEXT_BUDGET,
            "tail_window_count": min(INITIAL_TAIL_WINDOWS, len(text_windows)),
            "hit_neighbor_count": INITIAL_HIT_NEIGHBORS,
            "text_truncated": text_truncated,
        },
        "text_menu": {
            "window_id_format": "block_<文字块编号>",
            "available_text_window_range": _window_id_range(text_windows),
            "selected_window_ids": selected_window_ids,
            "tail_window_ids": selection.tail_window_ids,
            "hit_window_ids": selection.hit_window_ids,
            "note": "首轮不默认给开头普通步骤；如果需要环境/登录/前置上下文，可在 request_windows 里点名 block 或 block 区间。",
        },
        "evidence_text": evidence_text,
        "image_catalog": image_catalog,
        "window_catalog": [_window_catalog_item(window) for window in selected_windows],
        "request_hint": (
            "如需补证据，request_windows 使用 block_<文字块编号> 或 block_<开始>-block_<结束>；"
            "如需看图，request_images 使用截图编号。"
        ),
    }


def _hybrid_initial_report_payload(report: Any, windows: list[EvidenceWindow]) -> dict[str, Any]:
    evidence_text, text_truncated = _render_initial_evidence_text(windows)
    return {
        "reading_mode": "ai_hybrid_structured_summary",
        "evidence_strategy": (
            "AI Hybrid 总报告已从 HTML 内嵌 JSON 解析；首轮和后续补证据都只使用 "
            "hybrid_total_report 这一条专用证据窗口，不回退到普通 blocks 菜单。"
        ),
        "stats": {
            "window_count": len(windows),
            "image_count": len(getattr(report, "image_urls", []) or []),
            "text_truncated": text_truncated,
        },
        "evidence_text": evidence_text,
        "window_catalog": [_window_catalog_item(w) for w in windows[:40]],
        "request_hint": "如需复看 AI Hybrid 总报告，request_windows 使用 hybrid_total_report；如需看图，request_images 使用截图编号。",
    }


def _select_initial_text_windows(windows: list[EvidenceWindow], failure_anchor: str) -> InitialWindowSelection:
    if not windows:
        return InitialWindowSelection(windows=[], tail_window_ids=[], hit_window_ids=[])
    total = len(windows)
    tail_indices = set(range(max(0, total - INITIAL_TAIL_WINDOWS), total))
    hit_indices: set[int] = set()

    for idx, window in enumerate(windows):
        if _initial_window_is_hit(window, failure_anchor):
            start = max(0, idx - INITIAL_HIT_NEIGHBORS)
            end = min(total, idx + INITIAL_HIT_NEIGHBORS + 1)
            hit_indices.update(range(start, end))

    selected_indices = tail_indices | hit_indices
    selected_windows = [window for idx, window in enumerate(windows) if idx in selected_indices]
    return InitialWindowSelection(
        windows=selected_windows,
        tail_window_ids=[windows[idx].window_id for idx in sorted(tail_indices)],
        hit_window_ids=[windows[idx].window_id for idx in sorted(hit_indices)],
    )


def _initial_window_is_hit(window: EvidenceWindow, failure_anchor: str) -> bool:
    text = f"{window.title}\n{window.text}".lower()
    if any(marker.lower() in text for marker in EVIDENCE_HIT_MARKERS):
        return True
    for term in _query_terms(failure_anchor):
        if term and term.lower() in text:
            return True
    return False


def _render_initial_evidence_text(windows: list[EvidenceWindow]) -> tuple[str, bool]:
    parts: list[str] = []
    used = 0
    truncated = False
    for window in windows:
        rendered = _render_initial_window(window)
        if used + len(rendered) > INITIAL_TEXT_BUDGET:
            remaining = max(0, INITIAL_TEXT_BUDGET - used)
            if remaining > 300:
                parts.append(rendered[:remaining] + "\n...（首轮文字预算已满，可按 block 编号继续 request_windows）")
            truncated = True
            break
        parts.append(rendered)
        used += len(rendered)
    return "\n\n".join(parts), truncated


def _render_initial_window(window: EvidenceWindow) -> str:
    image_note = f"；关联截图 {', '.join(f'#{idx}' for idx in window.image_indices)}" if window.image_indices else ""
    return f"[{window.window_id}] {window.title}{image_note}\n{window.text}"


def _image_index_catalog(blocks: list[Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    last_text_block: int | None = None
    for position, block in enumerate(blocks):
        kind = str(getattr(block, "kind", "") or "").strip().lower()
        if kind == "text":
            last_text_block = _block_index(block, position)
            continue
        if kind != "image":
            continue
        image_index = _block_image_index(block)
        if image_index is None:
            continue
        catalog.append(
            {
                "image_index": image_index,
                "block_index": _block_index(block, position),
                "near_text_window": f"block_{last_text_block}" if last_text_block is not None else "",
            }
        )
    return catalog


def _report_evidence_windows(report: Any) -> list[EvidenceWindow]:
    if _uses_hybrid_evidence_mode(report):
        return _hybrid_evidence_windows(report)
    blocks = list(getattr(report, "blocks", []) or [])
    if blocks:
        indexed = _indexed_evidence_windows(blocks)
        if indexed:
            return indexed
    return []


def _hybrid_evidence_windows(report: Any) -> list[EvidenceWindow]:
    text = str(getattr(report, "logs_text", "") or "").strip()
    image_count = len(getattr(report, "image_urls", []) or [])
    image_indices = tuple(dict.fromkeys(_all_image_indices(text) or list(range(image_count))))
    if not text and not image_indices:
        return []
    return [
        EvidenceWindow(
            window_id="hybrid_total_report",
            title="AI Hybrid 总报告",
            text=text,
            image_indices=image_indices,
        )
    ]


def _indexed_evidence_windows(blocks: list[Any]) -> list[EvidenceWindow]:
    windows: list[EvidenceWindow] = []
    for position, block in enumerate(blocks):
        kind = str(getattr(block, "kind", "") or "").strip().lower()
        block_index = _block_index(block, position)
        if kind == "text":
            text = str(getattr(block, "text", "") or "").strip()
            if not text:
                continue
            context_text, image_indices = _block_context(blocks, position)
            windows.append(
                EvidenceWindow(
                    window_id=f"block_{block_index}",
                    title=_text_block_title(block_index, text),
                    text=context_text,
                    image_indices=image_indices,
                )
            )
        elif kind == "image":
            image_index = _block_image_index(block)
            if image_index is None:
                continue
            context_text, image_indices = _block_context(blocks, position)
            windows.append(
                EvidenceWindow(
                    window_id=f"image_{image_index}",
                    title=f"截图 #{image_index}",
                    text=context_text or f"[截图#{image_index}]",
                    image_indices=image_indices or (image_index,),
                )
            )
    return windows


def _block_context(blocks: list[Any], position: int) -> tuple[str, tuple[int, ...]]:
    parts: list[str] = []
    image_indices: list[int] = []

    def add_text(idx: int) -> None:
        block = blocks[idx]
        text = str(getattr(block, "text", "") or "").strip()
        if text:
            parts.append(f"[文字块#{_block_index(block, idx)}]\n{_shorten(text, 6000)}")

    def add_image(idx: int) -> None:
        block = blocks[idx]
        image_index = _block_image_index(block)
        if image_index is None:
            return
        image_indices.append(image_index)
        parts.append(f"[截图#{image_index}]")

    kind = str(getattr(blocks[position], "kind", "") or "").strip().lower()
    if kind == "text":
        add_text(position)
        idx = position + 1
        while idx < len(blocks) and str(getattr(blocks[idx], "kind", "") or "").strip().lower() == "image":
            add_image(idx)
            idx += 1
    elif kind == "image":
        start = position
        while start > 0 and str(getattr(blocks[start - 1], "kind", "") or "").strip().lower() == "image":
            start -= 1
        text_idx = start - 1
        if text_idx >= 0 and str(getattr(blocks[text_idx], "kind", "") or "").strip().lower() == "text":
            add_text(text_idx)
        idx = start
        while idx < len(blocks) and str(getattr(blocks[idx], "kind", "") or "").strip().lower() == "image":
            add_image(idx)
            idx += 1
    deduped_images = tuple(dict.fromkeys(image_indices))
    return _shorten("\n\n".join(parts), 4000), deduped_images


def _block_index(block: Any, fallback: int) -> int:
    try:
        return int(getattr(block, "index", fallback))
    except (TypeError, ValueError):
        return fallback


def _block_image_index(block: Any) -> int | None:
    value = getattr(block, "image_index", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_block_title(block_index: int, text: str) -> str:
    first_line = next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")
    if not first_line:
        return f"文字块 #{block_index}"
    return f"文字块 #{block_index}：{_shorten(first_line, 40)}"


def _all_image_indices(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\[截图#(\d+)\]", text or "")]


def _window_catalog_item(window: EvidenceWindow) -> dict[str, Any]:
    return {
        "window_id": window.window_id,
        "title": window.title,
        "image_indices": list(window.image_indices),
        "excerpt": _shorten(window.text, 500),
    }


def _window_id_range(windows: list[EvidenceWindow]) -> list[str]:
    if not windows:
        return []
    return [windows[0].window_id, windows[-1].window_id]


def _select_requested_windows(windows: list[EvidenceWindow], request_windows: list[str]) -> list[EvidenceWindow]:
    if not request_windows:
        return []
    by_id = {w.window_id: w for w in windows}
    result: list[EvidenceWindow] = []
    seen: set[str] = set()
    for value in request_windows:
        value = value.strip()
        if value in by_id:
            if value not in seen:
                result.append(by_id[value])
                seen.add(value)
            continue
        for window in _windows_from_range(windows, value):
            if window.window_id in seen:
                continue
            result.append(window)
            seen.add(window.window_id)
    return result


def _windows_from_range(windows: list[EvidenceWindow], value: str) -> list[EvidenceWindow]:
    match = re.fullmatch(r"block_(\d+)\s*(?:-|\.\.|~|到|至)\s*block_(\d+)", value)
    if not match:
        return []
    start = int(match.group(1))
    end = int(match.group(2))
    low, high = (start, end) if start <= end else (end, start)
    selected = [
        window
        for window in windows
        if window.window_id.startswith("block_") and low <= _window_block_number(window.window_id) <= high
    ]
    return selected[: max(1, INITIAL_TAIL_WINDOWS)]


def _window_block_number(window_id: str) -> int:
    match = re.fullmatch(r"block_(\d+)", window_id)
    if not match:
        return -1
    return int(match.group(1))


def _query_terms(query: str) -> set[str]:
    terms = set(_extract_terms(query))
    for token in re.findall(r"[A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,8}", query or ""):
        if token not in {"需要确认", "判断是否", "为什么", "是否成功", "当前", "本次"}:
            terms.add(token)
    return terms


def _new_requested_images(
    raw_images: list[int],
    images: list[str],
    used_images: set[int],
    max_images: int,
) -> list[int]:
    remaining = max(0, max_images - len(used_images))
    result: list[int] = []
    for idx in raw_images:
        if 0 <= idx < len(images) and idx not in used_images and idx not in result:
            result.append(idx)
        if len(result) >= remaining:
            break
    return result


def _evidence_window_content(query: str, windows: list[EvidenceWindow], image_indices: list[int]) -> list[dict[str, Any]]:
    payload = {
        "stage": "evidence_window",
        "question": query,
        "note": "以下是后台按你的问题补充的证据窗口。窗口只说明执行过程，不自动成为需求来源。",
        "windows": [
            {
                "window_id": window.window_id,
                "title": window.title,
                "image_indices": list(window.image_indices),
                "text": window.text,
            }
            for window in windows
        ],
        "provided_images": image_indices,
        "next_output_schema": {
            "need_more": "boolean",
            "root_cause_type": "case_repairable|product_mismatch|business_failure|flaky|insufficient_evidence",
            "root_cause_point": "围绕 failure_anchor 定位的具体点",
            "failure_type": "execution_failed|assertion_failed|business_failure|flaky_failure",
            "evidence": "关键证据",
            "key_image_index": "截图 index 或 null",
            "confidence": "0-100",
        },
    }
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]


async def _append_labeled_images(
    content: list[dict[str, Any]],
    images: list[str],
    indices: list[int],
    build_image_item: BuildImageItem,
) -> list[int]:
    shown: list[int] = []
    for idx in indices:
        if not (0 <= idx < len(images)):
            continue
        item = await build_image_item(images[idx])
        if item:
            content.append({"type": "text", "text": f"[截图#{idx}]"})
            content.append(item)
            shown.append(idx)
    return shown


def _need_more_query(data: dict[str, Any], anchor: dict[str, Any]) -> str:
    return " ".join(
        str(data.get(key) or "")
        for key in ("question", "note", "root_cause_point", "reason", "evidence")
    ).strip() or str(anchor.get("failure_anchor") or "")


def _normalize_root_result(data: dict[str, Any], report: Any, model_name: str, process: list[dict[str, Any]]) -> dict[str, Any]:
    root_type = str(data.get("root_cause_type") or "").strip()
    if not root_type:
        root_type = "case_repairable" if data.get("repairable") else _root_type_from_failure(data.get("failure_type"), report)
    return {
        "available": True,
        "repairable": root_type == "case_repairable",
        "root_cause_type": root_type,
        "failure_type": _normalize_failure_type(data.get("failure_type"), report),
        "repair_channel": "none",
        "report_failure_reason": "",
        "root_cause_point": str(data.get("root_cause_point") or data.get("reason") or ""),
        "reason": str(data.get("reason") or data.get("root_cause_point") or ""),
        "fix_reason": "",
        "evidence": str(data.get("evidence") or ""),
        "key_image_index": _safe_index(data.get("key_image_index"), len(getattr(report, "image_urls", []) or [])),
        "proposed_steps": "",
        "proposed_preconditions": "",
        "proposed_expected": "",
        "confidence": _safe_int(data.get("confidence")),
        "model_name": model_name,
        "process": process,
    }


def _final_result(data: dict[str, Any], report: Any, model_name: str, process: list[dict[str, Any]]) -> dict[str, Any]:
    channel = str(data.get("repair_channel") or "").strip().lower()
    if channel not in {"steps", "preconditions", "expected", "none"}:
        channel = "steps" if data.get("repairable") else "none"
    return {
        "available": True,
        "repairable": bool(data.get("repairable")),
        "failure_type": _normalize_failure_type(data.get("failure_type"), report),
        "repair_channel": channel,
        "report_failure_reason": str(data.get("report_failure_reason") or "").strip(),
        "root_cause_point": str(data.get("root_cause_point") or "").strip(),
        "reason": str(data.get("reason") or "模型未给出原因。"),
        "fix_reason": str(data.get("fix_reason") or "").strip(),
        "evidence": str(data.get("evidence") or "").strip(),
        "key_image_index": _safe_index(data.get("key_image_index"), len(getattr(report, "image_urls", []) or [])),
        "proposed_steps": str(data.get("proposed_steps") or ""),
        "proposed_preconditions": str(data.get("proposed_preconditions") or ""),
        "proposed_expected": str(data.get("proposed_expected") or ""),
        "confidence": _safe_int(data.get("confidence")),
        "model_name": model_name,
        "process": process,
    }


def _blocked_result(
    *,
    item: RepairDiagnosisInput,
    report: Any,
    model_name: str,
    process: list[dict[str, Any]],
    failure_anchor: str,
    root_cause_point: str,
    reason: str,
    evidence: str,
    key_image_index: int | None,
    failure_type: str,
    confidence: int,
) -> dict[str, Any]:
    return {
        "available": True,
        "repairable": False,
        "failure_type": failure_type if failure_type in _failure_types() else _normalize_failure_type(failure_type, report),
        "repair_channel": "none",
        "report_failure_reason": failure_anchor,
        "root_cause_point": root_cause_point,
        "reason": reason,
        "fix_reason": "",
        "evidence": evidence,
        "key_image_index": key_image_index,
        "proposed_steps": "",
        "proposed_preconditions": "",
        "proposed_expected": "",
        "confidence": confidence,
        "model_name": model_name,
        "process": process,
    }


def _model_unavailable(reason: str, model_name: str | None, process: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "available": False,
        "repairable": False,
        "failure_type": "model_unavailable" if model_name is None else "model_failed",
        "repair_channel": "none",
        "reason": reason,
        "fix_reason": "",
        "evidence": "",
        "proposed_steps": "",
        "proposed_preconditions": "",
        "proposed_expected": "",
        "confidence": 0,
        "model_name": model_name,
        "process": process,
    }


def _normalize_failure_type(value: object, report: Any) -> str:
    failure_type = str(value or getattr(report, "failure_type", "") or "unknown_failure")
    return failure_type if failure_type in _failure_types() else "unknown_failure"


def _non_repair_failure_type(root_type: str, report: Any) -> str:
    if root_type == "product_mismatch":
        return "assertion_failed"
    if root_type == "business_failure":
        return "business_failure"
    if root_type == "flaky":
        return "flaky_failure"
    return _normalize_failure_type(getattr(report, "failure_type", ""), report)


def _root_type_from_failure(value: object, report: Any) -> str:
    failure_type = _normalize_failure_type(value, report)
    if failure_type == "execution_failed":
        return "case_repairable"
    if failure_type == "business_failure":
        return "business_failure"
    if failure_type == "flaky_failure":
        return "flaky"
    if failure_type == "assertion_failed":
        return "product_mismatch"
    return "insufficient_evidence"


def _failure_types() -> set[str]:
    return {"execution_failed", "assertion_failed", "business_failure", "flaky_failure", "unknown_failure"}


def _report_failure_reason(report: Any, executor_reason: str = "") -> str:
    executor_reason = str(executor_reason or "").strip()
    report_reason = _failure_reason_from_report(report)
    if executor_reason and _norm(executor_reason) != _norm(str(getattr(report, "summary", "") or "")):
        return executor_reason
    return report_reason


def _failure_reason_from_report(report: Any) -> str:
    text = str(getattr(report, "logs_text", "") or "")
    patterns = (
        r"assert_fail\(content=['\"](?P<value>[^'\"]{4,800})['\"]\)",
        r"(?:失败原因|失败信息|错误原因|断言失败|校验失败)[:：]\s*(?P<value>[^\n]{4,800})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group("value").strip()
    return str(getattr(report, "summary", "") or "").strip()


def _loads_json(raw: str) -> dict[str, Any]:
    candidate = _extract_json(raw)
    attempts = [candidate]
    sanitized = candidate.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    sanitized = re.sub(r",\s*([}\]])", r"\1", sanitized)
    attempts.append(sanitized)
    for text in attempts:
        try:
            data = json.loads(text)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("无法从模型输出解析出 JSON")


def _extract_json(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return cleaned[start : end + 1]
    return cleaned


def _safe_int(value: object) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _safe_index(value: object, image_count: int) -> int | None:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    return idx if 0 <= idx < image_count else None


def selected_key_image_url(images: list[str], key_image_index: object) -> str:
    idx = _safe_index(key_image_index, len(images))
    return images[idx] if idx is not None else ""


def _compact_decision(data: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "need_more",
        "question",
        "note",
        "request_images",
        "request_windows",
        "root_cause_type",
        "root_cause_point",
        "failure_type",
        "repairable",
        "repair_channel",
        "reason",
        "key_image_index",
        "confidence",
    )
    return {key: data.get(key) for key in keys if key in data}


def _shorten(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()
