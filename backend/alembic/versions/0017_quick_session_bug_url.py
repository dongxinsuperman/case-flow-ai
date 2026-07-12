"""quick session bug reference url

Revision ID: 0017_quick_session_bug_url
Revises: 0016_quick_mode_tables
Create Date: 2026-06-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0017_quick_session_bug_url"
down_revision = "0016_quick_mode_tables"
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
                WHERE table_name = 'quick_sessions'
                  AND column_name = 'feishu_bug_url'
            ) THEN
                ALTER TABLE quick_sessions ADD COLUMN feishu_bug_url TEXT;
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
                WHERE table_name = 'quick_sessions'
                  AND column_name = 'feishu_bug_url'
            ) THEN
                ALTER TABLE quick_sessions DROP COLUMN feishu_bug_url;
            END IF;
        END $$;
        """
    )
