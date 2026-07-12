"""add reserved test window columns to requirement_items

Revision ID: 0003_requirement_test_window
Revises: 0002_group_function_map_files
Create Date: 2026-06-20
"""

from __future__ import annotations

from alembic import op

revision = "0003_requirement_test_window"
down_revision = "0002_group_function_map_files"
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
                  AND table_name = 'requirement_items'
                  AND column_name = 'test_window_start'
            ) THEN
                ALTER TABLE requirement_items ADD COLUMN test_window_start TIMESTAMPTZ;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'requirement_items'
                  AND column_name = 'test_window_end'
            ) THEN
                ALTER TABLE requirement_items ADD COLUMN test_window_end TIMESTAMPTZ;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE requirement_items DROP COLUMN IF EXISTS test_window_end")
    op.execute("ALTER TABLE requirement_items DROP COLUMN IF EXISTS test_window_start")
