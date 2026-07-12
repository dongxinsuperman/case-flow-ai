from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlparse

from app.services.ai_api.schemas import (
    AIAPICaseInput,
    AIAPIExecutionPlan,
    APIRequestPlan,
    AssertionResult,
    HTTPExchange,
    ScenarioExecutionResult,
    SecurityValidationResult,
)

REPORT_VERSION = "1.2"


def build_ai_api_report(
    *,
    case_input: AIAPICaseInput,
    status: str,
    status_reason: str,
    plan: AIAPIExecutionPlan | None = None,
    security: SecurityValidationResult | None = None,
    exchange: HTTPExchange | None = None,
    assertions: tuple[AssertionResult, ...] = (),
    scenario_results: tuple[ScenarioExecutionResult, ...] = (),
    error: str = "",
    repair_suggestion: str = "",
) -> str:
    title = case_input.title or "AI API 执行报告"
    stats = _collect_stats(
        plan=plan,
        exchange=exchange,
        assertions=assertions,
        scenario_results=scenario_results,
    )
    status_class = "success" if status == "success" else "failed"
    reason_label = _status_reason_label(status_reason)
    generated_at = _fmt_ts(datetime.now(UTC))
    sections = [
        _case_section(case_input),
        _execution_summary_section(stats=stats, status=status),
        _plan_summary_section(plan, error=error, repair_suggestion=repair_suggestion),
        _timeline_section(
            plan=plan,
            security=security,
            exchange=exchange,
            assertions=assertions,
            scenario_results=scenario_results,
            status_reason=status_reason,
            error=error,
            repair_suggestion=repair_suggestion,
        ),
        _diagnosis_section(status=status, error=error, repair_suggestion=repair_suggestion),
        _raw_evidence_section(
            plan=plan,
            security=security,
            exchange=exchange,
            assertions=assertions,
            scenario_results=scenario_results,
        ),
    ]

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_e(title)} · AI API 报告</title>
<style>
{_CSS}
</style>
</head>
<body>
<div class="single-container">
  <div class="hd">
    <div class="hd-title">
      <h1>{_e(title)}</h1>
      <span class="sub">· AI API</span>
      {_status_badge(status, size="lg")}
    </div>
    <div class="hd-meta">
      <span class="chip"><b>执行器</b>AI API</span>
      <span class="chip"><b>结论原因</b>{_e(reason_label)}</span>
      <span class="chip"><b>报告生成</b>{_e(generated_at)}</span>
    </div>
  </div>
  <div class="reason {status_class}">
    执行结果：{_e(_status_label(status))} · {_e(reason_label)}
  </div>
  {''.join(sections)}
  <div class="foot">AI API 报告 v{REPORT_VERSION} · 生成于 {generated_at}</div>
</div>
</body>
</html>"""


def _collect_stats(
    *,
    plan: AIAPIExecutionPlan | None,
    exchange: HTTPExchange | None,
    assertions: tuple[AssertionResult, ...],
    scenario_results: tuple[ScenarioExecutionResult, ...],
) -> dict[str, Any]:
    exchanges = _collect_exchanges(exchange, scenario_results)
    planned_requests = 0
    if plan is not None:
        if plan.scenarios:
            planned_requests = sum(len(item.steps) if item.steps else 1 for item in plan.scenarios)
        elif plan.request is not None:
            planned_requests = 1
    actual_requests = len(exchanges)
    total_assertions = len(assertions) + sum(
        len(item.assertions) + sum(len(step.assertions) for step in item.step_results)
        for item in scenario_results
    )
    passed_assertions = sum(1 for item in assertions if item.passed) + sum(
        1 for result in scenario_results for item in result.assertions if item.passed
    ) + sum(
        1
        for result in scenario_results
        for step in result.step_results
        for item in step.assertions
        if item.passed
    )
    started_at = min((item.started_at for item in exchanges if item.started_at), default=None)
    finished_at = max((item.finished_at for item in exchanges if item.finished_at), default=None)
    elapsed_values = [item.elapsed_ms for item in exchanges if item.elapsed_ms is not None]
    elapsed_ms = sum(elapsed_values) if elapsed_values else None
    interface_count = len({_interface_key(item) for item in exchanges})
    request_bytes = sum(item.request_bytes for item in exchanges)
    response_bytes = sum(item.response_bytes for item in exchanges)
    return {
        "planned_requests": planned_requests,
        "actual_requests": actual_requests,
        "interface_count": interface_count,
        "total_assertions": total_assertions,
        "passed_assertions": passed_assertions,
        "scenario_count": len(scenario_results) if scenario_results else (len(plan.scenarios) if plan else 0),
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": elapsed_ms,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
    }


def _execution_summary_section(*, stats: dict[str, Any], status: str) -> str:
    data_size_label = (
        f'请求 {_fmt_bytes(stats["request_bytes"])} / '
        f'响应 {_fmt_bytes(stats["response_bytes"])}'
    )
    items = [
        ("执行结果", _status_label(status), "ok" if status == "success" else "bad"),
        ("执行耗时", _fmt_elapsed(stats["elapsed_ms"]), ""),
        ("开始时间", _fmt_ts(stats["started_at"]), ""),
        ("结束时间", _fmt_ts(stats["finished_at"]), ""),
        ("请求", f'{stats["actual_requests"]}/{stats["planned_requests"]} 次', ""),
        ("接口", f'{stats["interface_count"]} 个', ""),
        ("数据量", data_size_label, ""),
        ("断言", f'{stats["passed_assertions"]}/{stats["total_assertions"]}', ""),
    ]
    cards = "".join(
        f"""
