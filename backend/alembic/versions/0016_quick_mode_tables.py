"""quick mode tables

Revision ID: 0016_quick_mode_tables
Revises: 0015_drop_case_asset_priority
Create Date: 2026-06-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0016_quick_mode_tables"
down_revision = "0015_drop_case_asset_priority"
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
        CREATE TABLE IF NOT EXISTS quick_sessions (
            session_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            suite_title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            function_files JSONB NOT NULL DEFAULT '[]'::jsonb,
            feishu_requirement_url TEXT,
            feishu_target JSONB NOT NULL DEFAULT '{}'::jsonb,
            current_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_cases (
            id SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES quick_sessions(session_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            suite_title TEXT NOT NULL,
            path_nodes JSONB NOT NULL DEFAULT '[]'::jsonb,
            core_nodes JSONB NOT NULL DEFAULT '{}'::jsonb,
            raw_title TEXT NOT NULL,
            clean_title TEXT NOT NULL,
            scenario_tags TEXT[] NOT NULL DEFAULT '{}',
            manual BOOLEAN NOT NULL DEFAULT false,
            status TEXT NOT NULL DEFAULT 'imported',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _create_index_if_missing(
        "idx_quick_cases_session_id",
        "CREATE INDEX idx_quick_cases_session_id ON quick_cases(session_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_case_bodies (
            case_id INTEGER PRIMARY KEY REFERENCES quick_cases(id) ON DELETE CASCADE,
            goal TEXT NOT NULL,
            preconditions TEXT NOT NULL,
            steps_text TEXT NOT NULL,
            expected_result TEXT NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_case_steps (
            id SERIAL PRIMARY KEY,
            case_id INTEGER NOT NULL REFERENCES quick_cases(id) ON DELETE CASCADE,
            step_order INTEGER NOT NULL,
            step_text TEXT NOT NULL
        )
        """
    )
    _create_index_if_missing(
        "idx_quick_case_steps_case_id",
        "CREATE INDEX idx_quick_case_steps_case_id ON quick_case_steps(case_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_case_work_items (
            case_id INTEGER PRIMARY KEY REFERENCES quick_cases(id) ON DELETE CASCADE,
            execution_status TEXT NOT NULL DEFAULT 'not_run',
            coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
            lifecycle_state TEXT NOT NULL DEFAULT '待验证',
            attention_reason TEXT,
            case_type TEXT NOT NULL DEFAULT 'auto',
            execution_target TEXT NOT NULL DEFAULT 'manual',
            tag_source TEXT,
            tag_reason TEXT,
            tag_confidence INTEGER NOT NULL DEFAULT 0,
            run_enabled BOOLEAN NOT NULL DEFAULT true,
            report_url TEXT,
            failure_type TEXT,
            failure_summary TEXT,
            active_execution_batch_id INTEGER,
            external_submission_id TEXT,
            execution_started_at TIMESTAMPTZ,
            execution_finished_at TIMESTAMPTZ,
            bug_url TEXT,
            bug_external_id TEXT,
            bugs JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _create_index_if_missing(
        "idx_quick_case_work_items_status",
        "CREATE INDEX idx_quick_case_work_items_status ON quick_case_work_items(execution_status)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_repair_drafts (
            id SERIAL PRIMARY KEY,
            case_id INTEGER NOT NULL REFERENCES quick_cases(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending',
            model_name TEXT,
            original_steps TEXT NOT NULL DEFAULT '',
            proposed_steps TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            case_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            report_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            gate_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            analysis_trace JSONB NOT NULL DEFAULT '[]'::jsonb,
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            diagnosis_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    _create_index_if_missing(
        "idx_quick_repair_drafts_case_id",
        "CREATE INDEX idx_quick_repair_drafts_case_id ON quick_repair_drafts(case_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_bug_drafts (
            case_id INTEGER PRIMARY KEY REFERENCES quick_cases(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            editable_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_execution_batches (
            id SERIAL PRIMARY KEY,
            session_id TEXT REFERENCES quick_sessions(session_id) ON DELETE CASCADE,
            submission_id TEXT NOT NULL UNIQUE,
            submission_name TEXT,
            callback_token TEXT NOT NULL UNIQUE,
            executor TEXT NOT NULL DEFAULT 'ai_phone',
            status TEXT NOT NULL DEFAULT 'submitted',
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            summary_report_url TEXT,
            raw_request JSONB NOT NULL DEFAULT '{}'::jsonb,
            raw_response JSONB NOT NULL DEFAULT '{}'::jsonb,
            raw_callback JSONB,
            raw_submission JSONB
        )
        """
    )
    _create_index_if_missing(
        "idx_quick_execution_batches_session_id",
        "CREATE INDEX idx_quick_execution_batches_session_id ON quick_execution_batches(session_id)",
    )
    _create_index_if_missing(
        "idx_quick_execution_batches_token",
        "CREATE INDEX idx_quick_execution_batches_token ON quick_execution_batches(callback_token)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_execution_items (
            id SERIAL PRIMARY KEY,
            batch_id INTEGER NOT NULL REFERENCES quick_execution_batches(id) ON DELETE CASCADE,
            case_id INTEGER REFERENCES quick_cases(id) ON DELETE SET NULL,
            external_case_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued',
            status_reason TEXT,
            run_id TEXT,
            report_url TEXT,
            device_alias_pool TEXT[],
            raw_item JSONB NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT quick_execution_items_batch_case_platform_key
                UNIQUE (batch_id, external_case_id, platform)
        )
        """
    )
    _create_index_if_missing(
        "idx_quick_execution_items_case_id",
        "CREATE INDEX idx_quick_execution_items_case_id ON quick_execution_items(case_id)",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quick_execution_items")
    op.execute("DROP TABLE IF EXISTS quick_execution_batches")
    op.execute("DROP TABLE IF EXISTS quick_bug_drafts")
    op.execute("DROP TABLE IF EXISTS quick_repair_drafts")
    op.execute("DROP TABLE IF EXISTS quick_case_work_items")
    op.execute("DROP TABLE IF EXISTS quick_case_steps")
    op.execute("DROP TABLE IF EXISTS quick_case_bodies")
    op.execute("DROP TABLE IF EXISTS quick_cases")
    op.execute("DROP TABLE IF EXISTS quick_sessions")
