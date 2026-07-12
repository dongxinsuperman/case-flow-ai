from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class QuickFunctionFileIn(BaseModel):
    filename: str
    content: str


class QuickFunctionFileOut(BaseModel):
    filename: str
    content: str
    char_count: int


class QuickImportIn(BaseModel):
    filename: str = "uploaded.md"
    content: str
    function_files: list[QuickFunctionFileIn] = Field(default_factory=list)


class QuickSessionPatchIn(BaseModel):
    function_files: list[QuickFunctionFileIn] | None = None
    feishu_requirement_url: str | None = None
    feishu_bug_url: str | None = None
    current_user_id: int | None = None


class QuickSessionSummaryOut(BaseModel):
    session_id: str
    source_name: str
    suite_title: str
    case_count: int
    function_files: list[QuickFunctionFileOut] = Field(default_factory=list)
    feishu_requirement_url: str | None = None
    feishu_bug_url: str | None = None
    current_user_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QuickCaseItemOut(BaseModel):
    id: int
    ordinal: int
    display_no: str = ""
    suite_title: str
    source_name: str
    asset_status: str
    raw_title: str
    clean_title: str
    path: str
    path_nodes: list[dict[str, Any]] = Field(default_factory=list)
    scenario_tags: list[str] = Field(default_factory=list)
    manual: bool
    execution_status: str
    coverage: dict[str, str] = Field(default_factory=dict)
    lifecycle_state: str
    attention_reason: str | None = None
    case_type: str
    execution_target: str
    tag_source: str | None = None
    tag_reason: str | None = None
    tag_confidence: int
    run_enabled: bool
    report_url: str | None = None
    failure_type: str | None = None
    failure_summary: str | None = None
    bug_url: str | None = None
    bugs: list[dict[str, str]] = Field(default_factory=list)
    diagnosis_ready: bool = False
    bug_draft_ready: bool = False
    external_submission_id: str | None = None
    execution_started_at: datetime | None = None
    execution_finished_at: datetime | None = None
    preconditions: str
    steps_text: str
    expected_result: str
    core_nodes: dict[str, Any] = Field(default_factory=dict)


class QuickSessionOut(BaseModel):
    session: QuickSessionSummaryOut
    cases: list[QuickCaseItemOut]


class QuickImportOut(BaseModel):
    session: QuickSessionSummaryOut
    cases: list[QuickCaseItemOut]
    warnings: list[str] = Field(default_factory=list)


class QuickCaseUpdateIn(BaseModel):
    raw_title: str | None = None
    clean_title: str | None = None
    preconditions: str | None = None
    steps_text: str | None = None
    expected_result: str | None = None


class QuickCaseMutationOut(BaseModel):
    case_id: int
    message: str


class QuickWorkItemUpdateIn(BaseModel):
    case_id: int
    execution_status: str | None = None
    execution_target: str | None = None
    run_enabled: bool | None = None


class QuickWorkItemUpdateOut(BaseModel):
    case_id: int
    execution_status: str
    execution_target: str
    run_enabled: bool


class QuickCoverageUpdateIn(BaseModel):
    case_id: int
    lane: str
    state: str


class QuickCoverageOut(BaseModel):
    case_id: int
    coverage: dict[str, str] = Field(default_factory=dict)


class QuickExportOut(BaseModel):
    filename: str
    content: str
    cleared: bool


class QuickClearOut(BaseModel):
    session_id: str
    cleared: bool


class QuickFeishuTargetIn(BaseModel):
    url: str


class QuickFeishuLinkCheckIn(BaseModel):
    url: str
    kind: Literal["requirement", "bug"] = "requirement"


class QuickFeishuTargetOut(BaseModel):
    url: str
    project_key: str | None = None
    work_item_type: str | None = None
    work_item_id: str | None = None
    title: str | None = None
    readable: bool = False
    message: str | None = None


class QuickAIPhoneSubmitIn(BaseModel):
    session_id: str
    case_ids: list[int] = Field(default_factory=list)
    device_alias_pools: dict[str, list[str]] | None = None
    submission_name: str | None = None
    cache_mode: str = "off"
    retry_max: int = 0
    # 一次点击的聚合 ID（前端生成，可选）。
    execution_request_group_id: str | None = None


class QuickAIPhoneSubmitOut(BaseModel):
    submission_id: str
    submission_name: str | None = None
    callback_url: str
    batch_id: int
    submitted_count: int
    response: dict[str, Any]
    # 可追踪的调用 ID（检查点 7）。
    call_id: str | None = None


class QuickAIPhoneCallbackOut(BaseModel):
    handled: bool
    event: str
    submission_id: str | None = None
    updated_case_ids: list[int] = Field(default_factory=list)


class QuickBugDraftOut(BaseModel):
    case_id: int
    space: str | None = None
    title: str
    description: str
    fields: list[dict[str, Any]]
    has_diagnosis_image: bool
    key_image: str | None = None
    key_images: list[dict[str, str]] = Field(default_factory=list)
    existing_bug_url: str | None = None
    submitted_bugs: list[dict[str, str]] = Field(default_factory=list)


class QuickBugSubmitIn(BaseModel):
    title: str
    description: str
    fields: list[dict[str, Any]] = Field(default_factory=list)
    key_images: list[dict[str, str]] | None = None
    reference_bug_url: str | None = None


class QuickBugSubmitOut(BaseModel):
    case_id: int
    bug_id: int
    bug_url: str
    submitted_count: int
    message: str


class QuickRepairPreviewIn(BaseModel):
    case_ids: list[int] = Field(default_factory=list)


class QuickRepairDraftOut(BaseModel):
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
    key_images: list[dict[str, Any]] = Field(default_factory=list)
    repair_channel: str = "none"
    process: list[dict[str, Any]] = Field(default_factory=list)
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
    gate: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class QuickRepairPreviewOut(BaseModel):
    items: list[QuickRepairDraftOut] = Field(default_factory=list)


class QuickRepairApplyIn(BaseModel):
    steps_text: str | None = None
    preconditions: str | None = None
    expected_result: str | None = None


class QuickRepairApplyOut(BaseModel):
    case_id: int
    message: str
