"""case work item submitted bugs list

Revision ID: 0013_case_work_item_bugs
Revises: 0012_case_steps_trg_fix
Create Date: 2026-06-25

新增 case_work_items.bugs（JSONB 列表）承载“已提交 bug”记录，支持一条 case 多次提交 bug。
单条的 bug_url / bug_external_id 保留为“最近一次”，向后兼容现有读取处。
"""

from __future__ import annotations

from alembic import op

revision = "0013_case_work_item_bugs"
down_revision = "0012_case_steps_trg_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 仅加列，沿用仓库既有迁移的通用写法（JSONB ... DEFAULT '[]'::jsonb）。
    # 不做历史单条 bug 的回填：回填需要 jsonb_build_* 等较“花”的写法，为兼容性从简；
    # 历史 bug_url 仍保留在列里，只是不进新的多条列表（可接受）。
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'case_work_items'
                  AND column_name = 'bugs'
            ) THEN
                ALTER TABLE case_work_items
                    ADD COLUMN bugs JSONB NOT NULL DEFAULT '[]'::jsonb;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE case_work_items DROP COLUMN IF EXISTS bugs")
