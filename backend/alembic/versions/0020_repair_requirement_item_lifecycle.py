"""repair materialized requirement item lifecycle statuses

Revision ID: 0020_repair_req_item_lifecycle
Revises: 0019_materialize_pool_items
Create Date: 2026-06-26
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import op


revision = "0020_repair_req_item_lifecycle"
down_revision = "0019_materialize_pool_items"
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


def upgrade() -> None:
    pairs, configured_spaces = _status_rules()
    if not configured_spaces:
        return
    pair_values = ", ".join(f"({_quote(space)}, {_quote(status)})" for space, status in pairs)
    space_values = ", ".join(f"({_quote(space)})" for space in configured_spaces)
    op.execute(
        f"""
        WITH status_map(source_space, external_status) AS (
            VALUES {pair_values}
        ),
        configured_spaces(source_space) AS (
            VALUES {space_values}
        )
        UPDATE requirement_items AS item
        SET lifecycle_status =
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM status_map AS sm
                    WHERE sm.source_space = pool.source_space
                      AND sm.external_status = pool.external_status
                ) THEN '测试中'
                ELSE '其他'
            END
        FROM requirement_pool AS pool
        WHERE item.pool_id = pool.id
          AND pool.source_type = 'feishu_project'
          AND EXISTS (
              SELECT 1 FROM configured_spaces AS cs
              WHERE cs.source_space = pool.source_space
          )
          AND item.lifecycle_status IS DISTINCT FROM
              CASE
                  WHEN EXISTS (
                      SELECT 1 FROM status_map AS sm
                      WHERE sm.source_space = pool.source_space
                        AND sm.external_status = pool.external_status
                  ) THEN '测试中'
                  ELSE '其他'
              END
        """
    )


def downgrade() -> None:
    # Data repair only. Do not guess previous incorrect values on downgrade.
    pass
