from __future__ import annotations

import json
import re
from typing import Protocol

from app.core.settings import get_settings
from app.services.ai_api.schemas import AIAPICaseInput, AIAPIExecutionPlan


class PlanCompiler(Protocol):
    async def compile(self, case_input: AIAPICaseInput) -> AIAPIExecutionPlan: ...


class StaticPlanCompiler:
    def __init__(self, plan: AIAPIExecutionPlan) -> None:
        self.plan = plan

    async def compile(self, case_input: AIAPICaseInput) -> AIAPIExecutionPlan:
        return self.plan


class LLMPlanCompiler:
    """Compile a natural-language API case into a strict HTTP execution plan."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        settings = get_settings()
        self.model = model or settings.llm_model
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.max_output_tokens = int(getattr(settings, "llm_max_tokens", 16000) or 16000)

    async def compile(self, case_input: AIAPICaseInput) -> AIAPIExecutionPlan:
        if not self.api_key or not self.model:
            return AIAPIExecutionPlan(
                executable=False,
                failure_type="model_unavailable",
                reason="未配置可用于 AI API 编译的模型。",
                repair_suggestion="请配置 CASE_FLOW_LLM_API_KEY / CASE_FLOW_LLM_MODEL，或先补充可执行计划。",
            )
        try:
            from openai import AsyncOpenAI
        except Exception as exc:
            return AIAPIExecutionPlan(
                executable=False,
                failure_type="model_unavailable",
                reason=f"模型 SDK 不可用：{exc}",
                repair_suggestion="请检查后端依赖是否安装 openai SDK。",
            )

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url or None)
        prompt = _build_compile_prompt(case_input)
        response = await client.responses.create(
            model=self.model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            temperature=0,
            max_output_tokens=self.max_output_tokens,
        )
        text = getattr(response, "output_text", "") or str(response)
        payload = json.loads(_extract_json(text))
        return AIAPIExecutionPlan.model_validate(payload)


def _build_compile_prompt(case_input: AIAPICaseInput) -> str:
    return (
        "你是 Case Flow 的 AI API 执行计划编译器。你只输出 JSON，不输出 Markdown。\n"
        "任务：根据用户本次任务，参考 functionMap/API 上下文，判断能否构造明确的 HTTP 请求计划。\n"
        "优先级规则必须遵守：用户本次任务 > 明确预期结果 > functionMap/API 上下文 > 全局默认配置。\n"
        "functionMap/API 上下文是只读执行参考，只能补齐 base_url、path、method、鉴权、字段、参数格式、错误响应格式和业务术语；"
        "不得改变、扩展、替换用户本次任务的目标和测试范围。\n"
        "functionMap 可能很长，只抽取与本次任务直接相关的接口和字段；不要把无关接口编排成 scenario，也不要在输出里复述 functionMap。\n"
        "如果用户任务与 functionMap/API 上下文冲突，以用户任务为准；仍无法构造明确请求或断言时，输出 executable=false。\n"
        "如果缺少 base_url、path、method、必要参数、鉴权或可验证预期，输出 executable=false；不要追问用户。\n"
        "如果信息充足且只是单次请求，输出 executable=true，并提供 request 与 assertions。\n"
        "如果用户描述的是 CRUD、边界值、异常入参、等价类等测试意图，输出 scenarios 数组；"
        "每个 scenario 都要有 expected_outcome，并提供 request+assertions 或 steps。\n"
        "只有用户本次任务明确表达 CRUD、边界值、异常入参、等价类等意图时才展开这些 scenario；"
        "不能因为 functionMap 中存在这些接口或规则就主动扩展测试范围。\n"
        "当后续请求需要标识、token 或某个数据：如果用户明确提供，就直接使用；"
        "如果用户未提供但前序步骤会产生该数据，就用 steps[].extract 从前序响应提取，"
        "后续请求用 {{变量名}} 引用；如果两者都没有，不要猜测，输出 executable=false。\n"
        "expected_outcome 只能是 accepted/rejected/changed/unchanged。"
        "rejected 表示非法请求应该被接口拒绝，HTTP 4xx 或业务错误码可能代表该 scenario 通过。\n"
        "request.method 只能是 GET/POST/PUT/PATCH/DELETE；body_type 为 none/json/form/raw。\n"
        "assertions 支持 status_code/json_path_exists/json_path_equals/body_contains/header_exists；"
        "extract 的值使用 JSONPath，例如 {\"userId\":\"$.data.id\"}。\n"
        "输出 schema 示例：\n"
        '{"executable":true,"reason":"...","request":{"method":"POST","url":"https://example.com",'
        '"headers":{},"query":{},"body_type":"json","body":{},"timeout_seconds":15},'
        '"assertions":[{"type":"status_code","expected":200}],"notes":[]}\n'
        '{"executable":true,"reason":"...","scenarios":[{"id":"limit_gt_1000","name":"超过最大值应被拒绝",'
        '"intent":"验证 limit=1001 被接口拦截","expected_outcome":"rejected",'
        '"request":{"method":"POST","url":"https://example.com","headers":{},"query":{},'
        '"body_type":"json","body":{"limit":1001},"timeout_seconds":15},'
        '"assertions":[{"type":"status_code","expected":400}],"required":true}],"notes":[]}\n'
        '{"executable":true,"reason":"...","scenarios":[{"id":"user_crud","name":"用户增删改查",'
        '"intent":"创建后使用返回 id 查询、修改、删除","expected_outcome":"changed","steps":['
        '{"id":"create","request":{"method":"POST","path":"/users","headers":{},'
        '"query":{},"body_type":"json","body":{"name":"Alice"},"timeout_seconds":15},'
        '"assertions":[{"type":"status_code","expected":201}],"extract":{"userId":"$.data.id"}},'
        '{"id":"query","request":{"method":"GET","path":"/users/{{userId}}","headers":{},'
        '"query":{},"body_type":"none","body":null,"timeout_seconds":15},'
        '"assertions":[{"type":"status_code","expected":200}]}],"required":true}],"notes":[]}\n'
        '{"executable":false,"failure_type":"compile_failed","reason":"...",'
        '"repair_suggestion":"..."}\n\n'
        f"输入 case：\n{case_input.to_prompt_text()}"
    )


def _extract_json(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return cleaned[start : end + 1]
    return cleaned
