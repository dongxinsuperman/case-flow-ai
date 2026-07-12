from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.case_assets import CaseAsset, CaseBody, CaseWorkItem
from app.services import executions
from app.services.ai_api import AIAPIExecutionResult
from app.services.ai_api.schemas import HTTPExchange


class _FakeSession:
    def __init__(self, case: object, body: object, work_item: object) -> None:
        self.case = case
        self.body = body
        self.work_item = work_item
        self.commits = 0

    async def get(self, model: object, _identity: int) -> object | None:
        if model is CaseAsset:
            return self.case
        if model is CaseBody:
            return self.body
        if model is CaseWorkItem:
            return self.work_item
        return None

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_aiapi_item_execution_persists_report_and_updates_work_item(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = SimpleNamespace(
        id=7,
        clean_title="登录接口返回 token",
        raw_title="登录接口返回 token",
        manual=False,
    )
    body = SimpleNamespace(
        preconditions="手机号 13800000000，密码 test123",
        steps_text="请求登录接口",
        expected_result="返回 token",
    )
    work_item = SimpleNamespace(
        execution_status="running",
        lifecycle_state="待验证",
        attention_reason=None,
        case_type="auto",
        run_enabled=True,
        report_url=None,
        failure_type=None,
        failure_summary=None,
        bug_url="http://bug.local/1",
        bug_external_id="BUG-1",
        active_execution_batch_id=11,
        external_submission_id=None,
        execution_started_at=None,
        execution_finished_at=None,
        updated_at=None,
    )
    item = SimpleNamespace(
        case_id=7,
        raw_item={"caseId": "cf-7", "functionMapContext": "endpoint=/login"},
        state="queued",
        status_reason=None,
        run_id=None,
        report_url=None,
    )
    batch = SimpleNamespace(
        id=11,
        submission_id="local-aiapi-test",
        raw_request={"functionMapContext": "base_url=https://api.example.com"},
        started_at=None,
    )
    session = _FakeSession(case, body, work_item)
    settings = SimpleNamespace(
        repair_image_dir=str(tmp_path),
        public_base_url="http://case-flow.local",
        aiapi_allowed_hosts_raw="api.example.com",
        aiapi_allowed_base_urls_raw="",
        aiapi_default_base_url="",
        aiapi_default_headers_raw='{"Authorization":"Bearer real-token"}',
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
            assert "base_url=https://api.example.com" in case_input.function_map_context
            assert "endpoint=/login" in case_input.function_map_context
            return AIAPIExecutionResult(
                status="success",
                status_reason="assertion_passed",
                report_html="<html>Bearer real-token test123</html>",
            )

    monkeypatch.setattr(executions, "get_settings", lambda: settings)
    monkeypatch.setattr(executions, "AIAPIKernel", FakeKernel)

    await executions._execute_aiapi_item(session, batch, item)  # type: ignore[arg-type]

    assert session.commits == 1
    assert item.state == "success"
    assert item.status_reason == "assertion_passed"
    assert item.raw_item["effectiveFunctionMapContext"] == "base_url=https://api.example.com\n\nendpoint=/login"
    assert item.report_url.startswith("http://case-flow.local/media/aiapi_reports/")
    assert work_item.execution_status == "passed"
    assert work_item.lifecycle_state == "已固化"
    assert work_item.report_url == item.report_url
    assert work_item.bug_url is None
    assert work_item.external_submission_id == "local-aiapi-test"
    assert "Bearer real-token" in next((tmp_path / "aiapi_reports").glob("*.html")).read_text()


def test_aiapi_security_config_parses_internal_executor_settings() -> None:
    settings = SimpleNamespace(
        aiapi_allowed_hosts_raw="api.example.com, internal.example.com",
        aiapi_allowed_base_urls_raw="https://api.example.com/v1",
        aiapi_default_base_url="https://api.example.com",
        aiapi_default_headers_raw='{"Authorization":"Bearer real-token"}',
        aiapi_allowed_methods_raw="GET,POST",
        aiapi_allow_private_networks=True,
        aiapi_max_timeout_seconds=30,
        aiapi_max_response_bytes=0,
        aiapi_follow_redirects=True,
    )

    config = executions._aiapi_security_config(settings)  # type: ignore[arg-type]

    assert config.allowed_hosts == ("api.example.com", "internal.example.com")
    assert config.allowed_base_urls == ("https://api.example.com/v1",)
    assert config.default_base_url == "https://api.example.com"
    assert config.default_headers["Authorization"] == "Bearer real-token"
    assert config.allowed_methods == frozenset({"GET", "POST"})
    assert config.allow_private_networks is True
    assert config.max_response_bytes == 0


def test_aiapi_result_payload_is_json_safe_with_exchange_timestamps() -> None:
    result = AIAPIExecutionResult(
        status="success",
        status_reason="assertion_passed",
        report_html="<html></html>",
        exchange=HTTPExchange(
            method="GET",
            url="https://api.example.com/ping",
            request_headers={},
            request_body=None,
            status_code=200,
            started_at=datetime(2026, 6, 28, 1, 30, tzinfo=UTC),
            finished_at=datetime(2026, 6, 28, 1, 30, 1, tzinfo=UTC),
        ),
    )

    payload = executions._aiapi_result_payload(result)

    assert payload["exchange"]["started_at"] == "2026-06-28T01:30:00+00:00"
    assert payload["exchange"]["finished_at"] == "2026-06-28T01:30:01+00:00"
    json.dumps(payload)
