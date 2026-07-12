from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentImageAttachment(BaseModel):
    url: str
    thumbnail_url: str | None = None
    filename: str = ""
    mime: str = ""
    size: int = 0


class AgentMessageOut(BaseModel):
    id: int
    role: str
    content: str
    dispatch_id: int | None = None
    attachments: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentSessionSummaryOut(BaseModel):
    id: int
    user_id: int
    title: str
    bug_target: dict[str, Any] = Field(default_factory=dict)
    pending_action: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentSessionOut(BaseModel):
    session: AgentSessionSummaryOut
    messages: list[AgentMessageOut] = Field(default_factory=list)


class AgentContextRef(BaseModel):
    mode: Literal["standard", "quick"]
    requirement_item_id: int | None = None
    quick_session_id: str | None = None
    use_current_function_map: bool = True


class AgentMessageCreateIn(BaseModel):
    content: str
    attachments: dict[str, Any] = Field(default_factory=dict)
    context_ref: AgentContextRef | None = None


class AgentUploadOut(BaseModel):
    images: list[AgentImageAttachment] = Field(default_factory=list)


class AgentCallbackOut(BaseModel):
    handled: bool
    event: str
    dispatch_id: int | None = None
    submission_id: str | None = None


class AgentToolListOut(BaseModel):
    tools: list[dict[str, Any]] = Field(default_factory=list)
