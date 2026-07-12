"""function map item mounts

Revision ID: 0027_fn_map_item_mounts
Revises: 0026_fn_map_group_mounts
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op


revision = "0027_fn_map_item_mounts"
down_revision = "0026_fn_map_group_mounts"
branch_labels = None
depends_on = None


def _create_index_if_missing(name: str, statement: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'i'
                  AND c.relname = '{name}'
                  AND n.nspname = current_schema()
            ) THEN
                {statement};
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS function_map_item_mounts (
            id SERIAL PRIMARY KEY,
            requirement_item_id INTEGER NOT NULL REFERENCES requirement_items(id) ON DELETE CASCADE,
            asset_id INTEGER NOT NULL REFERENCES function_map_assets(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT function_map_item_mounts_item_asset_key UNIQUE (requirement_item_id, asset_id)
        )
        """
    )
    _create_index_if_missing(
        "idx_function_map_item_mounts_item_id",
        "CREATE INDEX idx_function_map_item_mounts_item_id ON function_map_item_mounts(requirement_item_id)",
    )
    _create_index_if_missing(
        "idx_function_map_item_mounts_asset_id",
        "CREATE INDEX idx_function_map_item_mounts_asset_id ON function_map_item_mounts(asset_id)",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS function_map_item_mounts")
