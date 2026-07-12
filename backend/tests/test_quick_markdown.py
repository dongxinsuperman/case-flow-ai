from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.quick import QuickCase, QuickCaseBody, QuickCaseWorkItem, QuickSession
from app.schemas.quick import QuickCaseUpdateIn, QuickImportIn
from app.services import quick_importing
from app.services.quick_markdown import QuickParseError, parse_quick_markdown, render_quick_markdown


class _QuickEditSession:
    def __init__(self, case: object, body: object, work_item: object, quick_session: object) -> None:
        self.case = case
        self.body = body
        self.work_item = work_item
        self.quick_session = quick_session
        self.committed = False
        self.added_steps: list[object] = []

    async def get(self, model: object, _identity: object) -> object | None:
        if model is QuickCase:
            return self.case
        if model is QuickCaseBody:
            return self.body
        if model is QuickCaseWorkItem:
            return self.work_item
        if model is QuickSession:
            return self.quick_session
        return None

    async def execute(self, _statement: object) -> None:
        return None

    def add_all(self, values: object) -> None:
        self.added_steps.extend(list(values))

    async def commit(self) -> None:
        self.committed = True


class _QuickImportSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = False

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


def test_quick_markdown_parses_dynamic_path_with_last_four_core_levels() -> None:
    parsed = parse_quick_markdown(
        """
- 支付流程
  - 订单
    - 支付
      - 测试标题：支付成功
        - 前置条件：用户已登录
          - 操作步骤：提交订单
            - 预期结果：支付成功
""",
        "pay.md",
    )

    case = parsed.cases[0]
    assert parsed.suite_title == "支付流程"
    assert case.path_nodes == [
        {"level": 2, "label": "层级1", "rawText": "订单", "displayText": "订单"},
        {"level": 3, "label": "层级2", "rawText": "支付", "displayText": "支付"},
    ]
    assert case.clean_title == "支付成功"
    assert case.preconditions == "用户已登录"
    assert case.steps_text == "提交订单"
    assert case.expected_result == "支付成功"


def test_quick_markdown_fails_globally_on_broken_structure() -> None:
    with pytest.raises(QuickParseError) as exc:
        parse_quick_markdown(
            """
- 支付流程
  - 测试标题：支付成功
    - 前置条件：用户已登录
      - 操作步骤：提交订单
""",
            "broken.md",
        )

    assert "分支不足 5 级" in str(exc.value)


def test_quick_markdown_renders_back_to_markdown() -> None:
    content = render_quick_markdown(
        "支付流程",
        [
            {
                "path_nodes": [
                    {"label": "层级1", "displayText": "订单"},
                    {"label": "层级2", "displayText": "支付"},
                ],
                "core_nodes": {
                    "case_title": {"label": "测试标题", "trimmed": True, "separator": "："},
                    "preconditions": {"label": "前置条件", "trimmed": True, "separator": "："},
                    "steps": {"label": "操作步骤", "trimmed": True, "separator": "："},
                    "expected": {"label": "预期结果", "trimmed": True, "separator": "："},
                },
                "raw_title": "支付成功",
                "clean_title": "支付成功",
                "preconditions": "用户已登录",
                "steps_text": "提交订单",
                "expected_result": "支付成功",
            }
        ],
    )

    assert "- 支付流程" in content
    assert "  - 订单" in content
    assert "      - 测试标题：支付成功" in content
    assert "            - 预期结果：支付成功" in content


def test_quick_markdown_keeps_title_tags_when_exporting() -> None:
    parsed = parse_quick_markdown(
        """
- 返回测试
  - 完成页
    - 测试标题：【改造】逐层返回
      - 前置条件：有两层弹层
        - 操作步骤：连续返回
          - 预期结果：逐层关闭
""",
        "return.md",
    )
    case = parsed.cases[0]

    assert case.raw_title == "【改造】逐层返回"
    assert case.clean_title == "【改造】逐层返回"
    assert case.scenario_tags == ["改造"]

    content = render_quick_markdown("返回测试", [case.__dict__])
    assert "测试标题：【改造】逐层返回" in content


