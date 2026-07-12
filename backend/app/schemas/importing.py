from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ImportMarkdownIn(BaseModel):
    requirement_item_id: int
    filename: str = "uploaded.md"
    content: str
    confirm_independent: bool = False


class ImportReviewDecisionIn(BaseModel):
    incoming_key: str | None = None
    old_case_id: int | None = None
    action: Literal["add", "replace", "skip", "delete", "keep"]


class ImportReviewCommitIn(BaseModel):
    requirement_item_id: int
    filename: str = "uploaded.md"
    content: str
    decisions: list[ImportReviewDecisionIn] = Field(default_factory=list)


class ImportMarkdownOut(BaseModel):
    mode: str
    message: str | None = None
    task_id: str | None = None
    suite_title: str | None = None
    case_count: int | None = None
    batch: dict[str, Any] | None = None
    existing_batch: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class ImportReviewCommitOut(BaseModel):
    mode: str
    message: str
    batch: dict[str, Any]
    review: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class ImportJobStartOut(BaseModel):
    mode: str
    task_id: str
    message: str


class ImportJobStatusOut(BaseModel):
    status: Literal["pending", "done", "error"]
    result: dict[str, Any] | None = None
    error: str | None = None
    elapsed_ms: int | None = None
