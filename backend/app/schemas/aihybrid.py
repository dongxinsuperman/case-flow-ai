from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class HybridSubmitItemIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(
        validation_alias=AliasChoices("caseId", "case_id"),
        serialization_alias="caseId",
    )
    case_name: str = Field(
        default="",
        validation_alias=AliasChoices("caseName", "case_name"),
        serialization_alias="caseName",
    )
    run_content: str = Field(
        default="",
        validation_alias=AliasChoices("runContent", "run_content"),
        serialization_alias="runContent",
    )
    function_map_context: str = Field(
        default="",
        validation_alias=AliasChoices("functionMapContext", "function_map_context"),
        serialization_alias="functionMapContext",
    )
    function_maps: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("functionMaps", "function_maps"),
        serialization_alias="functionMaps",
    )
    platforms: list[str] = Field(default_factory=list)


class HybridSubmitIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    submission_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("submissionName", "submission_name"),
        serialization_alias="submissionName",
    )
    callback_url: str = Field(
        default="",
        validation_alias=AliasChoices("callbackUrl", "callback_url"),
        serialization_alias="callbackUrl",
    )
    items: list[HybridSubmitItemIn] = Field(default_factory=list)
    function_map_context: str = Field(
        default="",
        validation_alias=AliasChoices("functionMapContext", "function_map_context"),
        serialization_alias="functionMapContext",
    )
    function_maps: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("functionMaps", "function_maps"),
        serialization_alias="functionMaps",
    )
    cache_mode: str = Field(
        default="off",
        validation_alias=AliasChoices("cacheMode", "cache_mode"),
        serialization_alias="cacheMode",
    )
    retry_max: int = Field(
        default=0,
        validation_alias=AliasChoices("retryMax", "retry_max"),
        serialization_alias="retryMax",
    )


class HybridSubmitItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(serialization_alias="caseId")
    platform: str = "mixed"
    state: str = "queued"


class HybridSubmitOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    submission_id: str = Field(serialization_alias="submissionId")
    submission_name: str | None = Field(default=None, serialization_alias="submissionName")
    items: list[HybridSubmitItemOut] = Field(default_factory=list)


class HybridSubmissionStatusItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(serialization_alias="caseId")
    platform: str = "mixed"
    state: str
    report_url: str | None = Field(default=None, serialization_alias="reportUrl")
    status_reason: str | None = Field(default=None, serialization_alias="statusReason")


class HybridSubmissionStatusOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    submission_id: str = Field(serialization_alias="submissionId")
    submission_name: str | None = Field(default=None, serialization_alias="submissionName")
    state: str
    items: list[HybridSubmissionStatusItemOut] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
