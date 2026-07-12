from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class RequirementPool(Base):
    __tablename__ = "requirement_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="mock")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # 来源空间(飞书 project_key / 部门)，用于前端按空间筛选。
    source_space: Mapped[str | None] = mapped_column(Text)
    # 来源工作流状态原值(state_key)，便于后续判定“测试中”。
    external_status: Mapped[str | None] = mapped_column(Text)
    # 解析出的负责人(QA/tester)→case-flow 用户；建二级需求时直接挂为负责人。
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # 飞书工作项原始 payload 全量留存，不丢任何字段，便于后续按需回填。
    source_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    item: Mapped[RequirementItem | None] = relationship(back_populates="pool", uselist=False)


class RequirementGroup(Base):
    __tablename__ = "requirement_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    # 一级目录所属来源空间(飞书 project_key / 部门)，预留。
    source_space: Mapped[str | None] = mapped_column(Text)
    # 一级目录承载的 AI Phone functionMap 文件：[{"filename","content"}]，二级需求共享。
    function_map_files: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list[RequirementItem]] = relationship(
        back_populates="group",
        passive_deletes=True,
    )


class RequirementItem(Base):
    __tablename__ = "requirement_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_groups.id", ondelete="SET NULL"),
    )
    pool_id: Mapped[int] = mapped_column(ForeignKey("requirement_pool.id"), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    # 手动指定的版本，同一一级目录(group)内唯一（唯一索引 ux_requirement_items_group_version）。
    version: Mapped[str | None] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False, default="测试中")
    # 自动发现开关（检查点 4）：默认开启，可在首页任务块关闭；关闭后执行只用显式挂载。
    # 当前仅持久化开关意图，尚未接入执行/发现（发现本体见后续检查点）。
    auto_discovery_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # 测试时间区间：预留字段，未来可能由飞书返回；当前不展示、不参与首页过滤。
    test_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    group: Mapped[RequirementGroup | None] = relationship(back_populates="items")
    pool: Mapped[RequirementPool] = relationship(back_populates="item")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    # 飞书用户同步：以 user_key 作为稳定唯一身份；email/avatar 来自 user/query。
    feishu_user_key: Mapped[str | None] = mapped_column(Text, unique=True)
    email: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementAssignee(Base):
    __tablename__ = "requirement_assignees"

    requirement_item_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="tester")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
