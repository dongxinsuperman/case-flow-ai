from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class AIAPIDirectRunIn(BaseModel):
    title: str = ""
    preconditions: str = ""
    steps_text: str = ""
    expected_result: str = ""
    function_map_context: str = ""
    submission_name: str | None = None
    return_report_html: bool = True


class AIAPIDirectRunOut(BaseModel):
    run_id: str
    status: Literal["success", "failed"]
    status_reason: str
    report_url: str
    report_html: str | None = None
    result: dict[str, Any]
