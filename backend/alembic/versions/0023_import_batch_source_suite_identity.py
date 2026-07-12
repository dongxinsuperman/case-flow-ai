"""use source filename as import batch identity

Revision ID: 0023_import_batch_source_key
Revises: 0022_agent_assistant_tables
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op


revision = "0023_import_batch_source_key"
down_revision = "0022_agent_assistant_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM (
                    SELECT requirement_item_id, source_name
                    FROM import_batches
                    GROUP BY requirement_item_id, source_name
                    HAVING count(*) > 1
                ) duplicate_sources
            ) THEN
                RAISE EXCEPTION
                    'duplicate import_batches for requirement_item_id/source_name; merge or rename existing batches before applying revision 0023';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE import_batches
        DROP CONSTRAINT IF EXISTS import_batches_requirement_suite_key
        """
    )
    op.execute(
        """
        ALTER TABLE import_batches
        DROP CONSTRAINT IF EXISTS import_batches_requirement_item_id_suite_title_key
        """
    )
    op.execute(
        """
        ALTER TABLE import_batches
        DROP CONSTRAINT IF EXISTS import_batches_requirement_source_suite_key
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'import_batches_requirement_source_key'
                  AND conrelid = 'import_batches'::regclass
            ) THEN
                ALTER TABLE import_batches
                ADD CONSTRAINT import_batches_requirement_source_key
                UNIQUE (requirement_item_id, source_name);
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE import_batches
        DROP CONSTRAINT IF EXISTS import_batches_requirement_source_key
        """
    )
    op.execute(
        """
        ALTER TABLE import_batches
        ADD CONSTRAINT import_batches_requirement_suite_key
        UNIQUE (requirement_item_id, suite_title)
        """
    )
