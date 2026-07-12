from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.case_assets import CaseAsset, CaseBody, CaseRawNode, CaseWorkItem, ImportBatch
from app.schemas.workbench import CaseAssetCreateIn, CaseAssetUpdateIn
from app.services import case_assets, executions


class _FakeSession:
    def __init__(self, case: object, body: object, work_item: object) -> None:
        self.case = case
        self.body = body
        self.work_item = work_item
        self.committed = False
        self.added_steps: list[object] = []

    async def get(self, model: object, _identity: int) -> object | None:
        if model is CaseAsset:
            return self.case
        if model is CaseBody:
            return self.body
        if model is CaseWorkItem:
            return self.work_item
        return None

    async def execute(self, _statement: object) -> None:
        return None

    def add_all(self, values: object) -> None:
        self.added_steps.extend(list(values))

    async def commit(self) -> None:
        self.committed = True


class _ScalarOneResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _ScalarOneOrNoneResult:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class _ScalarsAllResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def scalars(self) -> _ScalarsAllResult:
        return self

    def all(self) -> list[object]:
        return self.values


class _RowsResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


@pytest.mark.asyncio
async def test_create_case_asset_adds_case_to_selected_existing_path() -> None:
    batch = SimpleNamespace(id=12, requirement_item_id=3, suite_title="登录测试集", case_count=2)
    path_a = [
        {"level": 2, "label": "模块", "rawText": "账号", "displayText": "账号"},
        {"level": 3, "label": "功能点", "rawText": "密码登录", "displayText": "密码登录"},
        {"level": 4, "label": "测试功能点", "rawText": "异常密码", "displayText": "异常密码"},
    ]
    path_b = [
        {"level": 2, "label": "模块", "rawText": "账号", "displayText": "账号"},
        {"level": 3, "label": "功能点", "rawText": "第三方登录", "displayText": "第三方登录"},
        {"level": 4, "label": "测试功能点", "rawText": "微信登录", "displayText": "微信登录"},
    ]
    old_case_a = CaseAsset(
        id=1,
        batch_id=12,
        ordinal=1,
        suite_title="登录测试集",
        path_nodes=path_a,
        module_name="账号",
        product_feature="密码登录",
        test_feature="异常密码",
        raw_title="密码错误提示",
        clean_title="密码错误提示",
        scenario_tags=[],
        manual=False,
        source_requirement_item_id=3,
    )
    old_case_b = CaseAsset(
        id=2,
        batch_id=12,
        ordinal=4,
        suite_title="登录测试集",
        path_nodes=path_b,
        module_name="账号",
        product_feature="第三方登录",
        test_feature="微信登录",
        raw_title="微信授权登录",
        clean_title="微信授权登录",
        scenario_tags=[],
        manual=False,
        source_requirement_item_id=3,
    )
    raw_a = SimpleNamespace(
        raw_payload={
            "core_labels": {
                "case_title": "测试标题",
                "preconditions": "前置条件",
                "steps": "操作步骤",
                "expected": "预期结果",
            },
            "core_nodes": {
                "case_title": {"level": 5, "label": "测试标题", "trimmed": True, "separator": "："},
                "preconditions": {"level": 6, "label": "前置条件", "trimmed": True, "separator": "："},
                "steps": {"level": 7, "label": "操作步骤", "trimmed": True, "separator": "："},
                "expected": {"level": 8, "label": "预期结果", "trimmed": True, "separator": "："},
            },
        }
    )

    class FakeCreateSession:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.committed = False
            self.execute_calls = 0

        async def get(self, model: object, identity: int) -> object | None:
            if model is ImportBatch and identity == 12:
                return batch
            return None

        async def execute(self, _statement: object) -> object:
            self.execute_calls += 1
            if self.execute_calls == 1:
                return _RowsResult([(old_case_a, raw_a), (old_case_b, SimpleNamespace(raw_payload={}))])
            return _ScalarOneOrNoneResult(7)

        def add(self, value: object) -> None:
            if isinstance(value, CaseAsset):
                value.id = 99
            self.added.append(value)

        def add_all(self, values: object) -> None:
            self.added.extend(list(values))

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            self.committed = True

    session = FakeCreateSession()

    result = await case_assets.create_case_asset(
        session,  # type: ignore[arg-type]
        CaseAssetCreateIn(
            requirement_item_id=3,
            batch_id=12,
            path_nodes=path_a,
            raw_title="连续输错 5 次锁定账号",
            preconditions="账号 已注册",
            steps_text="连续输入错误密码 5 次",
            expected_result="账号被锁定",
        ),
    )

    assert result is not None
    assert result.case_id == 99
    assert old_case_a.ordinal == 1
    assert old_case_b.ordinal == 4
    new_case = next(item for item in session.added if isinstance(item, CaseAsset))
    assert new_case.ordinal == 5
    assert new_case.path_nodes == path_a
    body = next(item for item in session.added if isinstance(item, CaseBody))
    assert body.goal == "连续输错 5 次锁定账号"
    raw = next(item for item in session.added if isinstance(item, CaseRawNode))
    assert raw.raw_payload["core_nodes"]["case_title"]["rawText"] == "测试标题：连续输错 5 次锁定账号"
    assert raw.raw_payload["core_nodes"]["preconditions"]["rawText"] == "前置条件：账号 已注册"
    work_item = next(item for item in session.added if isinstance(item, CaseWorkItem))
    assert work_item.attention_reason == "变更待确认"
    assert work_item.assigned_user_id == 7
    assert work_item.case_type == "changed"
    assert work_item.execution_target == "manual"
    assert batch.case_count == 3
    assert session.committed is True


