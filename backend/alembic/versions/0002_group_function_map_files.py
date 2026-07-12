"""add function_map_files to requirement_groups

Revision ID: 0002_group_function_map_files
Revises: 0001_initial
Create Date: 2026-06-20
"""

from __future__ import annotations

from alembic import op

revision = "0002_group_function_map_files"
down_revision = "0001_initial"
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
                  AND table_name = 'requirement_groups'
                  AND column_name = 'function_map_files'
            ) THEN
                ALTER TABLE requirement_groups
                ADD COLUMN function_map_files JSONB NOT NULL DEFAULT '[]'::jsonb;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE requirement_groups DROP COLUMN IF EXISTS function_map_files")