<div class="summary-card {state}">
  <div class="summary-label">{_e(label)}</div>
  <div class="summary-value">{_e(value)}</div>
</div>"""
        for label, value, state in items
    )
    return _section("执行概览", f'<div class="summary-grid">{cards}</div>')


def _case_section(case_input: AIAPICaseInput) -> str:
    return _section(
        "用户输入",
        _kv_block(
            [
                ("测试标题", case_input.title),
                ("前置条件", case_input.preconditions),
                ("操作步骤", case_input.steps_text),
                ("预期结果", case_input.expected_result),
                ("API 上下文", _loaded_label(case_input.function_map_context)),
            ]
        ),
    )


def _plan_summary_section(
    plan: AIAPIExecutionPlan | None,
    *,
    error: str,
    repair_suggestion: str,
) -> str:
    if plan is None:
        return _section(
            "AI 编排",
            _kv_block(
                [
                    ("结果", "未得到模型执行计划"),
                    ("问题说明", error),
                    ("修复建议", repair_suggestion),
                ]
            ),
        )

    body_parts: list[str] = []
    if plan.reason:
        body_parts.append(f'<div class="model-note"><b>模型说明</b>{_e(plan.reason)}</div>')
    plan_detail = _plan_detail_html(plan)
    rows: list[tuple[str, Any]] = []
    if not plan.executable:
        rows.extend(
            [
                ("结果", "不可执行"),
                ("失败分类", _status_reason_label(plan.failure_type)),
                ("修复建议", plan.repair_suggestion),
            ]
        )
    if rows:
        body_parts.append(_kv_block(rows))
    if plan_detail:
        body_parts.append(plan_detail)
    return _section("AI 编排", "".join(body_parts) or '<div class="empty">模型未补充编排说明。</div>')


def _timeline_section(
    *,
    plan: AIAPIExecutionPlan | None,
    security: SecurityValidationResult | None,
    exchange: HTTPExchange | None,
    assertions: tuple[AssertionResult, ...],
    scenario_results: tuple[ScenarioExecutionResult, ...],
    status_reason: str,
    error: str,
    repair_suggestion: str,
) -> str:
    if scenario_results:
        cards = "".join(
            _scenario_card(index, result)
            for index, result in enumerate(scenario_results, start=1)
        )
        return _section("请求与响应明细", f'<div class="timeline">{cards}</div>')

    if exchange is not None or security is not None or (plan and plan.request is not None):
        card = _request_card(
            step_no=1,
            title="单请求执行",
            intent=plan.reason if plan else "",
            expected_outcome="accepted",
            security=security,
            exchange=exchange,
            assertions=assertions,
            passed=all(item.passed for item in assertions) and not error,
            status_reason=status_reason,
            error=error,
            repair_suggestion=repair_suggestion,
        )
        return _section("请求与响应明细", f'<div class="timeline">{card}</div>')

    card = _compile_card(plan=plan, error=error, repair_suggestion=repair_suggestion)
    return _section("请求与响应明细", f'<div class="timeline">{card}</div>')


def _compile_card(
    *,
    plan: AIAPIExecutionPlan | None,
    error: str,
    repair_suggestion: str,
) -> str:
    reason = error or (plan.reason if plan else "") or "模型未能产出可执行请求计划。"
    suggestion = repair_suggestion or (plan.repair_suggestion if plan else "")
    body = [
        f'<div class="tl-thought"><b>编译结果</b>{_e(reason)}</div>',
        '<div class="tl-action"><b>动作</b>未发起 HTTP 请求</div>',
    ]
    if suggestion:
        body.append(f'<div class="diag failed"><b>修复建议</b>{_e(suggestion)}</div>')
    return _timeline_card(
        step_no=1,
        tag="未执行",
        tag_class="t-failed",
        title="模型编译未通过",
        elapsed_ms=None,
        body="".join(body),
    )


def _scenario_card(step_no: int, result: ScenarioExecutionResult) -> str:
    if result.step_results:
        return _step_scenario_card(step_no, result)

    title = result.name or result.scenario_id or f"场景 {step_no}"
    return _request_card(
        step_no=step_no,
        title=title,
        intent=result.intent,
        expected_outcome=result.expected_outcome,
        security=result.security,
        exchange=result.exchange,
        assertions=result.assertions,
        passed=result.passed,
        status_reason=result.status_reason,
        error=result.error,
        repair_suggestion=result.repair_suggestion,
        scenario_id=result.scenario_id,
        required=result.required,
    )


def _step_scenario_card(step_no: int, result: ScenarioExecutionResult) -> str:
    title = result.name or result.scenario_id or f"场景 {step_no}"
    tag = "通过" if result.passed else "失败"
    tag_class = "t-finished" if result.passed else "t-failed"
    rows = [
        ("场景 ID", result.scenario_id),
        ("场景意图", result.intent),
        ("预期语义", _expected_outcome_label(result.expected_outcome)),
        ("最终变量", _display(result.variables) if result.variables else ""),
        ("结论原因", _status_reason_label(result.status_reason)),
    ]
    step_cards = "".join(
        _request_card(
            step_no=index,
            title=step.name or step.step_id,
            intent=step.intent,
            expected_outcome=result.expected_outcome,
            security=step.security,
            exchange=step.exchange,
            assertions=step.assertions,
            passed=step.passed,
            status_reason=step.status_reason,
            error=step.error,
            repair_suggestion=step.repair_suggestion,
            scenario_id=step.step_id,
            required=True,
            variables_before=step.variables_before,
            variables_after=step.variables_after,
            extracted=step.extracted,
        )
        for index, step in enumerate(result.step_results, start=1)
    )
    body = [
        f'<div class="tl-thought"><b>场景目标</b>{_e(result.intent or title)}</div>',
        f'<dl class="kv compact">{_kv_rows(rows)}</dl>',
        f'<div class="nested-timeline">{step_cards}</div>',
    ]
    if result.error:
        body.append(f'<div class="diag failed"><b>失败说明</b>{_e(result.error)}</div>')
    if result.repair_suggestion:
        body.append(f'<div class="diag warn"><b>修复建议</b>{_e(result.repair_suggestion)}</div>')
    return _timeline_card(
        step_no=step_no,
        tag=tag,
        tag_class=tag_class,
        title=title,
        elapsed_ms=sum(step.exchange.elapsed_ms for step in result.step_results if step.exchange),
        body="".join(body),
    )


def _request_card(
    *,
    step_no: int,
    title: str,
    intent: str,
    expected_outcome: str,
    security: SecurityValidationResult | None,
    exchange: HTTPExchange | None,
    assertions: tuple[AssertionResult, ...],
    passed: bool,
    status_reason: str,
    error: str,
    repair_suggestion: str,
    scenario_id: str = "",
    required: bool = True,
    variables_before: dict[str, Any] | None = None,
    variables_after: dict[str, Any] | None = None,
    extracted: dict[str, Any] | None = None,
) -> str:
    tag = "通过" if passed else "失败"
    tag_class = "t-finished" if passed else "t-failed"
    request_line = _request_line(security=security, exchange=exchange)
    rows = [
        ("场景 ID", scenario_id),
        ("预期语义", _expected_outcome_label(expected_outcome)),
        ("HTTP 状态", exchange.status_code if exchange and exchange.status_code is not None else None),
        ("请求耗时", _fmt_elapsed(exchange.elapsed_ms if exchange else None)),
        ("请求大小", _fmt_bytes(exchange.request_bytes) if exchange else None),
        ("响应大小", _fmt_bytes(exchange.response_bytes) if exchange else None),
        ("开始时间", _fmt_ts(exchange.started_at if exchange else None)),
        ("结束时间", _fmt_ts(exchange.finished_at if exchange else None)),
        ("使用变量", _display(variables_before) if variables_before else ""),
        ("提取变量", _display(extracted) if extracted else ""),
        ("步骤后变量", _display(variables_after) if variables_after else ""),
        ("结论原因", _status_reason_label(status_reason)),
    ]
    if security is not None and not security.allowed:
        rows.insert(2, ("执行前校验", _security_label(security)))
    body = [
        f'<div class="tl-thought"><b>目标</b>{_e(intent or title)}</div>',
        f'<div class="tl-action"><b>请求</b>{_e(request_line or "未发起 HTTP 请求")}</div>',
        f'<dl class="kv compact">{_kv_rows(rows)}</dl>',
    ]
    body.append(_exchange_visible_details(exchange=exchange))
    if assertions:
        body.append(_assertions_html(assertions))
    if error:
        body.append(f'<div class="diag failed"><b>失败说明</b>{_e(error)}</div>')
    if repair_suggestion:
        body.append(f'<div class="diag warn"><b>修复建议</b>{_e(repair_suggestion)}</div>')
    return _timeline_card(
        step_no=step_no,
        tag=tag,
        tag_class=tag_class,
        title=title,
        elapsed_ms=exchange.elapsed_ms if exchange else None,
        body="".join(body),
    )


def _timeline_card(
    *,
    step_no: int,
    tag: str,
    tag_class: str,
    title: str,
    elapsed_ms: int | None,
    body: str,
) -> str:
    elapsed = (
        f'<span class="tl-elapsed">耗时 {_e(_fmt_elapsed(elapsed_ms))}</span>'
        if elapsed_ms is not None
        else ""
    )
    return f"""
