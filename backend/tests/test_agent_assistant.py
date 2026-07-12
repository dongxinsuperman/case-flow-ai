from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import agent


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.info: dict[str, object] = {}

    def add(self, item: object) -> None:
        if getattr(item, "id", None) is None:
            try:
                setattr(item, "id", len(self.added) + 1)
            except Exception:
                pass
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


class _FakeHTTPResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"submissionId": "sub-agent-1", "items": []}


class _FakeAsyncClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, json: dict[str, object]) -> _FakeHTTPResponse:
        self.posts.append((url, json))
        return _FakeHTTPResponse()


@pytest.mark.asyncio
async def test_agent_fallback_decides_natural_language_bug_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent, "_llm_client", lambda: (None, SimpleNamespace(llm_model="")))

    decision = await agent._decide_tool(  # type: ignore[arg-type]
        session=None,
        agent_session=SimpleNamespace(),
        message=SimpleNamespace(content="这个登录失败的问题帮我提 bug", attachments={}),
    )

    assert decision.tool_key == "bug_submit_feishu"
    assert decision.args["description"] == "这个登录失败的问题帮我提 bug"


@pytest.mark.asyncio
async def test_agent_fallback_defaults_plain_task_to_aiphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent, "_llm_client", lambda: (None, SimpleNamespace(llm_model="")))

    decision = await agent._decide_tool(  # type: ignore[arg-type]
        session=None,
        agent_session=SimpleNamespace(),
        message=SimpleNamespace(content="打开 App，检查首页推荐入口", attachments={}),
    )

    assert decision.tool_key == "aiphone_dispatch"
    assert decision.args["run_content"] == "打开 App，检查首页推荐入口"


@pytest.mark.asyncio
async def test_agent_fallback_answers_non_task_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent, "_llm_client", lambda: (None, SimpleNamespace(llm_model="")))

    decision = await agent._decide_tool(  # type: ignore[arg-type]
        session=None,
        agent_session=SimpleNamespace(),
        message=SimpleNamespace(content="你能做什么", attachments={}),
    )

    assert decision.tool_key == ""
    assert "直接说" in decision.direct_answer
    assert "接口" in decision.direct_answer


def test_agent_inlines_uploaded_media_before_model_call(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "agent_uploads"
    upload_dir.mkdir()
    (upload_dir / "shot.png").write_bytes(b"img")
    monkeypatch.setattr(
        agent,
        "get_settings",
        lambda: SimpleNamespace(repair_image_dir=str(tmp_path)),
    )

    content = agent._current_user_content(  # type: ignore[arg-type]
        SimpleNamespace(
            content="图里手机号是什么？",
            attachments={
                "images": [
                    {
                        "url": "http://127.0.0.1:8800/media/agent_uploads/shot.png",
                        "mime": "image/png",
                    }
                ]
            },
        )
    )

    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "图里手机号是什么？"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,aW1n"},
    }


def test_agent_does_not_send_plain_image_url_to_model() -> None:
    content = agent._current_user_content(  # type: ignore[arg-type]
        SimpleNamespace(
            content="看一下这张图",
            attachments={"images": [{"url": "http://127.0.0.1:8800/media/missing.png", "mime": "image/png"}]},
        )
    )

    assert content == "看一下这张图"