def test_quick_markdown_export_rejects_missing_raw_title() -> None:
    with pytest.raises(ValueError, match="缺少完整原始标题"):
        render_quick_markdown(
            "返回测试",
            [{"path_nodes": [], "core_nodes": {}, "clean_title": "逐层返回"}],
        )


def test_quick_markdown_import_folds_continuation_lines_into_core_fields() -> None:
    parsed = parse_quick_markdown(
        """
- 支付流程
  - 订单
    - 支付
      - 测试标题：创建站点
        - 前置条件：参数结构为
{
  "name": "CRUD测试站点",
  "url": "https://example.com"
}
          - 操作步骤：提交创建请求
            - 预期结果：200，可创建
""",
        "quick.md",
    )

    case = parsed.cases[0]
    assert case.preconditions == '参数结构为 { "name": "CRUD测试站点", "url": "https://example.com" }'
    assert case.steps_text == "提交创建请求"


def test_quick_markdown_export_folds_multiline_fields_and_round_trips() -> None:
    content = render_quick_markdown(
        "支付流程",
        [
            {
                "path_nodes": [{"label": "层级1", "displayText": "订单\n支付"}],
                "core_nodes": {
                    "case_title": {"label": "测试标题", "trimmed": True, "separator": "："},
                    "preconditions": {"label": "前置条件", "trimmed": True, "separator": "："},
                    "steps": {"label": "操作步骤", "trimmed": True, "separator": "："},
                    "expected": {"label": "预期结果", "trimmed": True, "separator": "："},
                },
                "raw_title": "支付\n成功",
                "clean_title": "支付\n成功",
                "preconditions": '参数结构为\n{\n  "name": "CRUD测试站点"\n}',
                "steps_text": "提交订单\n确认支付",
                "expected_result": "支付成功\n进入结果页",
            }
        ],
    )

    assert all(line.lstrip().startswith("- ") for line in content.splitlines() if line.strip())

    parsed = parse_quick_markdown(content, "roundtrip.md")
    case = parsed.cases[0]
    assert case.path_nodes[0]["displayText"] == "订单 支付"
    assert case.clean_title == "支付 成功"
    assert case.preconditions == '参数结构为 { "name": "CRUD测试站点" }'
    assert case.steps_text == "提交订单 确认支付"
    assert case.expected_result == "支付成功 进入结果页"


@pytest.mark.asyncio
async def test_quick_case_update_folds_multiline_core_fields() -> None:
    case = SimpleNamespace(
        id=7,
        session_id="quick-1",
        clean_title="旧标题",
        raw_title="旧标题",
        path_nodes=[],
        core_nodes={
            "case_title": {"label": "测试标题", "trimmed": True, "separator": "："},
            "preconditions": {"label": "前置条件", "trimmed": True, "separator": "："},
            "steps": {"label": "操作步骤", "trimmed": True, "separator": "："},
            "expected": {"label": "预期结果", "trimmed": True, "separator": "："},
        },
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
        execution_target="web",
        tag_source="model",
        tag_reason="模型判断为 Web，用户随后确认。",
        tag_confidence=73,
        report_url="http://report.local",
        failure_type="execution_failed",
        failure_summary="失败",
        bug_url="http://bug.local",
        bug_external_id="BUG-1",
        bugs=[{"url": "http://bug.local", "id": "BUG-1"}],
        active_execution_batch_id=1,
        external_submission_id="sub-1",
        execution_started_at=None,
        execution_finished_at=None,
        updated_at=None,
        run_enabled=True,
    )
    quick_session = SimpleNamespace(session_id="quick-1", updated_at=None)
    session = _QuickEditSession(case, body, work_item, quick_session)

    result = await quick_importing.update_case(
        session, 7, QuickCaseUpdateIn(
            raw_title="【改造】支付\n成功",
            preconditions='参数结构为\n{\n  "name": "CRUD测试站点"\n}',
            steps_text="提交订单\n确认支付",
            expected_result="支付成功\n进入结果页",
        )
    )

    assert result is not None
    assert session.committed is True
    assert body.goal == "【改造】支付 成功"
    assert case.raw_title == "【改造】支付 成功"
    assert case.clean_title == "【改造】支付 成功"
    assert case.scenario_tags == ["改造"]
    assert case.core_nodes["case_title"]["rawText"] == "测试标题：【改造】支付 成功"
    assert body.preconditions == '参数结构为 { "name": "CRUD测试站点" }'
    assert body.steps_text == "提交订单 确认支付"
    assert body.expected_result == "支付成功 进入结果页"
    assert case.core_nodes["steps"]["rawText"] == "操作步骤：提交订单 确认支付"
    assert work_item.execution_status == "not_run"
    assert work_item.execution_target == "web"
    assert work_item.case_type == "auto"
    assert work_item.tag_source == "model"
    assert work_item.tag_reason == "模型判断为 Web，用户随后确认。"
    assert work_item.tag_confidence == 73