<div class="tl-row">
  <div class="tl-body {tag_class}">
    <div class="tl-hd">
      <span class="tl-idx">步骤 #{step_no}</span>
      <span class="tl-name">{_e(title)}</span>
      <span class="tl-tag {tag_class}">{_e(tag)}</span>
      {elapsed}
    </div>
    {body}
  </div>
</div>"""


def _diagnosis_section(
    *,
    status: str,
    error: str,
    repair_suggestion: str,
) -> str:
    if status == "success" and not error and not repair_suggestion:
        return ""
    rows = [
        ("失败说明", error),
        ("修复建议", repair_suggestion),
    ]
    return _section("失败与修复建议", _kv_block(rows) or '<div class="diag warn">暂无额外诊断信息。</div>')


def _raw_evidence_section(
    *,
    plan: AIAPIExecutionPlan | None,
    security: SecurityValidationResult | None,
    exchange: HTTPExchange | None,
    assertions: tuple[AssertionResult, ...],
    scenario_results: tuple[ScenarioExecutionResult, ...],
) -> str:
    blocks = [
        _details_json("模型编译计划原文", _plan_to_report_dict(plan)),
        _details_json("执行前校验原文", _security_to_dict(security)),
        _details_json("实际请求与响应原文", _exchange_to_dict(exchange)),
        _details_json("断言结果原文", [_assertion_to_dict(item) for item in assertions]),
        _details_json("场景执行结果原文", [_scenario_result_to_dict(item) for item in scenario_results]),
    ]
    return _section("调试原文", "".join(blocks))


def _assertions_html(assertions: tuple[AssertionResult, ...]) -> str:
    rows = []
    for item in assertions:
        state_class = "ok" if item.passed else "bad"
        state_label = "通过" if item.passed else "失败"
        detail = _kv_rows(
            [
                ("类型", _assertion_type_label(item.type)),
                ("检查点", item.path or item.name),
                ("预期", _display(item.expected)),
                ("实际", _display(item.actual)),
            ]
        )
        rows.append(
            f"""
