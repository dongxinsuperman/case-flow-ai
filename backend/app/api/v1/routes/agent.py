from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.settings import get_settings
from app.schemas.agent import AgentCallbackOut, AgentMessageCreateIn, AgentSessionOut, AgentToolListOut, AgentUploadOut
from app.services import agent

def require_os_agent_enabled() -> None:
    if not get_settings().os_agent_enabled:
        raise HTTPException(status_code=404, detail="OS Agent 未启用")


router = APIRouter(prefix="/agent", dependencies=[Depends(require_os_agent_enabled)])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserIdQuery = Annotated[int, Query(ge=1)]


@router.get("/session", response_model=AgentSessionOut)
async def get_agent_session(session: SessionDep, user_id: UserIdQuery = 1) -> AgentSessionOut:
    try:
        return AgentSessionOut.model_validate(await agent.session_detail(session, user_id))
    except agent.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/session", response_model=AgentSessionOut)
async def reset_agent_session(session: SessionDep, user_id: UserIdQuery = 1) -> AgentSessionOut:
    try:
        return AgentSessionOut.model_validate(await agent.reset_session_context(session, user_id))
    except agent.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/messages", response_model=AgentSessionOut)
async def post_agent_message(
    payload: AgentMessageCreateIn,
    session: SessionDep,
    user_id: UserIdQuery = 1,
) -> AgentSessionOut:
    try:
        return AgentSessionOut.model_validate(
            await agent.post_message(session, user_id, payload.content, payload.attachments, payload.context_ref)
        )
    except agent.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/uploads", response_model=AgentUploadOut)
async def upload_agent_images(
    files: Annotated[list[UploadFile], File()],
    user_id: UserIdQuery = 1,
) -> AgentUploadOut:
    try:
        return AgentUploadOut(images=await agent.upload_images(files))
    except agent.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tools", response_model=AgentToolListOut)
async def agent_tools() -> AgentToolListOut:
    return AgentToolListOut(tools=agent.TOOL_DEFS)


@router.post("/aiphone/callback/{callback_token}", response_model=AgentCallbackOut)
async def agent_aiphone_callback(
    callback_token: str,
    payload: dict,
    session: SessionDep,
) -> AgentCallbackOut:
    result = await agent.apply_executor_callback(
        session,
        "aiphone_dispatch",
        get_settings().aiphone_base_url,
        callback_token,
        payload,
    )
    return AgentCallbackOut.model_validate(result)


@router.post("/aiweb/callback/{callback_token}", response_model=AgentCallbackOut)
async def agent_aiweb_callback(
    callback_token: str,
    payload: dict,
    session: SessionDep,
) -> AgentCallbackOut:
    result = await agent.apply_executor_callback(
        session,
        "aiweb_dispatch",
        get_settings().aiweb_base_url,
        callback_token,
        payload,
    )
    return AgentCallbackOut.model_validate(result)