@pytest.mark.asyncio
async def test_create_case_asset_rejects_unknown_path() -> None:
    batch = SimpleNamespace(id=12, requirement_item_id=3, suite_title="登录测试集", case_count=1)
    existing_path = [{"level": 2, "label": "模块", "rawText": "账号", "displayText": "账号"}]
    existing_case = CaseAsset(
        id=1,
        batch_id=12,
        ordinal=1,
        suite_title="登录测试集",
        path_nodes=existing_path,
        module_name="账号",
        product_feature=None,
        test_feature=None,
        raw_title="已有 Case",
        clean_title="已有 Case",
        scenario_tags=[],
        manual=False,
        source_requirement_item_id=3,
    )

    class FakeCreateSession:
        async def get(self, model: object, identity: int) -> object | None:
            if model is ImportBatch and identity == 12:
                return batch
            return None

        async def execute(self, _statement: object) -> _RowsResult:
            return _RowsResult([(existing_case, SimpleNamespace(raw_payload={}))])

    with pytest.raises(ValueError, match="已存在的完整层级"):
        await case_assets.create_case_asset(
            FakeCreateSession(),  # type: ignore[arg-type]
            CaseAssetCreateIn(
                requirement_item_id=3,
                batch_id=12,
                path_nodes=[{"level": 2, "label": "模块", "rawText": "订单", "displayText": "订单"}],
                raw_title="新增 Case",
            ),
        )


@pytest.mark.asyncio
async def test_case_edit_preserves_execution_target_and_tagging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = SimpleNamespace(
        module_name="模块",
        product_feature="功能",
        test_feature="测试点",
        raw_title="原始标题",
        clean_title="旧标题",
        path_nodes=[],
        scenario_tags=[],
        manual=False,
        updated_at=None,
    )
    body = SimpleNamespace(
        goal="旧标题",
        preconditions="旧前置",
        steps_text="旧步骤",
        expected_result="旧预期",
    )
    work_item = SimpleNamespace(
        execution_status="failed",
        coverage={"android": "failed"},
        lifecycle_state="待人工干预",
        attention_reason=None,
        case_type="auto",
        execution_target="app",
        tag_source="model",
        tag_reason="原先模型判断为 App。",
        tag_confidence=73,
        report_url="http://report.local/case.html",
        failure_type="execution_failed",
        failure_summary="失败",
        bug_url="http://bug.local/1",
        bug_external_id="1",
        bugs=[{"url": "http://bug.local/1", "id": "1"}],
        active_execution_batch_id=9,
        external_submission_id="sub-1",
        execution_started_at=None,
        execution_finished_at=None,
        updated_at=None,
    )
    session = _FakeSession(case, body, work_item)

    monkeypatch.setattr(case_assets, "_replace_raw_node", lambda *_args: _noop())

    result = await case_assets.update_case_asset(
        session, 1, CaseAssetUpdateIn(raw_title="【改造】新标题", preconditions="新前置")
    )

    assert result is not None
    assert session.committed is True
    assert case.raw_title == "【改造】新标题"
    assert case.clean_title == "【改造】新标题"
    assert case.scenario_tags == ["改造"]
    assert work_item.execution_status == "not_run"
    assert work_item.execution_target == "app"
    assert work_item.case_type == "auto"
    assert work_item.tag_source == "model"
    assert work_item.tag_reason == "原先模型判断为 App。"
    assert work_item.tag_confidence == 73
    assert work_item.report_url is None
    assert work_item.coverage == {}


