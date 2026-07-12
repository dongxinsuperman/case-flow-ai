"""function map assets

Revision ID: 0024_function_map_assets
Revises: 0023_import_batch_source_key
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op


revision = "0024_function_map_assets"
down_revision = "0023_import_batch_source_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS function_map_assets (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            content TEXT NOT NULL,
            targets TEXT[] NOT NULL DEFAULT '{}'::text[],
            tags TEXT[] NOT NULL DEFAULT '{}'::text[],
            source_type TEXT NOT NULL DEFAULT 'local_import',
            source_filename TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS function_map_assets")
