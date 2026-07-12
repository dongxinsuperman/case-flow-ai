from __future__ import annotations

import json

import httpx
import pytest

from app.services.ai_api import (
    AIAPICaseInput,
    AIAPIExecutionPlan,
    AIAPISecurityConfig,
    APIAssertion,
    APIExecutionScenario,
    APIExecutionStep,
    APIRequestPlan,
    StaticPlanCompiler,
    execute_ai_api_case,
)
from app.services.ai_api.compiler import _build_compile_prompt


def _case() -> AIAPICaseInput:
    return AIAPICaseInput(
        title="登录接口返回 token",
        preconditions="base_url=https://api.example.com，手机号 13800000000，密码 test123",
        steps_text="请求登录接口",
        expected_result="状态码 200，返回 token",
        function_map_context="登录接口 POST /login，请求字段 phone/password。",
    )


def test_compile_prompt_keeps_function_map_as_read_only_reference() -> None:
    case_input = AIAPICaseInput(
        title="只查询订单详情",
        steps_text="请求订单详情接口，订单 id 为 1001",
        expected_result="返回订单详情",
        function_map_context="POST /orders 创建订单\nDELETE /orders/{id} 删除订单\nGET /orders/{id} 查询订单",
    )

    prompt = _build_compile_prompt(case_input)

    assert "用户本次任务 > 明确预期结果 > functionMap/API 上下文 > 全局默认配置" in prompt
    assert "functionMap/API 上下文是只读执行参考" in prompt
    assert "不得改变、扩展、替换用户本次任务的目标和测试范围" in prompt
    assert "只抽取与本次任务直接相关的接口和字段" in prompt
    assert "不能因为 functionMap 中存在这些接口或规则就主动扩展测试范围" in prompt


@pytest.mark.asyncio
async def test_compile_failed_does_not_send_request() -> None:
    plan = AIAPIExecutionPlan(
        executable=False,
        failure_type="compile_failed",
        reason="缺少 base_url 和接口路径。",
        repair_suggestion="请补充接口地址和请求参数。",
    )

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("api.example.com",)),
    )

    assert result.status == "failed"
    assert result.status_reason == "compile_failed"
    assert result.exchange is None
    assert "缺少 base_url" in result.report_html
    assert "请补充接口地址" in result.report_html


@pytest.mark.asyncio
async def test_security_blocks_non_allowlisted_url() -> None:
    plan = AIAPIExecutionPlan(
        executable=True,
        request=APIRequestPlan(method="GET", url="https://evil.example.com/users"),
        assertions=[APIAssertion(type="status_code", expected=200)],
    )

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("api.example.com",)),
    )

    assert result.status == "failed"
    assert result.status_reason == "security_blocked"
    assert result.security is not None
    assert "allowlist" in result.security.reason


@pytest.mark.asyncio
async def test_security_allows_localhost_by_default() -> None:
    plan = AIAPIExecutionPlan(
        executable=True,
        request=APIRequestPlan(method="GET", url="http://127.0.0.1:8000/admin"),
        assertions=[APIAssertion(type="status_code", expected=200)],
    )

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("127.0.0.1",)),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True})),
    )

    assert result.status == "success"
    assert result.status_reason == "assertion_passed"
    assert result.security is not None
    assert result.security.allowed is True
    assert "127.0.0.1" in result.report_html


@pytest.mark.asyncio
async def test_successful_request_and_assertions() -> None:
    plan = AIAPIExecutionPlan(
        executable=True,
        request=APIRequestPlan(
            method="POST",
            url="https://api.example.com/login",
            headers={"Content-Type": "application/json", "Authorization": "Bearer real-token"},
            body_type="json",
            body={"phone": "13800000000", "password": "test123"},
        ),
        assertions=[
            APIAssertion(type="status_code", expected=200),
            APIAssertion(type="json_path_exists", path="$.token"),
            APIAssertion(type="json_path_equals", path="$.user.id", expected=7),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://api.example.com/login"
        return httpx.Response(200, json={"token": "abc", "user": {"id": 7}})

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("api.example.com",)),
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "success"
    assert result.status_reason == "assertion_passed"
    assert result.exchange is not None
    assert result.exchange.status_code == 200
    assert result.exchange.started_at is not None
    assert result.exchange.finished_at is not None
    assert all(item.passed for item in result.assertions)
    assert "模型编译计划" in result.report_html
    assert "执行耗时" in result.report_html
    assert "开始时间" in result.report_html
    assert "请求与响应明细" in result.report_html
    assert "执行结果：通过" in result.report_html
    assert "结论原因" in result.report_html
    assert "请求大小" in result.report_html
    assert "响应大小" in result.report_html
    assert "判定规则" not in result.report_html
    assert "statusReason" not in result.report_html
    assert result.exchange.request_headers["Authorization"] == "Bearer real-token"
    assert result.exchange.request_body["password"] == "test123"
    assert "Bearer real-token" in result.report_html
    assert "test123" in result.report_html


@pytest.mark.asyncio
async def test_report_preserves_full_response_by_default() -> None:
    response_text = "prefix-" + ("x" * 25_000) + "-suffix"
    plan = AIAPIExecutionPlan(
        executable=True,
        request=APIRequestPlan(method="GET", url="https://api.example.com/large"),
        assertions=[APIAssertion(type="body_contains", contains="-suffix")],
    )

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("api.example.com",)),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=response_text)),
    )

    assert result.status == "success"
    assert result.exchange is not None
    assert result.exchange.truncated is False
    assert result.exchange.response_text == response_text
    assert "-suffix" in result.report_html