@pytest.mark.asyncio
async def test_case_edit_folds_multiline_core_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = SimpleNamespace(
        module_name="模块",
        product_feature="功能",
        test_feature="测试点",
        raw_title="原始标题",
        clean_title="旧标题",
        path_nodes=[],
        scenario_tags=[],
        manual=False,
        updated_at=None,
    )
    body = SimpleNamespace(
        goal="旧标题",
        preconditions="旧前置",
        steps_text="旧步骤",
        expected_result="旧预期",
    )
    work_item = SimpleNamespace(
        execution_status="failed",
        coverage={"android": "failed"},
        lifecycle_state="待人工干预",
        attention_reason=None,
        case_type="auto",
        execution_target="app",
        tag_source="model",
        tag_reason="原先模型判断为 App。",
        tag_confidence=73,
        report_url="http://report.local/case.html",
        failure_type="execution_failed",
        failure_summary="失败",
        bug_url="http://bug.local/1",
        bug_external_id="1",
        bugs=[{"url": "http://bug.local/1", "id": "1"}],
        active_execution_batch_id=9,
        external_submission_id="sub-1",
        execution_started_at=None,
        execution_finished_at=None,
        updated_at=None,
    )
    session = _FakeSession(case, body, work_item)

    monkeypatch.setattr(case_assets, "_replace_raw_node", lambda *_args: _noop())

    result = await case_assets.update_case_asset(
        session,
        1,
        CaseAssetUpdateIn(
            raw_title="登录\n成功",
            preconditions='参数结构为\n{\n  "name": "CRUD测试站点"\n}',
            steps_text="打开 App\n提交登录",
            expected_result="进入首页\n展示欢迎语",
        ),
    )

    assert result is not None
    assert session.committed is True
    assert case.clean_title == "登录 成功"
    assert body.goal == "登录 成功"
    assert body.preconditions == '参数结构为 { "name": "CRUD测试站点" }'
    assert body.steps_text == "打开 App 提交登录"
    assert body.expected_result == "进入首页 展示欢迎语"


@pytest.mark.asyncio
async def test_delete_case_suite_removes_batch_and_reports_impact() -> None:
    batch = SimpleNamespace(id=12, requirement_item_id=3, suite_title="登录冒烟")

    class FakeSuiteDeleteSession:
        def __init__(self) -> None:
            self.execute_calls = 0
            self.deleted: list[object] = []
            self.committed = False

        async def scalar(self, _statement: object) -> object:
            return batch

        async def execute(self, _statement: object) -> _ScalarOneResult:
            self.execute_calls += 1
            return _ScalarOneResult(5 if self.execute_calls == 1 else 2)

        async def delete(self, value: object) -> None:
            self.deleted.append(value)

        async def commit(self) -> None:
            self.committed = True

    session = FakeSuiteDeleteSession()

    result = await case_assets.delete_case_suite(session, 3, 12)  # type: ignore[arg-type]

    assert result is not None
    assert result.batch_id == 12
    assert result.deleted_case_count == 5
    assert result.deleted_running_count == 2
    assert result.deleted_batch_id == 12
    assert result.suite_title == "登录冒烟"
    assert session.deleted == [batch]
    assert session.committed is True


@pytest.mark.asyncio
async def test_deleted_case_callback_is_ignored_when_item_mapping_was_removed() -> None:
    batch = SimpleNamespace(
        id=9,
        submission_id="sub-1",
        raw_request={"items": [{"caseId": "cf-7"}]},
        raw_response={},
        raw_callback=None,
    )

    class FakeCallbackSession:
        def __init__(self) -> None:
            self.execute_calls = 0
            self.commits = 0

        async def execute(self, _statement: object) -> object:
            self.execute_calls += 1
            if self.execute_calls == 1:
                return _ScalarOneOrNoneResult(batch)
            return _ScalarsAllResult([])

        async def commit(self) -> None:
            self.commits += 1

    payload = {
        "submissionId": "sub-1",
        "caseId": "cf-7",
        "platform": "android",
        "state": "success",
    }
    session = FakeCallbackSession()

    result = await executions._apply_item_event(  # type: ignore[arg-type]
        session,
        "ai_phone",
        "callback-token",
        payload,
        "submission.item.terminal",
    )

    assert result["handled"] is True
    assert result["ignored"] is True
    assert result["ignore_reason"] == "case_asset_deleted"
    assert result["updated_case_ids"] == []
    assert batch.raw_callback == payload
    assert session.commits == 1


async def _noop() -> None:
    return None
