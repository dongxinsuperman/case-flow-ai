from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database
from app.core.settings import get_settings
from app.models.agent import AgentBugSubmission, AgentDispatch, AgentMessage, AgentSession
from app.models.quick import QuickSession
from app.models.requirements import RequirementItem, User
from app.report_readers.base import ReportEvidence, ReportImageEvidence
from app.report_readers.html import read_report
from app.schemas.agent import AgentContextRef, AgentImageAttachment
from app.schemas.ai_api import AIAPIDirectRunIn
from app.services import bug_submit as formal_bug
from app.services import executions
from app.services import function_map_mount as function_map_mount_service
from app.services.ai_api.direct import run_direct_aiapi
from app.services.executor_platforms import executor_callback_base_url
from app.services.quick_bug_submit import (
    QuickBugSubmitError,
    _resolve_target_from_url,
    _target_to_payload,
)
from app.services.sources import feishu_project as fp


class AgentError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class _ToolDecision:
    tool_key: str
    args: dict[str, Any]
    direct_answer: str = ""


@dataclass(frozen=True)
class _AgentToolSpec:
    key: str
    kind: str
    description: str
    parameters: dict[str, Any]
    invocation_mode: str
    side_effect_level: str
    result_artifacts: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "description": self.description,
            "invocation_mode": self.invocation_mode,
            "side_effect_level": self.side_effect_level,
            "result_artifacts": list(self.result_artifacts),
        }

    def tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.key,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class _AgentFunctionMapResolution:
    context: str
    meta: dict[str, Any]


AGENT_DISPLAY_NAME = "OS Agent"
AGENT_INTRO_MESSAGE = (
    "我是 OS Agent，可以帮你处理从执行、取证到提交缺陷的端到端的日常任务。\n\n"
    "1. 手机端：操作真机打开 App、走业务流程、确认功能、复现反馈。\n"
    "2. 网页端：浏览器里打开网页，免登录、查信息、核数据、探查页面表现。\n"
    "3. 接口/API：发请求、验接口，覆盖边界与异常，自然语言跑增删改查。\n"
    "4. Bug/缺陷：结合你的描述、截图和最近的执行结果，建单到飞书。"
)

DECISION_SYSTEM_PROMPT = (
    "你是 Case Flow 里的轻量助手。用户不是在写标准测试用例，而是在随手表达想做的事："
    "看一下手机里的某个页面、查网页资料、跑接口或提交 bug。"
    "你可以自然聊天，也可以按需调用已接入工具。"
    "只有当用户明确让你查看、执行、测试、提交、准备或查询时才调用工具；普通问题直接自然回答。"
    "参数不够时先追问最少的问题，不要硬凑。"
    "提 bug 是自然语言提交：用户明确说提 bug、建单、发出去才调用；缺需求链接时先问链接。"
    "工具结果不要在同一轮自动串下一步；如果需要下一步，让用户再说一句。"
    "不要编造实时资源和能力覆盖：用户问支持哪些手机或浏览器槽时，"
    "只能基于已注册工具、配置或实际查询结果回答；没有查询工具或没有结果时，直接说明暂时不能确认。"
    "当前没有注册测试账号或业务数据准备工具；遇到这类请求时明确说明需要部署方二次开发。"
)

RESULT_SYSTEM_PROMPT = (
    "你要把工具执行结果改写成一条非常简洁的助手回复，像车载语音助手一样。"
    "用户知道自己刚才发了什么指令，不要复述任务背景，不要列长清单。"
    "只说执行结果；成功时一句结论加一句关键证据，失败时一句失败原因加一句下一步。"
    "报告、截图、日志只是附件，不要把原始日志或报告 URL 写进正文。"
    "AI Web 结果直接称为“AI Web 任务”，不要写成“Case Flow Agent AI Web任务”。"
)
BUG_TITLE_SYSTEM_PROMPT = "把用户通过助手提交的缺陷整理成一行 bug 标题，35 字以内，只输出标题。"
BUG_DESCRIPTION_SYSTEM_PROMPT = (
    "把助手对话中的 bug 证据整理成飞书 bug 描述。"
    "描述字段只承载缺陷证据；标题已由标题字段承载，需求已由关联需求字段承载。"
    "按对话、附件和最近报告中的已有证据组织内容，只生成信息明确的小节；没有对应信息的小节直接省略。"
    "可保留用户补充说明、复现线索、实际结果、预期结果、最近报告链接和截图说明；所有内容都必须有证据来源。"
)
RECENT_CONTEXT_LIMIT = 10
DECISION_IMAGE_LIMIT = 3
LLM_TOOL_CALL_LIMIT = 3

AGENT_TOOL_SPECS: tuple[_AgentToolSpec, ...] = (
    _AgentToolSpec(
        key="aiphone_dispatch",
        kind="external_executor",
        description="操作手机 App 或移动端页面，适合用户说想看 App 里的页面、验证手机端流程、检查安卓/iOS/Harmony 行为。",
        parameters={
            "type": "object",
            "properties": {
                "run_content": {"type": "string", "description": "整理后的手机端执行目标。"},
                "platform": {
                    "type": "string",
                    "enum": ["android", "ios", "harmony"],
                    "description": "用户明确指定的手机平台；未指定时留空，由后端按当前资源选择。",
                },
            },
            "required": ["run_content"],
        },
        invocation_mode="async_callback",
        side_effect_level="read_only",
        result_artifacts=("report_url", "key_images"),
    ),
    _AgentToolSpec(
        key="aiweb_dispatch",
        kind="external_executor",
        description="操作浏览器或 Web/H5 页面，适合查网页资料、验证 Web 流程、检查 Chrome/Safari/Firefox 行为。",
        parameters={
            "type": "object",
            "properties": {
                "run_content": {"type": "string", "description": "整理后的网页端执行目标。"},
                "platform": {
                    "type": "string",
                    "enum": ["chrome", "safari", "firefox"],
                    "description": "用户明确指定的浏览器；未指定时留空，由后端按当前资源选择。",
                },
            },
            "required": ["run_content"],
        },
        invocation_mode="async_callback",
        side_effect_level="read_only",
        result_artifacts=("report_url", "key_images"),
    ),
    _AgentToolSpec(
        key="aiphone_resource_probe",
        kind="resource_probe",
        description="查询当前 AI Phone 手机资源，适合用户问有没有 iOS/Android/Harmony 手机、哪些手机空闲或占用。",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["android", "ios", "harmony"],
                    "description": "要查询的手机平台；未指定时返回所有可见手机资源。",
                }
            },
        },
        invocation_mode="sync_short",
        side_effect_level="read_only",
        result_artifacts=("structured_json",),
    ),
    _AgentToolSpec(
        key="aiweb_resource_probe",
        kind="resource_probe",
        description="查询当前 AI Web 浏览器槽资源，适合用户问 Chrome/Safari/Firefox 有没有空闲槽或占用状态。",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["chrome", "safari", "firefox"],
                    "description": "要查询的浏览器；未指定时返回所有可见浏览器槽。",
                }
            },
        },
        invocation_mode="sync_short",
        side_effect_level="read_only",
        result_artifacts=("structured_json",),
    ),
    _AgentToolSpec(
        key="aiapi_run",
        kind="internal_executor",
        description="执行接口/API 探测或测试，适合 HTTP、接口、响应、断言、边界值、请求参数这类任务。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "preconditions": {"type": "string"},
                "steps_text": {"type": "string", "description": "接口执行步骤或探测目标。"},
                "expected_result": {"type": "string"},
            },
            "required": ["steps_text"],
        },
        invocation_mode="async_background",
        side_effect_level="read_only",
        result_artifacts=("report_url", "structured_json"),
    ),
    _AgentToolSpec(
        key="bug_submit_feishu",
        kind="business_action",
        description="用户明确要求提 bug、建单、发出去时使用。缺飞书需求链接时先追问，不要静默提交。",
        parameters={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "用户描述的问题和需要提交的缺陷内容。"},
                "requirement_url": {"type": "string", "description": "飞书需求详情页链接，可为空，缺失时后端会追问。"},
            },
            "required": ["description"],
        },
        invocation_mode="sync_short",
        side_effect_level="external_side_effect",
        result_artifacts=("bug_url",),
    ),
)

