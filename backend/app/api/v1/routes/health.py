from __future__ import annotations

from fastapi import APIRouter

from app.core.settings import get_settings
from app.schemas.health import AppConfigResponse, HealthResponse

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/config", response_model=AppConfigResponse)
async def app_config() -> AppConfigResponse:
    settings = get_settings()
    return AppConfigResponse(os_agent_enabled=settings.os_agent_enabled)
