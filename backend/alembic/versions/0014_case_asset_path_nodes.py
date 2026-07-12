"""add case asset path nodes

Revision ID: 0014_case_asset_path_nodes
Revises: 0013_case_work_item_bugs
Create Date: 2026-06-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0014_case_asset_path_nodes"
down_revision = "0013_case_work_item_bugs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'case_assets'
                  AND column_name = 'path_nodes'
            ) THEN
                ALTER TABLE case_assets
                ADD COLUMN path_nodes JSONB NOT NULL DEFAULT '[]'::jsonb;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'case_assets'
                  AND column_name = 'path_nodes'
            ) THEN
                ALTER TABLE case_assets DROP COLUMN path_nodes;
            END IF;
        END $$;
        """
    )