@pytest.mark.asyncio
async def test_agent_bug_images_merge_pending_and_current_attachments(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "agent_uploads"
    upload_dir.mkdir()
    (upload_dir / "pending.png").write_bytes(b"pending")
    (upload_dir / "current.png").write_bytes(b"current")
    monkeypatch.setattr(
        agent,
        "get_settings",
        lambda: SimpleNamespace(repair_image_dir=str(tmp_path)),
    )

    attachments = agent._merge_bug_attachments(  # type: ignore[attr-defined]
        {"images": [{"url": "/media/agent_uploads/pending.png", "filename": "pending.png"}]},
        {"images": [{"url": "/media/agent_uploads/current.png", "filename": "current.png"}]},
    )
    paths = await agent._agent_bug_image_paths(attachments, {})  # type: ignore[attr-defined]

    assert [label for label, _ in paths] == ["pending.png", "current.png"]
    assert [path.name for _, path in paths] == ["pending.png", "current.png"]


def test_agent_bug_description_does_not_embed_plain_image_urls() -> None:
    description = agent._bug_description(  # type: ignore[attr-defined]
        "把这个问题提 bug",
        {"images": [{"url": "/media/agent_uploads/shot.png", "filename": "shot.png"}]},
        SimpleNamespace(bug_target={}),
        {"key_images": [{"image": "https://report.local/fail.png", "platform": "安卓端"}]},
    )

    assert "/media/agent_uploads/shot.png" not in description
    assert "https://report.local/fail.png" not in description
    assert "用户随消息上传了 1 张截图" in description
    assert "关键截图：1 张" in description


def test_agent_bug_description_prompt_omits_empty_sections() -> None:
    assert "标题已由标题字段承载" in agent.BUG_DESCRIPTION_SYSTEM_PROMPT
    assert "需求已由关联需求字段承载" in agent.BUG_DESCRIPTION_SYSTEM_PROMPT
    assert "只生成信息明确的小节" in agent.BUG_DESCRIPTION_SYSTEM_PROMPT
    assert "没有对应信息的小节直接省略" in agent.BUG_DESCRIPTION_SYSTEM_PROMPT
    assert "所有内容都必须有证据来源" in agent.BUG_DESCRIPTION_SYSTEM_PROMPT


def test_agent_bug_description_omits_structured_title_and_requirement() -> None:
    description = agent._bug_description(  # type: ignore[attr-defined]
        (
            "标题：示例缺陷75\n"
            "https://project.feishu.cn/demo_project/story/detail/10003\n"
            "补充：进入验证页后展示失败"
        ),
        {"images": [{"url": "/media/agent_uploads/shot.png", "filename": "shot.png"}]},
        SimpleNamespace(
            bug_target={
                "url": "https://project.feishu.cn/demo_project/story/detail/10003",
                "raw": {"name": "【测试】示例需求"},
                "work_item_id": 10003,
            }
        ),
        {},
        title="示例缺陷75",
    )

    assert "标题" not in description
    assert "关联需求" not in description
    assert "project.feishu.cn" not in description
    assert "示例缺陷75" not in description
    assert "进入验证页后展示失败" in description
    assert "用户随消息上传了 1 张截图" in description


@pytest.mark.asyncio
async def test_agent_holds_bug_image_when_model_only_asks_requirement_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_decide_tools(_session: object, _agent_session: object, _message: object) -> list[agent._ToolDecision]:
        return [agent._ToolDecision("", {}, "这条 bug 关联哪个飞书需求？把需求链接发我就行。")]

    monkeypatch.setattr(agent, "_decide_tools", fake_decide_tools)

    agent_session = SimpleNamespace(id=1, pending_action={}, bug_target={})
    message = SimpleNamespace(
        id=10,
        content="我是交个 bug，把这几张图放上去",
        attachments={"images": [{"url": "/media/agent_uploads/shot.png", "filename": "shot.png"}]},
    )

    await agent._handle_user_message(_FakeSession(), agent_session, message)  # type: ignore[arg-type]

    pending = agent_session.pending_action
    assert pending["tool_key"] == "bug_submit_feishu"
    assert pending["description"] == "我是交个 bug，把这几张图放上去"
    assert pending["attachments"]["images"][0]["filename"] == "shot.png"


@pytest.mark.asyncio
async def test_agent_submits_pending_bug_image_when_requirement_link_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = SimpleNamespace(project_key="proj", work_item_type="story", work_item_id=123, raw={"name": "需求"})
    submitted: dict[str, object] = {}

    async def fake_bind(agent_session: object, url: str) -> object:
        agent_session.bug_target = {"project_key": "proj", "work_item_type": "story", "work_item_id": 123, "raw": {}}
        submitted["url"] = url
        return target

    async def fake_submit(_session: object, _agent_session: object, _message: object, args: dict, bound_target: object) -> None:
        submitted["args"] = args
        submitted["target"] = bound_target

    monkeypatch.setattr(agent, "_bind_bug_target", fake_bind)
    monkeypatch.setattr(agent, "_submit_bug", fake_submit)

    agent_session = SimpleNamespace(
        id=1,
        bug_target={},
        pending_action={
            "tool_key": "bug_submit_feishu",
            "description": "我是交个 bug，把图放上去",
            "args": {"description": "我是交个 bug，把图放上去"},
            "attachments": {"images": [{"url": "/media/agent_uploads/shot.png", "filename": "shot.png"}]},
        },
    )
    message = SimpleNamespace(
        id=11,
        content="https://project.feishu.cn/proj/story/detail/123",
        attachments={},
    )

    await agent._handle_user_message(_FakeSession(), agent_session, message)  # type: ignore[arg-type]

    args = submitted["args"]
    assert args["requirement_url"] == "https://project.feishu.cn/proj/story/detail/123"  # type: ignore[index]
    assert args["_pending_attachments"]["images"][0]["filename"] == "shot.png"  # type: ignore[index]
    assert submitted["target"] is target
    assert agent_session.pending_action == {}


@pytest.mark.asyncio
async def test_agent_directly_submits_bug_when_link_and_image_are_same_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = SimpleNamespace(project_key="proj", work_item_type="story", work_item_id=123, raw={"name": "需求"})
    submitted: dict[str, object] = {}

    async def fake_bind(_agent_session: object, _url: str) -> object:
        return target

    async def fake_submit(_session: object, _agent_session: object, message: object, args: dict, bound_target: object) -> None:
        submitted["message"] = message
        submitted["args"] = args
        submitted["target"] = bound_target

    monkeypatch.setattr(agent, "_bind_bug_target", fake_bind)
    monkeypatch.setattr(agent, "_submit_bug", fake_submit)

    agent_session = SimpleNamespace(id=1, pending_action={}, bug_target={})
    message = SimpleNamespace(
        id=12,
        content="提交 bug https://project.feishu.cn/proj/story/detail/123，把这张图带上",
        attachments={"images": [{"url": "/media/agent_uploads/shot.png", "filename": "shot.png"}]},
    )

    await agent._handle_user_message(_FakeSession(), agent_session, message)  # type: ignore[arg-type]

    assert submitted["target"] is target
    assert submitted["args"]["description"] == message.content  # type: ignore[index]
    assert submitted["message"] is message
    assert agent_session.pending_action == {}


@pytest.mark.asyncio
async def test_agent_drops_pending_bug_when_next_turn_is_unrelated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_decide_tools(_session: object, _agent_session: object, _message: object) -> list[agent._ToolDecision]:
        return [agent._ToolDecision("", {}, "我来查账号。")]

    monkeypatch.setattr(agent, "_decide_tools", fake_decide_tools)

    agent_session = SimpleNamespace(
        id=1,
        bug_target={},
        pending_action={
            "tool_key": "bug_submit_feishu",
            "description": "我是交个 bug",
            "attachments": {"images": [{"url": "/media/agent_uploads/shot.png"}]},
        },
    )
    message = SimpleNamespace(id=13, content="查一下 13800000000 的权益", attachments={})

    await agent._handle_user_message(_FakeSession(), agent_session, message)  # type: ignore[arg-type]

    assert agent_session.pending_action == {}


@pytest.mark.asyncio
async def test_agent_reports_missing_business_data_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "_llm_client", lambda: (None, SimpleNamespace(llm_model="")))

    decision = await agent._decide_tool(  # type: ignore[arg-type]
        session=None,
        agent_session=SimpleNamespace(),
        message=SimpleNamespace(content="请准备一个有会员权益的测试账号", attachments={}),
    )

    assert decision.tool_key == ""
    assert "需要部署方" in decision.direct_answer


