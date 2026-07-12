from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import (
    agent,
    ai_api,
    execution_logs,
    function_map_assets,
    health,
    quick,
    workbench,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(workbench.router, tags=["workbench"])
api_router.include_router(quick.router, tags=["quick"])
api_router.include_router(ai_api.router, tags=["aiapi"])
api_router.include_router(agent.router, tags=["agent"])
api_router.include_router(function_map_assets.router, tags=["function-map-assets"])
api_router.include_router(execution_logs.router, tags=["execution-logs"])
