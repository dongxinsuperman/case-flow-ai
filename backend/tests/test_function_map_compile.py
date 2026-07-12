from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import function_map_mount as service


class _AllResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _ScalarsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarsResult":
        return self

    def all(self) -> list[object]:
        return self._rows


class _SeqSession:
    """按调用顺序返回预设结果：item→group 查询用 .all()，挂载资产查询用 .scalars().all()。"""

    def __init__(self, results: list[object]) -> None:
        self._results = results
        self._index = 0

    async def execute(self, _stmt: object) -> object:
        result = self._results[self._index]
        self._index += 1
        return result


def _asset(asset_id: int, title: str, targets: list[str], content: str) -> SimpleNamespace:
    return SimpleNamespace(id=asset_id, title=title, targets=targets, content=content)


def _block(title: str, asset_id: int, source: str, content: str) -> str:
    return f"--- FUNCTION MAP: {title} ---\n资产 ID: {asset_id}\n来源: {source}\n\n{content}"


_GROUP = "一级目录显式挂载"
_ITEM = "二级需求显式挂载"


@pytest.mark.asyncio
async def test_compile_empty_when_no_items() -> None:
    out = await service.compile_top_level_context(_SeqSession([]), [], "app")  # type: ignore[arg-type]
    assert out.context == ""
    assert out.injected_asset_ids == []


@pytest.mark.asyncio
async def test_compile_filters_by_target_and_dedups() -> None:
    a = _asset(1, "A 标题", ["app"], "A 内容")
    b = _asset(2, "B 标题", ["web"], "B 内容")
    c = _asset(3, "C 标题", ["api"], "C 内容")
    session = _SeqSession(
        [
            _AllResult([(1, 10)]),  # item 1 属于 group 10
            _ScalarsResult([a, b]),  # 一级目录挂载
            _ScalarsResult([a, c]),  # 二级需求挂载（a 与一级重叠）
        ]
    )
    out = await service.compile_top_level_context(session, [1], "app")  # type: ignore[arg-type]
    # 只带 app，a 去重一次且来源记为一级目录继承，带资产边界
    assert out.context == _block("A 标题", 1, _GROUP, "A 内容")
    assert out.injected_asset_ids == [1]
    assert out.excluded_asset_ids == [2, 3]


@pytest.mark.asyncio
async def test_compile_hybrid_includes_all_targets_in_id_order() -> None:
    a = _asset(1, "A 标题", ["app"], "A")
    b = _asset(2, "B 标题", ["web"], "B")
    c = _asset(3, "C 标题", ["api"], "C")
    session = _SeqSession(
        [
            _AllResult([(1, 10)]),
            _ScalarsResult([a, b, c]),
            _ScalarsResult([]),
        ]
    )
    out = await service.compile_top_level_context(session, [1], "mixed")  # type: ignore[arg-type]
    assert out.context == "\n\n".join(
        [
            _block("A 标题", 1, _GROUP, "A"),
            _block("B 标题", 2, _GROUP, "B"),
            _block("C 标题", 3, _GROUP, "C"),
        ]
    )
    assert out.injected_asset_ids == [1, 2, 3]
    assert out.excluded_asset_ids == []


@pytest.mark.asyncio
async def test_compile_ungrouped_uses_only_item_mounts() -> None:
    d = _asset(5, "D 标题", ["app"], "D 内容")
    # 未归属：group_id 为 None，不查一级目录挂载，只查二级需求挂载
    session = _SeqSession([_AllResult([(5, None)]), _ScalarsResult([d])])
    out = await service.compile_top_level_context(session, [5], "app")  # type: ignore[arg-type]
    assert out.context == _block("D 标题", 5, _ITEM, "D 内容")
    assert out.injected_asset_ids == [5]


_QUICK = "快速会话选择"


@pytest.mark.asyncio
async def test_compile_quick_context_structured_and_filtered() -> None:
    a = _asset(1, "Q 标题", ["app"], "Q 内容")
    b = _asset(2, "B 标题", ["web"], "B 内容")
    session = _SeqSession([_ScalarsResult([a, b])])
    out = await service.compile_quick_context(session, "quick-1", "app")  # type: ignore[arg-type]
    assert out.context == _block("Q 标题", 1, _QUICK, "Q 内容")  # 只带 app
    assert out.injected_asset_ids == [1]
    assert out.excluded_asset_ids == [2]


@pytest.mark.asyncio
async def test_compile_quick_context_empty_session_id() -> None:
    out = await service.compile_quick_context(_SeqSession([]), "", "app")  # type: ignore[arg-type]
    assert out.context == ""
    assert out.injected_asset_ids == []