def test_agent_tool_registry_keeps_public_tools_without_company_cli() -> None:
    keys = {item["key"] for item in agent.TOOL_DEFS}

    assert keys == {
        "aiphone_dispatch",
        "aiweb_dispatch",
        "aiphone_resource_probe",
        "aiweb_resource_probe",
        "aiapi_run",
        "bug_submit_feishu",
    }


def test_agent_aiapi_tool_schema_hides_function_map_context() -> None:
    schemas = agent._decision_tools_schema()
    aiapi_schema = next(item for item in schemas if item["function"]["name"] == "aiapi_run")
    properties = aiapi_schema["function"]["parameters"]["properties"]

    assert "function_map_context" not in properties


@pytest.mark.asyncio
async def test_agent_resolves_standard_function_map_from_message_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        async def get(self, model: object, ident: object) -> object | None:
            if model is agent.RequirementItem and ident == 123:
                return SimpleNamespace(group_id=7)
            return None

    async def fake_compile(_session: object, item_ids: object, target: str) -> object:
        assert list(item_ids) == [123]
        assert target == "mixed"
        return agent.function_map_mount_service.TopLevelContext(context="功能地图 V2")

    monkeypatch.setattr(agent.function_map_mount_service, "compile_top_level_context", fake_compile)

    resolved = await agent._resolve_agent_function_map_context(  # type: ignore[arg-type]
        FakeSession(),
        SimpleNamespace(
            attachments={
                "context_ref": {
                    "mode": "standard",
                    "requirement_item_id": 123,
                    "use_current_function_map": True,
                }
            }
        ),
    )

    assert resolved.context == "功能地图 V2"
    assert resolved.meta["applied"] is True
    assert resolved.meta["source"] == "standard"
    assert resolved.meta["requirement_item_id"] == 123
    assert resolved.meta["group_id"] == 7
    assert resolved.meta["chars"] == len("功能地图 V2")


