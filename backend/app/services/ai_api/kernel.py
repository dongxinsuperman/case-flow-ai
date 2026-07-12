from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.services.ai_api.assertions import evaluate_assertions
from app.services.ai_api.compiler import LLMPlanCompiler, PlanCompiler
from app.services.ai_api.http_runner import execute_http_plan, execute_http_request
from app.services.ai_api.report import build_ai_api_report
from app.services.ai_api.schemas import (
    AIAPICaseInput,
    AIAPIExecutionPlan,
    AIAPIExecutionResult,
    AIAPISecurityConfig,
    APIExecutionScenario,
    APIExecutionStep,
    APIRequestPlan,
    AssertionResult,
    ScenarioExecutionResult,
    StepExecutionResult,
)
from app.services.ai_api.security import validate_plan_security, validate_request_security

_VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class AIAPIKernel:
    def __init__(
        self,
        *,
        compiler: PlanCompiler | None = None,
        security_config: AIAPISecurityConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.compiler = compiler or LLMPlanCompiler()
        self.security_config = security_config or AIAPISecurityConfig()
        self.transport = transport

    async def execute(self, case_input: AIAPICaseInput) -> AIAPIExecutionResult:
        try:
            plan = await self.compiler.compile(case_input)
        except Exception as exc:
            return _failed(
                case_input,
                status_reason="model_failed",
                error=str(exc),
                repair_suggestion="模型未能输出合法执行计划，请检查 case 描述和 functionMap/API 上下文。",
            )

        if not plan.executable:
            reason = plan.failure_type or "compile_failed"
            report = build_ai_api_report(
                case_input=case_input,
                status="failed",
                status_reason=reason,
                plan=plan,
                error=plan.reason,
                repair_suggestion=plan.repair_suggestion,
            )
            return AIAPIExecutionResult(
                status="failed",
                status_reason=reason,
                report_html=report,
                plan=plan,
                error=plan.reason,
                repair_suggestion=plan.repair_suggestion,
            )

        if plan.scenarios:
            return await self._execute_scenarios(case_input, plan)

        security = validate_plan_security(plan, self.security_config)
        if not security.allowed:
            report = build_ai_api_report(
                case_input=case_input,
                status="failed",
                status_reason="security_blocked",
                plan=plan,
                security=security,
                error=security.reason,
                repair_suggestion="请确认 API base_url / allowlist 配置，或修改 case 避免访问未授权目标。",
            )
            return AIAPIExecutionResult(
                status="failed",
                status_reason="security_blocked",
                report_html=report,
                plan=plan,
                security=security,
                error=security.reason,
                repair_suggestion="请确认 API base_url / allowlist 配置，或修改 case 避免访问未授权目标。",
            )

        exchange = await execute_http_plan(plan, security, self.security_config, transport=self.transport)
        if exchange.error:
            report = build_ai_api_report(
                case_input=case_input,
                status="failed",
                status_reason="request_error",
                plan=plan,
                security=security,
                exchange=exchange,
                error=exchange.error,
                repair_suggestion="请检查接口地址、网络、TLS、鉴权和请求参数。",
            )
            return AIAPIExecutionResult(
                status="failed",
                status_reason="request_error",
                report_html=report,
                plan=plan,
                security=security,
                exchange=exchange,
                error=exchange.error,
                repair_suggestion="请检查接口地址、网络、TLS、鉴权和请求参数。",
            )

        assertion_results = evaluate_assertions(plan.assertions, exchange)
        passed = all(result.passed for result in assertion_results)
        status = "success" if passed else "failed"
        status_reason = "assertion_passed" if passed else _failed_assertion_reason(assertion_results)
        report = build_ai_api_report(
            case_input=case_input,
            status=status,
            status_reason=status_reason,
            plan=plan,
            security=security,
            exchange=exchange,
            assertions=assertion_results,
            repair_suggestion="" if passed else "请根据报告中的请求、响应和失败断言修正 case 或接口实现。",
        )
        return AIAPIExecutionResult(
            status=status,
            status_reason=status_reason,
            report_html=report,
            plan=plan,
            security=security,
            exchange=exchange,
            assertions=assertion_results,
            repair_suggestion="" if passed else "请根据报告中的请求、响应和失败断言修正 case 或接口实现。",
        )

    async def _execute_scenarios(
        self,
        case_input: AIAPICaseInput,
        plan: AIAPIExecutionPlan,
    ) -> AIAPIExecutionResult:
        scenario_result_items: list[ScenarioExecutionResult] = []
        for item in plan.scenarios:
            scenario_result_items.append(await self._execute_scenario(item))
        scenario_results = tuple(scenario_result_items)
        failed_required = tuple(item for item in scenario_results if item.required and not item.passed)
        passed = not failed_required
        status = "success" if passed else "failed"
        status_reason = "assertion_passed" if passed else _scenario_failure_reason(failed_required)
        error = "" if passed else "; ".join(_scenario_error_summary(item) for item in failed_required[:3])
        repair_suggestion = (
            ""
            if passed
            else "请根据报告中的失败 scenario、真实请求、真实响应和断言结果修正 case 或接口实现。"
        )
        report = build_ai_api_report(
            case_input=case_input,
            status=status,
            status_reason=status_reason,
            plan=plan,
            scenario_results=scenario_results,
            error=error,
            repair_suggestion=repair_suggestion,
        )
        return AIAPIExecutionResult(
            status=status,
            status_reason=status_reason,
            report_html=report,
            plan=plan,
            scenario_results=scenario_results,
            error=error,
            repair_suggestion=repair_suggestion,
        )

    async def _execute_scenario(self, scenario: APIExecutionScenario) -> ScenarioExecutionResult:
        if scenario.steps:
            return await self._execute_step_scenario(scenario)

        if scenario.request is None:
            return ScenarioExecutionResult(
                scenario_id=scenario.id,
                name=scenario.name,
                intent=scenario.intent,
                expected_outcome=scenario.expected_outcome,
                required=scenario.required,
                passed=False,
                status_reason="plan_invalid",
                error="scenario 缺少 request。",
                repair_suggestion="请让模型输出 request 或 steps。",
            )

        security = validate_request_security(scenario.request, self.security_config)
        if not security.allowed:
            return ScenarioExecutionResult(
                scenario_id=scenario.id,
                name=scenario.name,
                intent=scenario.intent,
                expected_outcome=scenario.expected_outcome,
                required=scenario.required,
                passed=False,
                status_reason="security_blocked",
                security=security,
                error=security.reason,
                repair_suggestion="请确认 API base_url / allowlist 配置，或修改 case 避免访问未授权目标。",
            )

        exchange = await execute_http_request(
            scenario.request,
            security,
            self.security_config,
            transport=self.transport,
        )
        if exchange.error:
            return ScenarioExecutionResult(
                scenario_id=scenario.id,
                name=scenario.name,
                intent=scenario.intent,
                expected_outcome=scenario.expected_outcome,
                required=scenario.required,
                passed=False,
                status_reason="request_error",
                security=security,
                exchange=exchange,
                error=exchange.error,
                repair_suggestion="请检查接口地址、网络、TLS、鉴权和请求参数。",
            )

        assertion_results = evaluate_assertions(scenario.assertions, exchange)
        passed = all(result.passed for result in assertion_results)
        status_reason = "assertion_passed" if passed else _failed_assertion_reason(assertion_results)
        return ScenarioExecutionResult(
            scenario_id=scenario.id,
            name=scenario.name,
            intent=scenario.intent,
            expected_outcome=scenario.expected_outcome,
            required=scenario.required,
            passed=passed,
            status_reason=status_reason,
            security=security,
            exchange=exchange,
            assertions=assertion_results,
            repair_suggestion=(
                ""
                if passed
                else "请根据该 scenario 的 expected_outcome、响应和失败断言修正 case 或接口实现。"
            ),
        )

    async def _execute_step_scenario(self, scenario: APIExecutionScenario) -> ScenarioExecutionResult:
        variables: dict[str, Any] = {}
        step_results: list[StepExecutionResult] = []
        for step in scenario.steps:
            result = await self._execute_step(step, variables)
            step_results.append(result)
            variables = dict(result.variables_after)
            if not result.passed:
                return ScenarioExecutionResult(
                    scenario_id=scenario.id,
                    name=scenario.name,
                    intent=scenario.intent,
                    expected_outcome=scenario.expected_outcome,
                    required=scenario.required,
                    passed=False,
                    status_reason=result.status_reason,
                    step_results=tuple(step_results),
                    variables=variables,
                    error=result.error or _step_error_summary(result),
                    repair_suggestion=result.repair_suggestion,
                )

        return ScenarioExecutionResult(
            scenario_id=scenario.id,
            name=scenario.name,
            intent=scenario.intent,
            expected_outcome=scenario.expected_outcome,
            required=scenario.required,
            passed=True,
            status_reason="assertion_passed",
            step_results=tuple(step_results),
            variables=variables,
        )

    async def _execute_step(
        self,
        step: APIExecutionStep,
        variables: dict[str, Any],
    ) -> StepExecutionResult:
        variables_before = dict(variables)
        try:
            request = _render_request_plan(step.request, variables)
        except _VariableError as exc:
            return StepExecutionResult(
                step_id=step.id,
                name=step.name,
                intent=step.intent,
                passed=False,
                status_reason="variable_missing",
                variables_before=variables_before,
                variables_after=variables_before,
                error=str(exc),
                repair_suggestion="请补充该变量来源，或让前序步骤通过 extract 提取后再引用。",
            )

        security = validate_request_security(request, self.security_config)
        if not security.allowed:
            return StepExecutionResult(
                step_id=step.id,
                name=step.name,
                intent=step.intent,
                passed=False,
                status_reason="security_blocked",
                security=security,
                variables_before=variables_before,
                variables_after=variables_before,
                error=security.reason,
                repair_suggestion="请确认 API base_url / allowlist 配置，或修改 case 避免访问未授权目标。",
            )

        exchange = await execute_http_request(
            request,
            security,
            self.security_config,
            transport=self.transport,
        )
        if exchange.error:
            return StepExecutionResult(
                step_id=step.id,
                name=step.name,
                intent=step.intent,
                passed=False,
                status_reason="request_error",
                security=security,
                exchange=exchange,
                variables_before=variables_before,
                variables_after=variables_before,
                error=exchange.error,
                repair_suggestion="请检查接口地址、网络、TLS、鉴权和请求参数。",
            )

        assertion_results = evaluate_assertions(step.assertions, exchange)
        assertions_passed = all(result.passed for result in assertion_results)
        if not assertions_passed:
            return StepExecutionResult(
                step_id=step.id,
                name=step.name,
                intent=step.intent,
                passed=False,
                status_reason=_failed_assertion_reason(assertion_results),
                security=security,
                exchange=exchange,
                assertions=assertion_results,
                variables_before=variables_before,
                variables_after=variables_before,
                error="；".join(item.message for item in assertion_results if not item.passed),
                repair_suggestion="请根据该 step 的响应和失败断言修正 case、接口实现或提取路径。",
            )

        extracted, extract_error = _extract_variables(exchange, step.extract)
        if extract_error:
            return StepExecutionResult(
                step_id=step.id,
                name=step.name,
                intent=step.intent,
                passed=False,
                status_reason="extract_failed",
                security=security,
                exchange=exchange,
                assertions=assertion_results,
                variables_before=variables_before,
                variables_after=variables_before,
                error=extract_error,
                repair_suggestion="请确认响应结构和 extract JSONPath，或改用用户明确提供的数据。",
            )

        variables_after = {**variables_before, **extracted}
        return StepExecutionResult(
            step_id=step.id,
            name=step.name,
            intent=step.intent,
            passed=True,
            status_reason="assertion_passed",
            security=security,
            exchange=exchange,
            assertions=assertion_results,
            extracted=extracted,
            variables_before=variables_before,
            variables_after=variables_after,
        )


async def execute_ai_api_case(
    case_input: AIAPICaseInput,
    *,
    compiler: PlanCompiler | None = None,
    security_config: AIAPISecurityConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AIAPIExecutionResult:
    kernel = AIAPIKernel(compiler=compiler, security_config=security_config, transport=transport)
    return await kernel.execute(case_input)


def _failed(
    case_input: AIAPICaseInput,
    *,
    status_reason: str,
    error: str,
    repair_suggestion: str,
    plan: AIAPIExecutionPlan | None = None,
) -> AIAPIExecutionResult:
    report = build_ai_api_report(
        case_input=case_input,
        status="failed",
        status_reason=status_reason,
        plan=plan,
        error=error,
        repair_suggestion=repair_suggestion,
    )
    return AIAPIExecutionResult(
        status="failed",
        status_reason=status_reason,
        report_html=report,
        plan=plan,
        error=error,
        repair_suggestion=repair_suggestion,
    )


def _failed_assertion_reason(results: tuple[AssertionResult, ...]) -> str:
    for result in results:
        if not result.passed and result.type == "status_code":
            return "http_error"
    return "assertion_failed"


def _scenario_failure_reason(results: tuple[ScenarioExecutionResult, ...]) -> str:
    for reason in (
        "security_blocked",
        "variable_missing",
        "extract_failed",
        "request_error",
        "http_error",
        "assertion_failed",
    ):
        if any(result.status_reason == reason for result in results):
            return reason
    return "assertion_failed"


def _scenario_error_summary(result: ScenarioExecutionResult) -> str:
    detail = result.error
    if not detail:
        failed_assertions = [item.message for item in result.assertions if not item.passed]
        detail = "；".join(failed_assertions)
    return f"{result.scenario_id}: {detail or result.status_reason}"


def _step_error_summary(result: StepExecutionResult) -> str:
    failed_assertions = [item.message for item in result.assertions if not item.passed]
    detail = result.error or "；".join(failed_assertions) or result.status_reason
    return f"{result.step_id}: {detail}"


class _VariableError(ValueError):
    pass


def _render_request_plan(request: APIRequestPlan, variables: dict[str, Any]) -> APIRequestPlan:
    rendered = _render_value(request.model_dump(mode="json"), variables)
    return APIRequestPlan.model_validate(rendered)


def _render_value(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_string(value, variables)
    if isinstance(value, dict):
        return {key: _render_value(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, variables) for item in value]
    return value


def _render_string(value: str, variables: dict[str, Any]) -> Any:
    match = _VAR_PATTERN.fullmatch(value)
    if match:
        name = match.group(1)
        if name not in variables:
            raise _VariableError(f"缺少变量 {name}，无法渲染请求。")
        return variables[name]

    def replace_var(match_item: re.Match[str]) -> str:
        name = match_item.group(1)
        if name not in variables:
            raise _VariableError(f"缺少变量 {name}，无法渲染请求。")
        return str(variables[name])

    return _VAR_PATTERN.sub(replace_var, value)


def _extract_variables(exchange: Any, extract: dict[str, str]) -> tuple[dict[str, Any], str]:
    if not extract:
        return {}, ""
    try:
        data = json.loads(exchange.response_text or "")
    except Exception:
        return {}, "响应体不是可解析 JSON，无法执行变量提取。"

    extracted: dict[str, Any] = {}
    for name, path in extract.items():
        found, value = _json_path_get(data, path)
        if not found:
            return {}, f"未能从响应路径 {path} 提取变量 {name}。"
        extracted[name] = value
    return extracted, ""


def _json_path_get(data: Any, path: str) -> tuple[bool, Any]:
    if path == "$":
        return True, data
    if not path.startswith("$."):
        return False, None
    current = data
    for token in _tokenize_json_path(path[2:]):
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False, None
            current = current[token]
            continue
        if not isinstance(current, dict) or token not in current:
            return False, None
        current = current[token]
    return True, current


def _tokenize_json_path(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for part in path.split("."):
        if not part:
            continue
        match = re.match(r"^([^\[]+)((?:\[\d+\])*)$", part)
        if not match:
            tokens.append(part)
            continue
        tokens.append(match.group(1))
        for index in re.findall(r"\[(\d+)\]", match.group(2)):
            tokens.append(int(index))
    return tokens
