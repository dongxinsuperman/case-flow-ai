"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-18
"""

from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
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
        CREATE TABLE IF NOT EXISTS requirement_pool (
            id SERIAL PRIMARY KEY,
            external_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT,
            source_type TEXT NOT NULL DEFAULT 'mock',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS requirement_groups (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS requirement_items (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES requirement_groups(id) ON DELETE CASCADE,
            pool_id INTEGER NOT NULL UNIQUE REFERENCES requirement_pool(id),
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            lifecycle_status TEXT NOT NULL DEFAULT '执行中',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _create_index_if_missing(
        "idx_requirement_items_group_id",
        "CREATE INDEX idx_requirement_items_group_id ON requirement_items(group_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS requirement_assignees (
            requirement_item_id INTEGER NOT NULL REFERENCES requirement_items(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'tester',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (requirement_item_id, user_id)
        )
        """
    )
    _create_index_if_missing(
        "idx_requirement_assignees_user_id",
        "CREATE INDEX idx_requirement_assignees_user_id ON requirement_assignees(user_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS import_batches (
            id SERIAL PRIMARY KEY,
            suite_title TEXT NOT NULL,
            source_name TEXT NOT NULL,
            version TEXT,
            project_name TEXT,
            feature_name TEXT,
            requirement_item_id INTEGER NOT NULL REFERENCES requirement_items(id),
            imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            case_count INTEGER NOT NULL DEFAULT 0,
            raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (requirement_item_id, suite_title)
        )
        """
    )
    _create_index_if_missing(
        "idx_import_batches_requirement_item_id",
        "CREATE INDEX idx_import_batches_requirement_item_id ON import_batches(requirement_item_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_assets (
            id SERIAL PRIMARY KEY,
            batch_id INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            suite_title TEXT NOT NULL,
            module_name TEXT,
            product_feature TEXT,
            test_feature TEXT,
            priority TEXT,
            raw_title TEXT NOT NULL,
            clean_title TEXT NOT NULL,
            scenario_tags TEXT[] NOT NULL DEFAULT '{}',
            manual BOOLEAN NOT NULL DEFAULT false,
            status TEXT NOT NULL DEFAULT 'imported',
            version TEXT,
            project_name TEXT,
            feature_name TEXT,
            source_requirement_item_id INTEGER NOT NULL REFERENCES requirement_items(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _create_index_if_missing(
        "idx_case_assets_batch_id",
        "CREATE INDEX idx_case_assets_batch_id ON case_assets(batch_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_bodies (
            case_id INTEGER PRIMARY KEY REFERENCES case_assets(id) ON DELETE CASCADE,
            goal TEXT NOT NULL,
            preconditions TEXT NOT NULL,
            steps_text TEXT NOT NULL,
            expected_result TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_steps (
            id SERIAL PRIMARY KEY,
            case_id INTEGER NOT NULL REFERENCES case_assets(id) ON DELETE CASCADE,
            step_order INTEGER NOT NULL,
            step_text TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_raw_nodes (
            id SERIAL PRIMARY KEY,
            case_id INTEGER NOT NULL REFERENCES case_assets(id) ON DELETE CASCADE,
            raw_payload JSONB NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_work_items (
            case_id INTEGER PRIMARY KEY REFERENCES case_assets(id) ON DELETE CASCADE,
            assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            execution_status TEXT NOT NULL DEFAULT 'not_run',
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
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _create_index_if_missing(
        "idx_case_work_items_status",
        "CREATE INDEX idx_case_work_items_status ON case_work_items(execution_status)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS aiphone_execution_batches (
            id SERIAL PRIMARY KEY,
            submission_id TEXT NOT NULL UNIQUE,
            submission_name TEXT,
            requirement_item_id INTEGER REFERENCES requirement_items(id) ON DELETE SET NULL,
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
        "idx_aiphone_execution_batches_token",
        "CREATE INDEX idx_aiphone_execution_batches_token ON aiphone_execution_batches(callback_token)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS aiphone_execution_items (
            id SERIAL PRIMARY KEY,
            batch_id INTEGER NOT NULL REFERENCES aiphone_execution_batches(id) ON DELETE CASCADE,
            case_id INTEGER NOT NULL REFERENCES case_assets(id) ON DELETE CASCADE,
            external_case_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued',
            status_reason TEXT,
            run_id TEXT,
            report_url TEXT,
            device_alias_pool TEXT[],
            raw_item JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (batch_id, external_case_id, platform)
        )
        """
    )
    _create_index_if_missing(
        "idx_aiphone_execution_items_case_id",
        "CREATE INDEX idx_aiphone_execution_items_case_id ON aiphone_execution_items(case_id)",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_repair_drafts (
            id SERIAL PRIMARY KEY,
            case_id INTEGER NOT NULL REFERENCES case_assets(id) ON DELETE CASCADE,
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
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '7 days'
        )
        """
    )
    _create_index_if_missing(
        "idx_case_repair_drafts_case_status",
        "CREATE INDEX idx_case_repair_drafts_case_status ON case_repair_drafts(case_id, status)",
    )
    _create_index_if_missing(
        "idx_case_repair_drafts_expires_at",
        "CREATE INDEX idx_case_repair_drafts_expires_at ON case_repair_drafts(expires_at)",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_case_body_steps_text()
        RETURNS trigger AS $$
        DECLARE
            target_case_id integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_case_id := OLD.case_id;
            ELSE
                target_case_id := NEW.case_id;
            END IF;
            UPDATE case_bodies
            SET steps_text = COALESCE((
                SELECT string_agg(step_text, '、' ORDER BY step_order)
                FROM case_steps
                WHERE case_id = target_case_id
            ), '')
            WHERE case_id = target_case_id;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_sync_case_body_steps_text ON case_steps")
    op.execute(
        """
        CREATE TRIGGER trg_sync_case_body_steps_text
        AFTER INSERT OR UPDATE OR DELETE ON case_steps
        FOR EACH ROW EXECUTE PROCEDURE sync_case_body_steps_text()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_sync_case_body_steps_text ON case_steps")
    op.execute("DROP FUNCTION IF EXISTS sync_case_body_steps_text()")
    op.execute("DROP TABLE IF EXISTS case_repair_drafts")
    op.execute("DROP TABLE IF EXISTS aiphone_execution_items")
    op.execute("DROP TABLE IF EXISTS aiphone_execution_batches")
    op.execute("DROP TABLE IF EXISTS case_work_items")
    op.execute("DROP TABLE IF EXISTS case_raw_nodes")
    op.execute("DROP TABLE IF EXISTS case_steps")
    op.execute("DROP TABLE IF EXISTS case_bodies")
    op.execute("DROP TABLE IF EXISTS case_assets")
    op.execute("DROP TABLE IF EXISTS import_batches")
    op.execute("DROP TABLE IF EXISTS requirement_assignees")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS requirement_items")
    op.execute("DROP TABLE IF EXISTS requirement_groups")
    op.execute("DROP TABLE IF EXISTS requirement_pool")
