"""requirement_items.version (manual, unique per group)

Revision ID: 0005_requirement_item_version
Revises: 0004_feishu_source_fields
Create Date: 2026-06-20

二级需求纳入一级目录时手动指定版本，且同一一级目录内不可重复。
"""

from __future__ import annotations

from alembic import op

revision = "0005_requirement_item_version"
down_revision = "0004_feishu_source_fields"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: str, definition: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = '{table}'
                  AND column_name = '{column}'
            ) THEN
                ALTER TABLE {table} ADD COLUMN {definition};
            END IF;
        END
        $$;
        """
    )


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
    _add_column_if_missing("requirement_items", "version", "version TEXT")
    _create_index_if_missing(
        "ux_requirement_items_group_version",
        "CREATE UNIQUE INDEX ux_requirement_items_group_version "
        "ON requirement_items (group_id, version) WHERE version IS NOT NULL",
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_requirement_items_group_version")
    op.execute("ALTER TABLE requirement_items DROP COLUMN IF EXISTS version")