<div class="assertion {state_class}">
  <span class="assert-state">{state_label}</span>
  <div>
    <div class="assert-msg">{_e(item.message)}</div>
    <dl class="assert-detail">{detail}</dl>
  </div>
</div>"""
        )
    return f'<div class="assertions"><div class="mini-title">断言结果</div>{"".join(rows)}</div>'


def _exchange_visible_details(*, exchange: HTTPExchange | None) -> str:
    if exchange is None:
        return ""
    panels = [
        ("请求参数", _display(_query_params(exchange.url) or "无")),
        ("请求 Body", _display(exchange.request_body if exchange.request_body is not None else "无")),
        ("响应 Body", exchange.response_text or "无"),
    ]
    panel_html = "".join(
        f"""
<div class="io-panel">
  <div class="mini-title">{_e(title)}</div>
  <pre>{_e(value)}</pre>
</div>"""
        for title, value in panels
    )
    header_details = _details_json(
        "查看请求/响应 Header",
        {
            "请求 Header": exchange.request_headers or {},
            "响应 Header": exchange.response_headers or {},
        },
    )
    return f'<div class="io-grid">{panel_html}</div>{header_details}'


def _plan_to_report_dict(plan: AIAPIExecutionPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    payload = plan.model_dump(mode="json")
    payload.pop("notes", None)
    return payload


def _section(title: str, body: str) -> str:
    return f"""
<div class="section">
  <div class="section-title">{_e(title)}</div>
  <div class="section-body">{body}</div>
