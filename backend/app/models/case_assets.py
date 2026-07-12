from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint(
            "requirement_item_id",
            "source_name",
            name="import_batches_requirement_source_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suite_title: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text)
    project_name: Mapped[str | None] = mapped_column(Text)
    feature_name: Mapped[str | None] = mapped_column(Text)
    requirement_item_id: Mapped[int] = mapped_column(ForeignKey("requirement_items.id"), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    cases: Mapped[list[CaseAsset]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class CaseAsset(Base):
    __tablename__ = "case_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    suite_title: Mapped[str] = mapped_column(Text, nullable=False)
    path_nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    module_name: Mapped[str | None] = mapped_column(Text)
    product_feature: Mapped[str | None] = mapped_column(Text)
    test_feature: Mapped[str | None] = mapped_column(Text)
    raw_title: Mapped[str] = mapped_column(Text, nullable=False)
    clean_title: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    manual: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="imported")
    version: Mapped[str | None] = mapped_column(Text)
    project_name: Mapped[str | None] = mapped_column(Text)
    feature_name: Mapped[str | None] = mapped_column(Text)
    source_requirement_item_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_items.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped[ImportBatch] = relationship(back_populates="cases")
    body: Mapped[CaseBody] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        uselist=False,
    )
    steps: Mapped[list[CaseStep]] = relationship(back_populates="case", cascade="all, delete-orphan")
    work_item: Mapped[CaseWorkItem] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        uselist=False,
    )


class CaseBody(Base):
    __tablename__ = "case_bodies"

    case_id: Mapped[int] = mapped_column(ForeignKey("case_assets.id", ondelete="CASCADE"), primary_key=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    preconditions: Mapped[str] = mapped_column(Text, nullable=False)
    steps_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False)

    case: Mapped[CaseAsset] = relationship(back_populates="body")


class CaseStep(Base):
    __tablename__ = "case_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("case_assets.id", ondelete="CASCADE"), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_text: Mapped[str] = mapped_column(Text, nullable=False)

    case: Mapped[CaseAsset] = relationship(back_populates="steps")


class CaseRawNode(Base):
    __tablename__ = "case_raw_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("case_assets.id", ondelete="CASCADE"), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class CaseWorkItem(Base):
    __tablename__ = "case_work_items"

    case_id: Mapped[int] = mapped_column(ForeignKey("case_assets.id", ondelete="CASCADE"), primary_key=True)
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    execution_status: Mapped[str] = mapped_column(Text, nullable=False, default="not_run")
    # 覆盖标记：按泳道存三态（{lane: "passed"|"failed"}，未执行不落键）。纯展示提醒，不参与执行流转。
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    lifecycle_state: Mapped[str] = mapped_column(Text, nullable=False, default="待验证")
    attention_reason: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int | None] = mapped_column(Integer)
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
    # 已提交到飞书项目的 bug：链接 + 工作项 id（提交后回写）。
    # bug_url/bug_external_id 保留为“最近一次”；bugs 是完整列表，支持一条 case 多次提交 bug。
    bug_url: Mapped[str | None] = mapped_column(Text)
    bug_external_id: Mapped[str | None] = mapped_column(Text)
    bugs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    case: Mapped[CaseAsset] = relationship(back_populates="work_item")


class CaseRepairDraft(Base):
    __tablename__ = "case_repair_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("case_assets.id", ondelete="CASCADE"), nullable=False)
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
    # 诊断快照：{reason, evidence, key_image, repair_channel, proposed_preconditions, original_preconditions}
    diagnosis_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaseBugDraft(Base):
    """提交 bug 的预填草稿：失败后台自动生成（标题润色+描述+模型选项），点提交时秒开。"""

    __tablename__ = "case_bug_drafts"

    case_id: Mapped[int] = mapped_column(
        ForeignKey("case_assets.id", ondelete="CASCADE"), primary_key=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 可编辑选择项（模型预填）：[{field_key,type,required,options:[{name,id}],selected}]
    editable_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIPhoneExecutionBatch(Base):
    __tablename__ = "aiphone_execution_batches"
    __table_args__ = (
        UniqueConstraint(
            "executor",
            "submission_id",
            name="aiphone_execution_batches_executor_submission_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[str] = mapped_column(Text, nullable=False)
    submission_name: Mapped[str | None] = mapped_column(Text)
    requirement_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="SET NULL"),
    )
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


class AIPhoneExecutionItem(Base):
    __tablename__ = "aiphone_execution_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "external_case_id",
            "platform",
            name="aiphone_execution_items_batch_case_platform_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("aiphone_execution_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[int] = mapped_column(ForeignKey("case_assets.id", ondelete="CASCADE"), nullable=False)
    external_case_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    status_reason: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(Text)
    report_url: Mapped[str | None] = mapped_column(Text)
    device_alias_pool: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    raw_item: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
