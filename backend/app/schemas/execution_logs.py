from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ExecutionCallLogOut(BaseModel):
    id: int
    call_id: str
    request_group_id: str | None = None
    mode: str
    scope: str
    entry: str
    executor: str
    requirement_item_id: int | None = None
    quick_session_id: str | None = None
    case_ids: list[int] = []
    execution_batch_id: int | None = None
    submission_id: str | None = None
    trigger_user_id: int | None = None
    trigger_user_name: str | None = None
    requirement_item_title: str | None = None
    quick_session_title: str | None = None
    # 本次实际提交给执行器的 functionMapContext（从批次 raw_request 读，端过滤后的真实结果）
    submitted_function_map_context: str | None = None
    input: dict[str, Any] = {}
    function_map_result: dict[str, Any] | None = None
    effective_context: dict[str, Any] | None = None
    status: str
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ExecutionCallLogPage(BaseModel):
    items: list[ExecutionCallLogOut] = []
    total: int = 0
    page: int = 1
    page_size: int = 20
