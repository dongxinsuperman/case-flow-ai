"""feishu source integration fields

Revision ID: 0004_feishu_source_fields
Revises: 0003_requirement_test_window
Create Date: 2026-06-20

新增：
- users：feishu_user_key(唯一)、email、avatar_url —— 飞书用户同步落地。
- requirement_pool：source_space、external_status、owner_user_id、source_payload —— 保留来源空间/状态/负责人/全量原始 payload。
- requirement_groups：source_space —— 一级目录所属空间(部门)。
"""

from __future__ import annotations

from alembic import op

revision = "0004_feishu_source_fields"
down_revision = "0003_requirement_test_window"
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
    _add_column_if_missing("users", "feishu_user_key", "feishu_user_key TEXT")
    _add_column_if_missing("users", "email", "email TEXT")
    _add_column_if_missing("users", "avatar_url", "avatar_url TEXT")
    _create_index_if_missing(
        "ux_users_feishu_user_key",
        "CREATE UNIQUE INDEX ux_users_feishu_user_key ON users (feishu_user_key) WHERE feishu_user_key IS NOT NULL",
    )
    _add_column_if_missing("requirement_pool", "source_space", "source_space TEXT")
    _add_column_if_missing("requirement_pool", "external_status", "external_status TEXT")
    _add_column_if_missing("requirement_pool", "owner_user_id", "owner_user_id INTEGER REFERENCES users(id)")
    _add_column_if_missing(
        "requirement_pool",
        "source_payload",
        "source_payload JSONB NOT NULL DEFAULT '{}'::jsonb",
    )
    _add_column_if_missing("requirement_groups", "source_space", "source_space TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE requirement_groups DROP COLUMN IF EXISTS source_space")
    op.execute("ALTER TABLE requirement_pool DROP COLUMN IF EXISTS source_payload")
    op.execute("ALTER TABLE requirement_pool DROP COLUMN IF EXISTS owner_user_id")
    op.execute("ALTER TABLE requirement_pool DROP COLUMN IF EXISTS external_status")
    op.execute("ALTER TABLE requirement_pool DROP COLUMN IF EXISTS source_space")
    op.execute("DROP INDEX IF EXISTS ux_users_feishu_user_key")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar_url")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS feishu_user_key")
