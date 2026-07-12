"""case_bug_drafts (pre-filled bug submission draft)

Revision ID: 0008_case_bug_draft
Revises: 0007_case_bug_link
Create Date: 2026-06-23

失败后台自动生成 bug 预填草稿（标题/描述/模型选项），点提交时秒开。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_case_bug_draft"
down_revision = "0007_case_bug_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_bug_drafts",
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("case_assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("editable_fields", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("case_bug_drafts")
