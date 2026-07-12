"""function map quick session mounts

Revision ID: 0030_fn_map_quick_mounts
Revises: 0029_fn_map_title_unique
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op

revision = "0030_fn_map_quick_mounts"
down_revision = "0029_fn_map_title_unique"
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
        CREATE TABLE IF NOT EXISTS function_map_quick_mounts (
            id SERIAL PRIMARY KEY,
            quick_session_id TEXT NOT NULL REFERENCES quick_sessions(session_id) ON DELETE CASCADE,
            asset_id INTEGER NOT NULL REFERENCES function_map_assets(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT function_map_quick_mounts_session_asset_key UNIQUE (quick_session_id, asset_id)
        )
        """
    )
    _create_index_if_missing(
        "idx_function_map_quick_mounts_session_id",
        "CREATE INDEX idx_function_map_quick_mounts_session_id "
        "ON function_map_quick_mounts(quick_session_id)",
    )
    _create_index_if_missing(
        "idx_function_map_quick_mounts_asset_id",
        "CREATE INDEX idx_function_map_quick_mounts_asset_id ON function_map_quick_mounts(asset_id)",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS function_map_quick_mounts")
