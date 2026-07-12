"""case_work_items bug link (submitted feishu issue)

Revision ID: 0007_case_bug_link
Revises: 0006_repair_diagnosis_snapshot
Create Date: 2026-06-22

提交 bug 到飞书项目后，把 issue 链接/ID 回写到 case，便于查看与去重。
"""

from __future__ import annotations

from alembic import op

revision = "0007_case_bug_link"
down_revision = "0006_repair_diagnosis_snapshot"
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
                  AND table_name = 'case_work_items'
                  AND column_name = 'bug_url'
            ) THEN
                ALTER TABLE case_work_items ADD COLUMN bug_url TEXT;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'case_work_items'
                  AND column_name = 'bug_external_id'
            ) THEN
                ALTER TABLE case_work_items ADD COLUMN bug_external_id TEXT;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE case_work_items DROP COLUMN IF EXISTS bug_external_id")
    op.execute("ALTER TABLE case_work_items DROP COLUMN IF EXISTS bug_url")