@pytest.mark.asyncio
async def test_quick_import_uses_shared_case_tagging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_inputs: list[dict[str, object]] = []
    inserted: list[tuple[str, str, dict[str, object]]] = []

    async def fake_new_session_id(_session: object) -> str:
        return "quick-shared-tagging"

    async def fake_classify_cases(cases: list[dict[str, object]]) -> list[dict[str, object]]:
        seen_inputs.extend(cases)
        return [
            {
                "case_type": "auto",
                "execution_target": "api",
                "tag_source": "content_rule",
                "tag_reason": "接口规则命中。",
                "tag_confidence": 82,
            },
            {
                "case_type": "manual",
                "execution_target": "manual",
                "tag_source": "manual_fallback",
                "tag_reason": "规则和模型未给出可用判断。",
                "tag_confidence": 35,
            },
        ]

    async def fake_insert_case(
        _session: object,
        session_id: str,
        parsed_case: object,
        tag: dict[str, object],
    ) -> int:
        inserted.append((session_id, parsed_case.clean_title, tag))
        return len(inserted)

    async def fake_session_summary(_session: object, session_id: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "source_name": "quick.md",
            "suite_title": "快速导入",
            "case_count": len(inserted),
            "function_files": [],
        }

    async def fake_list_cases(_session: object, _session_id: str) -> list[object]:
        return []

    monkeypatch.setattr(quick_importing, "_new_session_id", fake_new_session_id)
    monkeypatch.setattr(quick_importing.case_tagging, "classify_cases", fake_classify_cases)
    monkeypatch.setattr(quick_importing, "_insert_case", fake_insert_case)
    monkeypatch.setattr(quick_importing, "_session_summary", fake_session_summary)
    monkeypatch.setattr(quick_importing, "list_cases", fake_list_cases)

    session = _QuickImportSession()
    result = await quick_importing.create_session_from_markdown(
        session,
        QuickImportIn(
            filename="quick.md",
            content="""
- 快速导入
  - 接口
    - 测试标题：登录接口
      - 前置条件：用户存在
        - 操作步骤：POST /api/login
          - 预期结果：返回 200
  - 兜底
    - 测试标题：查看记录
      - 前置条件：用户已登录
        - 操作步骤：进入页面查看
          - 预期结果：展示正常
""",
        ),
    )

    assert session.committed is True
    assert result.session.session_id == "quick-shared-tagging"
    assert [item["clean_title"] for item in seen_inputs] == ["登录接口", "查看记录"]
    assert seen_inputs[0]["steps_text"] == "POST /api/login"
    assert [(session_id, title, tag["execution_target"]) for session_id, title, tag in inserted] == [
        ("quick-shared-tagging", "登录接口", "api"),
        ("quick-shared-tagging", "查看记录", "manual"),
    ]
