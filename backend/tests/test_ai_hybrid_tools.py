from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.executions import AIPhoneDeviceListOut
from app.services import executions
from app.services.ai_hybrid import child_wait
from app.services.ai_hybrid.schemas import HybridToolInput
from app.services.ai_hybrid.tools import (
    AIAPITool,
    AIPhoneTool,
    AIWebTool,
    FunctionMapTool,
    ReportReaderTool,
    _render_run_content,
    _structured_fields,
)


class _SubmitResp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"submissionId": "child-submission"}


def _locked_phone_input(alias: str, *, steps: str = "老师创建班级") -> HybridToolInput:
    return HybridToolInput(
        tool="ai_phone",
        input=steps,
        raw={
            "title": "老师创建班级",
            "preconditions": "关闭 App「示例 App」（杀进程）后重新打开 App「示例 App」",
            "steps": steps,
            "expected": "班级创建成功",
            "device_alias": alias,
        },
    )


def _lock_settings(**overrides: object) -> SimpleNamespace:
    settings: dict[str, object] = {
        "aiphone_base_url": "http://127.0.0.1:8000",
        "public_base_url": "http://127.0.0.1:8800",
        "hybrid_max_wall_seconds": 60,
        "hybrid_device_wait_interval_seconds": 1,
        "hybrid_device_wait_max_attempts": 3,
    }
    settings.update(overrides)
    return SimpleNamespace(**settings)


def test_structured_fields_parses_four_sections() -> None:
    title, preconditions, steps, expected = _structured_fields(
        {
            "title": "验证章节列表",
            "preconditions": "关闭 App「示例 App」（杀进程）后重新打开 App「示例 App」",
            "steps": "进入学习页",
            "expected": "显示学习方法卡片",
        },
        "fallback",
        "默认标题",
    )
    assert title == "验证章节列表"
    assert preconditions == "关闭 App「示例 App」（杀进程）后重新打开 App「示例 App」"
    assert steps == "进入学习页"
    assert expected == "显示学习方法卡片"


def test_structured_fields_falls_back_to_plain_text() -> None:
    title, preconditions, steps, expected = _structured_fields({}, "只有一段自然语言", "默认标题")
    assert preconditions == ""
    assert expected == ""
    assert steps == "只有一段自然语言"
    assert title  # 自动派生标题


def test_render_run_content_matches_standard_format() -> None:
    content = _render_run_content(
        "标题", "关闭 App「示例 App」（杀进程）后重新打开 App「示例 App」", "步骤", "预期"
    )
    assert content == (
        "测试标题：标题\n\n"
        "前置条件：关闭 App「示例 App」（杀进程）后重新打开 App「示例 App」\n\n"
        "操作步骤：步骤\n\n预期结果：预期"
    )


def test_phone_platform_decision_uses_only_execution_constraints() -> None:
    tool = AIPhoneTool()

    assertion = tool._platform_decision(
        HybridToolInput(tool="ai_phone", input="检查当前设备是否为 iOS 环境")
    )
    explicit = tool._platform_decision(
        HybridToolInput(tool="ai_phone", input="在 iOS 设备上执行登录验证")
    )
    common_lane = tool._platform_decision(
        HybridToolInput(tool="ai_phone", input="验证 iOS 端登录流程")
    )

    assert assertion.platform == "android"
    assert assertion.source == "default"
    assert explicit.platform == "ios"
    assert explicit.source == "explicit"
    assert common_lane.platform == "ios"
    assert common_lane.source == "explicit"


def test_web_platform_decision_defaults_to_chrome_without_execution_constraint() -> None:
    tool = AIWebTool()

    assertion = tool._platform_decision(
        HybridToolInput(tool="ai_web", input="校验页面文案是否包含 Chrome")
    )
    explicit = tool._platform_decision(
        HybridToolInput(tool="ai_web", input="用 Safari 浏览器打开后台订单页")
    )

    assert assertion.platform == "chrome"
    assert assertion.source == "default"
    assert explicit.platform == "safari"
    assert explicit.source == "explicit"


