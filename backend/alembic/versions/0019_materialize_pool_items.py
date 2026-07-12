"""materialize existing pools as ungrouped requirement items

Revision ID: 0019_materialize_pool_items
Revises: 0018_nullable_req_item_group
Create Date: 2026-06-26
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import op


revision = "0019_materialize_pool_items"
down_revision = "0018_nullable_req_item_group"
branch_labels = None
depends_on = None


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _status_rules() -> tuple[list[tuple[str, str]], list[str]]:
    config_path = Path(__file__).resolve().parents[2] / "config" / "feishu_project.json"
    if not config_path.exists():
        return [], []
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    configured_spaces: list[str] = []
    for space in raw.get("spaces") or []:
        project_key = str(space.get("project_key") or "").strip()
        status_keys = [str(key) for key in (space.get("status_in_testing_state_keys") or [])]
        if not project_key or not status_keys:
            continue
        configured_spaces.append(project_key)
        pairs.extend((project_key, status_key) for status_key in status_keys)
    return pairs, configured_spaces


def _lifecycle_prefix_and_expr(pool_alias: str) -> tuple[str, str]:
    pairs, configured_spaces = _status_rules()
    if not configured_spaces:
        return "", "'测试中'"
    pair_values = ", ".join(f"({_quote(space)}, {_quote(status)})" for space, status in pairs)
    space_values = ", ".join(f"({_quote(space)})" for space in configured_spaces)
    prefix = f"""
        WITH status_map(source_space, external_status) AS (
            VALUES {pair_values}
        ),
        configured_spaces(source_space) AS (
            VALUES {space_values}
        )
    """
    expr = f"""
        CASE
            WHEN EXISTS (
                SELECT 1 FROM configured_spaces AS cs
                WHERE cs.source_space = {pool_alias}.source_space
            ) THEN
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM status_map AS sm
                        WHERE sm.source_space = {pool_alias}.source_space
                          AND sm.external_status = {pool_alias}.external_status
                    ) THEN '测试中'
                    ELSE '其他'
                END
            ELSE '测试中'
        END
    """
    return prefix, expr


def upgrade() -> None:
    lifecycle_prefix, lifecycle_expr = _lifecycle_prefix_and_expr("pool")
    op.execute(
        f"""
        {lifecycle_prefix}
        INSERT INTO requirement_items (
            group_id,
            pool_id,
            title,
            description,
            status,
            version,
            lifecycle_status,
            created_at
        )
        SELECT
            NULL,
            pool.id,
            pool.title,
            pool.description,
            'active',
            NULL,
            {lifecycle_expr},
            now()
        FROM requirement_pool AS pool
        LEFT JOIN requirement_items AS item ON item.pool_id = pool.id
        WHERE item.id IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO requirement_assignees (
            requirement_item_id,
            user_id,
            role,
            created_at
        )
        SELECT
            item.id,
            pool.owner_user_id,
            'tester',
            now()
        FROM requirement_items AS item
        JOIN requirement_pool AS pool ON pool.id = item.pool_id
        LEFT JOIN requirement_assignees AS assignee
            ON assignee.requirement_item_id = item.id
            AND assignee.user_id = pool.owner_user_id
        WHERE item.group_id IS NULL
            AND pool.owner_user_id IS NOT NULL
            AND assignee.requirement_item_id IS NULL
        """
    )


def downgrade() -> None:
    # Data migration only. Do not delete user-visible requirements or their imported cases on downgrade.
    pass
