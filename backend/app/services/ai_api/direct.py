from __future__ import annotations

import uuid
from pathlib import Path

from app.core.settings import Settings, get_settings
from app.schemas.ai_api import AIAPIDirectRunIn, AIAPIDirectRunOut
from app.services.ai_api import AIAPICaseInput, AIAPIKernel
from app.services.executions import _aiapi_result_payload, _aiapi_security_config


async def run_direct_aiapi(payload: AIAPIDirectRunIn) -> AIAPIDirectRunOut:
    settings = get_settings()
    run_id = f"direct-aiapi-{uuid.uuid4().hex}"
    case_input = AIAPICaseInput(
        title=payload.title or payload.submission_name or "AI API Direct Run",
        preconditions=payload.preconditions,
        steps_text=payload.steps_text,
        expected_result=payload.expected_result,
        function_map_context=payload.function_map_context,
    )
    kernel = AIAPIKernel(security_config=_aiapi_security_config(settings))
    result = await kernel.execute(case_input)
    report_url = _write_direct_report(settings, run_id, result.report_html)
    return AIAPIDirectRunOut(
        run_id=run_id,
        status=result.status,
        status_reason=result.status_reason,
        report_url=report_url,
        report_html=result.report_html if payload.return_report_html else None,
        result=_aiapi_result_payload(result),
    )


def _write_direct_report(settings: Settings, run_id: str, report_html: str) -> str:
    report_dir = Path(settings.repair_image_dir) / "aiapi_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename(run_id)}-{uuid.uuid4().hex}.html"
    (report_dir / filename).write_text(report_html, encoding="utf-8")
    path = f"/media/aiapi_reports/{filename}"
    base = str(settings.public_base_url or "").rstrip("/")
    return f"{base}{path}" if base else path


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or ""))
    return safe[:80] or "direct-aiapi"
