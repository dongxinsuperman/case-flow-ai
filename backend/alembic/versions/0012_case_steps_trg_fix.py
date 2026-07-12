"""fix case steps delete trigger compatibility

Revision ID: 0012_case_steps_trg_fix
Revises: 0011_case_work_item_coverage
Create Date: 2026-06-24

PostgreSQL DELETE triggers do not have a NEW record. Some versions reject
COALESCE(NEW.case_id, OLD.case_id) during DELETE with "record new is not
assigned yet", so branch on TG_OP explicitly.
"""

from __future__ import annotations

from alembic import op

revision = "0012_case_steps_trg_fix"
down_revision = "0011_case_work_item_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_case_body_steps_text()
        RETURNS trigger AS $$
        DECLARE
            target_case_id integer;
        BEGIN
            target_case_id := COALESCE(NEW.case_id, OLD.case_id);
            UPDATE case_bodies
            SET steps_text = COALESCE((
                SELECT string_agg(step_text, '、' ORDER BY step_order)
                FROM case_steps
                WHERE case_id = target_case_id
            ), '')
            WHERE case_id = target_case_id;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
        """
    )