@pytest.mark.asyncio
async def test_boundary_scenarios_pass_when_invalid_value_is_rejected() -> None:
    plan = AIAPIExecutionPlan(
        executable=True,
        reason="limit 参数最大允许 1000，需要验证边界值和越界值。",
        scenarios=[
            APIExecutionScenario(
                id="limit_eq_1000",
                name="边界内最大值应成功",
                intent="验证 limit=1000 仍被接口接受",
                expected_outcome="accepted",
                request=APIRequestPlan(
                    method="POST",
                    url="https://api.example.com/orders/query",
                    body_type="json",
                    body={"limit": 1000},
                ),
                assertions=[APIAssertion(type="status_code", expected=200)],
            ),
            APIExecutionScenario(
                id="limit_gt_1000",
                name="超过最大值应被拒绝",
                intent="验证 limit=1001 被接口拦截",
                expected_outcome="rejected",
                request=APIRequestPlan(
                    method="POST",
                    url="https://api.example.com/orders/query",
                    body_type="json",
                    body={"limit": 1001},
                ),
                assertions=[
                    APIAssertion(type="status_code", expected=400),
                    APIAssertion(type="body_contains", contains="不能大于1000"),
                ],
            ),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["limit"] == 1001:
            return httpx.Response(400, json={"message": "limit不能大于1000"})
        return httpx.Response(200, json={"items": []})

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("api.example.com",)),
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "success"
    assert result.status_reason == "assertion_passed"
    assert len(result.scenario_results) == 2
    assert all(item.passed for item in result.scenario_results)
    assert result.scenario_results[1].expected_outcome == "rejected"
    assert "limit_gt_1000" in result.report_html
    assert "非法请求应被拒绝" in result.report_html
    assert "步骤 #2" in result.report_html
    assert "不能大于1000" in result.report_html


@pytest.mark.asyncio
async def test_chained_steps_extract_and_reuse_variables() -> None:
    plan = AIAPIExecutionPlan(
        executable=True,
        reason="创建用户后查询同一个用户。",
        scenarios=[
            APIExecutionScenario(
                id="user_crud",
                name="用户新增后查询",
                intent="创建用户后使用返回的 userId 查询",
                expected_outcome="changed",
                steps=[
                    APIExecutionStep(
                        id="create_user",
                        name="创建用户",
                        request=APIRequestPlan(
                            method="POST",
                            url="https://api.example.com/users",
                            body_type="json",
                            body={"name": "Alice"},
                        ),
                        assertions=[APIAssertion(type="status_code", expected=201)],
                        extract={"userId": "$.data.id"},
                    ),
                    APIExecutionStep(
                        id="query_user",
                        name="查询用户",
                        request=APIRequestPlan(
                            method="GET",
                            url="https://api.example.com/users/{{userId}}",
                        ),
                        assertions=[
                            APIAssertion(type="status_code", expected=200),
                            APIAssertion(type="json_path_equals", path="$.data.name", expected="Alice"),
                        ],
                    ),
                ],
            )
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"id": "u-100", "name": "Alice"}})
        assert str(request.url) == "https://api.example.com/users/u-100"
        return httpx.Response(200, json={"data": {"id": "u-100", "name": "Alice"}})

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("api.example.com",)),
        transport=httpx.MockTransport(handler),
    )

    scenario = result.scenario_results[0]
    assert result.status == "success"
    assert scenario.variables["userId"] == "u-100"
    assert scenario.step_results[0].extracted == {"userId": "u-100"}
    assert scenario.step_results[1].exchange is not None
    assert scenario.step_results[1].exchange.url == "https://api.example.com/users/u-100"
    assert "提取变量" in result.report_html
    assert "u-100" in result.report_html


@pytest.mark.asyncio
async def test_boundary_scenario_fails_when_invalid_value_is_accepted() -> None:
    plan = AIAPIExecutionPlan(
        executable=True,
        scenarios=[
            APIExecutionScenario(
                id="limit_gt_1000",
                name="超过最大值应被拒绝",
                intent="验证 limit=1001 被接口拦截",
                expected_outcome="rejected",
                request=APIRequestPlan(
                    method="POST",
                    url="https://api.example.com/orders/query",
                    body_type="json",
                    body={"limit": 1001},
                ),
                assertions=[
                    APIAssertion(type="status_code", expected=400),
                    APIAssertion(type="body_contains", contains="不能大于1000"),
                ],
            )
        ],
    )

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("api.example.com",)),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"items": []})),
    )

    assert result.status == "failed"
    assert result.status_reason == "http_error"
    assert len(result.scenario_results) == 1
    assert result.scenario_results[0].passed is False
    assert result.scenario_results[0].status_reason == "http_error"
    assert "limit_gt_1000" in result.error
    assert "状态码不符合预期" in result.report_html


@pytest.mark.asyncio
async def test_assertion_failure_generates_report() -> None:
    plan = AIAPIExecutionPlan(
        executable=True,
        request=APIRequestPlan(method="GET", url="https://api.example.com/profile"),
        assertions=[
            APIAssertion(type="status_code", expected=200),
            APIAssertion(type="json_path_exists", path="$.token"),
        ],
    )

    result = await execute_ai_api_case(
        _case(),
        compiler=StaticPlanCompiler(plan),
        security_config=AIAPISecurityConfig(allowed_hosts=("api.example.com",)),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True})),
    )

    assert result.status == "failed"
    assert result.status_reason == "assertion_failed"
    assert [item.passed for item in result.assertions] == [True, False]
    assert "JSON 路径不存在" in result.report_html
