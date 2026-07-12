"""allow requirement items without a group

Revision ID: 0018_nullable_req_item_group
Revises: 0017_quick_session_bug_url
Create Date: 2026-06-26 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "0018_nullable_req_item_group"
down_revision = "0017_quick_session_bug_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            fk_name text;
        BEGIN
            SELECT c.conname INTO fk_name
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid
             AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = 'requirement_items'::regclass
              AND c.confrelid = 'requirement_groups'::regclass
              AND c.contype = 'f'
              AND a.attname = 'group_id'
            LIMIT 1;

            IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE requirement_items DROP CONSTRAINT %I', fk_name);
            END IF;

            ALTER TABLE requirement_items ALTER COLUMN group_id DROP NOT NULL;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'requirement_items'::regclass
                  AND conname = 'requirement_items_group_id_fkey'
            ) THEN
                ALTER TABLE requirement_items
                    ADD CONSTRAINT requirement_items_group_id_fkey
                    FOREIGN KEY (group_id)
                    REFERENCES requirement_groups(id)
                    ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            fk_name text;
            null_count integer;
        BEGIN
            SELECT count(*) INTO null_count
            FROM requirement_items
            WHERE group_id IS NULL;
            IF null_count > 0 THEN
                RAISE EXCEPTION 'Cannot downgrade: requirement_items.group_id has % NULL rows', null_count;
            END IF;

            SELECT c.conname INTO fk_name
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid
             AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = 'requirement_items'::regclass
              AND c.confrelid = 'requirement_groups'::regclass
              AND c.contype = 'f'
              AND a.attname = 'group_id'
            LIMIT 1;

            IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE requirement_items DROP CONSTRAINT %I', fk_name);
            END IF;

            ALTER TABLE requirement_items ALTER COLUMN group_id SET NOT NULL;
            ALTER TABLE requirement_items
                ADD CONSTRAINT requirement_items_group_id_fkey
                FOREIGN KEY (group_id)
                REFERENCES requirement_groups(id)
                ON DELETE CASCADE;
        END $$;
        """
    )
