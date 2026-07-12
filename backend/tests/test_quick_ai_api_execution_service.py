from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.quick import QuickCase, QuickCaseBody, QuickCaseWorkItem
from app.services import quick_executions
from app.services.ai_api import AIAPIExecutionResult


class _FakeSession:
    def __init__(self, case: object, body: object, work_item: object) -> None:
        self.case = case
        self.body = body
        self.work_item = work_item
        self.commits = 0

    async def get(self, model: object, _identity: int) -> object | None:
        if model is QuickCase:
            return self.case
        if model is QuickCaseBody:
            return self.body
        if model is QuickCaseWorkItem:
            return self.work_item
        return None

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_quick_aiapi_item_execution_persists_report_and_updates_work_item(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = SimpleNamespace(
        id=17,
        clean_title="quick 登录接口返回 token",
        raw_title="quick 登录接口返回 token",
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
        bug_url="http://bug.local/quick-1",
        bug_external_id="BUG-Q1",
        active_execution_batch_id=21,
        external_submission_id=None,
        execution_started_at=None,
        execution_finished_at=None,
        updated_at=None,
    )
    item = SimpleNamespace(
        case_id=17,
        raw_item={"caseId": "qcf-17", "functionMapContext": "endpoint=/quick-login"},
        state="queued",
        status_reason=None,
        run_id=None,
        report_url=None,
    )
    batch = SimpleNamespace(
        id=21,
        submission_id="local-quick-aiapi-test",
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
            assert "endpoint=/quick-login" in case_input.function_map_context
            return AIAPIExecutionResult(
                status="success",
                status_reason="assertion_passed",
                report_html="<html>Bearer real-token test123</html>",
            )

    monkeypatch.setattr(quick_executions, "get_settings", lambda: settings)
    monkeypatch.setattr(quick_executions, "AIAPIKernel", FakeKernel)

    await quick_executions._execute_aiapi_item(session, batch, item)  # type: ignore[arg-type]

    assert session.commits == 1
    assert item.state == "success"
    assert item.status_reason == "assertion_passed"
    assert item.raw_item["effectiveFunctionMapContext"] == "base_url=https://api.example.com\n\nendpoint=/quick-login"
    assert item.report_url.startswith("http://case-flow.local/media/aiapi_reports/")
    assert work_item.execution_status == "passed"
    assert work_item.lifecycle_state == "已固化"
    assert work_item.report_url == item.report_url
    assert work_item.bug_url is None
    assert work_item.external_submission_id == "local-quick-aiapi-test"
    assert "Bearer real-token" in next((tmp_path / "aiapi_reports").glob("*.html")).read_text()