TOOL_REGISTRY: dict[str, _AgentToolSpec] = {tool.key: tool for tool in AGENT_TOOL_SPECS}
TOOL_DEFS: list[dict[str, Any]] = [tool.public_dict() for tool in AGENT_TOOL_SPECS]


async def get_or_create_session(session: AsyncSession, user_id: int) -> AgentSession:
    user = await session.get(User, user_id)
    if user is None:
        raise AgentError("当前用户不存在")
    existing = await session.scalar(select(AgentSession).where(AgentSession.user_id == user_id))
    if existing is not None:
        return existing
    agent_session = AgentSession(user_id=user_id, title=AGENT_DISPLAY_NAME)
    session.add(agent_session)
    await session.commit()
    await session.refresh(agent_session)
    return agent_session


async def session_detail(session: AsyncSession, user_id: int) -> dict[str, Any]:
    agent_session = await get_or_create_session(session, user_id)
    messages = await _messages(session, agent_session.id)
    return {"session": agent_session, "messages": messages}


async def reset_session_context(session: AsyncSession, user_id: int) -> dict[str, Any]:
    agent_session = await get_or_create_session(session, user_id)
    await session.execute(delete(AgentBugSubmission).where(AgentBugSubmission.session_id == agent_session.id))
    await session.execute(delete(AgentDispatch).where(AgentDispatch.session_id == agent_session.id))
    await session.execute(delete(AgentMessage).where(AgentMessage.session_id == agent_session.id))
    agent_session.title = AGENT_DISPLAY_NAME
    agent_session.default_tool = None
    agent_session.default_resource_pool = {}
    agent_session.function_context = ""
    agent_session.bug_target = {}
    agent_session.pending_action = {}
    agent_session.updated_at = func.now()
    _append_message(session, agent_session.id, "assistant", AGENT_INTRO_MESSAGE)
    await session.commit()
    return await session_detail(session, user_id)


async def post_message(
    session: AsyncSession,
    user_id: int,
    content: str,
    attachments: dict[str, Any] | None = None,
    context_ref: AgentContextRef | dict[str, Any] | None = None,
) -> dict[str, Any]:
    agent_session = await get_or_create_session(session, user_id)
    clean_content = str(content or "").strip()
    safe_attachments = _safe_json_object(attachments)
    normalized_context_ref = _normalize_agent_context_ref(context_ref)
    if normalized_context_ref:
        safe_attachments["context_ref"] = normalized_context_ref
    msg = AgentMessage(
        session_id=agent_session.id,
        role="user",
        content=clean_content,
        attachments=safe_attachments,
    )
    session.add(msg)
    agent_session.updated_at = func.now()
    await session.flush()
    await session.commit()
    await session.refresh(agent_session)
    await session.refresh(msg)

    try:
        await _handle_user_message(session, agent_session, msg)
    except AgentError as exc:
        _append_message(session, agent_session.id, "assistant", str(exc))
    except Exception as exc:
        _append_message(session, agent_session.id, "assistant", f"这次处理失败：{exc}")
    agent_session.updated_at = func.now()
    background_aiapi_dispatch_ids = list(session.info.pop("agent_aiapi_dispatch_ids", []))
    await session.commit()
    for dispatch_id in background_aiapi_dispatch_ids:
        _launch_aiapi_dispatch(dispatch_id)
    return await session_detail(session, user_id)


async def upload_images(files: list[Any]) -> list[AgentImageAttachment]:
    settings = get_settings()
    upload_dir = Path(settings.repair_image_dir) / "agent_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    images: list[AgentImageAttachment] = []
    for file in files[:6]:
        content_type = str(getattr(file, "content_type", "") or "")
        if not content_type.startswith("image/"):
            raise AgentError("只支持上传图片")
        raw = await file.read()
        if len(raw) > 6 * 1024 * 1024:
            raise AgentError("单张图片不能超过 6MB")
        suffix = _image_suffix(getattr(file, "filename", "") or "", content_type)
        name = f"{uuid.uuid4().hex}{suffix}"
        path = upload_dir / name
        path.write_bytes(raw)
        url = _public_media_url(f"/media/agent_uploads/{name}")
        images.append(
            AgentImageAttachment(
                url=url,
                thumbnail_url=url,
                filename=str(getattr(file, "filename", "") or name),
                mime=content_type,
                size=len(raw),
            )
        )
    return images