@pytest.mark.asyncio
async def test_aiweb_tool_does_not_register_child_wait_when_callback_base_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_register(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("child wait should not be registered before callback base is valid")

    monkeypatch.setattr(child_wait, "register", fail_register)

    result = await AIWebTool().run(
        HybridToolInput(
            tool="ai_web",
            input="检查订单",
            raw={
                "title": "检查订单",
                "preconditions": "打开订单管理后台平台",
                "steps": "查看订单列表",
                "expected": "订单展示正常",
            },
        ),
        SimpleNamespace(
            aiweb_base_url="http://127.0.0.1:8009",
            aiweb_callback_base_url="",
            public_base_url="http://127.0.0.1:8800",
        ),
    )

    assert result.status == "failed"
    assert result.reason
    assert result.reason.startswith("callback_base_error:")


@pytest.mark.asyncio
async def test_external_executor_cancel_stops_hybrid_wait_without_cancelling_submitted_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = asyncio.Event()
    submitted = asyncio.Event()
    forgotten: list[str] = []
    requests: list[str] = []

    async def fake_list_devices() -> object:
        return SimpleNamespace(
            source="service",
            devices=[{"alias": "chrome-1", "platform": "chrome", "occupancy": "idle"}],
        )

    def fake_register(token: str, *_args: object, **_kwargs: object) -> asyncio.Event:
        return waiting

    def fake_forget(token: str) -> None:
        forgotten.append(token)

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, json: object = None) -> _SubmitResp:
            requests.append(url)
            submitted.set()
            return _SubmitResp()

    monkeypatch.setattr(executions, "list_aiweb_devices", fake_list_devices)
    monkeypatch.setattr(child_wait, "register", fake_register)
    monkeypatch.setattr(child_wait, "forget", fake_forget)
    monkeypatch.setattr("app.services.ai_hybrid.tools.httpx.AsyncClient", FakeClient)

    task = asyncio.create_task(
        AIWebTool().run(
            HybridToolInput(
                tool="ai_web",
                input="检查订单",
                raw={
                    "title": "检查订单",
                    "preconditions": "打开订单管理后台平台",
                    "steps": "查看订单列表",
                    "expected": "订单展示正常",
                },
            ),
            SimpleNamespace(
                aiweb_base_url="http://127.0.0.1:8009",
                aiweb_callback_base_url="http://127.0.0.1:8800",
                public_base_url="http://127.0.0.1:8800",
                hybrid_max_wall_seconds=60,
            ),
        )
    )
    await asyncio.wait_for(submitted.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(forgotten) == 1
    assert requests == ["http://127.0.0.1:8009/api/submissions"]


@pytest.mark.asyncio
async def test_external_executor_cancel_during_submit_forgets_local_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted = asyncio.Event()
    child_wait._pending.clear()

    async def fake_list_devices() -> object:
        return SimpleNamespace(
            source="service",
            devices=[{"alias": "chrome-1", "platform": "chrome", "occupancy": "idle"}],
        )

    class BlockingClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "BlockingClient":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, _url: str, json: object = None) -> _SubmitResp:
            submitted.set()
            await asyncio.Event().wait()
            raise AssertionError("cancelled submit must not resume")

    monkeypatch.setattr(executions, "list_aiweb_devices", fake_list_devices)
    monkeypatch.setattr("app.services.ai_hybrid.tools.httpx.AsyncClient", BlockingClient)

    task = asyncio.create_task(
        AIWebTool().run(
            HybridToolInput(
                tool="ai_web",
                input="检查订单",
                raw={
                    "title": "检查订单",
                    "preconditions": "打开订单管理后台平台",
                    "steps": "查看订单列表",
                    "expected": "订单展示正常",
                },
            ),
            SimpleNamespace(
                aiweb_base_url="http://127.0.0.1:8009",
                aiweb_callback_base_url="http://127.0.0.1:8800",
                public_base_url="http://127.0.0.1:8800",
                hybrid_max_wall_seconds=60,
            ),
        )
    )
    await asyncio.wait_for(submitted.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert child_wait._pending == {}


@pytest.mark.asyncio
async def test_child_callback_after_hybrid_stop_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "stopped-child-token"
    child_wait._pending.clear()
    child_wait.register(token, "http://127.0.0.1:8009")
    child_wait.forget(token)
    monkeypatch.setattr(executions, "get_settings", lambda: SimpleNamespace(aiphone_base_url="http://127.0.0.1:8000"))

    result = await executions.apply_aihybrid_child_callback(
        token,
        {
            "event": "submission.item.terminal",
            "submissionId": "child-submission",
            "caseId": "child-case",
            "state": "success",
        },
    )

    assert result["handled"] is False
    assert result["updated_case_ids"] == []


@pytest.mark.asyncio
async def test_aiphone_tool_blocks_before_submit_when_resource_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_devices() -> AIPhoneDeviceListOut:
        return AIPhoneDeviceListOut(source="service", devices=[])

    def fail_register(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("child wait should not be registered when no resource is available")

    monkeypatch.setattr(executions, "list_aiphone_devices", fake_list_devices)
    monkeypatch.setattr(child_wait, "register", fail_register)

    result = await AIPhoneTool().run(
        HybridToolInput(
            tool="ai_phone",
            input="验证登录流程",
            raw={
                "title": "验证登录",
                "preconditions": "关闭 App「示例 App」（杀进程）后重新打开 App「示例 App」",
                "steps": "验证登录流程",
                "expected": "登录成功",
            },
        ),
        SimpleNamespace(
            aiphone_base_url="http://127.0.0.1:8000",
            public_base_url="http://127.0.0.1:8800",
        ),
    )

    assert result.status == "needs_human"
    assert result.reason == "resource_not_available"
    assert result.raw["submitted"] is False
    assert result.raw["execution_strategy"]["platform"] == "android"
    assert "当前没有可用 Android 手机" in result.raw["resource"]["message"]


@pytest.mark.asyncio
async def test_aiapi_tool_forwards_expected(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run(payload: object) -> object:
        captured["expected_result"] = getattr(payload, "expected_result", None)
        captured["title"] = getattr(payload, "title", None)
        captured["preconditions"] = getattr(payload, "preconditions", None)
        captured["steps_text"] = getattr(payload, "steps_text", None)
        return SimpleNamespace(
            status="success",
            status_reason="ok",
            report_url="http://r/report.html",
            model_dump=lambda mode="json": {"status": "success"},
        )

    monkeypatch.setattr("app.services.ai_hybrid.tools.run_direct_aiapi", fake_run)

    await AIAPITool().run(
        HybridToolInput(
            tool="ai_api",
            input="POST /api/user 创建账号",
            raw={
                "title": "创建用户",
                "preconditions": "已有站点 siteId",
                "steps": "POST /api/user",
                "expected": "返回 200 且含 userId",
            },
        ),
        SimpleNamespace(),
    )
    assert captured["expected_result"] == "返回 200 且含 userId"
    assert captured["title"] == "创建用户"
    assert captured["preconditions"] == "已有站点 siteId"
    assert captured["steps_text"] == "POST /api/user"


@pytest.mark.asyncio
async def test_aiapi_tool_needs_human_when_steps_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_run(_payload: object) -> object:
        nonlocal called
        called = True
        return SimpleNamespace(status="success", status_reason="ok", report_url=None, model_dump=lambda mode="json": {})

    monkeypatch.setattr("app.services.ai_hybrid.tools.run_direct_aiapi", fake_run)

    result = await AIAPITool().run(
        HybridToolInput(tool="ai_api", input="", raw={"title": "创建用户", "expected": "返回 200 且含 userId"}),
        SimpleNamespace(),
    )
    assert result.status == "needs_human"
    assert result.reason == "missing_steps"
    assert called is False


@pytest.mark.asyncio
async def test_aiapi_tool_needs_human_when_expected_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_run(_payload: object) -> object:
        nonlocal called
        called = True
        return SimpleNamespace(
            status="success",
            status_reason="ok",
            report_url=None,
            model_dump=lambda mode="json": {"status": "success"},
        )

    monkeypatch.setattr("app.services.ai_hybrid.tools.run_direct_aiapi", fake_run)

    result = await AIAPITool().run(
        HybridToolInput(tool="ai_api", input="调接口", raw={"title": "调接口", "steps": "GET /ping"}),
        SimpleNamespace(),
    )
    assert result.status == "needs_human"
    assert result.reason == "missing_expected"
    assert called is False


@pytest.mark.asyncio
async def test_aiphone_tool_submits_default_android_when_platform_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list_devices() -> AIPhoneDeviceListOut:
        return AIPhoneDeviceListOut(
            source="service",
            devices=[{"alias": "dev-a", "serial": "s1", "platform": "android", "occupancy": "idle"}],
        )

    monkeypatch.setattr(executions, "list_aiphone_devices", fake_list_devices)

    import asyncio

    ready = asyncio.Event()
    ready.set()
    monkeypatch.setattr(child_wait, "register", lambda *_a, **_k: ready)
    monkeypatch.setattr(child_wait, "take_result", lambda _token: {"state": "success", "reportUrl": "http://r/report.html"})
    monkeypatch.setattr(child_wait, "forget", lambda _token: None)

    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"submissionId": "sub-1"}

    class _FakeClient:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

        async def post(self, url: str, json: object = None) -> _Resp:
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr("app.services.ai_hybrid.tools.httpx.AsyncClient", _FakeClient)

    result = await AIPhoneTool().run(
        HybridToolInput(
            tool="ai_phone",
            input="进入视频中心播放视频",
            raw={
                "title": "视频播放",
                "preconditions": "关闭 App「示例 App」（杀进程）后重新打开 App「示例 App」",
                "steps": "进入视频中心播放视频",
                "expected": "视频正常播放",
            },
        ),
        SimpleNamespace(
            aiphone_base_url="http://127.0.0.1:8000",
            public_base_url="http://127.0.0.1:8800",
            hybrid_max_wall_seconds=60,
        ),
    )

    assert result.status == "success"
    submitted_payload = captured["json"]
    assert isinstance(submitted_payload, dict)
    item = submitted_payload["items"][0]
    assert item["platforms"] == ["android"]
    assert item["caseName"] == "视频播放"
    assert item["runContent"] == (
        "测试标题：视频播放\n\n"
        "前置条件：关闭 App「示例 App」（杀进程）后重新打开 App「示例 App」\n\n"
        "操作步骤：进入视频中心播放视频\n\n预期结果：视频正常播放"
    )
    assert result.raw["execution_strategy"]["platform"] == "android"
    assert result.raw["execution_strategy"]["source"] == "default"


@pytest.mark.asyncio
async def test_aiphone_hard_lock_submits_only_the_requested_device(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list_devices() -> AIPhoneDeviceListOut:
        return AIPhoneDeviceListOut(
            source="service",
            devices=[
                {"alias": "teacher-device", "serial": "a", "platform": "android", "occupancy": "idle"},
                {"alias": "student-device", "serial": "b", "platform": "android", "occupancy": "idle"},
            ],
        )

    import asyncio

    ready = asyncio.Event()
    ready.set()
    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"submissionId": "sub-1"}

    class _Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, _url: str, json: object = None) -> _Resp:
            captured["payload"] = json
            return _Resp()

    monkeypatch.setattr(executions, "list_aiphone_devices", fake_list_devices)
    monkeypatch.setattr(child_wait, "register", lambda *_args, **_kwargs: ready)
    monkeypatch.setattr(child_wait, "take_result", lambda _token: {"state": "success"})
    monkeypatch.setattr(child_wait, "forget", lambda _token: None)
    monkeypatch.setattr("app.services.ai_hybrid.tools.httpx.AsyncClient", _Client)

    result = await AIPhoneTool().run(_locked_phone_input("teacher-device"), _lock_settings())
    assert result.status == "success"
    item = captured["payload"]["items"][0]  # type: ignore[index]
    assert item["deviceAliasPools"] == {"android": ["teacher-device"]}
    assert result.raw["resource"]["state"] == "locked"


@pytest.mark.asyncio
async def test_aiphone_hard_lock_never_reassigns_unavailable_or_busy_device(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    async def fake_list_devices() -> AIPhoneDeviceListOut:
        calls["count"] += 1
        return AIPhoneDeviceListOut(
            source="service",
            devices=[{"alias": "teacher-device", "serial": "a", "platform": "android", "occupancy": "busy"}],
        )

    async def no_wait(_seconds: float) -> None:
        pass

    def must_not_submit(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("hard-locked device busy: must not submit to another device")

    monkeypatch.setattr(executions, "list_aiphone_devices", fake_list_devices)
    monkeypatch.setattr("app.services.ai_hybrid.tools.asyncio.sleep", no_wait)
    monkeypatch.setattr(child_wait, "register", must_not_submit)
    result = await AIPhoneTool().run(_locked_phone_input("teacher-device"), _lock_settings())
    assert result.status == "needs_human"
    assert result.reason == "device_busy_timeout"
    assert calls["count"] == 3
    assert result.raw["submitted"] is False


@pytest.mark.asyncio
async def test_aiphone_hard_lock_blocks_missing_or_platform_conflicting_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing() -> AIPhoneDeviceListOut:
        return AIPhoneDeviceListOut(
            source="service",
            devices=[{"alias": "other", "serial": "b", "platform": "android", "occupancy": "idle"}],
        )

    monkeypatch.setattr(executions, "list_aiphone_devices", missing)
    missing_result = await AIPhoneTool().run(_locked_phone_input("teacher-device"), _lock_settings())
    assert missing_result.status == "needs_human"
    assert missing_result.reason == "device_not_available"
    assert missing_result.raw["submitted"] is False

    async def platform_conflict() -> AIPhoneDeviceListOut:
        return AIPhoneDeviceListOut(
            source="service",
            devices=[{"alias": "teacher-device", "serial": "a", "platform": "android", "occupancy": "idle"}],
        )

    monkeypatch.setattr(executions, "list_aiphone_devices", platform_conflict)
    conflict = await AIPhoneTool().run(
        _locked_phone_input("teacher-device", steps="在 iOS 设备上执行登录验证"), _lock_settings()
    )
    assert conflict.status == "needs_human"
    assert conflict.reason == "device_platform_conflict"
    assert conflict.raw["submitted"] is False


@pytest.mark.asyncio
async def test_function_map_tool_reads_structured_map_without_defaulting() -> None:
    maps = [{"asset_id": 1, "title": "账号绑定", "targets": ["app"], "content": "老师→teacher-device"}]
    result = await FunctionMapTool().run(
        HybridToolInput(tool="function_map", input="", function_maps=maps, raw={"asset_id": "1"}),
        SimpleNamespace(),
    )
    assert result.status == "success"
    assert result.raw["targets"] == ["app"]
    assert result.raw["content"] == "老师→teacher-device"


@pytest.mark.asyncio
async def test_aiphone_tool_needs_human_when_preconditions_missing() -> None:
    result = await AIPhoneTool().run(
        HybridToolInput(
            tool="ai_phone",
            input="进入视频中心播放视频",
            raw={"title": "播放视频", "steps": "进入视频中心播放视频", "expected": "视频正常播放"},
        ),
        SimpleNamespace(),
    )
    assert result.status == "needs_human"
    assert result.reason == "missing_preconditions"
    assert result.raw["submitted"] is False


@pytest.mark.asyncio
async def test_aiweb_tool_needs_human_when_expected_missing() -> None:
    result = await AIWebTool().run(
        HybridToolInput(
            tool="ai_web",
            input="校验后台配置",
            raw={"title": "校验配置", "preconditions": "打开知识点管理后台平台", "steps": "查看配置项"},
        ),
        SimpleNamespace(),
    )
    assert result.status == "needs_human"
    assert result.reason == "missing_expected"
    assert result.raw["submitted"] is False


_FAKE_INDEX = {
    "available": True,
    "url": "http://127.0.0.1/report.html",
    "executor": "ai_web",
    "blocks": [
        {"i": 0, "kind": "text", "text": "课程 ID 为 37eaeece0-57f7-11e7-9139-d35a4f1315a7"},
        {"i": 1, "kind": "image", "imgNo": 0, "url": "http://127.0.0.1/shots/1.png"},
    ],
    "stats": {"block_count": 2, "text_chars": 40, "image_count": 1},
}


@pytest.mark.asyncio
async def test_report_reader_tool_outline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_index(report_url: str, **_kwargs: object) -> dict[str, object]:
        return _FAKE_INDEX

    monkeypatch.setattr("app.services.ai_hybrid.tools.report_observer.fetch_index", fake_fetch_index)

    result = await ReportReaderTool().run(
        HybridToolInput(
            tool="report_reader",
            input="获取课程 ID",
            raw={"report_url": "http://127.0.0.1/report.html", "executor": "ai_web", "mode": "outline"},
        ),
        SimpleNamespace(),
    )

    assert result.status == "success"
    assert result.reason == "read_ok"
    assert result.raw["observation_mode"] == "outline"
    assert result.raw["stats"]["image_count"] == 1


@pytest.mark.asyncio
async def test_report_reader_tool_defaults_to_outline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_index(report_url: str, **_kwargs: object) -> dict[str, object]:
        return _FAKE_INDEX

    monkeypatch.setattr("app.services.ai_hybrid.tools.report_observer.fetch_index", fake_fetch_index)

    result = await ReportReaderTool().run(
        HybridToolInput(
            tool="report_reader",
            input="获取课程 ID",
            raw={"report_url": "http://127.0.0.1/report.html", "executor": "ai_web"},
        ),
        SimpleNamespace(),
    )
    assert result.raw["observation_mode"] == "outline"


@pytest.mark.asyncio
async def test_report_reader_tool_image_returns_data_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_index(report_url: str, **_kwargs: object) -> dict[str, object]:
        return _FAKE_INDEX

    async def fake_fetch_data_uri(url: str, **_kwargs: object) -> str:
        return "data:image/png;base64,ZmFrZQ=="

    monkeypatch.setattr("app.services.ai_hybrid.tools.report_observer.fetch_index", fake_fetch_index)
    monkeypatch.setattr("app.services.ai_hybrid.tools.report_observer._fetch_data_uri", fake_fetch_data_uri)

    result = await ReportReaderTool().run(
        HybridToolInput(
            tool="report_reader",
            input="看截图",
            raw={"report_url": "http://127.0.0.1/report.html", "executor": "ai_web", "mode": "image", "image": 0},
        ),
        SimpleNamespace(),
    )

    assert result.status == "success"
    assert result.raw["observation_mode"] == "image"
    assert result.raw["image_data_uri"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_report_reader_tool_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await ReportReaderTool().run(
        HybridToolInput(tool="report_reader", input="读报告", raw={"executor": "ai_web"}),
        SimpleNamespace(),
    )
    assert result.status == "needs_human"
    assert result.reason == "missing_report_url"
