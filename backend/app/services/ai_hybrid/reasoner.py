from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import ValidationError

from app.services.ai_hybrid import function_map_ctx
from app.services.ai_hybrid.llm import llm_client
from app.services.ai_hybrid.prompts import ORCHESTRATOR_SYSTEM_PROMPT, TOOL_SPECS
from app.services.ai_hybrid.schemas import HybridDecision, HybridInput, HybridToolResult

# observation 喂回模型时只做极端防护，不做“省 token 式”语义削减。
# report_reader 已经按报告语义分层，reasoner 不能再粗暴截断 toc/history。
_MAX_STR = 64000
# 单次消息里最多附多少张被点名的截图（越靠后越新，取最近的）。
_MAX_IMAGES = 12
_MAX_COMPLETION_TOKENS = 16000


async def react_step(
    *,
    inp: HybridInput,
    history: list[dict[str, Any]],
    settings: Any,
    aiphone_devices: list[dict[str, Any]] | None = None,
    aiphone_devices_error: str | None = None,
) -> HybridDecision | None:
    """标准 ReAct 单步：模型看 goal + case + 工具规格 + 历史，直接给下一步决策。

    返回 None 表示 LLM 不可用（runner 走规则兜底）。其余异常统一收敛为 finish/needs_human。
    """
    client, llm_settings = llm_client(settings)
    if client is None or not getattr(llm_settings, "llm_model", ""):
        return None

    max_tokens = int(getattr(llm_settings, "llm_max_tokens", _MAX_COMPLETION_TOKENS) or _MAX_COMPLETION_TOKENS)
    clean_history, image_attachments = _split_history_images(history)
    payload = {
        "goal": inp.goal or inp.title,
        "case": {
            "title": inp.title,
            "preconditions": inp.preconditions,
            "steps_text": inp.steps_text,
            "expected_result": inp.expected_result,
            "source_ref": inp.source_ref,
        },
        "function_map_catalog": function_map_ctx.build_catalog(
            inp.function_maps, inp.function_map_context or ""
        ),
        "aiphone_devices": aiphone_devices or [],
        "aiphone_devices_error": aiphone_devices_error or "",
        "tools": TOOL_SPECS,
        "history": clean_history,
        "decision_schema": {
            "format": "Return one valid json object only.",
            "thought": "string",
            "action": "call_tool | finish",
            "tool": " | ".join(str(tool["name"]) for tool in TOOL_SPECS) + "（action=call_tool 必填）",
            "tool_input": "必须匹配所选 tool 的 input_schema",
            "tool_input_by_tool": {
                str(tool["name"]): tool.get("input_schema", {}) for tool in TOOL_SPECS
            },
            "verdict": "success | failed | needs_human（action=finish 必填）",
            "final": {"attribution": "string", "evidence": ["string"], "suggestions": ["string"]},
        },
    }
    user_content = _build_user_content(json.dumps(payload, ensure_ascii=False), image_attachments)
    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=llm_settings.llm_model,
                messages=[
                    {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        )
        content = str(response.choices[0].message.content or "")
        return parse_decision(content)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        return _needs_human_decision(
            "reasoner 输出结构不符合 schema",
            "reasoner_parse_failed",
            "Hybrid reasoner 输出无法解析，需要人工判断。",
            str(exc),
            ["检查模型输出格式或收紧 prompt。"],
        )
    except Exception as exc:
        return _needs_human_decision(
            "reasoner 调用失败",
            "reasoner_error",
            "Hybrid reasoner 调用异常，需要人工判断。",
            str(exc),
            ["检查 LLM 配置、网络或服务状态。"],
        )


def parse_decision(content: str) -> HybridDecision:
    data = json.loads(_extract_json_object(content))
    return HybridDecision.model_validate(data)


def observe(result: HybridToolResult) -> dict[str, Any]:
    """把一次工具返回压成"接近原文"的 observation 供模型阅读。

    不做关键字分类，只做超长保护。report_reader 的分层结果（outline/read/search 文本、
    image 的上下文）原样带出，让模型自己判断。若是 image 动作，其 base64 data URI 不进
    JSON 文本（会炸上下文），而是抽到 `_image` 里、由 react_step 作为真正的图片块附上去。
    """
    raw = dict(result.raw or {})
    image_data_uri = raw.pop("image_data_uri", None)
    # Function Map 是设备绑定的权威证据，不能把写在正文后段的绑定静默截断；其他工具仍
    # 使用既有的上限防止大报告撑爆上下文。
    max_str = None if result.tool == "function_map" else _MAX_STR
    observation: dict[str, Any] = {
        "tool": result.tool,
        "status": result.status,
        "reason": result.reason,
        "report_url": result.report_url,
        "summary_report_url": result.summary_report_url,
        "raw": _compact(raw, max_str),
    }
    if image_data_uri:
        observation["_image"] = {"imgNo": raw.get("imgNo"), "data_uri": image_data_uri}
    if result.evidence:
        observation["evidence"] = _compact(result.evidence, max_str)
    return observation


def _split_history_images(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把 history 拆成「给模型读的 JSON 文本」和「要附的截图」。

    base64 图不进 JSON（避免炸上下文），改成 image_url 块附在消息里；文本里只留一条
    「该图已附在消息中」的提示，让模型把 [截图#N] 和图片对上号。
    """
    clean: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    for entry in history:
        new_entry = dict(entry)
        observation = new_entry.get("observation")
        if isinstance(observation, dict) and observation.get("_image"):
            image = observation["_image"]
            trimmed = {key: value for key, value in observation.items() if key != "_image"}
            trimmed["image_attached"] = {
                "imgNo": image.get("imgNo"),
                "note": "该截图已作为图片附在本条消息中，请直接看图。",
            }
            new_entry["observation"] = trimmed
            if image.get("data_uri"):
                attachments.append({"imgNo": image.get("imgNo"), "data_uri": image["data_uri"]})
        clean.append(new_entry)
    return clean, attachments[-_MAX_IMAGES:]


def _build_user_content(payload_text: str, attachments: list[dict[str, Any]]) -> Any:
    if not attachments:
        return payload_text
    content: list[dict[str, Any]] = [{"type": "text", "text": payload_text}]
    for att in attachments:
        content.append({"type": "text", "text": f"[截图#{att.get('imgNo')}]"})
        content.append({"type": "image_url", "image_url": {"url": att["data_uri"]}})
    return content


def _compact(value: Any, max_str: int | None = _MAX_STR) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            # request/submitted 是提交回执，对下一步决策没帮助，去掉减噪。
            if key in {"request", "submitted"}:
                continue
            compact[str(key)] = _compact(item, max_str)
        return compact
    if isinstance(value, list):
        return [_compact(item, max_str) for item in value]
    if isinstance(value, str):
        if max_str is None or len(value) <= max_str:
            return value
        return value[:max_str] + f"\n…（已截断，共 {len(value)} 字，可 call_tool report_reader 取全文）"
    return value


def _needs_human_decision(
    thought: str,
    status_reason: str,
    attribution: str,
    error: str,
    suggestions: list[str],
) -> HybridDecision:
    return HybridDecision(
        thought=thought,
        action="finish",
        verdict="needs_human",
        status_reason=status_reason,
        final={"attribution": attribution, "evidence": [error[:600]], "suggestions": suggestions},
    )


def _extract_json_object(content: str) -> str:
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        return text[start : end + 1]
    return text