@pytest.mark.asyncio
async def test_agent_resolves_quick_function_map_from_message_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        async def get(self, model: object, ident: object) -> object | None:
            if model is agent.QuickSession and ident == "quick-1":
                return SimpleNamespace()  # 存在即可，正文由编译器给
            return None

    async def fake_compile(_session: object, quick_session_id: str, target: str) -> object:
        assert quick_session_id == "quick-1"
        assert target == "mixed"
        return agent.function_map_mount_service.TopLevelContext(context="# login.md\n登录规则")

    monkeypatch.setattr(agent.function_map_mount_service, "compile_quick_context", fake_compile)

    resolved = await agent._resolve_agent_function_map_context(  # type: ignore[arg-type]
        FakeSession(),
        SimpleNamespace(
            attachments={
                "context_ref": {
                    "mode": "quick",
                    "quick_session_id": "quick-1",
                    "use_current_function_map": True,
                }
            }
        ),
    )

    assert resolved.context == "# login.md\n登录规则"
    assert resolved.meta["applied"] is True
    assert resolved.meta["source"] == "quick"
    assert resolved.meta["quick_session_id"] == "quick-1"


@pytest.mark.asyncio
async def test_agent_function_map_context_can_be_disabled_per_message() -> None:
    resolved = await agent._resolve_agent_function_map_context(  # type: ignore[arg-type]
        SimpleNamespace(),
        SimpleNamespace(
            attachments={
                "context_ref": {
                    "mode": "standard",
                    "requirement_item_id": 123,
                    "use_current_function_map": False,
                }
            }
        ),
    )

    assert resolved.context == ""
    assert resolved.meta["applied"] is False
    assert resolved.meta["reason"] == "disabled"


