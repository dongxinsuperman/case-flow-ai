"""repair draft diagnosis snapshot (reason/evidence/key image/channel)

Revision ID: 0006_repair_diagnosis_snapshot
Revises: 0005_requirement_item_version
Create Date: 2026-06-22

诊断修复：把"失败原因+证据指证+关键图路径+修复渠道+前置候选"结构化存进修复草稿，
供弹窗展示与后续提 bug 使用。
"""

from __future__ import annotations

from alembic import op

revision = "0006_repair_diagnosis_snapshot"
down_revision = "0005_requirement_item_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'case_repair_drafts'
                  AND column_name = 'diagnosis_snapshot'
            ) THEN
                ALTER TABLE case_repair_drafts
                ADD COLUMN diagnosis_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE case_repair_drafts DROP COLUMN IF EXISTS diagnosis_snapshot")
