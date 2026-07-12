from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import workbench as service


class _FakeSession:
    def __init__(self, item: object | None) -> None:
        self._item = item
        self.committed = 0

    async def get(self, _model: object, _pk: int) -> object | None:
        return self._item

    async def commit(self) -> None:
        self.committed += 1


@pytest.mark.asyncio
async def test_set_auto_discovery_toggles_and_persists() -> None:
    item = SimpleNamespace(id=1, auto_discovery_enabled=True)
    session = _FakeSession(item)
    out = await service.set_requirement_item_auto_discovery(session, 1, False)  # type: ignore[arg-type]
    assert out.requirement_item_id == 1
    assert out.auto_discovery_enabled is False
    assert item.auto_discovery_enabled is False
    assert session.committed == 1


@pytest.mark.asyncio
async def test_set_auto_discovery_not_found_raises() -> None:
    session = _FakeSession(None)
    with pytest.raises(ValueError):
        await service.set_requirement_item_auto_discovery(session, 99, True)  # type: ignore[arg-type]
