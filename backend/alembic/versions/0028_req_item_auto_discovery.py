"""requirement item auto discovery switch

Revision ID: 0028_req_item_auto_disc
Revises: 0027_fn_map_item_mounts
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op

revision = "0028_req_item_auto_disc"
down_revision = "0027_fn_map_item_mounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE requirement_items
        ADD COLUMN IF NOT EXISTS auto_discovery_enabled BOOLEAN NOT NULL DEFAULT true
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE requirement_items DROP COLUMN IF EXISTS auto_discovery_enabled")
