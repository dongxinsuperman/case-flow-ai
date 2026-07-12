"""scope execution submission uniqueness by executor

Revision ID: 0021_exec_executor_unique
Revises: 0020_repair_req_item_lifecycle
Create Date: 2026-06-27
"""

from __future__ import annotations

from alembic import op


revision = "0021_exec_executor_unique"
down_revision = "0020_repair_req_item_lifecycle"
branch_labels = None
depends_on = None


def _drop_constraint(table: str, constraint: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")


def _add_unique_if_missing(table: str, constraint: str, columns: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = '{constraint}'
            ) THEN
                ALTER TABLE {table}
                ADD CONSTRAINT {constraint} UNIQUE ({columns});
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    _drop_constraint("aiphone_execution_batches", "aiphone_execution_batches_submission_id_key")
    _drop_constraint("quick_execution_batches", "quick_execution_batches_submission_id_key")
    _add_unique_if_missing(
        "aiphone_execution_batches",
        "aiphone_execution_batches_executor_submission_key",
        "executor, submission_id",
    )
    _add_unique_if_missing(
        "quick_execution_batches",
        "quick_execution_batches_executor_submission_key",
        "executor, submission_id",
    )


def downgrade() -> None:
    _drop_constraint("aiphone_execution_batches", "aiphone_execution_batches_executor_submission_key")
    _drop_constraint("quick_execution_batches", "quick_execution_batches_executor_submission_key")
    _add_unique_if_missing(
        "aiphone_execution_batches",
        "aiphone_execution_batches_submission_id_key",
        "submission_id",
    )
    _add_unique_if_missing(
        "quick_execution_batches",
        "quick_execution_batches_submission_id_key",
        "submission_id",
    )
