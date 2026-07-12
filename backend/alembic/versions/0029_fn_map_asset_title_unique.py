"""function map asset title unique constraint

Revision ID: 0029_fn_map_title_unique
Revises: 0028_req_item_auto_disc
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op

revision = "0029_fn_map_title_unique"
down_revision = "0028_req_item_auto_disc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 标题全局唯一硬约束：服务层预检查友好报错，DB 唯一索引兜住并发竞态。
    # 前置要求：库内无重名 title（有则先清理，否则本迁移失败）。
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_function_map_assets_title "
        "ON function_map_assets (title)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_function_map_assets_title")
