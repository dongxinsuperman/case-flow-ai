from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AIPhoneDeviceListOut(BaseModel):
    source: str
    devices: list[dict[str, Any]]
    error: str | None = None


class AIPhoneSubmitIn(BaseModel):
    case_ids: list[int] = Field(default_factory=list)
    device_alias_pools: dict[str, list[str]] | None = None
    submission_name: str | None = None
    cache_mode: str = "off"
    retry_max: int = 0
    # 一次点击的聚合 ID（前端生成，可选）：把多路 submit 的调用日志串起来。
    execution_request_group_id: str | None = None
    # 触发人（前端当前用户，可选）：记入执行流水台账。
    current_user_id: int | None = None


class AIPhoneSubmitOut(BaseModel):
    submission_id: str
    submission_name: str | None = None
    callback_url: str
    batch_id: int
    submitted_count: int
    response: dict[str, Any]
    # 可追踪的调用 ID（检查点 7）；后台化后前端凭它对上台账。
    call_id: str | None = None


class AIPhoneCallbackOut(BaseModel):
    handled: bool
    event: str
    submission_id: str | None = None
    updated_case_ids: list[int] = Field(default_factory=list)


class CasePlatformResultOut(BaseModel):
    """单条 case 最近一次批次里、每个端的执行结果（供多端“查看报告”浮层）。"""

    platform: str
    state: str
    report_url: str | None = None
    run_id: str | None = None
    status_reason: str | None = None
