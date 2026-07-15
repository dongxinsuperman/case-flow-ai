from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.function_map_asset import FunctionMapAsset
from app.schemas.function_map_asset import (
    FunctionMapAssetContentOverwriteIn,
    FunctionMapAssetCreateIn,
    FunctionMapAssetMetaUpdateIn,
    normalize_targets,
)
from app.services import function_map_asset as service


# ---- 纯逻辑：适用端归一 ----

def test_normalize_targets_dedup_and_canonical_order() -> None:
    assert normalize_targets(["web", "app", "web", "APP"]) == ["app", "web"]
    assert normalize_targets(["api"]) == ["api"]


def test_normalize_targets_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        normalize_targets(["app", "mixed"])


# ---- Schema 校验：必填与适用端 ----

def test_create_schema_strips_and_normalizes() -> None:
    payload = FunctionMapAssetCreateIn(
        title="  App 登录态说明  ",
        description=" 用于登录相关 case ",
        content=" 正文 ",
        targets=["web", "app", "app"],
    )
    assert payload.title == "App 登录态说明"
    assert payload.description == "用于登录相关 case"
    assert payload.content == "正文"
    assert payload.targets == ["app", "web"]


def test_create_schema_requires_title_description_content() -> None:
    for field in ("title", "description", "content"):
        kwargs = {
            "title": "标题",
            "description": "解释",
            "content": "正文",
            "targets": ["app"],
        }
        kwargs[field] = "   "
        with pytest.raises(ValidationError):
            FunctionMapAssetCreateIn(**kwargs)


def test_create_schema_requires_at_least_one_target() -> None:
    with pytest.raises(ValidationError):
        FunctionMapAssetCreateIn(title="t", description="d", content="c", targets=[])


# ---- 服务层：用假 session 验证增删查改与 not found ----

class _ScalarResult:
    def __init__(self, assets: list[FunctionMapAsset]) -> None:
        self._assets = assets

    def all(self) -> list[FunctionMapAsset]:
        return self._assets


class _FakeResult:
    def __init__(self, assets: list[FunctionMapAsset]) -> None:
        self._assets = assets

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._assets)

    def scalar(self) -> int:
        # count(*) 查询：返回资产条数
        return len(self._assets)

    def all(self) -> list:
        # 挂载引用查询在假 session 下返回空，reference_count/mounts 记为 0/[]。
        return []


class _FakeSession:
    def __init__(self, assets: list[FunctionMapAsset] | None = None) -> None:
        self.store: dict[int, FunctionMapAsset] = {a.id: a for a in (assets or [])}
        self.committed = 0

    async def get(self, _model: object, pk: int) -> FunctionMapAsset | None:
        return self.store.get(pk)

    def add(self, obj: FunctionMapAsset) -> None:
        obj.id = obj.id or (max(self.store, default=0) + 1)
        self.store[obj.id] = obj

    async def commit(self) -> None:
        self.committed += 1

    async def refresh(self, obj: FunctionMapAsset) -> None:
        now = datetime.now(timezone.utc)
        obj.created_at = getattr(obj, "created_at", None) or now
        obj.updated_at = now

    async def delete(self, obj: FunctionMapAsset) -> None:
        self.store.pop(obj.id, None)

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(list(self.store.values()))


def _asset(asset_id: int, title: str, targets: list[str]) -> FunctionMapAsset:
    now = datetime.now(timezone.utc)
    asset = FunctionMapAsset(
        title=title,
        description=f"{title} 的解释",
        content=f"{title} 的正文",
        targets=targets,
        source_type="local_import",
        source_filename=None,
    )
    asset.id = asset_id
    asset.created_at = now
    asset.updated_at = now
    return asset


@pytest.mark.asyncio
async def test_create_asset_persists_and_maps_output() -> None:
    session = _FakeSession()
    payload = FunctionMapAssetCreateIn(
        title="App 登录态说明",
        description="登录相关",
        content="正文内容",
        targets=["app"],
        source_filename="login.md",
    )
    out = await service.create_asset(session, payload)  # type: ignore[arg-type]
    assert out.id > 0
    assert out.title == "App 登录态说明"
    assert out.targets == ["app"]
    assert out.source_type == "local_import"
    assert out.source_filename == "login.md"
    assert session.committed == 1


