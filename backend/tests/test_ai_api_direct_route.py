from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services.ai_api import AIAPIExecutionResult
from app.services.ai_api import direct as aiapi_direct


@pytest.mark.asyncio
async def test_direct_aiapi_run_returns_report_and_result(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        repair_image_dir=str(tmp_path),
        public_base_url="http://testserver",
        aiapi_allowed_hosts_raw="api.example.com",
        aiapi_allowed_base_urls_raw="",
        aiapi_default_base_url="",
        aiapi_default_headers_raw="",
        aiapi_allowed_methods_raw="GET,POST",
        aiapi_allow_private_networks=False,
        aiapi_max_timeout_seconds=20,
        aiapi_max_response_bytes=0,
        aiapi_follow_redirects=False,
    )

    class FakeKernel:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def execute(self, case_input: object) -> AIAPIExecutionResult:
            assert case_input.title == "外部调用订单 limit 边界测试"
            assert "limit 不能大于 1000" in case_input.expected_result
            return AIAPIExecutionResult(
                status="success",
                status_reason="assertion_passed",
                report_html="<html>limit_gt_1000</html>",
            )

    monkeypatch.setattr(aiapi_direct, "get_settings", lambda: settings)
    monkeypatch.setattr(aiapi_direct, "AIAPIKernel", FakeKernel)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/aiapi/run",
            json={
                "title": "外部调用订单 limit 边界测试",
                "steps_text": "测试 limit=1000 和 limit=1001",
                "expected_result": "limit 不能大于 1000",
                "function_map_context": "base_url=https://api.example.com",
                "return_report_html": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"].startswith("direct-aiapi-")
    assert payload["status"] == "success"
    assert payload["status_reason"] == "assertion_passed"
    assert payload["report_url"].startswith("http://testserver/media/aiapi_reports/")
    assert payload["report_html"] == "<html>limit_gt_1000</html>"
    assert payload["result"]["status"] == "success"
    assert "limit_gt_1000" in next((tmp_path / "aiapi_reports").glob("*.html")).read_text()