def _normalize_agent_context_ref(value: AgentContextRef | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    raw = value.model_dump() if isinstance(value, AgentContextRef) else dict(value or {})
    mode = str(raw.get("mode") or "").strip().lower()
    use_current_function_map = bool(raw.get("use_current_function_map", raw.get("useCurrentFunctionMap", True)))
    if mode == "standard":
        requirement_item_id = _positive_int(raw.get("requirement_item_id") or raw.get("requirementItemId"))
        if not requirement_item_id:
            return {}
        return {
            "mode": "standard",
            "requirement_item_id": requirement_item_id,
            "use_current_function_map": use_current_function_map,
        }
    if mode == "quick":
        quick_session_id = str(raw.get("quick_session_id") or raw.get("quickSessionId") or "").strip()
        if not quick_session_id:
            return {}
        return {
            "mode": "quick",
            "quick_session_id": quick_session_id,
            "use_current_function_map": use_current_function_map,
        }
    return {}


def _agent_context_ref_from_message(message: AgentMessage) -> dict[str, Any]:
    attachments = dict(message.attachments or {})
    raw = attachments.get("context_ref") or attachments.get("contextRef")
    return _normalize_agent_context_ref(raw if isinstance(raw, dict) else None)


async def _resolve_agent_function_map_context(
    session: AsyncSession,
    message: AgentMessage,
) -> _AgentFunctionMapResolution:
    ref = _agent_context_ref_from_message(message)
    if not ref:
        return _agent_function_map_result("", {}, "no_context_ref", False)
    if not bool(ref.get("use_current_function_map", True)):
        return _agent_function_map_result("", ref, "disabled", False)

    mode = str(ref.get("mode") or "")
    if mode == "standard":
        return await _resolve_standard_agent_function_map(session, ref)
    if mode == "quick":
        return await _resolve_quick_agent_function_map(session, ref)
    return _agent_function_map_result("", ref, "unsupported_context_mode", False)


async def _resolve_standard_agent_function_map(
    session: AsyncSession,
    ref: dict[str, Any],
) -> _AgentFunctionMapResolution:
    requirement_item_id = _positive_int(ref.get("requirement_item_id"))
    if not requirement_item_id:
        return _agent_function_map_result("", ref, "missing_requirement_item_id", False)
    item = await session.get(RequirementItem, requirement_item_id)
    if item is None:
        return _agent_function_map_result("", ref, "requirement_not_found", False)
    top = await function_map_mount_service.compile_top_level_context(
        session, [requirement_item_id], "mixed"
    )
    meta_ref = {**ref}
    group_id = _positive_int(getattr(item, "group_id", None))
    if group_id:
        meta_ref["group_id"] = group_id
    if not top.context.strip():
        return _agent_function_map_result("", meta_ref, "empty_function_map", False)
    return _agent_function_map_result(top.context, meta_ref, "applied", True)


async def _resolve_quick_agent_function_map(
    session: AsyncSession,
    ref: dict[str, Any],
) -> _AgentFunctionMapResolution:
    quick_session_id = str(ref.get("quick_session_id") or "").strip()
    if not quick_session_id:
        return _agent_function_map_result("", ref, "missing_quick_session_id", False)
    quick_session = await session.get(QuickSession, quick_session_id)
    if quick_session is None:
        return _agent_function_map_result("", ref, "quick_session_not_found", False)
    context = (
        await function_map_mount_service.compile_quick_context(session, quick_session_id, "mixed")
    ).context
    if not context.strip():
        return _agent_function_map_result("", ref, "empty_function_map", False)
    return _agent_function_map_result(context, ref, "applied", True)


def _agent_function_map_result(
    context: str,
    ref: dict[str, Any],
    reason: str,
    applied: bool,
) -> _AgentFunctionMapResolution:
    text = str(context or "").strip()
    meta = {
        "applied": bool(applied and text),
        "reason": reason,
        "source": str(ref.get("mode") or "none"),
        "chars": len(text) if applied and text else 0,
    }
    for key in ("mode", "requirement_item_id", "quick_session_id", "group_id", "use_current_function_map"):
        if key in ref:
            meta[key] = ref[key]
    return _AgentFunctionMapResolution(context=text if applied else "", meta=meta)




def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


async def apply_executor_callback(
    session: AsyncSession,
    tool_key: str,
    base_url: str,
    callback_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from app.services.executions import _normalize_executor_urls

    payload = _normalize_executor_urls(payload, base_url)
    dispatch = await session.scalar(
        select(AgentDispatch).where(
            AgentDispatch.callback_token == callback_token,
            AgentDispatch.tool_key == tool_key,
        )
    )
    if dispatch is None:
        return {"handled": False, "event": str(payload.get("event") or "agent.orphan_callback")}
    event = str(payload.get("event") or "")
    report_url = payload.get("reportUrl") or payload.get("report_url") or dispatch.report_url
    state = str(
        payload.get("state")
        or payload.get("submissionState")
        or payload.get("submission_state")
        or "callback_received"
    ).lower()
    status = _terminal_status(state)
    dispatch.status = status
    dispatch.report_url = report_url
    dispatch.run_id = payload.get("runId") or payload.get("run_id") or dispatch.run_id
    dispatch.platform = str(payload.get("platform") or dispatch.platform or "")
    dispatch.result_payload = payload
    dispatch.finished_at = func.now() if status in {"passed", "failed"} else dispatch.finished_at
    await session.flush()
    if report_url or status in {"passed", "failed"}:
        await _write_result_message(session, dispatch)
    await session.commit()
    return {
        "handled": True,
        "event": event or "agent.callback",
        "dispatch_id": dispatch.id,
        "submission_id": dispatch.submission_id,
    }


def _is_pending_bug(pending: dict[str, Any]) -> bool:
    return str(pending.get("tool_key") or "") == "bug_submit_feishu"


def _store_pending_bug(
    agent_session: AgentSession,
    message: AgentMessage,
    args: dict[str, Any] | None = None,
) -> None:
    raw_args = dict(args or {})
    description = str(raw_args.get("description") or message.content or "").strip()
    raw_args["description"] = description
    agent_session.pending_action = {
        "tool_key": "bug_submit_feishu",
        "description": description,
        "args": raw_args,
        "attachments": _safe_json_object(message.attachments),
    }


def _pending_bug_submit_args(pending: dict[str, Any], requirement_url: str) -> dict[str, Any]:
    args = dict(pending.get("args") or {})
    args.setdefault("description", pending.get("description") or "")
    args["requirement_url"] = requirement_url
    attachments = pending.get("attachments")
    if isinstance(attachments, dict):
        args["_pending_attachments"] = attachments
    return args


def _clear_pending_bug(agent_session: AgentSession) -> None:
    agent_session.pending_action = {}


def _should_hold_bug_image_for_requirement(message: AgentMessage) -> bool:
    return bool(_attachment_images(message.attachments)) and _looks_like_bug_submission_intent(message.content)


def _looks_like_bug_submission_intent(text: str) -> bool:
    raw = str(text or "")
    compact = re.sub(r"\s+", "", raw).lower()
    if any(word in compact for word in ("bug", "提bug", "提交bug", "建单", "提单")):
        return True
    if any(word in raw for word in ("缺陷", "提交缺陷", "提缺陷")):
        return True
    issue_hit = any(word in raw for word in ("问题", "异常", "报错", "失败", "错误", "不对"))
    submit_hit = any(word in raw for word in ("提", "提交", "发出去", "建", "放上去", "带上", "截图"))
    return issue_hit and submit_hit


def _looks_like_pending_bug_cancel(text: str) -> bool:
    raw = str(text or "").strip()
    compact = re.sub(r"\s+", "", raw)
    return compact in {"取消", "算了", "不提了", "先不提了", "别提了", "不用提了"}


async def _handle_user_message(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
) -> None:
    pending = dict(agent_session.pending_action or {})
    link = _first_feishu_url(message.content)
    should_hold_bug_image = _should_hold_bug_image_for_requirement(message)
    if _is_pending_bug(pending) and _looks_like_pending_bug_cancel(message.content):
        _clear_pending_bug(agent_session)
        _append_message(session, agent_session.id, "assistant", "已取消这次 bug 提交。")
        return
    if link:
        target = await _bind_bug_target(agent_session, link)
        if _is_pending_bug(pending):
            args = _pending_bug_submit_args(pending, link)
            await _submit_bug(session, agent_session, message, args, target)
            _clear_pending_bug(agent_session)
            return
        if _looks_like_bug_submission_intent(message.content):
            await _submit_bug(
                session,
                agent_session,
                message,
                {"description": message.content, "requirement_url": link},
                target,
            )
            return
        _append_message(
            session,
            agent_session.id,
            "assistant",
            f"已识别并绑定需求：{target.raw.get('name') or target.work_item_id}。你可以继续说“把刚才的问题提 bug”。",
        )
        return

    handled = False
    decisions = await _decide_tools(session, agent_session, message)
    for decision in decisions:
        if decision.direct_answer:
            content = decision.direct_answer
            if should_hold_bug_image:
                _store_pending_bug(agent_session, message, {"description": message.content})
                if "需求" not in content or "链接" not in content:
                    content = "这条 bug 关联哪个飞书需求？把需求链接发我就行。"
            elif _is_pending_bug(pending):
                _clear_pending_bug(agent_session)
                pending = {}
            _append_message(session, agent_session.id, "assistant", content)
            handled = True
            continue
        if decision.tool_key == "bug_submit_feishu":
            requirement_url = str(decision.args.get("requirement_url") or "").strip()
            target = await _bind_bug_target(agent_session, requirement_url) if requirement_url else None
            if not agent_session.bug_target:
                _store_pending_bug(agent_session, message, decision.args)
                _append_message(session, agent_session.id, "assistant", "这条 bug 关联哪个飞书需求？把需求链接发我就行。")
                return
            await _submit_bug(session, agent_session, message, decision.args, target)
            handled = True
            continue
        if _is_pending_bug(pending):
            _clear_pending_bug(agent_session)
            pending = {}
        if decision.tool_key in {"aiphone_dispatch", "aiweb_dispatch"}:
            await _dispatch_external_executor(session, agent_session, message, decision)
            handled = True
            continue
        if decision.tool_key in {"aiphone_resource_probe", "aiweb_resource_probe"}:
            await _run_resource_probe(session, agent_session, message, decision)
            handled = True
            continue
        if decision.tool_key == "aiapi_run":
            await _dispatch_aiapi(session, agent_session, message, decision)
            handled = True
            continue
    if handled:
        return
    _append_message(
        session,
        agent_session.id,
        "assistant",
        "可以，直接说你想看什么就行。比如看手机里的一个页面、查个网页、跑个接口或者提 bug。",
    )


async def _decide_tool(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
) -> _ToolDecision:
    decisions = await _decide_tools(session, agent_session, message)
    return decisions[0] if decisions else _ToolDecision("", {}, "")


async def _decide_tools(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
) -> list[_ToolDecision]:
    text = message.content.lower()
    if _looks_like_phone_resource_query(text):
        return [_ToolDecision("aiphone_resource_probe", {"platform": _platform_from_text(text, is_web=False)})]
    if _looks_like_web_resource_query(text):
        return [_ToolDecision("aiweb_resource_probe", {"platform": _platform_from_text(text, is_web=True)})]
    llm_decisions = await _llm_decide(session, agent_session, message)
    if llm_decisions is not None:
        return llm_decisions
    if any(word in text for word in ("提bug", "提 bug", "提交bug", "提交 bug", "建单", "缺陷")):
        return [_ToolDecision("bug_submit_feishu", {"description": message.content})]
    if any(word in text for word in ("接口", "api", "http", "响应", "断言", "limit")):
        return [
            _ToolDecision(
                "aiapi_run",
                {
                    "title": _title_from_text(message.content, "AI API 任务"),
                    "steps_text": message.content,
                    "expected_result": "按用户描述判断接口行为是否符合预期。",
                },
            )
        ]
    if any(word in text for word in ("测试账号", "权益", "会员", "vip", "造数", "数据准备")):
        return [_ToolDecision(
            "",
            {},
            "当前开源版本未提供测试账号或业务数据准备工具，需要部署方根据自己的系统二次开发。",
        )]
    if any(word in text for word in ("网页", "web", "浏览器", "h5", "chrome", "safari", "firefox")):
        return [_ToolDecision("aiweb_dispatch", {"run_content": message.content})]
    if any(word in text for word in ("手机", "app", "安卓", "ios", "iphone", "执行", "跑", "测试", "验证", "打开", "检查", "操作")):
        return [_ToolDecision("aiphone_dispatch", {"run_content": message.content})]
    return [
        _ToolDecision(
            "",
            {},
            "可以，直接说你想看什么就行。比如手机里的页面、网页上的内容、接口请求，或者把某个问题提成 bug。",
        )
    ]


async def _llm_decide(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
) -> list[_ToolDecision] | None:
    client, settings = _llm_client()
    if client is None or not settings.llm_model:
        return None
    max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
    tools = _decision_tools_schema()
    tool_names = {
        str(tool.get("function", {}).get("name") or "")
        for tool in tools
    }
    try:
        messages = await _decision_messages(session, agent_session, message)
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.1,
                max_tokens=max_tokens,
            )
        )
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            content = (getattr(msg, "content", None) or "").strip()
            return [_ToolDecision("", {}, content)] if content else None
        decisions: list[_ToolDecision] = []
        for call in calls[:LLM_TOOL_CALL_LIMIT]:
            name = str(call.function.name or "")
            if name not in tool_names:
                continue
            args_text = call.function.arguments or "{}"
            args = json.loads(args_text)
            decisions.append(_ToolDecision(name, _safe_json_object(args)))
        return decisions or None
    except Exception:
        return None


