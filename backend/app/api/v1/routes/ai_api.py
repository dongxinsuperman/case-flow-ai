from __future__ import annotations

from fastapi import APIRouter

from app.schemas.ai_api import AIAPIDirectRunIn, AIAPIDirectRunOut
from app.services.ai_api.direct import run_direct_aiapi

router = APIRouter(prefix="/aiapi")


@router.post("/run", response_model=AIAPIDirectRunOut)
async def run_aiapi(payload: AIAPIDirectRunIn) -> AIAPIDirectRunOut:
    return await run_direct_aiapi(payload)
