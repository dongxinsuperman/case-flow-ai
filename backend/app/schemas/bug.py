from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BugFieldOut(BaseModel):
    field_key: str
    label: str
    type: str | None = None
    editable: bool = False
    required: bool = False
    options: list[dict[str, Any]] = []
    selected: Any = None
    display: str = ""
    submit_value: Any = None


class BugDraftOut(BaseModel):
    case_id: int
    space: str | None = None
    title: str
    description: str
    fields: list[BugFieldOut] = []
    has_diagnosis_image: bool = False
    key_image: str | None = None
    # 每端关键截图证据：[{platform, image}]
    key_images: list[dict[str, str]] = []
    existing_bug_url: str | None = None
    # 已提交 bug 列表（支持多次提交）：[{url, id}]
    submitted_bugs: list[dict[str, str]] = []


class BugSubmitIn(BaseModel):
    title: str
    description: str
    fields: list[dict[str, Any]] = []
    # 本次提交要附带的端证据图 [{platform, image}]。None=带全部（默认）；[]=不带；子集=部分带。
    key_images: list[dict[str, str]] | None = None


class BugImageOut(BaseModel):
    platform: str
    image: str


class BugImageUploadOut(BaseModel):
    images: list[BugImageOut] = []


class BugSubmitOut(BaseModel):
    case_id: int
    bug_id: int
    bug_url: str
    submitted_count: int = 1
    message: str
