"""执行策略调用日志（检查点 7 台账）。

每次点击执行拆出的每一路 submit 都落一条：记录触发人、模式、范围、入口、
关联对象、原始输入摘要、编译/提交状态与失败原因。Function Map 编译明细与最终
上下文（`function_map_result` / `effective_context`）留给检查点 6 自动发现填充。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExecutionStrategyCallLog(Base):
    __tablename__ = "execution_strategy_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 可追踪的调用 ID（uuid），返回给前端；同一次点击多路 submit 共享 request_group_id。
    call_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    request_group_id: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text, nullable=False)  # standard | quick
    scope: Mapped[str] = mapped_column(Text, nullable=False)  # single | batch
    entry: Mapped[str] = mapped_column(Text, nullable=False)  # submit 路由 / 服务方法
    executor: Mapped[str] = mapped_column(Text, nullable=False)  # ai_phone/ai_web/ai_hybrid/ai_api
    requirement_item_id: Mapped[int | None] = mapped_column(Integer)
    quick_session_id: Mapped[str | None] = mapped_column(Text)
    case_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    execution_batch_id: Mapped[int | None] = mapped_column(Integer)
    submission_id: Mapped[str | None] = mapped_column(Text)
    trigger_user_id: Mapped[int | None] = mapped_column(Integer)
    # 原始执行输入摘要：目标执行器、设备/平台选择、缓存/重试、自动发现开关状态等。
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 检查点 6 填充：顶层/ item 级发现、注入、去重、排除、原因。
    function_map_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 检查点 6 填充：提交级 / item 级 / 合成后的最终上下文（可摘要）。
    effective_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # compiling | submitted | compile_failed | submit_failed
    status: Mapped[str] = mapped_column(Text, nullable=False, default="compiling")
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