def _decision_tools_schema() -> list[dict[str, Any]]:
    return [tool.tool_schema() for tool in AGENT_TOOL_SPECS]


async def _decision_messages(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
) -> list[dict[str, Any]]:
    rows = await _messages(session, agent_session.id)
    prior_messages = [
        item for item in rows
        if item.id != message.id and item.content
    ][-RECENT_CONTEXT_LIMIT:]
    messages: list[dict[str, Any]] = [{"role": "system", "content": DECISION_SYSTEM_PROMPT}]
    for item in prior_messages:
        role = "user" if item.role == "user" else "assistant"
        messages.append({"role": role, "content": _message_context_text(item)})
    messages.append({"role": "user", "content": _current_user_content(message)})
    return messages


def _message_context_text(message: AgentMessage) -> str:
    prefix = "用户" if message.role == "user" else "助手"
    text = _truncate(str(message.content or ""), 700)
    attachments = dict(message.attachments or {})
    hints: list[str] = []
    if attachments.get("report_url"):
        hints.append(f"报告：{attachments.get('report_url')}")
    if attachments.get("bug_url"):
        hints.append(f"bug：{attachments.get('bug_url')}")
    if attachments.get("status"):
        hints.append(f"状态：{attachments.get('status')}")
    suffix = f"\n附件摘要：{'；'.join(hints)}" if hints else ""
    return f"{prefix}：{text}{suffix}"


def _current_user_content(message: AgentMessage) -> Any:
    text = str(message.content or "").strip() or "用户只发了图片，请结合图片判断要不要调用工具。"
    images = _attachment_images(message.attachments)
    image_parts: list[dict[str, Any]] = []
    for image in images[:DECISION_IMAGE_LIMIT]:
        image_part = _model_image_part(image)
        if image_part:
            image_parts.append(image_part)
    if not image_parts:
        return text
    return [{"type": "text", "text": text}, *image_parts]


def _model_image_part(image: dict[str, Any]) -> dict[str, Any] | None:
    url = str(image.get("url") or "").strip()
    if not url:
        return None
    if url.startswith("data:image/"):
        return {"type": "image_url", "image_url": {"url": url}}
    data_uri = _local_media_data_uri(url, str(image.get("mime") or ""))
    if data_uri:
        return {"type": "image_url", "image_url": {"url": data_uri}}
    return None


def _local_media_data_uri(url: str, declared_mime: str = "") -> str | None:
    path = _local_media_path(url)
    if path is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > 8 * 1024 * 1024:
        return None
    mime = _image_mime(path, declared_mime)
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def _local_media_path(url: str) -> Path | None:
    parsed = urlparse(url)
    media_path = unquote(parsed.path if parsed.scheme else url)
    if not media_path.startswith("/media/"):
        return None
    relative = media_path.removeprefix("/media/").lstrip("/")
    if not relative:
        return None
    base_dir = Path(get_settings().repair_image_dir).resolve()
    candidate = (base_dir / relative).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _image_mime(path: Path, declared_mime: str = "") -> str:
    declared = declared_mime.split(";", 1)[0].strip().lower()
    if declared.startswith("image/"):
        return declared
    guessed = (mimetypes.guess_type(path.name)[0] or "").lower()
    if guessed.startswith("image/"):
        return guessed
    return "image/png"


def _looks_like_phone_resource_query(text: str) -> bool:
    lowered = str(text or "").lower()
    platform_hit = any(word in lowered for word in ("ios", "iphone", "android", "安卓", "harmony", "鸿蒙"))
    phone_hit = any(word in lowered for word in ("手机", "真机", "设备"))
    resource_hit = any(word in lowered for word in ("可用", "能用", "空闲", "占用", "资源", "有没有", "有吗", "哪些", "几台", "列表"))
    short_platform_followup = platform_hit and len(lowered.strip()) <= 24 and any(word in lowered for word in ("呢", "吗", "?"))
    return (phone_hit and resource_hit) or (platform_hit and resource_hit) or short_platform_followup


def _looks_like_web_resource_query(text: str) -> bool:
    lowered = str(text or "").lower()
    platform_hit = any(word in lowered for word in ("chrome", "safari", "firefox"))
    web_hit = any(word in lowered for word in ("浏览器", "网页", "web", "h5", "槽"))
    resource_hit = any(word in lowered for word in ("可用", "能用", "空闲", "占用", "资源", "有没有", "有吗", "哪些", "几个", "列表"))
    short_platform_followup = platform_hit and len(lowered.strip()) <= 24 and any(word in lowered for word in ("呢", "吗", "?"))
    return (web_hit and resource_hit) or (platform_hit and resource_hit) or short_platform_followup


def _platform_from_text(text: str, *, is_web: bool) -> str:
    lowered = str(text or "").lower()
    if is_web:
        if "safari" in lowered or "webkit" in lowered:
            return "safari"
        if "firefox" in lowered:
            return "firefox"
        if "chrome" in lowered:
            return "chrome"
        return ""
    if "ios" in lowered or "iphone" in lowered:
        return "ios"
    if "harmony" in lowered or "鸿蒙" in lowered:
        return "harmony"
    if "android" in lowered or "安卓" in lowered:
        return "android"
    return ""


def _normalize_executor_platform(value: Any, *, is_web: bool) -> str:
    text = str(value or "").strip().lower()
    allowed = {"chrome", "safari", "firefox"} if is_web else {"android", "ios", "harmony"}
    if text == "webkit":
        text = "safari"
    return text if text in allowed else ""


def _device_alias(device: dict[str, Any]) -> str:
    return str(device.get("alias") or device.get("serial") or "").strip()


def _device_platform(device: dict[str, Any]) -> str:
    return str(device.get("platform") or "").strip().lower()


def _is_busy_device(device: dict[str, Any]) -> bool:
    return str(device.get("occupancy") or "").strip().lower() == "busy"


def _platform_label(platform: str, *, is_web: bool) -> str:
    if is_web:
        return {"chrome": "Chrome", "safari": "Safari", "firefox": "Firefox"}.get(platform, platform or "浏览器")
    return {"android": "Android", "ios": "iOS", "harmony": "Harmony"}.get(platform, platform or "手机")


def _resource_kind(is_web: bool) -> str:
    return "浏览器槽" if is_web else "手机"


def _summarize_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "alias": _device_alias(device),
            "serial": str(device.get("serial") or ""),
            "platform": _device_platform(device),
            "occupancy": str(device.get("occupancy") or "idle"),
            "model": str(device.get("model") or ""),
            "os_version": str(device.get("osVersion") or device.get("os_version") or ""),
        }
        for device in devices
    ]


