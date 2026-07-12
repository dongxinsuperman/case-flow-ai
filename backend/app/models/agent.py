from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", name="agent_sessions_user_id_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="OS Agent")
    default_tool: Mapped[str | None] = mapped_column(Text)
    default_resource_pool: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    function_context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bug_target: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pending_action: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list[AgentMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AgentMessage.id",
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dispatch_id: Mapped[int | None] = mapped_column(Integer)
    attachments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[AgentSession] = relationship(back_populates="messages")


class AgentDispatch(Base):
    __tablename__ = "agent_dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("agent_messages.id", ondelete="SET NULL"))
    tool_key: Mapped[str] = mapped_column(Text, nullable=False)
    tool_kind: Mapped[str] = mapped_column(Text, nullable=False)
    submission_id: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(Text)
    callback_token: Mapped[str | None] = mapped_column(Text, unique=True)
    platform: Mapped[str | None] = mapped_column(Text)
    resource_pool: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    report_url: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    input_args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_urls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentBugSubmission(Base):
    __tablename__ = "agent_bug_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("agent_messages.id", ondelete="SET NULL"))
    target_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    editable_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="preparing")
    bug_url: Mapped[str | None] = mapped_column(Text)
    bug_external_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
