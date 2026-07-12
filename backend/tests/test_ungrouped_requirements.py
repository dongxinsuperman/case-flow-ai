from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import workbench
from app.services.workbench import _pool_status


def test_pool_status_is_computed_from_requirement_item() -> None:
    pool = SimpleNamespace(status="pending")

    assert _pool_status(pool, None) == "pending"
    assert _pool_status(pool, SimpleNamespace(group_id=None)) == "created"
    assert _pool_status(pool, SimpleNamespace(group_id=12)) == "bound"


class _TupleRows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _TupleSession:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.committed = False

    async def execute(self, _query: object) -> _TupleRows:
        return _TupleRows(self.rows)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_attach_existing_ungrouped_pool_item_to_group() -> None:
    pool = SimpleNamespace(id=3, external_key="P-1")
    item = SimpleNamespace(id=9, group_id=None, version=None, title="需求")
    session = _TupleSession([(pool, item, None)])

    attached = await workbench._attach_pool_items_to_group(  # type: ignore[arg-type]
        session,
        5,
        [(3, "1.0")],
    )

    assert attached == [item]
    assert item.group_id == 5
    assert item.version == "1.0"


@pytest.mark.asyncio
async def test_create_from_pool_returns_existing_ungrouped_item() -> None:
    pool = SimpleNamespace(id=3, external_key="P-1")
    item = SimpleNamespace(
        id=9,
        group_id=None,
        title="需求",
        status="active",
        version=None,
        lifecycle_status="测试中",
    )
    session = _TupleSession([(pool, item, None)])

    result = await workbench.create_requirement_items_from_pool(  # type: ignore[arg-type]
        session,
        SimpleNamespace(pool_ids=[3]),
    )

    assert session.committed is True
    assert result.items[0].id == 9
    assert result.items[0].group_id is None
