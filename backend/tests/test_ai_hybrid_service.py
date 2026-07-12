from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.aihybrid import HybridSubmitIn
from app.services.ai_hybrid import service
from app.services.ai_hybrid.schemas import HybridRunResult


@pytest.mark.asyncio
async def test_aihybrid_service_posts_item_and_submission_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service._submissions.clear()
    callbacks: list[dict[str, object]] = []

    async def fake_run_hybrid(inp: object, settings: object) -> HybridRunResult:
        assert inp.function_map_context == "functionMapContext\n\nitemFunctionMapContext"
        return HybridRunResult(
            status="success",
            status_reason="all_required_steps_passed",
            final_summary="ok",
            child_results_payload=[],
            reasoning_trace=[],
        )

    async def fake_write_report(settings: object, submission_id: str, source_ref: str, result: object) -> str:
        return f"http://case-flow.local/media/aihybrid_reports/{submission_id}-{source_ref}.html"

    async def fake_post_parent_callback(url: str, payload: dict[str, object], record: dict[str, object]) -> None:
        callbacks.append(payload)

    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(service, "run_hybrid", fake_run_hybrid)
    monkeypatch.setattr(service, "write_hybrid_report", fake_write_report)
    monkeypatch.setattr(service, "_post_parent_callback", fake_post_parent_callback)

    response = await service.accept_submission(
        HybridSubmitIn.model_validate(
            {
                "submissionName": "Hybrid test",
                "callbackUrl": "http://case-flow.local/api/v1/aihybrid/callback/token",
                "functionMapContext": "functionMapContext",
                "items": [
                    {
                        "caseId": "cf-1",
                        "caseName": "混合 case",
                        "runContent": "先调用 api，再打开 app 检查结果",
                        "functionMapContext": "itemFunctionMapContext",
                    }
                ],
            }
        )
    )

    for _ in range(20):
        if len(callbacks) >= 2:
            break
        await asyncio.sleep(0)

    assert response.submission_id.startswith("aihybrid-")
    assert response.items[0].case_id == "cf-1"
    assert callbacks[0]["event"] == "submission.item.terminal"
    assert callbacks[0]["caseId"] == "cf-1"
    assert callbacks[0]["platform"] == "mixed"
    assert callbacks[0]["state"] == "success"
    assert callbacks[1]["event"] == "submission.terminal"
    assert callbacks[1]["submissionState"] == "done"
    status = service.get_submission(response.submission_id)
    assert status is not None
    assert status.state == "done"
    assert status.items[0].state == "success"
    service._submissions.clear()
