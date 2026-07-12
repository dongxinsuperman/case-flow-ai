"""case work item display order

Revision ID: 0009_work_item_order
Revises: 0008_case_bug_draft
Create Date: 2026-06-23

Keep the database schema aligned with CaseWorkItem.display_order, which is used
for stable case ordering in workbench queries.
"""

from __future__ import annotations

from alembic import op

revision = "0009_work_item_order"
down_revision = "0008_case_bug_draft"
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
                  AND column_name = 'display_order'
            ) THEN
                ALTER TABLE case_work_items ADD COLUMN display_order INTEGER;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE case_work_items DROP COLUMN IF EXISTS display_order")