async def _run_resource_probe(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
    decision: _ToolDecision,
) -> None:
    is_web = decision.tool_key == "aiweb_resource_probe"
    platform = _normalize_executor_platform(
        decision.args.get("platform") or _platform_from_text(message.content, is_web=is_web),
        is_web=is_web,
    )
    result = await _list_agent_resources(is_web)
    devices = list(result.devices)
    content = _resource_probe_reply(devices, result.source, result.error, platform, is_web=is_web)
    dispatch = AgentDispatch(
        session_id=agent_session.id,
        message_id=message.id,
        tool_key=decision.tool_key,
        tool_kind="resource_probe",
        platform=platform or "",
        status="failed" if result.source == "unavailable" and not devices else "passed",
        input_args=decision.args,
        result_payload={
            "source": result.source,
            "error": result.error,
            "platform": platform,
            "devices": _summarize_devices(devices),
        },
    )
    session.add(dispatch)
    await session.flush()
    _append_message(
        session,
        agent_session.id,
        "assistant",
        content,
        {"tool_key": decision.tool_key, "resource": dispatch.result_payload},
        dispatch.id,
    )
    message.dispatch_id = dispatch.id


async def _list_agent_resources(is_web: bool) -> Any:
    return await (executions.list_aiweb_devices() if is_web else executions.list_aiphone_devices())


def _resource_probe_reply(
    devices: list[dict[str, Any]],
    source: str,
    error: str | None,
    platform: str,
    *,
    is_web: bool,
) -> str:
    kind = _resource_kind(is_web)
    if source == "unavailable" and not devices:
        suffix = f"：{_truncate(str(error or ''), 80)}" if error else "。"
        return f"当前无法获取{kind}资源{suffix}"
    visible = [device for device in devices if not platform or _device_platform(device) == platform]
    if platform:
        label = _platform_label(platform, is_web=is_web)
        if not visible:
            return f"当前没有可用 {label} {kind}。"
        return _resource_group_line(label, visible, kind)
    if not devices:
        return f"当前没有可用{kind}。"
    groups: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        groups.setdefault(_device_platform(device) or "unknown", []).append(device)
    order = ["chrome", "safari", "firefox"] if is_web else ["android", "ios", "harmony"]
    ordered_platforms = [item for item in order if item in groups] + [item for item in groups if item not in order]
    lines = [
        _resource_group_line(_platform_label(item, is_web=is_web), groups[item], kind)
        for item in ordered_platforms
    ]
    return "；".join(lines)


def _resource_group_line(label: str, devices: list[dict[str, Any]], kind: str) -> str:
    idle = [device for device in devices if not _is_busy_device(device)]
    busy = [device for device in devices if _is_busy_device(device)]
    unit = "台" if kind == "手机" else "个"
    parts: list[str] = []
    if idle:
        parts.append(f"{len(idle)} {unit}空闲：{_join_aliases(idle)}")
    if busy:
        parts.append(f"{len(busy)} {unit}占用中：{_join_aliases(busy)}")
    return f"{label} {kind}：" + "，".join(parts)


def _join_aliases(devices: list[dict[str, Any]], limit: int = 3) -> str:
    aliases = [_device_alias(device) for device in devices if _device_alias(device)]
    text = "、".join(aliases[:limit])
    if len(aliases) > limit:
        text += f" 等 {len(aliases)} 个"
    return text or f"{len(devices)} 个"


async def _select_executor_resource(is_web: bool, requested_platform: str = "") -> dict[str, Any]:
    result = await _list_agent_resources(is_web)
    devices = list(result.devices)
    kind = _resource_kind(is_web)
    if result.source == "unavailable" and not devices:
        detail = f"：{_truncate(str(result.error or ''), 80)}" if result.error else "。"
        return {
            "blocked": f"当前无法获取{kind}资源，未提交任务{detail}",
            "source": result.source,
            "error": result.error,
            "devices": [],
        }

    target_platform = requested_platform or _first_available_platform(devices, is_web=is_web)
    if not target_platform:
        return {
            "blocked": f"当前没有可用{kind}，未提交任务。",
            "source": result.source,
            "error": result.error,
            "devices": _summarize_devices(devices),
        }

    matched = [device for device in devices if _device_platform(device) == target_platform]
    if not matched:
        label = _platform_label(target_platform, is_web=is_web)
        return {
            "blocked": f"当前没有可用 {label} {kind}，未提交任务。",
            "source": result.source,
            "error": result.error,
            "platform": target_platform,
            "devices": _summarize_devices(devices),
        }

    idle = [device for device in matched if not _is_busy_device(device)]
    selected = idle or matched
    aliases = [_device_alias(device) for device in selected if _device_alias(device)]
    label = _platform_label(target_platform, is_web=is_web)
    if idle:
        start_message = f"我用 {label} {kind}去看，跑完告诉你。"
    else:
        start_message = f"{label} {kind}都在占用中，我已排队，跑完告诉你。"
    return {
        "platform": target_platform,
        "device_alias_pools": {target_platform: aliases},
        "start_message": start_message,
        "source": result.source,
        "error": result.error,
        "devices": _summarize_devices(selected),
    }


def _first_available_platform(devices: list[dict[str, Any]], *, is_web: bool) -> str:
    if not devices:
        return ""
    order = ["chrome", "safari", "firefox"] if is_web else ["android", "ios", "harmony"]
    idle_platforms = {_device_platform(device) for device in devices if not _is_busy_device(device)}
    all_platforms = {_device_platform(device) for device in devices}
    for platform in order:
        if platform in idle_platforms:
            return platform
    for platform in order:
        if platform in all_platforms:
            return platform
    return next(iter(all_platforms), "")


async def _dispatch_external_executor(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
    decision: _ToolDecision,
) -> None:
    settings = get_settings()
    is_web = decision.tool_key == "aiweb_dispatch"
    base_url = settings.aiweb_base_url if is_web else settings.aiphone_base_url
    callback_slug = "aiweb" if is_web else "aiphone"
    callback_token = uuid.uuid4().hex
    try:
        callback_base_url = executor_callback_base_url(
            settings,
            "ai_web" if is_web else "ai_phone",
            "AI Web" if is_web else "AI Phone",
        )
    except ValueError as exc:
        raise AgentError(str(exc)) from exc
    callback_url = f"{callback_base_url}/api/v1/agent/{callback_slug}/callback/{callback_token}"
    run_content = str(decision.args.get("run_content") or message.content).strip()
    if not run_content:
        raise AgentError("任务内容为空")
    requested_platform = _normalize_executor_platform(
        decision.args.get("platform") or _platform_from_text(run_content, is_web=is_web),
        is_web=is_web,
    )
    resource_selection = await _select_executor_resource(is_web, requested_platform)
    if resource_selection.get("blocked"):
        dispatch = AgentDispatch(
            session_id=agent_session.id,
            message_id=message.id,
            tool_key=decision.tool_key,
            tool_kind="external_executor",
            platform=requested_platform,
            status="blocked",
            input_args={"run_content": run_content, "requested_platform": requested_platform},
            result_payload=_safe_json_object(resource_selection),
        )
        session.add(dispatch)
        await session.flush()
        _append_message(
            session,
            agent_session.id,
            "assistant",
            str(resource_selection["blocked"]),
            {"tool_key": decision.tool_key, "resource": resource_selection},
            dispatch.id,
        )
        message.dispatch_id = dispatch.id
        return

    default_platform = str(resource_selection["platform"])
    device_alias_pools = dict(resource_selection["device_alias_pools"])
    function_map = await _resolve_agent_function_map_context(session, message)
    submitted_item = {
        "caseId": f"agent-{uuid.uuid4().hex[:12]}",
        "caseName": _title_from_text(run_content, "OS Agent 任务"),
        "runContent": run_content,
        "platforms": [default_platform],
    }
    request_payload = {
        "submissionName": f"{'AI Web' if is_web else 'AI Phone'} {datetime.now():%Y%m%d-%H%M%S}",
        "callbackUrl": callback_url,
        "cacheMode": "off",
        "retryMax": 0,
        "deviceAliasPools": device_alias_pools,
        "items": [submitted_item],
    }
    if function_map.context:
        request_payload["functionMapContext"] = function_map.context
    dispatch = AgentDispatch(
        session_id=agent_session.id,
        message_id=message.id,
        tool_key=decision.tool_key,
        tool_kind="external_executor",
        callback_token=callback_token,
        platform=default_platform,
        status="running",
        input_args={**request_payload, "agentFunctionMapContext": function_map.meta},
    )
    session.add(dispatch)
    await session.flush()
    await session.commit()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{base_url.rstrip('/')}/api/submissions", json=request_payload)
            response.raise_for_status()
            response_payload = response.json()
    except httpx.HTTPStatusError as exc:
        error = f"{'AI Web' if is_web else 'AI Phone'} 提交失败：{exc.response.text}"
        _mark_dispatch_failed(dispatch, error)
        raise AgentError(error) from exc
    except Exception as exc:
        error = f"{'AI Web' if is_web else 'AI Phone'} 提交失败：{exc}"
        _mark_dispatch_failed(dispatch, error)
        raise AgentError(error) from exc
    submission_id = str(response_payload.get("submissionId") or response_payload.get("id") or "")
    if not submission_id:
        _mark_dispatch_failed(dispatch, "执行器未返回 submissionId")
        raise AgentError("执行器未返回 submissionId")
    dispatch.submission_id = submission_id
    dispatch.result_payload = _safe_json_object(response_payload)
    assistant = _append_message(
        session,
        agent_session.id,
        "assistant",
        str(resource_selection["start_message"]),
        {
            "tool_key": decision.tool_key,
            "submission_id": submission_id,
            "resource": resource_selection,
            "function_map_context": function_map.meta,
        },
    )
    dispatch.message_id = message.id
    assistant.dispatch_id = dispatch.id
    message.dispatch_id = dispatch.id


