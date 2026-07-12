"""Function Map 挂载关系：业务对象引用某个 Function Map 资产。

挂载只存结构化引用（不提前固化正文）。按挂载位置分表，FK 更清晰：
- 一级目录：function_map_group_mounts
- 二级需求：function_map_item_mounts
- 快速会话：function_map_quick_mounts（快速模式从资产库选中的引用）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FunctionMapGroupMount(Base):
    __tablename__ = "function_map_group_mounts"
    __table_args__ = (
        UniqueConstraint("group_id", "asset_id", name="function_map_group_mounts_group_asset_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("function_map_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FunctionMapItemMount(Base):
    __tablename__ = "function_map_item_mounts"
    __table_args__ = (
        UniqueConstraint(
            "requirement_item_id",
            "asset_id",
            name="function_map_item_mounts_item_asset_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_item_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("function_map_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FunctionMapQuickMount(Base):
    __tablename__ = "function_map_quick_mounts"
    __table_args__ = (
        UniqueConstraint(
            "quick_session_id",
            "asset_id",
            name="function_map_quick_mounts_session_asset_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quick_session_id: Mapped[str] = mapped_column(
        ForeignKey("quick_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("function_map_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