@pytest.mark.asyncio
async def test_agent_standard_function_map_is_message_level_realtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirement_groups = {101: 7, 202: 8}
    group_maps = {7: "需求 A functionMap V1", 8: "需求 B functionMap"}

    class FakeSession:
        async def get(self, model: object, ident: object) -> object | None:
            if model is agent.RequirementItem and ident in requirement_groups:
                return SimpleNamespace(group_id=requirement_groups[int(ident)])
            return None

    async def fake_compile(_session: object, item_ids: object, _target: str) -> object:
        ids = list(item_ids)
        gid = requirement_groups.get(ids[0]) if ids else None
        return agent.function_map_mount_service.TopLevelContext(context=group_maps.get(gid, ""))

    monkeypatch.setattr(agent.function_map_mount_service, "compile_top_level_context", fake_compile)

    def message(requirement_item_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            attachments={
                "context_ref": {
                    "mode": "standard",
                    "requirement_item_id": requirement_item_id,
                    "use_current_function_map": True,
                }
            }
        )

    first = await agent._resolve_agent_function_map_context(FakeSession(), message(101))  # type: ignore[arg-type]
    group_maps[7] = "需求 A functionMap V2"
    changed = await agent._resolve_agent_function_map_context(FakeSession(), message(101))  # type: ignore[arg-type]
    switched = await agent._resolve_agent_function_map_context(FakeSession(), message(202))  # type: ignore[arg-type]
    group_maps[8] = ""
    deleted = await agent._resolve_agent_function_map_context(FakeSession(), message(202))  # type: ignore[arg-type]

    assert first.context == "需求 A functionMap V1"
    assert changed.context == "需求 A functionMap V2"
    assert switched.context == "需求 B functionMap"
    assert deleted.context == ""
    assert deleted.meta["applied"] is False
    assert deleted.meta["reason"] == "empty_function_map"


@pytest.mark.asyncio
async def test_agent_quick_function_map_is_message_level_realtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = {"content": "quick function V1"}

    class FakeSession:
        async def get(self, model: object, ident: object) -> object | None:
            if model is agent.QuickSession and ident == "quick-1":
                return SimpleNamespace()
            return None

    async def fake_compile(_session: object, _sid: str, _target: str) -> object:
        return agent.function_map_mount_service.TopLevelContext(context=holder["content"])

    monkeypatch.setattr(agent.function_map_mount_service, "compile_quick_context", fake_compile)

    message = SimpleNamespace(
        attachments={
            "context_ref": {
                "mode": "quick",
                "quick_session_id": "quick-1",
                "use_current_function_map": True,
            }
        }
    )

    first = await agent._resolve_agent_function_map_context(FakeSession(), message)  # type: ignore[arg-type]
    holder["content"] = "quick function V2"
    changed = await agent._resolve_agent_function_map_context(FakeSession(), message)  # type: ignore[arg-type]
    holder["content"] = ""
    deleted = await agent._resolve_agent_function_map_context(FakeSession(), message)  # type: ignore[arg-type]

    assert first.context == "quick function V1"
    assert changed.context == "quick function V2"
    assert deleted.context == ""
    assert deleted.meta["reason"] == "empty_function_map"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_key", "base_url"),
    [
        ("aiphone_dispatch", "http://aiphone.local"),
        ("aiweb_dispatch", "http://aiweb.local"),
    ],
)
async def test_agent_external_executor_submits_function_map_context(
    monkeypatch: pytest.MonkeyPatch,
    tool_key: str,
    base_url: str,
) -> None:
    class FakeSession(_FakeSession):
        async def get(self, model: object, ident: object) -> object | None:
            if model is agent.RequirementItem and ident == 123:
                return SimpleNamespace(group_id=7)
            return None

    async def fake_compile(_session: object, item_ids: object, _target: str) -> object:
        assert list(item_ids) == [123]
        return agent.function_map_mount_service.TopLevelContext(context="执行器 functionMap")

    captured_clients: list[_FakeAsyncClient] = []

    def fake_client_factory(*_args: object, **_kwargs: object) -> _FakeAsyncClient:
        client = _FakeAsyncClient()
        captured_clients.append(client)
        return client

    monkeypatch.setattr(agent.function_map_mount_service, "compile_top_level_context", fake_compile)
    async def fake_resources(is_web: bool) -> SimpleNamespace:
        return SimpleNamespace(
            source="service",
            error=None,
            devices=[
                {
                    "alias": "chrome-slot" if is_web else "android-phone",
                    "platform": "chrome" if is_web else "android",
                    "occupancy": "idle",
                }
            ],
        )

    monkeypatch.setattr(agent, "_list_agent_resources", fake_resources)
    monkeypatch.setattr(agent, "get_settings", lambda: SimpleNamespace(
        aiphone_base_url="http://aiphone.local",
        aiweb_base_url="http://aiweb.local",
    ))
    monkeypatch.setattr(agent, "executor_callback_base_url", lambda *_args, **_kwargs: "http://case-flow.local")
    monkeypatch.setattr(agent.httpx, "AsyncClient", fake_client_factory)

    session = FakeSession()
    message = SimpleNamespace(
        id=10,
        content="打开页面检查一下",
        attachments={
            "context_ref": {
                "mode": "standard",
                "requirement_item_id": 123,
                "use_current_function_map": True,
            }
        },
        dispatch_id=None,
    )

    await agent._dispatch_external_executor(  # type: ignore[arg-type]
        session,
        SimpleNamespace(id=1),
        message,
        agent._ToolDecision(tool_key, {"run_content": "打开页面检查一下"}),
    )

    assert captured_clients
    assert captured_clients[0].posts[0][0] == f"{base_url}/api/submissions"
    payload = captured_clients[0].posts[0][1]
    assert payload["functionMapContext"] == "执行器 functionMap"
    dispatch = next(item for item in session.added if getattr(item, "tool_key", "") == tool_key)
    assert dispatch.input_args["agentFunctionMapContext"]["applied"] is True
    assert dispatch.input_args["agentFunctionMapContext"]["chars"] == len("执行器 functionMap")


