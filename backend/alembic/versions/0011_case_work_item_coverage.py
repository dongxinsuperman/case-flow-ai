"""case work item coverage lanes

Revision ID: 0011_case_work_item_coverage
Revises: 0010_bug_draft_times
Create Date: 2026-06-24

新增 case_work_items.coverage（JSONB）承载覆盖标记：按泳道存三态（passed/failed），
未执行的泳道不落键。纯展示提醒层，与 execution_status 解耦，不参与执行流转。
"""

from __future__ import annotations

from alembic import op

revision = "0011_case_work_item_coverage"
down_revision = "0010_bug_draft_times"
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
                  AND table_name = 'case_work_items'
                  AND column_name = 'coverage'
            ) THEN
                ALTER TABLE case_work_items
                    ADD COLUMN coverage JSONB NOT NULL DEFAULT '{}'::jsonb;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE case_work_items DROP COLUMN IF EXISTS coverage")