</div>"""


def _kv_rows(rows: list[tuple[str, Any]]) -> str:
    html_rows = []
    for key, value in rows:
        if value in (None, ""):
            continue
        html_rows.append(f"<dt>{_e(key)}</dt><dd>{_e(_display(value))}</dd>")
    return "".join(html_rows)


def _kv_block(rows: list[tuple[str, Any]]) -> str:
    body = _kv_rows(rows)
    return f'<dl class="kv">{body}</dl>' if body else ""


def _details_json(title: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=str) if value is not None else "null"
    return f"""
<details class="raw">
  <summary>{_e(title)}</summary>
  <pre>{_e(payload)}</pre>
</details>"""


def _collect_exchanges(
    exchange: HTTPExchange | None,
    scenario_results: tuple[ScenarioExecutionResult, ...],
) -> list[HTTPExchange]:
    items: list[HTTPExchange] = []
    if exchange is not None:
        items.append(exchange)
    items.extend(item.exchange for item in scenario_results if item.exchange is not None)
    items.extend(
        step.exchange
        for item in scenario_results
        for step in item.step_results
        if step.exchange is not None
    )
    return items


def _interface_key(exchange: HTTPExchange) -> str:
    parsed = urlparse(exchange.url)
    if parsed.scheme and parsed.netloc:
        return f"{exchange.method.upper()} {parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    return f"{exchange.method.upper()} {exchange.url}"


def _plan_detail_html(plan: AIAPIExecutionPlan) -> str:
    if not plan.executable:
        return ""
    if plan.scenarios:
        items = "".join(
            _scenario_plan_item(index, scenario)
            for index, scenario in enumerate(plan.scenarios, start=1)
        )
    elif plan.request is not None:
        items = _request_plan_item(1, "单次请求", plan.request, plan.assertions)
    else:
        items = ""
    if not items:
        return ""
    return f'<div class="plan-list">{items}</div>'


def _scenario_plan_item(index: int, scenario: Any) -> str:
    title = scenario.name or scenario.id or f"场景 {index}"
    meta = _expected_outcome_label(scenario.expected_outcome)
    if getattr(scenario, "steps", None):
        requests = "".join(
            _request_plan_item(step_index, step.name or step.id, step.request, step.assertions, compact=True)
            for step_index, step in enumerate(scenario.steps, start=1)
        )
    elif scenario.request is not None:
        requests = _request_plan_item(1, "请求", scenario.request, scenario.assertions, compact=True)
    else:
        requests = '<div class="plan-request">未编排请求</div>'
    return f"""
<div class="plan-item">
  <div class="plan-title">{index}. {_e(title)} <span>{_e(meta)}</span></div>
  <div class="plan-intent">{_e(scenario.intent or "模型未补充场景说明")}</div>
  <div class="plan-requests">{requests}</div>
</div>"""


def _request_plan_item(
    index: int,
    title: str,
    request: APIRequestPlan,
    assertions: list[Any],
    *,
    compact: bool = False,
) -> str:
    value = request.model_dump(mode="json")
    line = _request_plan_brief(value)
    assertion_text = _assertion_plan_brief(assertions)
    prefix = f"{index}. " if compact else ""
    assertion_html = ""
    if assertion_text:
        assertion_html = f'<div class="plan-assertions">断言：{_e(assertion_text)}</div>'
    return f"""
<div class="plan-request">
  <div class="plan-request-line">{_e(prefix + title)}：{_e(line)}</div>
  {assertion_html}
