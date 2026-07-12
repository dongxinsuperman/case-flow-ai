from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from difflib import SequenceMatcher
from typing import Any

from app.core.settings import get_settings
from app.services.markdown_parser import ParsedCase, ParsedMarkdown

MODEL_TOP_N = int(os.environ.get("CASE_FLOW_CASE_MATCH_MODEL_TOP_N", "3"))
PRIMARY_MATCH_THRESHOLD = int(os.environ.get("CASE_FLOW_CASE_MATCH_PRIMARY_THRESHOLD", "80"))


def build_import_review(
    parsed: ParsedMarkdown,
    existing_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    existing_by_digest: dict[str, list[dict[str, Any]]] = {}
    for case in existing_cases:
        existing_by_digest.setdefault(case_digest(case), []).append(case)

    exact_count = 0
    exact_matched_old_ids: set[int] = set()
    review_items: list[dict[str, Any]] = []

    for parsed_case in parsed.cases:
        incoming = parsed_case_to_dict(parsed_case)
        digest = case_digest(incoming)
        exact_candidates = existing_by_digest.get(digest) or []
        if exact_candidates:
            matched_old = exact_candidates.pop(0)
            exact_matched_old_ids.add(int(matched_old["id"]))
            exact_count += 1
            continue

        review_items.append(
            {
                "incoming_key": incoming_case_key(parsed_case, digest),
                "digest": digest,
                "incoming": incoming,
                "candidates": top_candidates(
                    incoming,
                    [
                        case
                        for case in existing_cases
                        if int(case["id"]) not in exact_matched_old_ids
                    ],
                    MODEL_TOP_N,
                ),
                "model_used": False,
                "model_summary": "",
            }
        )

    enrich_with_model(review_items)
    primary_old_ids = assign_primary_matches(review_items)
    prune_candidates_after_primary_match(review_items)

    delete_items = [
        {
            "delete_key": f"delete:{case['id']}",
            "old_case_id": case["id"],
            "old_case": compact_case(case),
            "reason": "同文件二次导入的新快照中未出现该旧 case，且它未被任何新 case 锁定为 1:1 主候选。",
        }
        for case in existing_cases
        if int(case["id"]) not in exact_matched_old_ids
        and int(case["id"]) not in primary_old_ids
    ]

    return {
        "suite_title": parsed.suite_title,
        "case_count": len(parsed.cases),
        "exact_count": exact_count,
        "review_count": len(review_items),
        "delete_count": len(delete_items),
        "primary_match_threshold": PRIMARY_MATCH_THRESHOLD,
        "model_top_n": MODEL_TOP_N,
        "review_items": review_items,
        "delete_items": delete_items,
    }


def assign_primary_matches(review_items: list[dict[str, Any]]) -> set[int]:
    pairs: list[tuple[float, int, int]] = []
    for item_index, item in enumerate(review_items):
        model_best_id = int(item.get("model_best_case_id") or 0)
        model_similarity = number_or_none(item.get("model_similarity"))
        model_should_lock = bool(item.get("model_should_lock"))
        for candidate in item.get("candidates") or []:
            old_case_id = int(candidate.get("case_id") or 0)
            if not old_case_id:
                continue
            local_score = float(candidate.get("similarity") or 0)
            model_locks_candidate = (
                model_should_lock
                and model_best_id == old_case_id
                and model_similarity is not None
            )
            score = local_score
            if model_locks_candidate:
                score = max(local_score, model_similarity, float(PRIMARY_MATCH_THRESHOLD))
            if local_score >= PRIMARY_MATCH_THRESHOLD or model_locks_candidate:
                pairs.append((score, item_index, old_case_id))

    pairs.sort(key=lambda row: (-row[0], row[1], row[2]))
    assigned_items: set[int] = set()
    assigned_old_ids: set[int] = set()
    for score, item_index, old_case_id in pairs:
        if item_index in assigned_items or old_case_id in assigned_old_ids:
            continue
        review_items[item_index]["primary_old_case_id"] = old_case_id
        review_items[item_index]["primary_similarity"] = round(score, 1)
        assigned_items.add(item_index)
        assigned_old_ids.add(old_case_id)
    return assigned_old_ids


def prune_candidates_after_primary_match(review_items: list[dict[str, Any]]) -> None:
    primary_owner_by_old_id = {
        int(item["primary_old_case_id"]): index
        for index, item in enumerate(review_items)
        if item.get("primary_old_case_id")
    }
    for item_index, item in enumerate(review_items):
        filtered = []
        for candidate in item.get("candidates") or []:
            old_case_id = int(candidate.get("case_id") or 0)
            owner_index = primary_owner_by_old_id.get(old_case_id)
            if owner_index is None or owner_index == item_index:
                filtered.append(candidate)
        item["candidates"] = filtered


def number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parsed_case_to_dict(case: ParsedCase) -> dict[str, Any]:
    data = asdict(case)
    data["path"] = join_path(data)
    return data


def incoming_case_key(case: ParsedCase, digest: str) -> str:
    return f"{case.ordinal}:{digest[:16]}"


def case_digest(case: dict[str, Any]) -> str:
    payload = {
        "path_nodes": [normalize_text(value) for value in path_values(case)],
        "raw_title": normalize_text(case.get("raw_title")),
        "preconditions": normalize_text(case.get("preconditions")),
        "steps_text": normalize_text(case.get("steps_text")),
        "expected_result": normalize_text(case.get("expected_result")),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", "", text)
    return text.strip().lower()


def top_candidates(
    incoming: dict[str, Any],
    existing_cases: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    candidates = [
        {
            "case_id": case["id"],
            "ordinal": case.get("ordinal"),
            "raw_title": case.get("raw_title"),
            "path": join_path(case),
            "path_nodes": case.get("path_nodes") or [],
            "module_name": case.get("module_name"),
            "product_feature": case.get("product_feature"),
            "test_feature": case.get("test_feature"),
            "preconditions": case.get("preconditions"),
            "steps_text": case.get("steps_text"),
            "expected_result": case.get("expected_result"),
            "similarity": round(case_similarity(incoming, case), 1),
            "change_hint": local_change_hint(incoming, case),
        }
        for case in existing_cases
    ]
    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    return candidates[:limit]


def case_similarity(incoming: dict[str, Any], existing: dict[str, Any]) -> float:
    path_score = ratio(join_path(incoming), join_path(existing))
    title_score = ratio(incoming.get("raw_title"), existing.get("raw_title"))
    pre_score = ratio(incoming.get("preconditions"), existing.get("preconditions"))
    steps_score = ratio(incoming.get("steps_text"), existing.get("steps_text"))
    expected_score = ratio(incoming.get("expected_result"), existing.get("expected_result"))
    return (
        path_score * 0.20
        + title_score * 0.25
        + pre_score * 0.10
        + steps_score * 0.30
        + expected_score * 0.15
    )


def ratio(left: Any, right: Any) -> float:
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    if not left_text and not right_text:
        return 100.0
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio() * 100


def join_path(case: dict[str, Any]) -> str:
    return " / ".join(path_values(case))


def path_values(case: dict[str, Any]) -> list[str]:
    nodes = case.get("path_nodes") or []
    if isinstance(nodes, list):
        return [
            str(
                (node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text") or "")
            )
            for node in nodes
            if isinstance(node, dict)
            and (node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text"))
        ]
    return []


CHANGE_HINT_FIELDS = (
    ("raw_title", "标题"),
    ("preconditions", "前置条件"),
    ("steps_text", "操作步骤"),
    ("expected_result", "预期结果"),
)


def _readable(value: Any) -> str:
    """保留大小写、把连续空白压成单空格——用于做可读的方向化差异。"""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def field_diff_hint(label: str, new_value: Any, old_value: Any, limit: int = 40) -> str:
    """方向化字段差异。固定基准=新导入(new) 对比 旧(old)：多了/少了/替换。"""
    old_text = _readable(old_value)
    new_text = _readable(new_value)
    matcher = SequenceMatcher(None, old_text, new_text)
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete") and old_text[i1:i2].strip():
            removed.append(old_text[i1:i2])
        if tag in ("replace", "insert") and new_text[j1:j2].strip():
            added.append(new_text[j1:j2])

    def clip(segments: list[str]) -> str:
        joined = "".join(segments).strip()
        return joined[:limit] + ("…" if len(joined) > limit else "")

    add_text = clip(added)
    remove_text = clip(removed)
    if add_text and remove_text:
        return f"{label}：「{remove_text}」→「{add_text}」"
    if add_text:
        return f"{label}：新比旧多了「{add_text}」"
    if remove_text:
        return f"{label}：新比旧少了「{remove_text}」"
    return f"{label}：仅格式/大小写差异"


def local_change_hint(incoming: dict[str, Any], existing: dict[str, Any]) -> str:
    """逐字段方向化原因（新导入为基准 vs 旧 case）。"""
    hints = []
    if normalize_text(join_path(incoming)) != normalize_text(join_path(existing)):
        hints.append(field_diff_hint("路径", join_path(incoming), join_path(existing)))
    hints.extend(
        field_diff_hint(label, incoming.get(key), existing.get(key))
        for key, label in CHANGE_HINT_FIELDS
        if normalize_text(incoming.get(key)) != normalize_text(existing.get(key))
    )
    return "；".join(hints) if hints else "无结构差异"


class ModelMatchError(RuntimeError):
    """模型碰撞判断失败。显式抛出，避免静默退回“仅本地规则”的结果。"""


def enrich_with_model(review_items: list[dict[str, Any]]) -> None:
    if not review_items:
        # 全是精确匹配，本轮不需要模型判断，正常返回。
        return
    settings = get_settings()
    api_key = settings.llm_api_key or os.environ.get("ARK_API_KEY") or os.environ.get("CASE_FLOW_LLM_API_KEY")
    if not api_key:
        raise ModelMatchError(
            "未配置模型 API Key，无法进行模型碰撞判断；本次未产生碰撞结果、未入库，请配置后重试。"
        )
    try:
        from openai import OpenAI
    except Exception as exc:
        raise ModelMatchError(
            "模型 SDK 不可用，无法进行模型碰撞判断；本次未产生碰撞结果、未入库。"
        ) from exc

    base_url = os.environ.get(
        "ARK_BASE_URL",
        settings.llm_base_url
        or os.environ.get("CASE_FLOW_LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    )
    client = OpenAI(base_url=base_url, api_key=api_key)
    model = settings.llm_model or os.environ.get(
        "ARK_MODEL",
        os.environ.get("CASE_FLOW_LLM_MODEL", "doubao-seed-2-0-pro-260215"),
    )
    max_output_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
    prompt_payload = [
        {
            "incoming_key": item["incoming_key"],
            "incoming": compact_case(item["incoming"]),
            "candidates": [compact_candidate(candidate) for candidate in item["candidates"]],
        }
        for item in review_items
    ]
    prompt = (
        "你是测试用例资产打磨阶段的碰撞判断助手。请比较新导入 case 和候选旧 case，判断新 case "
        "是否可能替代某条旧 case。打磨阶段是同一需求版本的工作快照覆盖，不是最终历史归档。"
        "模块、功能点、测试功能点变化不代表一定不同，要结合标题、前置条件、步骤、预期结果判断测试意图。"
        "只输出 JSON，不要输出解释文本。\n"
        "输出格式：{\"items\":[{\"incoming_key\":\"...\",\"best_case_id\":123,"
        "\"model_similarity\":0-100,\"should_lock\":true,"
        "\"summary\":\"变化点摘要\",\"risk\":\"low|medium|high\"}]}\n"
        "should_lock 表示是否建议把新 case 与 best_case_id 锁成 1:1 主候选。只有测试意图基本一致，"
        "只是路径迁移、标题补充、步骤措辞或局部内容变化时才为 true；"
        "如果业务目标变了，即使标题相同也为 false。"
        "summary（变化点摘要）必须【以新导入为轴】，描述旧 case 相对新导入的差异并写明方向："
        "新导入新增了什么 / 新导入缺少了什么 / 新导入把旧的某处改成了什么；不要只说差异量而不指明新旧方向。"
        "summary 中只用“新导入/旧 case”指代，禁止出现 case_id、best_case_id 等内部标识或数字 id。\n"
        "必须为输入里的每一个 incoming_key 各输出恰好一条 item，一条都不能少、不能合并；"
        "即使判断为“无任何旧 case 匹配、是全新 case”，也要输出该条，此时 best_case_id 置为 null、"
        "should_lock 置为 false、model_similarity 填最接近候选的估计分（没有候选则填 0），并在 summary 说明为何无匹配。\n"
        "模型只提供建议，最终落库必须由用户决策。\n"
        f"输入：{json.dumps(prompt_payload, ensure_ascii=False)}"
    )
    kwargs = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "temperature": 0.1,
        "max_output_tokens": max_output_tokens,
    }
    try:
        # 先带 reasoning 调；端点不支持该参数时回退到不带 reasoning——这是兼容性回退，不是失败。
        try:
            response = client.responses.create(**kwargs, reasoning={"effort": "medium"})
        except Exception:
            response = client.responses.create(**kwargs)
    except Exception as exc:
        raise ModelMatchError(
            f"模型碰撞判断调用失败（{exc.__class__.__name__}）；本次未产生碰撞结果、未入库，请稍后重试。"
        ) from exc
    try:
        parsed = json.loads(extract_json(getattr(response, "output_text", "") or str(response)))
    except Exception as exc:
        raise ModelMatchError(
            "模型返回内容无法解析为碰撞判断结果；本次未产生碰撞结果、未入库，请稍后重试。"
        ) from exc

    by_key = {
        str(item.get("incoming_key")): item
        for item in parsed.get("items", [])
        if item.get("incoming_key")
    }
    missing_count = sum(1 for item in review_items if item["incoming_key"] not in by_key)
    if missing_count:
        raise ModelMatchError(
            f"模型只返回了 {len(review_items) - missing_count}/{len(review_items)} 条 case 的碰撞判断，"
            f"缺 {missing_count} 条未判断；碰撞未完整完成，本次未产生碰撞结果、未入库，请稍后重试。"
        )
    for item in review_items:
        model_item = by_key[item["incoming_key"]]
        item["model_used"] = True
        item["model_summary"] = str(model_item.get("summary") or "")
        item["model_similarity"] = model_item.get("model_similarity")
        item["model_best_case_id"] = model_item.get("best_case_id")
        item["model_should_lock"] = bool(model_item.get("should_lock"))
        item["model_risk"] = model_item.get("risk")


def extract_json(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return cleaned[start : end + 1]
    return cleaned


def compact_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "ordinal": case.get("ordinal"),
        "path": join_path(case),
        "title": case.get("raw_title"),
        "preconditions": truncate(case.get("preconditions")),
        "steps": truncate(case.get("steps_text")),
        "expected": truncate(case.get("expected_result")),
    }


def compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": candidate.get("case_id"),
        "ordinal": candidate.get("ordinal"),
        "path": join_path(candidate),
        "title": candidate.get("raw_title"),
        "local_similarity": candidate.get("similarity"),
        "change_hint": candidate.get("change_hint"),
    }


def truncate(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    return text[:limit] + ("..." if len(text) > limit else "")