@pytest.mark.asyncio
async def test_agent_external_executor_omits_empty_or_disabled_function_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession(_FakeSession):
        async def get(self, model: object, ident: object) -> object | None:
            if model is agent.RequirementItem and ident == 123:
                return SimpleNamespace(group_id=7)
            return None

    async def fake_compile(_session: object, _item_ids: object, _target: str) -> object:
        return agent.function_map_mount_service.TopLevelContext(context="")

    captured_clients: list[_FakeAsyncClient] = []

    def fake_client_factory(*_args: object, **_kwargs: object) -> _FakeAsyncClient:
        client = _FakeAsyncClient()
        captured_clients.append(client)
        return client

    monkeypatch.setattr(agent.function_map_mount_service, "compile_top_level_context", fake_compile)
    async def fake_resources(_is_web: bool) -> SimpleNamespace:
        return SimpleNamespace(
            source="service",
            error=None,
            devices=[{"alias": "android-phone", "platform": "android", "occupancy": "idle"}],
        )

    monkeypatch.setattr(agent, "_list_agent_resources", fake_resources)
    monkeypatch.setattr(agent, "get_settings", lambda: SimpleNamespace(
        aiphone_base_url="http://aiphone.local",
        aiweb_base_url="http://aiweb.local",
    ))
    monkeypatch.setattr(agent, "executor_callback_base_url", lambda *_args, **_kwargs: "http://case-flow.local")
    monkeypatch.setattr(agent.httpx, "AsyncClient", fake_client_factory)

    session = FakeSession()
    message = SimpleNamespace(
        id=10,
        content="打开 App",
        attachments={
            "context_ref": {
                "mode": "standard",
                "requirement_item_id": 123,
                "use_current_function_map": True,
            }
        },
        dispatch_id=None,
    )

    await agent._dispatch_external_executor(  # type: ignore[arg-type]
        session,
        SimpleNamespace(id=1),
        message,
        agent._ToolDecision("aiphone_dispatch", {"run_content": "打开 App"}),
    )

    payload = captured_clients[0].posts[0][1]
    assert "functionMapContext" not in payload
    dispatch = next(item for item in session.added if getattr(item, "tool_key", "") == "aiphone_dispatch")
    assert dispatch.input_args["agentFunctionMapContext"]["applied"] is False
    assert dispatch.input_args["agentFunctionMapContext"]["reason"] == "empty_function_map"


