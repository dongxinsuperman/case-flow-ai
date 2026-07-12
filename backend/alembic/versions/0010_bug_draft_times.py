"""bug draft timestamp nullability

Revision ID: 0010_bug_draft_times
Revises: 0009_work_item_order
Create Date: 2026-06-23

Keep case_bug_drafts timestamp columns aligned with the ORM model.
"""

from __future__ import annotations

from alembic import op

revision = "0010_bug_draft_times"
down_revision = "0009_work_item_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'case_bug_drafts'
                  AND column_name = 'created_at'
            ) THEN
                UPDATE case_bug_drafts SET created_at = now() WHERE created_at IS NULL;
                ALTER TABLE case_bug_drafts ALTER COLUMN created_at SET NOT NULL;
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'case_bug_drafts'
                  AND column_name = 'updated_at'
            ) THEN
                UPDATE case_bug_drafts SET updated_at = now() WHERE updated_at IS NULL;
                ALTER TABLE case_bug_drafts ALTER COLUMN updated_at SET NOT NULL;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'case_bug_drafts'
                  AND column_name = 'created_at'
            ) THEN
                ALTER TABLE case_bug_drafts ALTER COLUMN created_at DROP NOT NULL;
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'case_bug_drafts'
                  AND column_name = 'updated_at'
            ) THEN
                ALTER TABLE case_bug_drafts ALTER COLUMN updated_at DROP NOT NULL;
            END IF;
        END
        $$;
        """
    )
