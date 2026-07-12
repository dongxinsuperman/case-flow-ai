from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

HTTPMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
BodyType = Literal["none", "json", "form", "raw"]
ExpectedOutcome = Literal["accepted", "rejected", "changed", "unchanged"]
AssertionType = Literal[
    "status_code",
    "json_path_exists",
    "json_path_equals",
    "body_contains",
    "header_exists",
]


class AIAPICaseInput(BaseModel):
    title: str = ""
    preconditions: str = ""
    steps_text: str = ""
    expected_result: str = ""
    function_map_context: str = ""

    def to_prompt_text(self) -> str:
        function_map_context = self.function_map_context.strip()
        function_map_section = (
            "\n".join(
                [
                    "【functionMap/API 上下文（只读执行参考）】",
                    "用途：只能用于补齐接口 base_url、path、method、鉴权、字段名、参数格式、错误响应格式和业务术语。",
                    "优先级：低于用户本次任务；不得改变、扩展或替换用户本次任务的目标和测试范围。",
                    "冲突处理：如果它与用户本次任务冲突，以用户本次任务为准；仍无法构造明确请求时，输出 executable=false。",
                    "长度处理：上下文可能很长，只提取与本次任务直接相关的内容，不要复述或编排无关接口。",
                    f"内容：\n{function_map_context}",
                ]
            )
            if function_map_context
            else "【functionMap/API 上下文（只读执行参考）】\n未加载。"
        )
        return "\n\n".join(
            [
                "【用户本次任务】",
                f"测试标题：{self.title}",
                f"前置条件：{self.preconditions}",
                f"操作步骤：{self.steps_text}",
                f"预期结果：{self.expected_result}",
                function_map_section,
            ]
        ).strip()


class APIRequestPlan(BaseModel):
    method: HTTPMethod
    url: str | None = None
    path: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body_type: BodyType = "none"
    body: Any = None
    timeout_seconds: int = Field(default=15, ge=1, le=120)

    @model_validator(mode="after")
    def require_url_or_path(self) -> APIRequestPlan:
        if not (self.url or self.path):
            raise ValueError("request.url 或 request.path 至少需要一个")
        if self.body_type == "none":
            self.body = None
        return self


class APIAssertion(BaseModel):
    type: AssertionType
    expected: Any = None
    path: str | None = None
    name: str | None = None
    contains: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> APIAssertion:
        if self.type == "status_code" and self.expected is None:
            raise ValueError("status_code 断言必须提供 expected")
        if self.type in {"json_path_exists", "json_path_equals"} and not self.path:
            raise ValueError(f"{self.type} 断言必须提供 path")
        if self.type == "json_path_equals" and self.expected is None:
            raise ValueError("json_path_equals 断言必须提供 expected")
        if self.type == "body_contains" and not (self.contains or self.expected):
            raise ValueError("body_contains 断言必须提供 contains 或 expected")
        if self.type == "header_exists" and not self.name:
            raise ValueError("header_exists 断言必须提供 name")
        return self


class APIExecutionStep(BaseModel):
    id: str
    name: str = ""
    intent: str = ""
    request: APIRequestPlan
    assertions: list[APIAssertion] = Field(default_factory=list)
    extract: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_assertions_or_extract(self) -> APIExecutionStep:
        if not self.assertions and not self.extract:
            raise ValueError("step 必须提供 assertions 或 extract")
        return self


class APIExecutionScenario(BaseModel):
    id: str
    name: str = ""
    intent: str = ""
    expected_outcome: ExpectedOutcome
    request: APIRequestPlan | None = None
    assertions: list[APIAssertion] = Field(default_factory=list)
    steps: list[APIExecutionStep] = Field(default_factory=list)
    required: bool = True

    @model_validator(mode="after")
    def require_request_or_steps(self) -> APIExecutionScenario:
        if self.steps:
            return self
        if self.request is None:
            raise ValueError("scenario 必须提供 request 或 steps")
        if not self.assertions:
            raise ValueError("单请求 scenario 必须提供至少一条 assertion")
        return self


class AIAPIExecutionPlan(BaseModel):
    executable: bool
    reason: str = ""
    request: APIRequestPlan | None = None
    assertions: list[APIAssertion] = Field(default_factory=list)
    scenarios: list[APIExecutionScenario] = Field(default_factory=list)
    failure_type: str | None = None
    repair_suggestion: str = ""
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_execution_shape(self) -> AIAPIExecutionPlan:
        if self.executable:
            if self.scenarios:
                return self
            if self.request is None:
                raise ValueError("executable=true 时必须提供 request 或 scenarios")
            if not self.assertions:
                raise ValueError("executable=true 且使用单请求计划时必须提供至少一条 assertion")
        return self


@dataclass(frozen=True)
class AIAPISecurityConfig:
    allowed_hosts: tuple[str, ...] = ()
    allowed_base_urls: tuple[str, ...] = ()
    default_base_url: str = ""
    default_headers: dict[str, str] = field(default_factory=dict)
    allowed_methods: frozenset[str] = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
    allow_private_networks: bool = True
    max_timeout_seconds: int = 20
    # 0 means no truncation. AI API is an internal executor; reports should preserve
    # the real request/response unless a deployment explicitly caps it later.
    max_response_bytes: int = 0
    follow_redirects: bool = False


@dataclass(frozen=True)
class SecurityValidationResult:
    allowed: bool
    url: str = ""
    method: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class HTTPExchange:
    method: str
    url: str
    request_headers: dict[str, str]
    request_body: Any
    status_code: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_text: str = ""
    elapsed_ms: int = 0
    request_bytes: int = 0
    response_bytes: int = 0
    truncated: bool = False
    error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class AssertionResult:
    type: str
    passed: bool
    message: str
    expected: Any = None
    actual: Any = None
    path: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class StepExecutionResult:
    step_id: str
    name: str
    intent: str
    passed: bool
    status_reason: str
    security: SecurityValidationResult | None = None
    exchange: HTTPExchange | None = None
    assertions: tuple[AssertionResult, ...] = ()
    extracted: dict[str, Any] = field(default_factory=dict)
    variables_before: dict[str, Any] = field(default_factory=dict)
    variables_after: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    repair_suggestion: str = ""


@dataclass(frozen=True)
class ScenarioExecutionResult:
    scenario_id: str
    name: str
    intent: str
    expected_outcome: ExpectedOutcome
    required: bool
    passed: bool
    status_reason: str
    security: SecurityValidationResult | None = None
    exchange: HTTPExchange | None = None
    assertions: tuple[AssertionResult, ...] = ()
    step_results: tuple[StepExecutionResult, ...] = ()
    variables: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    repair_suggestion: str = ""


@dataclass(frozen=True)
class AIAPIExecutionResult:
    status: Literal["success", "failed"]
    status_reason: str
    report_html: str
    plan: AIAPIExecutionPlan | None = None
    security: SecurityValidationResult | None = None
    exchange: HTTPExchange | None = None
    assertions: tuple[AssertionResult, ...] = ()
    scenario_results: tuple[ScenarioExecutionResult, ...] = ()
    error: str = ""
    repair_suggestion: str = ""
