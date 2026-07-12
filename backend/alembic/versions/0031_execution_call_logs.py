"""execution strategy call logs (checkpoint 7)

Revision ID: 0031_exec_call_logs
Revises: 0030_fn_map_quick_mounts
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op

revision = "0031_exec_call_logs"
down_revision = "0030_fn_map_quick_mounts"
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
        CREATE TABLE IF NOT EXISTS execution_strategy_call_logs (
            id SERIAL PRIMARY KEY,
            call_id TEXT NOT NULL,
            request_group_id TEXT,
            mode TEXT NOT NULL,
            scope TEXT NOT NULL,
            entry TEXT NOT NULL,
            executor TEXT NOT NULL,
            requirement_item_id INTEGER,
            quick_session_id TEXT,
            case_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            execution_batch_id INTEGER,
            submission_id TEXT,
            trigger_user_id INTEGER,
            input JSONB NOT NULL DEFAULT '{}'::jsonb,
            function_map_result JSONB,
            effective_context JSONB,
            status TEXT NOT NULL DEFAULT 'compiling',
            failure_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT execution_strategy_call_logs_call_id_key UNIQUE (call_id)
        )
        """
    )
    _create_index_if_missing(
        "idx_execution_strategy_call_logs_request_group",
        "CREATE INDEX idx_execution_strategy_call_logs_request_group "
        "ON execution_strategy_call_logs(request_group_id)",
    )
    _create_index_if_missing(
        "idx_execution_strategy_call_logs_created_at",
        "CREATE INDEX idx_execution_strategy_call_logs_created_at "
        "ON execution_strategy_call_logs(created_at DESC)",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS execution_strategy_call_logs")
