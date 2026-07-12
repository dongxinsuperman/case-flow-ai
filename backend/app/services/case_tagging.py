from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from app.core.settings import get_settings

MODEL_TARGETS = {"app", "web", "api", "manual"}
MODEL_MAX_CONCURRENCY = 3
MODEL_TIMEOUT_SECONDS = 90
MODEL_TAG_REASON = "模型根据标题和前置条件判断执行端。"
MODEL_TAG_CONFIDENCE = 70


def classify_case(case: dict[str, Any]) -> dict[str, Any]:
    tags = [str(item).lower() for item in case.get("scenario_tags") or []]
    raw_title = str(case.get("raw_title") or "")
    text_parts = [
        " / ".join(_path_values(case)),
        case.get("raw_title"),
        case.get("preconditions"),
        case.get("steps_text"),
        case.get("expected_result"),
    ]
    text = "\n".join(str(part or "") for part in text_parts).lower()

    if case.get("manual") or "人工" in raw_title or "人工" in tags:
        return _result("manual", "manual_flag", "标题标签或解析结果标记为人工执行。", 95)

    explicit = _explicit_target(raw_title, tags)
    if explicit:
        return explicit

    if _matches(text, API_PATTERNS):
        return _result("api", "content_rule", "内容包含 API、接口、HTTP 请求或响应断言等明确线索。", 82)

    if _matches(text, MANUAL_PATTERNS):
        return _result("manual", "content_rule", "内容包含人工确认、后台人工处理或线下核验等线索。", 78)

    if _matches(text, WEB_PATTERNS):
        return _result(
            "web",
            "content_rule",
            "内容包含明确 Web、H5、浏览器、后台、平台、站点或 PC Web 线索。",
            80,
        )

    if _matches(text, APP_PATTERNS):
        return _result("app", "content_rule", "内容包含 App、移动端、底部 Tab、手机系统等线索。", 76)

    return _result("manual", "manual_fallback", "规则没有给出可用判断，按人工执行兜底。", 35)


async def classify_cases(
    cases: list[dict[str, Any]],
    *,
    use_model: bool = True,
) -> list[dict[str, Any]]:
    tags = [classify_case(case) for case in cases]
    if not use_model:
        return tags

    fallback_items = [
        (index, case)
        for index, (case, tag) in enumerate(zip(cases, tags, strict=True))
        if tag.get("tag_source") == "manual_fallback"
    ]
    if not fallback_items:
        return tags

    model_targets = await _classify_fallbacks_with_model(fallback_items)
    for index, target in model_targets.items():
        tags[index] = _result(target, "model", MODEL_TAG_REASON, MODEL_TAG_CONFIDENCE)
    return tags


def _explicit_target(raw_title: str, tags: list[str]) -> dict[str, Any] | None:
    title = raw_title.lower()
    source = f"{title} {' '.join(tags)}"
    explicit_map = [
        ("manual", ["【人工】", "人工"]),
        ("api", ["【api】", "【接口】", "api", "接口"]),
        ("web", ["【web】", "【h5】", "web", "h5", "网页"]),
        ("app", ["【app】", "app", "移动端", "android", "ios"]),
    ]
    for target, markers in explicit_map:
        if any(marker.lower() in source for marker in markers):
            return _result(target, "title_tag", f"标题或标签中明确出现 {target.upper()} 执行端线索。", 90)
    return None


def _result(target: str, source: str, reason: str, confidence: int) -> dict[str, Any]:
    return {
        "execution_target": target,
        "tag_source": source,
        "tag_reason": reason,
        "tag_confidence": confidence,
        "case_type": "manual" if target == "manual" else "auto",
    }


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


