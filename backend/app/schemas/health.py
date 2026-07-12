from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str


class AppConfigResponse(BaseModel):
    os_agent_enabled: bool
