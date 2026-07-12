from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RepairPreviewIn(BaseModel):
    case_ids: list[int]


class RepairDraftOut(BaseModel):
    draft_id: int | None = None
    case_id: int
    case_title: str
    path: str
    status: str
    repairable: bool
    failure_type: str
    reason: str
    fix_reason: str = ""
    evidence: str = ""
    key_image: str | None = None
    # 多端证据：每端各一张关键截图 [{platform, image}]，供修复预览逐端展示（hybrid/多端）。
    key_images: list[dict[str, Any]] = []
    repair_channel: str = "none"
    process: list[dict[str, Any]] = []
    original_steps: str
    proposed_steps: str
    original_preconditions: str = ""
    proposed_preconditions: str = ""
    original_expected: str = ""
    proposed_expected: str = ""
    report_url: str | None = None
    report_summary: str = ""
    bug_url: str | None = None
    model_name: str | None = None
    gate: dict[str, Any] = {}
    created_at: datetime | None = None


class RepairPreviewOut(BaseModel):
    items: list[RepairDraftOut]


class RepairApplyIn(BaseModel):
    steps_text: str | None = None
    preconditions: str | None = None
    expected_result: str | None = None


class RepairApplyOut(BaseModel):
    case_id: int
    message: str
