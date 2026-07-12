"""agent assistant tables

Revision ID: 0022_agent_assistant_tables
Revises: 0021_exec_executor_unique
Create Date: 2026-06-28
"""

from __future__ import annotations

from alembic import op


revision = "0022_agent_assistant_tables"
down_revision = "0021_exec_executor_unique"
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
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT 'OS Agent',
            default_tool TEXT,
            default_resource_pool JSONB NOT NULL DEFAULT '{}'::jsonb,
            function_context TEXT NOT NULL DEFAULT '',
            bug_target JSONB NOT NULL DEFAULT '{}'::jsonb,
            pending_action JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT agent_sessions_user_id_key UNIQUE (user_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            dispatch_id INTEGER,
            attachments JSONB NOT NULL DEFAULT '{}'::jsonb,
            read_at TIMESTAMPTZ,
            seen_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _create_index_if_missing(
        "idx_agent_messages_session_id",
        "CREATE INDEX idx_agent_messages_session_id ON agent_messages(session_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_dispatches (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            message_id INTEGER REFERENCES agent_messages(id) ON DELETE SET NULL,
            tool_key TEXT NOT NULL,
            tool_kind TEXT NOT NULL,
            submission_id TEXT,
            run_id TEXT,
            callback_token TEXT UNIQUE,
            platform TEXT,
            resource_pool JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'running',
            report_url TEXT,
            summary TEXT,
            input_args JSONB NOT NULL DEFAULT '{}'::jsonb,
            result_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            artifact_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ
        )
        """
    )
    _create_index_if_missing(
        "idx_agent_dispatches_session_id",
        "CREATE INDEX idx_agent_dispatches_session_id ON agent_dispatches(session_id)",
    )
    _create_index_if_missing(
        "idx_agent_dispatches_callback_token",
        "CREATE INDEX idx_agent_dispatches_callback_token ON agent_dispatches(callback_token)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_bug_submissions (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
            source_message_id INTEGER REFERENCES agent_messages(id) ON DELETE SET NULL,
            target_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            editable_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'preparing',
            bug_url TEXT,
            bug_external_id TEXT,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _create_index_if_missing(
        "idx_agent_bug_submissions_session_id",
        "CREATE INDEX idx_agent_bug_submissions_session_id ON agent_bug_submissions(session_id)",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_bug_submissions")
    op.execute("DROP TABLE IF EXISTS agent_dispatches")
    op.execute("DROP TABLE IF EXISTS agent_messages")
    op.execute("DROP TABLE IF EXISTS agent_sessions")
