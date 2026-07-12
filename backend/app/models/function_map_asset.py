"""Function Map 资产：全局可复用的上下文卡片。

一份资产 = 标题 + 解释 + 正文 + 适用端（app/web/api 多选）。
首版只由本地文本导入生成正文，不做在线编辑；挂载关系在后续检查点单独建表。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FunctionMapAsset(Base):
    __tablename__ = "function_map_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 标题全局唯一（迁移 0029 建唯一索引 ux_function_map_assets_title）。
    title: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    targets: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="local_import")
    source_filename: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
