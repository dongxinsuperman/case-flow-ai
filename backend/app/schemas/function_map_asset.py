from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

CANONICAL_TARGETS: tuple[str, ...] = ("app", "web", "api")


def normalize_targets(value: list[str] | None) -> list[str]:
    """校验并归一适用端：只允许 app/web/api，去重，按固定顺序返回。"""
    seen: set[str] = set()
    for raw in value or []:
        target = str(raw or "").strip().lower()
        if target not in CANONICAL_TARGETS:
            raise ValueError(f"适用端只能是 app / web / api，收到：{raw!r}")
        seen.add(target)
    return [target for target in CANONICAL_TARGETS if target in seen]


class FunctionMapAssetMetaUpdateIn(BaseModel):
    """编辑元信息：标题、解释、适用端可在线编辑。"""

    title: str
    description: str
    targets: list[str]

    @field_validator("title")
    @classmethod
    def _title_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("标题不能为空")
        return text

    @field_validator("description")
    @classmethod
    def _description_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("解释不能为空")
        return text

    @field_validator("targets")
    @classmethod
    def _targets_required(cls, value: list[str]) -> list[str]:
        normalized = normalize_targets(value)
        if not normalized:
            raise ValueError("适用端至少选择一个：app / web / api")
        return normalized


class FunctionMapAssetContentOverwriteIn(BaseModel):
    """导入覆盖：正文不在线编辑，只能用导入文件的正文覆盖。"""

    content: str
    source_filename: str | None = None

    @field_validator("content")
    @classmethod
    def _content_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("正文不能为空")
        return text

    @field_validator("source_filename")
    @classmethod
    def _source_filename_clean(cls, value: str | None) -> str | None:
        text = str(value or "").strip()
        return text or None


class FunctionMapAssetCreateIn(FunctionMapAssetMetaUpdateIn, FunctionMapAssetContentOverwriteIn):
    """资产库里新建资产（元信息 + 正文，只创建不挂载）。"""


class FunctionMapMountIn(BaseModel):
    asset_id: int


class FunctionMapMountRefOut(BaseModel):
    """资产被挂载在哪个业务对象上。scope 为 group（一级目录）或 item（二级需求）。"""

    scope: str
    id: int
    name: str


class MountTargetItemOut(BaseModel):
    id: int
    title: str
    version: str | None = None


class MountTargetGroupOut(BaseModel):
    id: int
    name: str
    items: list[MountTargetItemOut] = []


class MountTargetPageOut(BaseModel):
    """挂载目标（容器）按顶层分页：一级目录（含空、组内子项全带）+ 未进入目录的二级需求同级。"""

    groups: list[MountTargetGroupOut] = []
    ungrouped_items: list[MountTargetItemOut] = []
    total: int = 0
    page: int = 1
    page_size: int = 0


class FunctionMapAssetListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    targets: list[str]
    updated_at: datetime
    reference_count: int = 0


class FunctionMapAssetPageOut(BaseModel):
    items: list[FunctionMapAssetListItemOut] = []
    total: int = 0
    page: int = 1
    page_size: int = 0


class FunctionMapAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    content: str
    targets: list[str]
    source_type: str
    source_filename: str | None = None
    created_at: datetime
    updated_at: datetime
    reference_count: int = 0
    mounts: list[FunctionMapMountRefOut] = []


class FunctionMapAssetExportOut(BaseModel):
    """导出结果，应能作为后续导入来源。"""

    title: str
    description: str
    content: str
    targets: list[str]