@pytest.mark.asyncio
async def test_agent_aiapi_dispatch_uses_backend_function_map_and_ignores_model_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession(_FakeSession):
        async def get(self, model: object, ident: object) -> object | None:
            if model is agent.RequirementItem and ident == 123:
                return SimpleNamespace(group_id=7)
            return None

    async def fake_compile(_session: object, _item_ids: object, _target: str) -> object:
        return agent.function_map_mount_service.TopLevelContext(context="API functionMap")

    monkeypatch.setattr(agent.function_map_mount_service, "compile_top_level_context", fake_compile)

    session = FakeSession()
    message = SimpleNamespace(
        id=10,
        content="跑一下接口",
        attachments={
            "context_ref": {
                "mode": "standard",
                "requirement_item_id": 123,
                "use_current_function_map": True,
            }
        },
        dispatch_id=None,
    )

    await agent._dispatch_aiapi(  # type: ignore[arg-type]
        session,
        SimpleNamespace(id=1),
        message,
        agent._ToolDecision(
            "aiapi_run",
            {
                "steps_text": "请求登录接口",
                "function_map_context": "模型不该能传这个",
            },
        ),
    )

    dispatch = next(item for item in session.added if getattr(item, "tool_key", "") == "aiapi_run")
    assert dispatch.input_args["function_map_context"] == "API functionMap"
    assert dispatch.input_args["agentFunctionMapContext"]["applied"] is True
    assert "模型不该能传这个" not in dispatch.input_args.values()


@pytest.mark.asyncio
async def test_agent_aiapi_dispatch_omits_function_map_when_disabled() -> None:
    session = _FakeSession()
    message = SimpleNamespace(
        id=10,
        content="跑一下接口",
        attachments={
            "context_ref": {
                "mode": "standard",
                "requirement_item_id": 123,
                "use_current_function_map": False,
            }
        },
        dispatch_id=None,
    )

    await agent._dispatch_aiapi(  # type: ignore[arg-type]
        session,
        SimpleNamespace(id=1),
        message,
        agent._ToolDecision("aiapi_run", {"steps_text": "请求登录接口"}),
    )

    dispatch = next(item for item in session.added if getattr(item, "tool_key", "") == "aiapi_run")
    assert "function_map_context" not in dispatch.input_args
    assert dispatch.input_args["agentFunctionMapContext"]["applied"] is False
    assert dispatch.input_args["agentFunctionMapContext"]["reason"] == "disabled"


@pytest.mark.asyncio
async def test_agent_fallback_routes_device_resource_question_to_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent, "_llm_client", lambda: (None, SimpleNamespace(llm_model="")))

    ios_decision = await agent._decide_tool(  # type: ignore[arg-type]
        session=None,
        agent_session=SimpleNamespace(),
        message=SimpleNamespace(content="目前我有iOS手机能用吗？", attachments={}),
    )
    android_decision = await agent._decide_tool(  # type: ignore[arg-type]
        session=None,
        agent_session=SimpleNamespace(),
        message=SimpleNamespace(content="android呢？", attachments={}),
    )

    assert ios_decision.tool_key == "aiphone_resource_probe"
    assert ios_decision.args["platform"] == "ios"
    assert android_decision.tool_key == "aiphone_resource_probe"
    assert android_decision.args["platform"] == "android"


def test_agent_resource_probe_reply_is_short_and_specific() -> None:
    devices = [
        {"alias": "学习工具红米k70", "platform": "android", "occupancy": "idle"},
    ]

    ios_reply = agent._resource_probe_reply(devices, "service", None, "ios", is_web=False)
    android_reply = agent._resource_probe_reply(devices, "service", None, "android", is_web=False)

    assert ios_reply == "当前没有可用 iOS 手机。"
    assert android_reply == "Android 手机：1 台空闲：学习工具红米k70"