async def _dispatch_aiapi(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
    decision: _ToolDecision,
) -> None:
    function_map = await _resolve_agent_function_map_context(session, message)
    input_args = {
        key: value
        for key, value in dict(decision.args or {}).items()
        if key != "function_map_context"
    }
    if function_map.context:
        input_args["function_map_context"] = function_map.context
    input_args["agentFunctionMapContext"] = function_map.meta
    dispatch = AgentDispatch(
        session_id=agent_session.id,
        message_id=message.id,
        tool_key="aiapi_run",
        tool_kind="internal_executor",
        run_id=f"agent-aiapi-{uuid.uuid4().hex}",
        platform="api",
        status="running",
        input_args=input_args,
    )
    session.add(dispatch)
    await session.flush()
    _append_message(
        session,
        agent_session.id,
        "assistant",
        "我先跑一下接口，结束后把结论和报告发回来。",
        {"tool_key": "aiapi_run", "function_map_context": function_map.meta},
        dispatch.id,
    )
    message.dispatch_id = dispatch.id
    await session.flush()
    session.info.setdefault("agent_aiapi_dispatch_ids", []).append(dispatch.id)


def _launch_aiapi_dispatch(dispatch_id: int) -> None:
    task = asyncio.create_task(_run_aiapi_dispatch(dispatch_id))
    task.add_done_callback(_consume_task_exception)


def _mark_dispatch_failed(dispatch: AgentDispatch, error: str) -> None:
    dispatch.status = "failed"
    dispatch.summary = error
    dispatch.result_payload = {"error": error}
    dispatch.finished_at = func.now()


async def _run_aiapi_dispatch(dispatch_id: int) -> None:
    async with database.AsyncSessionLocal() as session:
        dispatch = await session.get(AgentDispatch, dispatch_id)
        if dispatch is None:
            return
        args = dict(dispatch.input_args or {})
        payload = AIAPIDirectRunIn(
            title=str(args.get("title") or "OS Agent API 任务"),
            preconditions=str(args.get("preconditions") or ""),
            steps_text=str(args.get("steps_text") or args.get("run_content") or ""),
            expected_result=str(args.get("expected_result") or "按用户描述判断是否符合预期。"),
            function_map_context=str(args.get("function_map_context") or ""),
            submission_name=str(args.get("title") or "OS Agent API 任务"),
            return_report_html=False,
        )
        try:
            result = await run_direct_aiapi(payload)
            dispatch.status = "passed" if result.status == "success" else "failed"
            dispatch.run_id = result.run_id
            dispatch.report_url = result.report_url
            dispatch.summary = result.status_reason
            dispatch.result_payload = result.model_dump()
            dispatch.finished_at = func.now()
            await _write_result_message(session, dispatch)
            await session.commit()
        except Exception as exc:
            dispatch.status = "failed"
            dispatch.summary = str(exc)
            dispatch.result_payload = {"error": str(exc)}
            dispatch.finished_at = func.now()
            await _write_result_message(session, dispatch)
            await session.commit()


async def _submit_bug(
    session: AsyncSession,
    agent_session: AgentSession,
    message: AgentMessage,
    args: dict[str, Any],
    target: Any | None,
) -> None:
    user = await session.get(User, agent_session.user_id)
    if user is None:
        raise AgentError("当前用户不存在")
    if not getattr(user, "feishu_user_key", None):
        raise AgentError("当前用户没有绑定飞书 user_key，不能作为报告人提交 bug")
    if target is None:
        target_payload = dict(agent_session.bug_target or {})
        if not target_payload:
            raise AgentError("提交 bug 前需要先绑定飞书需求链接")
        target = _TargetFromPayload(target_payload)
    else:
        target_payload = _target_to_payload(target)
        agent_session.bug_target = target_payload

    cfg = formal_bug.load_issue_config().get(target.project_key)
    if cfg is None or not cfg.template_id:
        raise AgentError(f"空间 {target.project_key} 未配置 bug 模板")

    description_source = str(args.get("description") or message.content or "").strip()
    pending_attachments = (
        args.get("_pending_attachments")
        if isinstance(args.get("_pending_attachments"), dict)
        else {}
    )
    bug_attachments = _merge_bug_attachments(pending_attachments, message.attachments)
    recent_evidence = (
        await _latest_result_evidence(session, agent_session.id, prefer_failed=True)
        if _should_use_recent_evidence(description_source)
        else {}
    )
    title = await _bug_title(description_source)
    image_paths = await _agent_bug_image_paths(bug_attachments, recent_evidence)
    evidence_draft = _bug_description(description_source, bug_attachments, agent_session, recent_evidence, title)
    description = await _bug_description_from_evidence(evidence_draft)
    bug_snapshot = AgentBugSubmission(
        session_id=agent_session.id,
        source_message_id=message.id,
        target_payload=target_payload,
        title=title,
        description=description,
        status="preparing",
    )
    session.add(bug_snapshot)
    await session.flush()

    create_pairs: list[dict[str, Any]] = [{"field_key": cfg.description_field, "field_value": description}]
    if cfg.link_requirement_field:
        create_pairs.append(
            {
                "field_key": cfg.link_requirement_field,
                "field_value": formal_bug._format_value(
                    cfg.link_requirement_field_type,
                    formal_bug._requirement_link_value(target.work_item_id, cfg.link_requirement_field_type),
                ),
            }
        )
    create_pairs.extend(await _bug_required_pairs(cfg, target, user))

    client = fp.FeishuProjectClient()
    if not client.enabled:
        raise AgentError("飞书项目凭证未配置")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            token = await client._plugin_token(http)
            new_id = await client.create_work_item(
                http,
                token,
                cfg.project_key,
                cfg.work_item_type,
                cfg.template_id,
                title,
                create_pairs,
            )
        bug_url = f"{get_settings().feishu_project_site_domain.rstrip('/')}/{cfg.project_key}/{cfg.work_item_type}/detail/{new_id}"
        if image_paths:
            async with httpx.AsyncClient(timeout=60) as image_http:
                await formal_bug.append_rich_text_images_to_bug(
                    image_http,
                    cfg.project_key,
                    cfg.work_item_type,
                    new_id,
                    cfg.description_field,
                    description,
                    image_paths,
                    log_context="agent",
                )
        bug_snapshot.status = "submitted"
        bug_snapshot.bug_url = bug_url
        bug_snapshot.bug_external_id = str(new_id)
        bug_snapshot.updated_at = func.now()
        _append_message(
            session,
            agent_session.id,
            "result",
            f"已提交 bug：{bug_url}\n\n我按当前对话和证据自动整理了标题与描述，字段可能不如手动提交精确。",
            {"bug_url": bug_url, "bug_id": str(new_id), "target": target_payload},
        )
    except Exception as exc:
        bug_snapshot.status = "failed"
        bug_snapshot.error = str(exc)
        bug_snapshot.updated_at = func.now()
        raise AgentError(f"提交 bug 失败：{exc}") from exc