</div>"""


def _assertion_plan_brief(assertions: list[Any]) -> str:
    parts = []
    for item in assertions:
        if item.type == "status_code":
            parts.append(f"状态码={_display(item.expected)}")
        elif item.type == "json_path_exists":
            parts.append(f"{item.path} 存在")
        elif item.type == "json_path_equals":
            parts.append(f"{item.path} = {_display(item.expected)}")
        elif item.type == "body_contains":
            parts.append(f"响应包含 {_display(item.contains or item.expected)}")
        elif item.type == "header_exists":
            parts.append(f"Header {item.name} 存在")
        else:
            parts.append(_assertion_type_label(item.type))
    return "；".join(parts)


def _scenario_plan_line(item: Any) -> str:
    if getattr(item, "steps", None):
        return (
            f"{item.id or '-'}：{item.name or '-'} / "
            f"{_expected_outcome_label(item.expected_outcome)} / "
            f"链式 {len(item.steps)} 步"
        )
    return (
        f"{item.id or '-'}：{item.name or '-'} / "
        f"{_expected_outcome_label(item.expected_outcome)} / "
        f"{_request_plan_brief(item.request.model_dump(mode='json') if item.request else {})}"
    )


def _request_plan_brief(value: dict[str, Any]) -> str:
    method = value.get("method") or "HTTP"
    target = value.get("url") or value.get("path") or "未指定 URL"
    query = value.get("query")
    headers = value.get("headers")
    body = value.get("body")
    parts = [f"{method} {target}"]
    if query not in (None, "", {}):
        parts.append(f"query {_display(query)}")
    if headers not in (None, "", {}):
        parts.append(f"headers {_display(headers)}")
    if body not in (None, "", {}):
        parts.append(f"body {_display(body)}")
    return "，".join(parts)


def _request_line(
    *,
    security: SecurityValidationResult | None,
    exchange: HTTPExchange | None,
) -> str:
    if exchange is not None:
        return f"{exchange.method} {exchange.url}"
    if security is not None and (security.method or security.url):
        return f"{security.method or 'HTTP'} {security.url or '未解析 URL'}"
    return ""


def _security_label(security: SecurityValidationResult | None) -> str:
    if security is None:
        return "未执行"
    if security.allowed:
        return "通过"
    return f"未通过：{security.reason}"


def _status_label(status: str) -> str:
    return {"success": "通过", "failed": "失败"}.get(status, status or "未知")


def _status_badge(status: str, *, size: str = "") -> str:
    cls = "badge-success" if status == "success" else "badge-failed"
    extra = f" {size}" if size else ""
    return f'<span class="badge {cls}{extra}">{_e(_status_label(status))}</span>'


_STATUS_REASON_LABELS = {
    "assertion_passed": "断言通过",
    "compile_failed": "模型判断信息不足，无法执行",
    "plan_invalid": "模型计划不合法",
    "model_unavailable": "模型不可用",
    "model_failed": "模型输出异常",
    "security_blocked": "执行前校验未通过",
    "variable_missing": "变量缺失",
    "extract_failed": "变量提取失败",
    "request_error": "请求异常",
    "http_error": "HTTP 状态不符合预期",
    "assertion_failed": "响应断言失败",
    "case_not_found": "用例不存在",
    "needs_human": "需人工判断",
}


def _status_reason_label(value: str | None) -> str:
    if not value:
        return "—"
    return _STATUS_REASON_LABELS.get(value, value)


_EXPECTED_OUTCOME_LABELS = {
    "accepted": "合法请求应被接受",
    "rejected": "非法请求应被拒绝",
    "changed": "应产生状态变化",
    "unchanged": "不应改变数据",
}


def _expected_outcome_label(value: str | None) -> str:
    if not value:
        return "—"
    return _EXPECTED_OUTCOME_LABELS.get(value, value)


_ASSERTION_TYPE_LABELS = {
    "status_code": "状态码",
    "json_path_exists": "JSON 路径存在",
    "json_path_equals": "JSON 路径值",
    "body_contains": "响应体包含文本",
    "header_exists": "响应 Header 存在",
}


def _assertion_type_label(value: str | None) -> str:
    if not value:
        return "—"
    return _ASSERTION_TYPE_LABELS.get(value, value)


def _security_to_dict(security: SecurityValidationResult | None) -> dict[str, Any] | None:
    if security is None:
        return None
    return {
        "允许执行": security.allowed,
        "请求方法": security.method,
        "请求地址": security.url,
        "请求头": security.headers,
        "原因": security.reason,
    }


def _exchange_to_dict(exchange: HTTPExchange | None) -> dict[str, Any] | None:
    if exchange is None:
        return None
    return {
        "请求方法": exchange.method,
        "请求地址": exchange.url,
        "请求头": exchange.request_headers,
        "请求体": exchange.request_body,
        "请求大小": _fmt_bytes(exchange.request_bytes),
        "响应状态码": exchange.status_code,
        "响应头": exchange.response_headers,
        "响应正文": exchange.response_text,
        "响应大小": _fmt_bytes(exchange.response_bytes),
        "请求耗时毫秒": exchange.elapsed_ms,
        "开始时间": _fmt_ts(exchange.started_at),
        "结束时间": _fmt_ts(exchange.finished_at),
        "是否截断": exchange.truncated,
        "错误": exchange.error,
    }


def _assertion_to_dict(result: AssertionResult) -> dict[str, Any]:
    return {
        "断言类型": _assertion_type_label(result.type),
        "是否通过": result.passed,
        "说明": result.message,
        "预期值": result.expected,
        "实际值": result.actual,
        "JSON 路径": result.path,
        "Header 名称": result.name,
    }


def _scenario_result_to_dict(result: ScenarioExecutionResult) -> dict[str, Any]:
    return {
        "场景 ID": result.scenario_id,
        "名称": result.name,
        "意图": result.intent,
        "预期语义": _expected_outcome_label(result.expected_outcome),
        "是否必需": result.required,
        "是否通过": result.passed,
        "结论原因": _status_reason_label(result.status_reason),
        "执行前校验": _security_to_dict(result.security),
        "请求与响应": _exchange_to_dict(result.exchange),
        "断言": [_assertion_to_dict(item) for item in result.assertions],
        "步骤结果": [_step_result_to_dict(item) for item in result.step_results],
        "最终变量": result.variables,
        "错误": result.error,
        "修复建议": result.repair_suggestion,
    }


def _step_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "步骤 ID": result.step_id,
        "名称": result.name,
        "意图": result.intent,
        "是否通过": result.passed,
        "结论原因": _status_reason_label(result.status_reason),
        "执行前校验": _security_to_dict(result.security),
        "请求与响应": _exchange_to_dict(result.exchange),
        "断言": [_assertion_to_dict(item) for item in result.assertions],
        "提取变量": result.extracted,
        "步骤前变量": result.variables_before,
        "步骤后变量": result.variables_after,
        "错误": result.error,
        "修复建议": result.repair_suggestion,
    }


def _display(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value)


def _loaded_label(value: str) -> str:
    return "已加载" if str(value or "").strip() else "未加载"


def _query_params(url: str) -> dict[str, Any]:
    pairs = parse_qsl(urlparse(url).query, keep_blank_values=True)
    params: dict[str, Any] = {}
    for key, value in pairs:
        if key in params:
            existing = params[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                params[key] = [existing, value]
        else:
            params[key] = value
    return params


def _fmt_ts(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return str(value)


def _fmt_elapsed(ms: int | None) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remain = divmod(int(seconds), 60)
    return f"{minutes}m{remain}s"


def _fmt_bytes(size: int | None) -> str:
    if size is None:
        return "—"
    if size < 1024:
        return f"{size} B"
    kb = size / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    return f"{mb / 1024:.1f} GB"


def _e(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif;
  background: #0f172a;
  color: #e2e8f0;
  line-height: 1.55;
}
code { font-family: ui-monospace, Menlo, monospace; color: #a5f3fc; }
.single-container { max-width: 1200px; margin: 0 auto; padding: 24px; }
.hd {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 18px 22px;
  margin-bottom: 12px;
}
.hd-title {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.hd-title h1 { font-size: 20px; color: #f8fafc; font-weight: 600; }
.hd-title .sub { color: #64748b; font-size: 13px; font-weight: 400; }
.hd-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #94a3b8;
  align-items: center;
}
.chip {
  background: #334155;
  padding: 4px 12px;
  border-radius: 6px;
  font-family: ui-monospace, Menlo, monospace;
  color: #cbd5e1;
}
.chip b {
  color: #94a3b8;
  font-weight: 500;
  margin-right: 6px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif;
}
.badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0;
  white-space: nowrap;
}
.badge.lg { font-size: 12px; padding: 4px 14px; }
.badge-success { background: #064e3b; color: #6ee7b7; border: 1px solid #047857; }
.badge-failed { background: #7f1d1d; color: #fca5a5; border: 1px solid #b91c1c; }
.reason { margin: 12px 0 16px; border-radius: 8px; padding: 10px 14px; font-size: 13px; }
.reason.success { background: #052e23; color: #6ee7b7; border: 1px solid #047857; }
.reason.failed { background: #450a0a; color: #fca5a5; border: 1px solid #b91c1c; }
.section { margin-top: 18px; }
.section-title {
  font-size: 13px;
  color: #cbd5e1;
  font-weight: 600;
  letter-spacing: 0;
  margin-bottom: 8px;
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.section-body {
  background: #0a1120;
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 12px 14px;
}
.section-body > dl, .kv {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 8px 14px;
  font-size: 13px;
}
.kv.compact { margin-top: 10px; }
dt { color: #94a3b8; font-weight: 600; }
dd { color: #e2e8f0; white-space: pre-wrap; word-break: break-word; }
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: 8px;
}
.summary-card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 12px;
  min-height: 64px;
}
.summary-card.ok { border-color: #047857; }
.summary-card.bad { border-color: #b91c1c; }
.summary-label { color: #94a3b8; font-size: 12px; margin-bottom: 4px; }
.summary-value { color: #f8fafc; font-size: 16px; font-weight: 700; word-break: break-word; }
.plan-list {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}
.plan-item, .plan-request {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 12px;
}
.plan-title { color: #f8fafc; font-size: 13px; font-weight: 700; }
.plan-title span { color: #a5f3fc; font-size: 12px; font-weight: 600; margin-left: 8px; }
.plan-intent { color: #cbd5e1; font-size: 13px; margin-top: 6px; white-space: pre-wrap; }
.plan-requests { display: grid; gap: 8px; margin-top: 8px; }
.plan-request-line { color: #e2e8f0; font-size: 12px; white-space: pre-wrap; word-break: break-word; }
.plan-assertions { color: #94a3b8; font-size: 12px; margin-top: 6px; white-space: pre-wrap; }
.timeline { background: #0a1120; border-radius: 10px; padding: 2px 0; }
.nested-timeline {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}
.nested-timeline .tl-row { padding: 0; }
.tl-row { padding: 6px 0; }
.tl-row + .tl-row { border-top: 1px solid #111c30; }
.tl-body {
  background: #1e293b;
  border: 1px solid #334155;
  border-left: 3px solid #60a5fa;
  border-radius: 8px;
  padding: 12px 14px;
  margin: 4px 0;
}
.tl-body.t-finished { border-left-color: #34d399; }
.tl-body.t-failed { border-left-color: #f87171; }
.tl-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #cbd5e1;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.tl-idx { color: #60a5fa; font-weight: 700; font-family: ui-monospace, Menlo, monospace; }
.tl-name { color: #f8fafc; font-weight: 600; }
.tl-tag { background: #1e3a8a; color: #bfdbfe; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.tl-tag.t-finished { background: #064e3b; color: #6ee7b7; }
.tl-tag.t-failed { background: #7f1d1d; color: #fca5a5; }
.tl-elapsed { color: #fbbf24; font-family: ui-monospace, Menlo, monospace; font-size: 11px; }
.tl-thought {
  background: #0f172a;
  border-left: 3px solid #fbbf24;
  border-radius: 4px;
  padding: 8px 12px;
  font-size: 13px;
  color: #e2e8f0;
  white-space: pre-wrap;
  word-break: break-word;
  margin-bottom: 8px;
}
.tl-thought b, .tl-action b, .diag b { color: #fbbf24; margin-right: 6px; }
.tl-action {
  background: #0f172a;
  border-left: 3px solid #60a5fa;
  border-radius: 4px;
  padding: 8px 12px;
  font-size: 12px;
  color: #a5f3fc;
  font-family: ui-monospace, Menlo, monospace;
  white-space: pre-wrap;
  word-break: break-all;
  margin-bottom: 10px;
}
.io-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 8px;
  margin-top: 12px;
}
.io-panel {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  overflow: hidden;
}
.io-panel .mini-title { padding: 8px 10px; border-bottom: 1px solid #1e293b; }
.io-panel pre { border-top: 0; min-height: 72px; }
.assertions { margin-top: 12px; display: grid; gap: 8px; }
.mini-title { color: #94a3b8; font-size: 12px; font-weight: 600; }
.assertion {
  display: grid;
  grid-template-columns: 54px 1fr;
  gap: 10px;
  align-items: start;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 8px 10px;
}
.assertion.ok { border-color: #047857; }
.assertion.bad { border-color: #b91c1c; }
.assert-state {
  font-size: 11px;
  font-weight: 700;
  border-radius: 4px;
  padding: 2px 6px;
  text-align: center;
  background: #334155;
  color: #cbd5e1;
}
.assertion.ok .assert-state { background: #064e3b; color: #6ee7b7; }
.assertion.bad .assert-state { background: #7f1d1d; color: #fca5a5; }
.assert-msg { color: #e2e8f0; font-size: 13px; }
.assert-detail {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 4px 8px;
  margin-top: 4px;
  font-size: 12px;
}
.assert-detail dt { color: #64748b; }
.assert-detail dd { color: #cbd5e1; }
.diag {
  margin-top: 10px;
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
}
.diag.success { background: #052e23; color: #6ee7b7; border: 1px solid #047857; }
.diag.warn { background: #422006; color: #fcd34d; border: 1px solid #b45309; }
.diag.failed { background: #450a0a; color: #fca5a5; border: 1px solid #b91c1c; }
details.raw {
  margin-top: 10px;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 8px;
  overflow: hidden;
}
details.raw summary {
  cursor: pointer;
  padding: 8px 12px;
  color: #cbd5e1;
  font-size: 12px;
  font-weight: 600;
}
.raw-content { padding: 12px; border-top: 1px solid #1e293b; }
pre {
  background: #020617;
  color: #cbd5e1;
  padding: 12px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.5;
  border-top: 1px solid #1e293b;
}
.foot { text-align: center; color: #475569; font-size: 11px; margin-top: 28px; padding-bottom: 16px; }
@media (max-width: 720px) {
  .single-container { padding: 14px; }
  .section-body > dl, .kv, .assert-detail { grid-template-columns: 1fr; }
  .summary-grid, .io-grid { grid-template-columns: 1fr; }
  .assertion { grid-template-columns: 1fr; }
}
"""