@pytest.mark.asyncio
async def test_create_asset_with_direct_content_marks_manual_source() -> None:
    session = _FakeSession()
    payload = FunctionMapAssetCreateIn(
        title="直接填写的 Map", description="手写说明", content="正文内容", targets=["app"]
    )
    out = await service.create_asset(session, payload)  # type: ignore[arg-type]
    assert out.source_type == "manual"
    assert out.source_filename is None


@pytest.mark.asyncio
async def test_create_asset_duplicate_title_raises() -> None:
    session = _FakeSession([_asset(1, "已存在标题", ["app"])])
    payload = FunctionMapAssetCreateIn(
        title="已存在标题", description="解释", content="正文", targets=["app"]
    )
    with pytest.raises(ValueError):
        await service.create_asset(session, payload)  # type: ignore[arg-type]
    assert session.committed == 0


@pytest.mark.asyncio
async def test_get_asset_not_found_raises() -> None:
    session = _FakeSession()
    with pytest.raises(ValueError):
        await service.get_asset(session, 999)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_update_meta_changes_metadata_only() -> None:
    asset = _asset(1, "旧标题", ["app"])
    asset.content = "原正文"
    session = _FakeSession([asset])
    payload = FunctionMapAssetMetaUpdateIn(title="新标题", description="新解释", targets=["web", "api"])
    out = await service.update_meta(session, 1, payload)  # type: ignore[arg-type]
    assert out.title == "新标题"
    assert out.description == "新解释"
    assert out.targets == ["web", "api"]
    assert out.content == "原正文"


@pytest.mark.asyncio
async def test_update_meta_duplicate_title_raises() -> None:
    session = _FakeSession([_asset(1, "A", ["app"]), _asset(2, "B", ["web"])])
    payload = FunctionMapAssetMetaUpdateIn(title="B", description="解释", targets=["app"])
    with pytest.raises(ValueError):
        await service.update_meta(session, 1, payload)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_update_meta_not_found_raises() -> None:
    session = _FakeSession()
    payload = FunctionMapAssetMetaUpdateIn(title="t", description="d", targets=["app"])
    with pytest.raises(ValueError):
        await service.update_meta(session, 42, payload)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_overwrite_content_replaces_content_only() -> None:
    asset = _asset(1, "标题", ["app"])
    session = _FakeSession([asset])
    payload = FunctionMapAssetContentOverwriteIn(content="覆盖后的正文", source_filename="new.md")
    out = await service.overwrite_content(session, 1, payload)  # type: ignore[arg-type]
    assert out.content == "覆盖后的正文"
    assert out.title == "标题"
    assert out.source_filename == "new.md"
    assert out.source_type == "local_import"


@pytest.mark.asyncio
async def test_direct_content_save_marks_manual_and_clears_source_filename() -> None:
    asset = _asset(1, "标题", ["app"])
    asset.source_filename = "old.md"
    session = _FakeSession([asset])
    out = await service.overwrite_content(
        session, 1, FunctionMapAssetContentOverwriteIn(content="直接修改后的正文")
    )  # type: ignore[arg-type]
    assert out.content == "直接修改后的正文"
    assert out.source_type == "manual"
    assert out.source_filename is None


@pytest.mark.asyncio
async def test_overwrite_content_not_found_raises() -> None:
    session = _FakeSession()
    payload = FunctionMapAssetContentOverwriteIn(content="c")
    with pytest.raises(ValueError):
        await service.overwrite_content(session, 42, payload)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_delete_asset_removes_and_not_found() -> None:
    session = _FakeSession([_asset(1, "标题", ["app"])])
    await service.delete_asset(session, 1)  # type: ignore[arg-type]
    assert 1 not in session.store
    with pytest.raises(ValueError):
        await service.delete_asset(session, 1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_assets_maps_rows() -> None:
    session = _FakeSession([_asset(1, "A", ["app"]), _asset(2, "B", ["web", "api"])])
    result = await service.list_assets(session)  # type: ignore[arg-type]
    assert {item.id for item in result.items} == {1, 2}
    assert result.total == 2


@pytest.mark.asyncio
async def test_list_assets_rejects_bad_target_filter() -> None:
    session = _FakeSession()
    with pytest.raises(ValueError):
        await service.list_assets(session, target="mixed")  # type: ignore[arg-type]
