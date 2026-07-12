"""drop case asset priority

Revision ID: 0015_drop_case_asset_priority
Revises: 0014_case_asset_path_nodes
Create Date: 2026-06-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0015_drop_case_asset_priority"
down_revision = "0014_case_asset_path_nodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'case_assets'
                  AND column_name = 'priority'
            ) THEN
                ALTER TABLE case_assets DROP COLUMN priority;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'case_assets'
                  AND column_name = 'priority'
            ) THEN
                ALTER TABLE case_assets ADD COLUMN priority TEXT;
            END IF;
        END $$;
        """
    )