async def _bug_required_pairs(cfg: formal_bug.SpaceIssueConfig, target: Any, user: User) -> list[dict[str, Any]]:
    client = fp.FeishuProjectClient()
    if not client.enabled:
        return []
    pairs: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=25) as http:
        token = await client._plugin_token(http)
        raw_meta = await client.get_create_meta(http, token, cfg.project_key, cfg.work_item_type)
    meta_norm = formal_bug._normalize_meta(raw_meta)
    for meta in meta_norm:
        key = str(meta.get("field_key") or "")
        typ = str(meta.get("type") or "")
        if key in {cfg.title_field, cfg.description_field, cfg.link_requirement_field}:
            continue
        src = cfg.field_sources.get(key) or {}
        semantic = formal_bug._field_semantic(cfg, meta, src)
        raw: Any = None
        if semantic == "submitter":
            raw = [str(user.feishu_user_key)]
        elif semantic == "linked_requirement":
            raw = formal_bug._requirement_link_value(target.work_item_id, typ)
        elif src.get("source") == "fixed":
            raw = src.get("value")
        elif src.get("source") == "current_month":
            raw = formal_bug._current_month_label()
        elif src.get("source") == "current_month_zh":
            raw = formal_bug._current_month_zh_label()
        value = formal_bug._format_value(typ, raw)
        if value not in (None, "", [], {}):
            pairs.append({"field_key": key, "field_value": value})
    return pairs


async def _write_result_message(session: AsyncSession, dispatch: AgentDispatch) -> None:
    report_url = _absolute_url(dispatch.report_url) if dispatch.report_url else ""
    evidence_summary = ""
    evidence: ReportEvidence | None = None
    if report_url:
        evidence = await read_report(report_url, dispatch.tool_key)
        evidence_summary = evidence.summary
    summary = await _summarize_result(dispatch, evidence_summary)
    attachments = {
        "tool_key": dispatch.tool_key,
        "status": dispatch.status,
        "report_url": dispatch.report_url,
        "key_images": _select_key_images(dispatch, evidence),
        "result": dispatch.result_payload,
    }
    _append_message(session, dispatch.session_id, "result", summary, attachments, dispatch.id)
    dispatch.summary = summary


async def _summarize_result(dispatch: AgentDispatch, evidence_summary: str) -> str:
    client, settings = _llm_client()
    fallback = _fallback_result_summary(dispatch, evidence_summary)
    if client is None or not settings.llm_model:
        return fallback
    max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
    try:
        payload = {
            "tool": dispatch.tool_key,
            "status": dispatch.status,
            "report_url": dispatch.report_url,
            "summary": dispatch.summary,
            "evidence": evidence_summary[:3000],
            "result": dispatch.result_payload,
        }
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": RESULT_SYSTEM_PROMPT,
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
            )
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or fallback
    except Exception:
        return fallback


def _fallback_result_summary(dispatch: AgentDispatch, evidence_summary: str) -> str:
    label = {
        "aiphone_dispatch": "手机任务",
        "aiweb_dispatch": "AI Web 任务",
        "aiapi_run": "接口任务",
    }.get(dispatch.tool_key, dispatch.tool_key)
    if dispatch.status == "passed":
        status_text = "已完成" if dispatch.tool_key == "aiweb_dispatch" else "完成"
    elif dispatch.status == "failed":
        status_text = "失败"
    else:
        status_text = str(dispatch.status or "")
    parts = [f"{label}{status_text}。"]
    cleaned_evidence = _clean_agent_result_evidence(dispatch.tool_key, evidence_summary)
    if cleaned_evidence:
        parts.append(_compact_result_evidence(cleaned_evidence))
    return "\n".join(part for part in parts if part)


def _compact_result_evidence(evidence_summary: str, limit: int = 120) -> str:
    lines = []
    for raw_line in str(evidence_summary or "").splitlines():
        line = re.sub(r"^[✅🔍📌\-\d.、\s]+", "", raw_line.strip().strip("`")).strip()
        if not line:
            continue
        compact = re.sub(r"\s+", "", line).lower()
        if compact.rstrip("：:") in {"关键信息", "关键执行信息"}:
            continue
        if any(marker in compact for marker in ("http://", "https://", "submissionid", "提交id", "完整报告")):
            continue
        lines.append(line)
        if len(lines) >= 2:
            break
    text = "；".join(line for line in lines if line)
    return text[:limit] + ("..." if len(text) > limit else "")


def _select_key_images(dispatch: AgentDispatch, evidence: ReportEvidence | None) -> list[dict[str, Any]]:
    if evidence is None:
        return []
    images = list(evidence.image_evidence)
    if not images and evidence.image_urls:
        images = [ReportImageEvidence(index=index, url=url) for index, url in enumerate(evidence.image_urls)]
    if not images:
        return []

    status = str(getattr(dispatch, "status", "") or "")
    platform = str(getattr(dispatch, "platform", "") or "")
    scored = [
        (_image_relevance_score(image, status, len(images)), image)
        for image in images
    ]
    positive = [(score, image) for score, image in scored if score > 0]
    if positive:
        selected = [
            image
            for _score, image in sorted(positive, key=lambda item: (-item[0], item[1].index))[:3]
        ]
    elif status == "failed":
        selected = images[-2:]
    elif status == "passed":
        selected = images[-1:]
    else:
        selected = []

    return [
        {
            "image": image.url,
            "platform": platform,
            "index": image.index,
            "caption": _image_caption(image, status),
        }
        for image in sorted(selected, key=lambda item: item.index)
    ]


def _image_relevance_score(image: ReportImageEvidence, status: str, image_count: int) -> int:
    text = image.context.lower()
    score = 0
    failure_keywords = (
        "失败",
        "报错",
        "错误",
        "异常",
        "超时",
        "断言",
        "未找到",
        "找不到",
        "无法",
        "失败原因",
        "error",
        "exception",
        "timeout",
        "failed",
        "not found",
    )
    success_keywords = (
        "成功",
        "完成",
        "通过",
        "最终",
        "结果",
        "查询结果",
        "详情",
        "显示",
        "已打开",
        "passed",
        "success",
        "done",
    )
    if status == "failed":
        score += sum(20 for keyword in failure_keywords if keyword in text)
        if score:
            score += image.index
    elif status == "passed":
        score += sum(16 for keyword in success_keywords if keyword in text)
        if score:
            score += image.index * 2
    else:
        score += 8 if text else 0
    if score and image.index == image_count - 1:
        score += 6
    return score


def _image_caption(image: ReportImageEvidence, status: str) -> str:
    if status == "failed":
        return "失败现场" if _image_relevance_score(image, status, image.index + 1) > 0 else "执行截图"
    if status == "passed":
        return "结果截图"
    return "关键截图"


def _clean_agent_result_evidence(tool_key: str, evidence_summary: str) -> str:
    text = str(evidence_summary or "").strip()
    if not text:
        return ""
    if tool_key != "aiweb_dispatch":
        return text

    text = re.sub(r"Case\s*Flow\s*Agent\s*AI\s*Web", "AI Web", text, flags=re.IGNORECASE)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result: list[str] = []
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line).lower()
        is_redundant_opening = (
            index == 0
            and ("aiweb" in compact)
            and ("任务" in line)
            and any(marker in line for marker in ("执行完成", "已经完成", "已完成", "运行成功"))
        )
        if is_redundant_opening:
            continue
        result.append(line)
    return "\n".join(result)


async def _bind_bug_target(agent_session: AgentSession, url: str) -> Any:
    try:
        target = await _resolve_target_from_url(url, require_configured_requirement=True)
    except QuickBugSubmitError as exc:
        raise AgentError(str(exc)) from exc
    agent_session.bug_target = _target_to_payload(target)
    return target


class _TargetFromPayload:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.url = str(payload.get("url") or "")
        self.project_key = str(payload.get("project_key") or "")
        self.work_item_type = str(payload.get("work_item_type") or "story")
        self.work_item_id = int(payload.get("work_item_id") or 0)
        self.raw = dict(payload.get("raw") or {})


