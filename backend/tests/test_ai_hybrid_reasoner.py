from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.ai_hybrid import reasoner
from app.services.ai_hybrid.schemas import HybridInput


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
    assert user_payload["history"] == []


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
