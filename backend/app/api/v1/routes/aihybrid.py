from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.aihybrid import HybridSubmissionStatusOut, HybridSubmitIn, HybridSubmitOut
from app.services.ai_hybrid import service

router = APIRouter(prefix="/aihybrid")


@router.post("/api/submissions", response_model=HybridSubmitOut)
async def submit_hybrid(payload: HybridSubmitIn) -> HybridSubmitOut:
    try:
        return await service.accept_submission(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/submissions/{submission_id}", response_model=HybridSubmissionStatusOut)
async def get_hybrid_submission(submission_id: str) -> HybridSubmissionStatusOut:
    result = service.get_submission(submission_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Hybrid submission not found")
    return result