async def _classify_fallbacks_with_model(
    items: list[tuple[int, dict[str, Any]]],
) -> dict[int, str]:
    if not items:
        return {}
    settings = get_settings()
    api_key = settings.llm_api_key or os.environ.get("ARK_API_KEY") or os.environ.get("CASE_FLOW_LLM_API_KEY")
    model = settings.llm_model or os.environ.get(
        "ARK_MODEL",
        os.environ.get("CASE_FLOW_LLM_MODEL", "doubao-seed-2-0-pro-260215"),
    )
    if not api_key or not model:
        return {}
    try:
        from openai import AsyncOpenAI
    except Exception:
        return {}

    base_url = settings.llm_base_url or os.environ.get(
        "ARK_BASE_URL",
        os.environ.get("CASE_FLOW_LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    )
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=MODEL_TIMEOUT_SECONDS,
        max_retries=0,
    )
    max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
    try:
        batches = _split_even(items, min(MODEL_MAX_CONCURRENCY, len(items)))
        results = await asyncio.gather(
            *(_request_model_batch(client, model, batch, max_tokens) for batch in batches),
            return_exceptions=True,
        )
    finally:
        await client.close()

    merged: dict[int, str] = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        merged.update(result)
    return merged


async def _request_model_batch(
    client: Any,
    model: str,
    batch: list[tuple[int, dict[str, Any]]],
    max_tokens: int,
) -> dict[int, str]:
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": _build_model_prompt(batch)}],
                temperature=0,
                max_tokens=max_tokens,
            ),
            timeout=MODEL_TIMEOUT_SECONDS,
        )
    except Exception:
        return {}

    text = getattr(response.choices[0].message, "content", "") or ""
    try:
        parsed = json.loads(_extract_json_array(text))
    except Exception:
        return {}
    if not isinstance(parsed, list):
        return {}

    valid_indexes = {index for index, _case in batch}
    targets: dict[int, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        target = str(item.get("target") or "").strip().lower()
        if index in valid_indexes and target in MODEL_TARGETS:
            targets[index] = target
    return targets


def _build_model_prompt(batch: list[tuple[int, dict[str, Any]]]) -> str:
    payload = json.dumps(
        [_model_case_payload(index, case) for index, case in batch],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "把测试用例分成app/web/api/manual四类。"
        "api=接口/HTTP/JSON/状态码；"
        "web=浏览器/H5/网页/PC/平台/后台/管理端/控制台/站点/网站/cb/vm/视频后台/课程后台；"
        "app=手机App/小程序/移动端；manual=人工审核/线下/后台人工/信息不足。"
        '只输出JSON数组：[{"id":1,"target":"api"}]。cases='
        f"{payload}"
    )


def _model_case_payload(index: int, case: dict[str, Any]) -> dict[str, Any]:
    title = case.get("raw_title") or ""
    return {
        "id": index,
        "title": _truncate(title, 80),
        "pre": _truncate(case.get("preconditions") or "", 180),
    }


def _extract_json_array(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start >= 0 and end >= start:
        return cleaned[start : end + 1]
    return cleaned


def _split_even(
    items: list[tuple[int, dict[str, Any]]],
    parts: int,
) -> list[list[tuple[int, dict[str, Any]]]]:
    if parts <= 1:
        return [items]
    base_size, remainder = divmod(len(items), parts)
    batches: list[list[tuple[int, dict[str, Any]]]] = []
    cursor = 0
    for part in range(parts):
        size = base_size + (1 if part < remainder else 0)
        if size <= 0:
            continue
        batches.append(items[cursor : cursor + size])
        cursor += size
    return batches


def _truncate(value: Any, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= max_chars else text[:max_chars]


def _path_values(case: dict[str, Any]) -> list[str]:
    nodes = case.get("path_nodes") or []
    if isinstance(nodes, list):
        return [
            str(
                node.get("displayText")
                or node.get("display_text")
                or node.get("rawText")
                or node.get("raw_text")
                or ""
            )
            for node in nodes
            if isinstance(node, dict)
            and (
                node.get("displayText")
                or node.get("display_text")
                or node.get("rawText")
                or node.get("raw_text")
            )
        ]
    return []


API_PATTERNS = [
    r"\bapi\b",
    r"接口",
    r"http[s]?://",
    r"\b(get|post|put|delete|patch)\b",
    r"请求参数",
    r"响应",
    r"状态码",
    r"返回码",
    r"json",
    r"服务端",
]

MANUAL_PATTERNS = [
    r"人工确认",
    r"人工审核",
    r"人工检查",
    r"线下",
    r"运营后台.*(审核|配置|处理|确认)",
    r"管理后台.*(审核|配置|处理|确认)",
    r"数据库核查",
    r"后台人工",
]

WEB_PATTERNS = [
    r"(?<![a-z0-9])web(?![a-z0-9])",
    r"(?<![a-z0-9])h5(?![a-z0-9])",
    r"网页",
    r"网站",
    r"站点",
    r"浏览器",
    r"url",
    r"pc\s*端",
    r"视频后台",
    r"课程后台",
    r"管理端",
    r"控制台",
    r"后台",
    r"平台",
    r"(?<![a-z0-9])cb(?![a-z0-9])",
    r"(?<![a-z0-9])vm(?![a-z0-9])",
]

APP_PATTERNS = [
    r"\bapp\b",
    r"移动端",
    r"手机",
    r"android",
    r"\bios\b",
    r"底部\s*tab",
    r"底部导航",
    r"启动应用",
    r"进入.*tab",
    r"小程序",
]
