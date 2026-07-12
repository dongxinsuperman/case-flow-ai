"""drop tags from function map assets

Revision ID: 0025_function_map_asset_drop_tags
Revises: 0024_function_map_assets
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op


revision = "0025_fn_map_drop_tags"
down_revision = "0024_function_map_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE function_map_assets DROP COLUMN IF EXISTS tags")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE function_map_assets "
        "ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}'::text[]"
    )