@pytest.mark.asyncio
async def test_agent_select_executor_resource_uses_matching_idle_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resources(is_web: bool) -> SimpleNamespace:
        return SimpleNamespace(
            source="service",
            error=None,
            devices=[
                {"alias": "学习工具红米k70", "platform": "android", "occupancy": "idle"},
            ],
        )

    monkeypatch.setattr(agent, "_list_agent_resources", fake_resources)

    selected = await agent._select_executor_resource(False, "android")
    blocked = await agent._select_executor_resource(False, "ios")

    assert selected["device_alias_pools"] == {"android": ["学习工具红米k70"]}
    assert selected["platform"] == "android"
    assert blocked["blocked"] == "当前没有可用 iOS 手机，未提交任务。"


def test_agent_recent_context_window_is_ten_turns() -> None:
    assert agent.RECENT_CONTEXT_LIMIT == 10


def test_agent_reset_intro_names_os_agent_capabilities() -> None:
    assert agent.AGENT_DISPLAY_NAME == "OS Agent"
    assert "手机" in agent.AGENT_INTRO_MESSAGE
    assert "网页" in agent.AGENT_INTRO_MESSAGE
    assert "接口" in agent.AGENT_INTRO_MESSAGE
    assert "bug" in agent.AGENT_INTRO_MESSAGE.lower()
    assert "测试账号" not in agent.AGENT_INTRO_MESSAGE


def test_agent_result_summary_is_chat_message_with_report_attachment_style() -> None:
    dispatch = SimpleNamespace(
        tool_key="aiphone_dispatch",
        status="passed",
        report_url="http://case-flow.local/report.html",
    )

    summary = agent._fallback_result_summary(dispatch, "登录链路通过，关键页面正常。")  # type: ignore[arg-type]

    assert summary.startswith("手机任务完成。")
    assert "登录链路通过" in summary
    assert "http://case-flow.local/report.html" not in summary


def test_agent_aiweb_result_summary_uses_short_name() -> None:
    dispatch = SimpleNamespace(
        tool_key="aiweb_dispatch",
        status="passed",
        report_url="http://case-flow.local/aiweb.html",
    )

    summary = agent._fallback_result_summary(  # type: ignore[arg-type]
        dispatch,
        "✅ 你的Case Flow Agent AI Web任务已经执行完成，全部运行成功。\n"
        "🔍 关键信息：\n"
        "本次任务提交ID为`abc123`，共成功执行1个任务项。",
    )

    assert summary.startswith("AI Web 任务已完成。")
    assert "Case Flow Agent" not in summary
    assert "提交ID" not in summary


def test_agent_key_images_prefer_failure_evidence() -> None:
    dispatch = SimpleNamespace(status="failed", platform="android")
    evidence = SimpleNamespace(
        image_evidence=[
            agent.ReportImageEvidence(index=0, url="start.png", context="打开首页"),
            agent.ReportImageEvidence(index=1, url="input.png", context="输入搜索内容"),
            agent.ReportImageEvidence(index=2, url="fail.png", context="执行失败：未找到确认按钮"),
            agent.ReportImageEvidence(index=3, url="last.png", context="任务结束"),
        ],
        image_urls=[],
    )

    images = agent._select_key_images(dispatch, evidence)  # type: ignore[arg-type]

    assert [item["image"] for item in images] == ["fail.png"]
    assert images[0]["caption"] == "失败现场"


def test_agent_key_images_fallback_to_final_success_image() -> None:
    dispatch = SimpleNamespace(status="passed", platform="chrome")
    evidence = SimpleNamespace(
        image_evidence=[
            agent.ReportImageEvidence(index=0, url="home.png", context="打开首页"),
            agent.ReportImageEvidence(index=1, url="search.png", context="输入搜索词"),
            agent.ReportImageEvidence(index=2, url="result.png", context="最终查询结果显示北京明天天气"),
        ],
        image_urls=[],
    )

    images = agent._select_key_images(dispatch, evidence)  # type: ignore[arg-type]

    assert [item["image"] for item in images] == ["result.png"]
    assert images[0]["caption"] == "结果截图"
