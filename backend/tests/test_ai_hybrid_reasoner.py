from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.ai_hybrid import reasoner
from app.services.ai_hybrid.prompts import ORCHESTRATOR_SYSTEM_PROMPT, TOOL_SPECS
from app.services.ai_hybrid.schemas import HybridInput, HybridToolResult


@pytest.mark.asyncio
async def test_react_step_parses_json_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    create_calls: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            create_calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"thought":"证据足够","action":"finish","verdict":"success",'
                                '"final":{"attribution":"已通过","evidence":["report"],"suggestions":[]}}'
                            )
                        )
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(
        reasoner,
        "llm_client",
        lambda _settings: (client, SimpleNamespace(llm_model="fake-model")),
    )

    decision = await reasoner.react_step(
        inp=HybridInput(title="检查订单"),
        history=[],
        settings=SimpleNamespace(),
    )

    assert decision is not None
    assert decision.action == "finish"
    assert decision.verdict == "success"
    assert create_calls[0]["response_format"] == {"type": "json_object"}
    assert create_calls[0]["max_tokens"] == reasoner._MAX_COMPLETION_TOKENS
    messages = create_calls[0]["messages"]
    assert "json" in (messages[0]["content"] + messages[1]["content"])
    user_payload = json.loads(create_calls[0]["messages"][1]["content"])
    tool_input_by_tool = user_payload["decision_schema"]["tool_input_by_tool"]
    assert "cli" not in tool_input_by_tool
    assert "report_url" in tool_input_by_tool["report_reader"]["properties"]
    assert "function_map" in tool_input_by_tool
    assert "device_alias" in tool_input_by_tool["ai_phone"]["properties"]
    assert "device_alias" not in tool_input_by_tool["ai_web"]["properties"]
    assert user_payload["history"] == []


@pytest.mark.asyncio
async def test_react_step_receives_map_catalog_and_snapshot_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    class _Completions:
        def create(self, **kwargs: object) -> object:
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"thought":"ok","action":"finish","verdict":"needs_human","final":{}}'))]
            )

    monkeypatch.setattr(
        reasoner,
        "llm_client",
        lambda _settings: (SimpleNamespace(chat=SimpleNamespace(completions=_Completions())), SimpleNamespace(llm_model="fake")),
    )
    await reasoner.react_step(
        inp=HybridInput(function_maps=[{"asset_id": 7, "title": "账号设备", "targets": ["app"], "content": "body"}]),
        history=[],
        settings=SimpleNamespace(),
        aiphone_devices=[],
        aiphone_devices_error="device_snapshot_error: unavailable",
    )
    payload = json.loads(captured[0]["messages"][1]["content"])
    assert payload["function_map_catalog"] == [{"asset_id": 7, "title": "账号设备", "description": "", "targets": ["app"]}]
    assert payload["aiphone_devices_error"] == "device_snapshot_error: unavailable"


def test_orchestrator_prompt_reads_all_maps_as_target_scoped_references() -> None:
    prompt = ORCHESTRATOR_SYSTEM_PROMPT
    assert "覆盖读取 function_map_catalog 中的全部 Map" in prompt
    assert "Map 永远是参考信息" in prompt
    assert "只含 app / web / api 的 Map 只用于对应端参考" in prompt
    assert "不要让 Map 替你决定流程、调用顺序或最终结论" in prompt
    assert "只准用于选机" not in prompt
    assert "仅用于判断「账号/角色 → 设备」绑定" not in prompt

    function_map_spec = next(spec for spec in TOOL_SPECS if spec["name"] == "function_map")
    description = str(function_map_spec["description"])
    assert "主脑需要覆盖 function_map_catalog 中的全部资产" in description
    assert "每次正文结果都带适用端 targets" in description
    assert "仍按既有 ai_phone.device_alias 设备硬锁规则处理" in description

    # 既有账号→设备硬锁规则必须保持，不因 Map 扩大为全量参考而被弱化。
    assert "只有 Map 或 case 能唯一确定「本步骤账号/角色 → 某 alias」时才填 ai_phone.device_alias" in prompt
    assert "快照失败不能把已有绑定改回默认池" in prompt


def test_function_map_observation_keeps_full_binding_evidence() -> None:
    body = "绑定=" + "设备" * 40000
    observed = reasoner.observe(HybridToolResult(tool="function_map", status="success", raw={"content": body}))
    assert observed["raw"]["content"] == body


@pytest.mark.asyncio
async def test_react_step_preserves_report_reader_outline_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    create_calls: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            create_calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"thought":"继续读尾部","action":"finish","verdict":"failed",'
                                '"final":{"attribution":"失败","evidence":["tail"],"suggestions":[]}}'
                            )
                        )
                    )
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(
        reasoner,
        "llm_client",
        lambda _settings: (client, SimpleNamespace(llm_model="fake-model")),
    )
    toc = [{"i": idx, "kind": "text", "preview": f"步骤 {idx}"} for idx in range(40)]

    decision = await reasoner.react_step(
        inp=HybridInput(title="检查长报告"),
        history=[
            {
                "step": 1,
                "thought": "先读报告目录",
                "action": "call_tool",
                "tool": "report_reader",
                "tool_input": {"mode": "outline"},
                "observation": {"raw": {"observation_mode": "outline", "tail_toc": toc}},
            }
        ],
        settings=SimpleNamespace(),
    )

    assert decision is not None
    user_payload = json.loads(create_calls[0]["messages"][1]["content"])
    tail_toc = user_payload["history"][0]["observation"]["raw"]["tail_toc"]
    assert len(tail_toc) == 40
    assert tail_toc[-1]["preview"] == "步骤 39"


@pytest.mark.asyncio
async def test_react_step_parse_failure_returns_finish_needs_human(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompletions:
        def create(self, **_kwargs: object) -> object:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(
        reasoner,
        "llm_client",
        lambda _settings: (client, SimpleNamespace(llm_model="fake-model")),
    )

    decision = await reasoner.react_step(
        inp=HybridInput(title="检查订单"),
        history=[],
        settings=SimpleNamespace(),
    )

    assert decision is not None
    assert decision.action == "finish"
    assert decision.verdict == "needs_human"
    assert decision.status_reason == "reasoner_parse_failed"


@pytest.mark.asyncio
async def test_react_step_returns_none_when_llm_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reasoner, "llm_client", lambda _settings: (None, SimpleNamespace(llm_model="")))

    decision = await reasoner.react_step(
        inp=HybridInput(title="检查订单"),
        history=[],
        settings=SimpleNamespace(),
    )

    assert decision is None
