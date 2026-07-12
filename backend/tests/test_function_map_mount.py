from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.function_map_asset import FunctionMapAsset
from app.models.requirements import RequirementGroup, RequirementItem
from app.services import function_map_mount as service


class _Result:
    def __init__(self, value: object | None = None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _FakeSession:
    def __init__(self, group: object | None = None, asset: object | None = None,
                 existing: object | None = None, item: object | None = None) -> None:
        self._group = group
        self._asset = asset
        self._existing = existing
        self._item = item
        self.added: list[object] = []
        self.committed = 0

    async def get(self, model: object, _pk: int) -> object | None:
        if model is RequirementGroup:
            return self._group
        if model is RequirementItem:
            return self._item
        if model is FunctionMapAsset:
            return self._asset
        return None

    async def execute(self, _stmt: object) -> _Result:
        return _Result(self._existing)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed += 1


@pytest.mark.asyncio
async def test_mount_to_group_group_not_found() -> None:
    session = _FakeSession(group=None)
    with pytest.raises(ValueError):
        await service.mount_to_group(session, 1, 1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mount_to_group_asset_not_found() -> None:
    session = _FakeSession(group=SimpleNamespace(id=1), asset=None)
    with pytest.raises(ValueError):
        await service.mount_to_group(session, 1, 99)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mount_to_group_adds_new_mount() -> None:
    session = _FakeSession(group=SimpleNamespace(id=1), asset=SimpleNamespace(id=2), existing=None)
    await service.mount_to_group(session, 1, 2)  # type: ignore[arg-type]
    assert len(session.added) == 1
    assert session.committed == 1


@pytest.mark.asyncio
async def test_mount_to_group_idempotent_when_exists() -> None:
    session = _FakeSession(group=SimpleNamespace(id=1), asset=SimpleNamespace(id=2), existing=123)
    await service.mount_to_group(session, 1, 2)  # type: ignore[arg-type]
    assert session.added == []
    assert session.committed == 0


@pytest.mark.asyncio
async def test_unmount_from_group_commits() -> None:
    session = _FakeSession()
    await service.unmount_from_group(session, 1, 2)  # type: ignore[arg-type]
    assert session.committed == 1


@pytest.mark.asyncio
async def test_list_group_mounts_group_not_found() -> None:
    session = _FakeSession(group=None)
    with pytest.raises(ValueError):
        await service.list_group_mounts(session, 1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mount_to_item_item_not_found() -> None:
    session = _FakeSession(item=None)
    with pytest.raises(ValueError):
        await service.mount_to_item(session, 1, 1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mount_to_item_asset_not_found() -> None:
    session = _FakeSession(item=SimpleNamespace(id=1), asset=None)
    with pytest.raises(ValueError):
        await service.mount_to_item(session, 1, 99)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mount_to_item_adds_new_mount() -> None:
    session = _FakeSession(item=SimpleNamespace(id=1), asset=SimpleNamespace(id=2), existing=None)
    await service.mount_to_item(session, 1, 2)  # type: ignore[arg-type]
    assert len(session.added) == 1
    assert session.committed == 1


@pytest.mark.asyncio
async def test_mount_to_item_idempotent_when_exists() -> None:
    session = _FakeSession(item=SimpleNamespace(id=1), asset=SimpleNamespace(id=2), existing=7)
    await service.mount_to_item(session, 1, 2)  # type: ignore[arg-type]
    assert session.added == []
    assert session.committed == 0


@pytest.mark.asyncio
async def test_unmount_from_item_commits() -> None:
    session = _FakeSession()
    await service.unmount_from_item(session, 1, 2)  # type: ignore[arg-type]
    assert session.committed == 1


@pytest.mark.asyncio
async def test_list_item_mounts_item_not_found() -> None:
    session = _FakeSession(item=None)
    with pytest.raises(ValueError):
        await service.list_item_mounts(session, 1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mount_to_quick_session_not_found() -> None:
    session = _FakeSession()  # get(QuickSession) 返回 None
    with pytest.raises(ValueError):
        await service.mount_to_quick(session, "q1", 1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_quick_mounts_session_not_found() -> None:
    session = _FakeSession()
    with pytest.raises(ValueError):
        await service.list_quick_mounts(session, "q1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_unmount_from_quick_commits() -> None:
    session = _FakeSession()
    await service.unmount_from_quick(session, "q1", 2)  # type: ignore[arg-type]
    assert session.committed == 1


# ---- 挂载目标顶层分页 ----

class _ScalarsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarsResult":
        return self

    def all(self) -> list[object]:
        return self._rows


class _SeqSession:
    """按调用顺序依次返回预设结果（第 1 次 execute 返回目录，第 2 次返回二级需求）。"""

    def __init__(self, results: list[list[object]]) -> None:
        self._results = results
        self._index = 0

    async def execute(self, _stmt: object) -> _ScalarsResult:
        rows = self._results[self._index]
        self._index += 1
        return _ScalarsResult(rows)


def _grp(gid: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=gid, name=name)


def _itm(iid: int, title: str, group_id: int | None) -> SimpleNamespace:
    return SimpleNamespace(id=iid, title=title, version=None, group_id=group_id)


@pytest.mark.asyncio
async def test_mount_targets_includes_empty_group_and_ungrouped() -> None:
    groups = [_grp(1, "目录A"), _grp(2, "空目录B")]
    items = [_itm(10, "需求A1", 1), _itm(20, "未归属需求", None)]
    session = _SeqSession([groups, items])
    result = await service.list_mount_targets(session, page=1, page_size=50)  # type: ignore[arg-type]
    assert result.total == 3  # 2 目录 + 1 未归属二级需求
    names = {g.name: [it.title for it in g.items] for g in result.groups}
    assert names["目录A"] == ["需求A1"]
    assert names["空目录B"] == []  # 空目录也在
    assert [it.title for it in result.ungrouped_items] == ["未归属需求"]


@pytest.mark.asyncio
async def test_mount_targets_pagination_by_top_level() -> None:
    groups = [_grp(i, f"目录{i}") for i in range(1, 6)]
    items: list[object] = []
    session = _SeqSession([groups, items])
    result = await service.list_mount_targets(session, page=1, page_size=2)  # type: ignore[arg-type]
    assert result.total == 5
    assert len(result.groups) == 2  # 每页 2 个顶层


@pytest.mark.asyncio
async def test_mount_targets_keyword_matches_group_or_child() -> None:
    groups = [_grp(1, "登录目录"), _grp(2, "支付目录")]
    items = [_itm(10, "扫码支付", 2), _itm(20, "登录态", 1)]
    session = _SeqSession([groups, items])
    result = await service.list_mount_targets(session, page=1, page_size=50, keyword="支付")  # type: ignore[arg-type]
    assert [g.name for g in result.groups] == ["支付目录"]


@pytest.mark.asyncio
async def test_mount_targets_focus_item_jumps_to_group_page() -> None:
    groups = [_grp(i, f"目录{i}") for i in range(1, 6)]
    items = [_itm(i * 10, f"需求{i}", i) for i in range(1, 6)]
    session = _SeqSession([groups, items])
    # 目录4 在顶层下标 3，页大小 2 → 应落在第 2 页
    result = await service.list_mount_targets(  # type: ignore[arg-type]
        session, page=1, page_size=2, focus_item_id=40
    )
    assert result.page == 2
    assert {g.id for g in result.groups} == {3, 4}


@pytest.mark.asyncio
async def test_mount_targets_focus_ungrouped_item_page() -> None:
    groups = [_grp(1, "目录1"), _grp(2, "目录2")]
    items = [_itm(100, "散1", None), _itm(200, "散2", None), _itm(300, "散3", None)]
    session = _SeqSession([groups, items])
    # 顶层：[g1, g2, i100, i200, i300]，i300 下标 4，页大小 2 → 第 3 页
    result = await service.list_mount_targets(  # type: ignore[arg-type]
        session, page=1, page_size=2, focus_item_id=300
    )
    assert result.page == 3
    assert [it.id for it in result.ungrouped_items] == [300]


@pytest.mark.asyncio
async def test_mount_targets_focus_group_page() -> None:
    groups = [_grp(i, f"目录{i}") for i in range(1, 6)]
    session = _SeqSession([groups, []])
    # 目录5 下标 4，页大小 2 → 第 3 页
    result = await service.list_mount_targets(  # type: ignore[arg-type]
        session, page=1, page_size=2, focus_group_id=5
    )
    assert result.page == 3
    assert [g.id for g in result.groups] == [5]


@pytest.mark.asyncio
async def test_mount_targets_focus_not_found_falls_back_to_page() -> None:
    groups = [_grp(i, f"目录{i}") for i in range(1, 6)]
    session = _SeqSession([groups, []])
    result = await service.list_mount_targets(  # type: ignore[arg-type]
        session, page=2, page_size=2, focus_item_id=999
    )
    assert result.page == 2  # 目标不存在，沿用请求页码
