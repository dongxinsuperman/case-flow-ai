from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class QuickSession(Base):
    __tablename__ = "quick_sessions"

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    suite_title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    function_files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    feishu_requirement_url: Mapped[str | None] = mapped_column(Text)
    feishu_bug_url: Mapped[str | None] = mapped_column(Text)
    feishu_target: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    current_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cases: Mapped[list[QuickCase]] = relationship(back_populates="session", cascade="all, delete-orphan")


class QuickCase(Base):
    __tablename__ = "quick_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("quick_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    suite_title: Mapped[str] = mapped_column(Text, nullable=False)
    path_nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    core_nodes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_title: Mapped[str] = mapped_column(Text, nullable=False)
    clean_title: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    manual: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="imported")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[QuickSession] = relationship(back_populates="cases")
    body: Mapped[QuickCaseBody] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        uselist=False,
    )
    steps: Mapped[list[QuickCaseStep]] = relationship(back_populates="case", cascade="all, delete-orphan")
    work_item: Mapped[QuickCaseWorkItem] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        uselist=False,
    )


class QuickCaseBody(Base):
    __tablename__ = "quick_case_bodies"

    case_id: Mapped[int] = mapped_column(ForeignKey("quick_cases.id", ondelete="CASCADE"), primary_key=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    preconditions: Mapped[str] = mapped_column(Text, nullable=False)
    steps_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False)

    case: Mapped[QuickCase] = relationship(back_populates="body")


class QuickCaseStep(Base):
    __tablename__ = "quick_case_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("quick_cases.id", ondelete="CASCADE"), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_text: Mapped[str] = mapped_column(Text, nullable=False)

    case: Mapped[QuickCase] = relationship(back_populates="steps")


class QuickCaseWorkItem(Base):
    __tablename__ = "quick_case_work_items"

    case_id: Mapped[int] = mapped_column(ForeignKey("quick_cases.id", ondelete="CASCADE"), primary_key=True)
    execution_status: Mapped[str] = mapped_column(Text, nullable=False, default="not_run")
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    lifecycle_state: Mapped[str] = mapped_column(Text, nullable=False, default="待验证")
    attention_reason: Mapped[str | None] = mapped_column(Text)
    case_type: Mapped[str] = mapped_column(Text, nullable=False, default="auto")
    execution_target: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    tag_source: Mapped[str | None] = mapped_column(Text)
    tag_reason: Mapped[str | None] = mapped_column(Text)
    tag_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    report_url: Mapped[str | None] = mapped_column(Text)
    failure_type: Mapped[str | None] = mapped_column(Text)
    failure_summary: Mapped[str | None] = mapped_column(Text)
    active_execution_batch_id: Mapped[int | None] = mapped_column(Integer)
    external_submission_id: Mapped[str | None] = mapped_column(Text)
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bug_url: Mapped[str | None] = mapped_column(Text)
    bug_external_id: Mapped[str | None] = mapped_column(Text)
    bugs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    case: Mapped[QuickCase] = relationship(back_populates="work_item")


class QuickRepairDraft(Base):
    __tablename__ = "quick_repair_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("quick_cases.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    model_name: Mapped[str | None] = mapped_column(Text)
    original_steps: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposed_steps: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    case_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    report_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    gate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    analysis_trace: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    diagnosis_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuickBugDraft(Base):
    __tablename__ = "quick_bug_drafts"

    case_id: Mapped[int] = mapped_column(ForeignKey("quick_cases.id", ondelete="CASCADE"), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    editable_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuickExecutionBatch(Base):
    __tablename__ = "quick_execution_batches"
    __table_args__ = (
        UniqueConstraint(
            "executor",
            "submission_id",
            name="quick_execution_batches_executor_submission_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("quick_sessions.session_id", ondelete="CASCADE"),
    )
    submission_id: Mapped[str] = mapped_column(Text, nullable=False)
    submission_name: Mapped[str | None] = mapped_column(Text)
    callback_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    executor: Mapped[str] = mapped_column(Text, nullable=False, default="ai_phone")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="submitted")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_report_url: Mapped[str | None] = mapped_column(Text)
    raw_request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_callback: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    raw_submission: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class QuickExecutionItem(Base):
    __tablename__ = "quick_execution_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "external_case_id",
            "platform",
            name="quick_execution_items_batch_case_platform_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("quick_execution_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[int | None] = mapped_column(ForeignKey("quick_cases.id", ondelete="SET NULL"))
    external_case_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    status_reason: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(Text)
    report_url: Mapped[str | None] = mapped_column(Text)
    device_alias_pool: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    raw_item: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