def _append_message(
    session: AsyncSession,
    session_id: int,
    role: str,
    content: str,
    attachments: dict[str, Any] | None = None,
    dispatch_id: int | None = None,
) -> AgentMessage:
    msg = AgentMessage(
        session_id=session_id,
        role=role,
        content=content,
        attachments=_safe_json_object(attachments),
        dispatch_id=dispatch_id,
    )
    session.add(msg)
    return msg


async def _messages(session: AsyncSession, session_id: int) -> list[AgentMessage]:
    rows = await session.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.id.asc())
    )
    return list(rows.scalars().all())


def _llm_client() -> tuple[Any | None, Any]:
    settings = get_settings()
    api_key = settings.llm_api_key or os.environ.get("ARK_API_KEY") or os.environ.get("CASE_FLOW_LLM_API_KEY")
    if not api_key:
        return None, settings
    try:
        from openai import OpenAI
    except Exception:
        return None, settings
    kwargs: dict[str, Any] = {"api_key": api_key}
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    return OpenAI(**kwargs), settings


def _consume_task_exception(task: asyncio.Task[None]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        return


async def _bug_title(text: str) -> str:
    client, settings = _llm_client()
    fallback = _title_from_text(text, "OS Agent 提交的缺陷")
    if client is None or not settings.llm_model:
        return fallback[:60]
    max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
    try:
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": BUG_TITLE_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
                max_tokens=max_tokens,
            )
        )
        return ((resp.choices[0].message.content or "").strip() or fallback)[:60]
    except Exception:
        return fallback[:60]


async def _bug_description_from_evidence(evidence_draft: str) -> str:
    client, settings = _llm_client()
    if client is None or not settings.llm_model:
        return evidence_draft
    max_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)
    try:
        resp = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": BUG_DESCRIPTION_SYSTEM_PROMPT},
                    {"role": "user", "content": evidence_draft[:5000]},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
            )
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or evidence_draft
    except Exception:
        return evidence_draft


async def _latest_result_evidence(
    session: AsyncSession,
    session_id: int,
    prefer_failed: bool = False,
) -> dict[str, Any]:
    result: AgentMessage | None = None
    if prefer_failed:
        rows = await session.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id, AgentMessage.role == "result")
            .order_by(AgentMessage.id.desc())
            .limit(8)
        )
        for item in rows.scalars().all():
            attachments = dict(item.attachments or {})
            if attachments.get("status") == "failed":
                result = item
                break
    if result is None:
        result = await session.scalar(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id, AgentMessage.role == "result")
            .order_by(AgentMessage.id.desc())
            .limit(1)
        )
    if result is None:
        return {}
    attachments = dict(result.attachments or {})
    return {
        "content": result.content,
        "report_url": attachments.get("report_url"),
        "key_images": attachments.get("key_images") or [],
        "status": attachments.get("status"),
    }


def _should_use_recent_evidence(text: str) -> bool:
    return any(word in str(text or "") for word in ("刚才", "上次", "最近", "这个", "那个", "这次", "失败", "报告", "截图"))


def _merge_bug_attachments(*attachments_list: dict[str, Any] | None) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    for attachments in attachments_list:
        for image in _attachment_images(attachments):
            url = str(image.get("url") or "").strip()
            key = url or str(image.get("thumbnail_url") or image.get("filename") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            images.append(dict(image))
    return {"images": images} if images else {}


async def _agent_bug_image_paths(
    attachments: dict[str, Any],
    recent_evidence: dict[str, Any] | None,
) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    seen: set[str] = set()

    async def add_image(label: str, url: str, mime: str = "") -> None:
        if not url:
            return
        path = _local_media_path(url)
        if path is None and url.startswith(("http://", "https://")):
            path = await _download_agent_bug_image(url, mime)
        if path is None:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        paths.append((label, path))

    for image in _attachment_images(attachments):
        label = str(image.get("filename") or "用户补充截图")
        await add_image(label, str(image.get("url") or ""), str(image.get("mime") or ""))

    evidence = recent_evidence or {}
    key_images = evidence.get("key_images")
    if isinstance(key_images, list):
        for item in key_images:
            if not isinstance(item, dict):
                continue
            label = str(item.get("platform") or item.get("caption") or "关键截图")
            await add_image(label, str(item.get("image") or ""))
    return paths


async def _download_agent_bug_image(url: str, declared_mime: str = "") -> Path | None:
    settings = get_settings()
    base_dir = Path(settings.repair_image_dir) / "agent_bug_evidence"
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("agent bug image download failed url=%s error=%s", url, exc)
        return None

    content_type = str(resp.headers.get("content-type") or declared_mime or "")
    url_path = Path(urlparse(url).path)
    url_suffix = url_path.suffix.lower()
    content_type_main = content_type.split(";", 1)[0].strip().lower()
    suffix = formal_bug._upload_image_suffix(url_path.name, content_type)
    if content_type_main and not content_type_main.startswith("image/") and url_suffix not in {
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".webp",
    }:
        logger.warning("agent bug image download skipped non-image url=%s content_type=%s", url, content_type)
        return None
    filename = f"agent_bug_{uuid.uuid4().hex[:12]}{suffix}"
    path = base_dir / filename
    try:
        path.write_bytes(resp.content)
    except OSError as exc:
        logger.warning("agent bug image download write failed url=%s path=%s error=%s", url, path, exc)
        return None
    return path


def _bug_description(
    text: str,
    attachments: dict[str, Any],
    agent_session: AgentSession,
    recent_evidence: dict[str, Any] | None = None,
    title: str = "",
) -> str:
    parts: list[str] = []
    user_text = _bug_description_user_text(text, title)
    if user_text:
        parts.append(f"**用户补充说明**\n{user_text}")
    images = _attachment_images(attachments)
    if images:
        parts.append(f"**用户补充截图**\n用户随消息上传了 {len(images)} 张截图，已由系统附加到 bug 描述。")
    evidence = recent_evidence or {}
    if evidence:
        evidence_lines = [str(evidence.get("content") or "").strip()]
        if evidence.get("status"):
            evidence_lines.append(f"状态：{evidence.get('status')}")
        if evidence.get("report_url"):
            evidence_lines.append(f"报告：{evidence.get('report_url')}")
        key_images = evidence.get("key_images")
        if isinstance(key_images, list) and key_images:
            urls = [
                str(item.get("image") or "")
                for item in key_images
                if isinstance(item, dict) and item.get("image")
            ]
            if urls:
                evidence_lines.append(f"关键截图：{len(urls)} 张，已由系统附加到 bug 描述。")
        parts.append("**最近一次 OS Agent 结果**\n" + "\n".join(line for line in evidence_lines if line))
    return "\n\n".join(parts)


def _bug_description_user_text(text: str, title: str = "") -> str:
    cleaned = re.sub(r"https?://[^\s]+/[^\s]+/[^\s]+/detail/\d+", "", str(text or ""))
    title_text = str(title or "").strip()
    if len(title_text) >= 4:
        cleaned = cleaned.replace(title_text, "")
    cleaned = re.sub(r"(?im)^\s*(?:标题|title)\s*[:：]\s*", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*(?:需求链接|关联需求)\s*[:：]\s*", "", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(" \t\r\n，,。；;、")


async def _run_command(argv: list[str], timeout: int) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise AgentError(f"命令超时：{' '.join(argv[:3])}")
    return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), int(proc.returncode)


def _safe_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _attachment_images(attachments: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (attachments or {}).get("images")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _image_suffix(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return suffix
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(content_type, ".png")


def _public_media_url(path: str) -> str:
    base = str(get_settings().public_base_url or "").rstrip("/")
    return f"{base}{path}" if base else path


def _absolute_url(url: str | None) -> str:
    if not url:
        return ""
    if str(url).startswith(("http://", "https://")):
        return str(url)
    return _public_media_url(str(url))


def _first_feishu_url(text: str) -> str:
    match = re.search(r"https?://[^\s]+/[^\s]+/[^\s]+/detail/\d+", str(text or ""))
    return match.group(0).rstrip("。)，)") if match else ""


def _title_from_text(text: str, fallback: str) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return (clean[:40] or fallback).strip()


def _terminal_status(state: str) -> str:
    lowered = str(state or "").lower()
    if lowered in {"success", "passed", "pass", "done"}:
        return "passed"
    if lowered in {"failed", "fail", "error", "cancelled", "timeout", "expired"}:
        return "failed"
    return "running"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."
