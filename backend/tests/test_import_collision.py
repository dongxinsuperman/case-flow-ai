from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.importing import ImportMarkdownIn
from app.models.case_assets import CaseAsset
from app.services import case_matching, importing
from app.services.markdown_import_config import parse_markdown_import_config
from app.services.markdown_parser import ParsedMarkdown, parse_markdown


@pytest.fixture(autouse=True)
def disable_model_matching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_matching, "enrich_with_model", lambda review_items: None)


def parse_cases(body: str) -> ParsedMarkdown:
    return parse_markdown(body, "cases.md")


def test_parser_keeps_default_8_level_projection() -> None:
    parsed = parse_cases(BASE_TWO_CASES)
    first = parsed.cases[0]

    assert first.path_nodes == [
        {"level": 2, "label": "模块", "rawText": "模块A", "displayText": "模块A"},
        {"level": 3, "label": "功能点", "rawText": "功能A", "displayText": "功能A"},
        {"level": 4, "label": "测试功能点", "rawText": "测试点A", "displayText": "测试点A"},
    ]
    assert first.raw_title == "进入页面显示卡片"
    assert first.preconditions == "用户已登录"
    assert first.steps_text == "打开 App、进入学习页"
    assert first.expected_result == "展示学习卡片"


def test_parser_treats_priority_marker_as_plain_path_text() -> None:
    parsed = parse_markdown(
        """
- 学习方法 V1.1 测试用例
  - 模块A(P0)
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
""",
        "cases.md",
    )
    case = parsed.cases[0]

    assert case.path_nodes[0]["displayText"] == "模块A(P0)"
    assert case.module_name == "模块A(P0)"


def test_parser_supports_dynamic_level_config_and_half_width_separator() -> None:
    config = parse_markdown_import_config(
        {
            "levels": [
                {"index": 1, "role": "suite", "displayLabel": "测试集合"},
                {"index": 2, "role": "path", "displayLabel": "业务线"},
                {"index": 3, "role": "case_title", "displayLabel": "用例名"},
                {"index": 4, "role": "preconditions", "displayLabel": "前提"},
                {"index": 5, "role": "steps", "displayLabel": "步骤"},
                {"index": 6, "role": "expected", "displayLabel": "断言"},
            ],
            "trimSeparators": ["：", ":"],
        }
    )
    parsed = parse_markdown(
        """
- 登录测试
  - 账号体系
    - 用例名:手机号验证码登录成功
      - 前提:用户已注册
        - 步骤:打开 App、输入手机号、输入验证码
          - 断言:进入首页
""",
        "dynamic.md",
        config,
    )

    case = parsed.cases[0]
    assert case.path_nodes == [
        {"level": 2, "label": "业务线", "rawText": "账号体系", "displayText": "账号体系"},
    ]
    assert case.core_nodes["case_title"]["rawText"] == "用例名:手机号验证码登录成功"
    assert case.raw_title == "手机号验证码登录成功"
    assert case.preconditions == "用户已注册"
    assert case.expected_result == "进入首页"


def existing_rows(parsed: ParsedMarkdown) -> list[dict]:
    rows = []
    for case in parsed.cases:
        row = case_matching.parsed_case_to_dict(case)
        row["id"] = case.ordinal
        rows.append(row)
    return rows


async def _noop() -> None:
    return None


BASE_TWO_CASES = """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
        - 测试标题：点击更多跳转详情页
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页、点击更多
              - 预期结果：进入详情页
"""


def test_exact_cases_are_skipped() -> None:
    original = parse_cases(BASE_TWO_CASES)

    review = case_matching.build_import_review(original, existing_rows(original))

    assert review["exact_count"] == 2
    assert review["review_count"] == 0
    assert review["delete_count"] == 0


def test_title_tags_are_kept_and_participate_in_collision() -> None:
    original = parse_cases(
        """
- 返回测试
  - 完成页
    - 弹层
      - 返回
        - 测试标题：【回归】【重点】逐层返回
          - 前置条件：有两层弹层
            - 操作步骤：连续返回
              - 预期结果：逐层关闭
"""
    )
    changed = parse_cases(
        """
- 返回测试
  - 完成页
    - 弹层
      - 返回
        - 测试标题：【改造】【重点】逐层返回
          - 前置条件：有两层弹层
            - 操作步骤：连续返回
              - 预期结果：逐层关闭
"""
    )

    original_case = original.cases[0]
    assert original_case.raw_title == "【回归】【重点】逐层返回"
    assert original_case.clean_title == "【回归】【重点】逐层返回"
    assert original_case.scenario_tags == ["回归", "重点"]

    review = case_matching.build_import_review(changed, existing_rows(original))

    assert review["exact_count"] == 0
    assert review["review_count"] == 1
    assert review["review_items"][0]["incoming"]["raw_title"] == "【改造】【重点】逐层返回"
    assert "标题" in review["review_items"][0]["candidates"][0]["change_hint"]


def test_title_suffix_changes_lock_one_to_one_without_delete_candidates() -> None:
    original = parse_cases(BASE_TWO_CASES)
    changed_content = """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片1
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
        - 测试标题：点击更多跳转详情页2
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页、点击更多
              - 预期结果：进入详情页
"""
    changed = parse_cases(changed_content)

    review = case_matching.build_import_review(changed, existing_rows(original))

    assert review["exact_count"] == 0
    assert review["review_count"] == 2
    assert review["delete_count"] == 0
    assert {item["primary_old_case_id"] for item in review["review_items"]} == {1, 2}


def test_missing_old_case_becomes_delete_candidate() -> None:
    original = parse_cases(BASE_TWO_CASES)
    changed = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
"""
    )

    review = case_matching.build_import_review(changed, existing_rows(original))

    assert review["exact_count"] == 1
    assert review["review_count"] == 0
    assert review["delete_count"] == 1
    assert review["delete_items"][0]["old_case_id"] == 2


def test_extra_new_case_requires_review_without_delete_candidate() -> None:
    original = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
"""
    )
    changed = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
  - 模块B
    - 功能B
      - 测试点B
        - 测试标题：后台人工核对学习记录
          - 前置条件：运营后台存在学习记录
            - 操作步骤：人工打开后台，搜索用户记录并核对字段
              - 预期结果：后台记录与 App 展示一致
"""
    )

    review = case_matching.build_import_review(changed, existing_rows(original))

    assert review["exact_count"] == 1
    assert review["review_count"] == 1
    assert review["delete_count"] == 0
    assert review["review_items"][0].get("primary_old_case_id") is None


@pytest.mark.asyncio
async def test_import_same_suite_different_filename_does_not_collide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str]] = []

    async def fake_get_batch(_session: object, requirement_item_id: int, source_name: str) -> None:
        calls.append((requirement_item_id, source_name))
        return None

    async def fake_requirement_has_batches(_session: object, _requirement_item_id: int) -> bool:
        return True

    async def fail_replace_import(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("different filenames must not replace or collide without confirmation")

    monkeypatch.setattr(importing, "_ensure_requirement_exists", lambda *_args: _noop())
    monkeypatch.setattr(importing, "get_import_batch_by_source", fake_get_batch)
    monkeypatch.setattr(importing, "requirement_has_batches", fake_requirement_has_batches)
    monkeypatch.setattr(importing, "replace_import", fail_replace_import)

    result = await importing.import_markdown(
        SimpleNamespace(),
        ImportMarkdownIn(requirement_item_id=7, filename="new-file.md", content=BASE_TWO_CASES),
    )

    assert result.mode == "independent_confirm_required"
    assert calls == [(7, "new-file.md")]


@pytest.mark.asyncio
async def test_import_same_filename_different_suite_title_still_collides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = parse_cases(BASE_TWO_CASES)
    changed_content = """
- 学习方法 V2 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片1
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
"""
    batch = SimpleNamespace(
        id=31,
        suite_title=original.suite_title,
        source_name="cases.md",
        requirement_item_id=7,
        imported_at=None,
        case_count=2,
        raw_metadata={},
    )

    async def fake_get_batch(_session: object, requirement_item_id: int, source_name: str) -> object:
        assert (requirement_item_id, source_name) == (7, "cases.md")
        return batch

    async def fake_list_cases(_session: object, batch_id: int) -> list[dict]:
        assert batch_id == 31
        return existing_rows(original)

    monkeypatch.setattr(importing, "_ensure_requirement_exists", lambda *_args: _noop())
    monkeypatch.setattr(importing, "get_import_batch_by_source", fake_get_batch)
    monkeypatch.setattr(importing, "list_cases_for_batch", fake_list_cases)

    result = await importing.import_markdown(
        SimpleNamespace(),
        ImportMarkdownIn(requirement_item_id=7, filename="cases.md", content=changed_content),
    )

    assert result.mode == "collision_review"
    assert result.review is not None
    assert result.review["suite_title"] == "学习方法 V2 测试用例"
    assert result.review["review_count"] == 1


@pytest.mark.asyncio
async def test_import_same_filename_same_cases_syncs_suite_title_without_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = parse_cases(BASE_TWO_CASES)
    retitled_content = BASE_TWO_CASES.replace("学习方法 V1.1 测试用例", "学习方法 V2 测试用例")
    batch = SimpleNamespace(
        id=31,
        suite_title=original.suite_title,
        source_name="cases.md",
        requirement_item_id=7,
        imported_at=None,
        case_count=2,
        raw_metadata={},
    )
    synced: list[str] = []

    async def fake_get_batch(_session: object, requirement_item_id: int, source_name: str) -> object:
        assert (requirement_item_id, source_name) == (7, "cases.md")
        return batch

    async def fake_list_cases(_session: object, batch_id: int) -> list[dict]:
        assert batch_id == 31
        return existing_rows(original)

    async def fake_update_title(_session: object, batch_obj: object, suite_title: str) -> bool:
        assert batch_obj is batch
        synced.append(suite_title)
        return True

    monkeypatch.setattr(importing, "_ensure_requirement_exists", lambda *_args: _noop())
    monkeypatch.setattr(importing, "get_import_batch_by_source", fake_get_batch)
    monkeypatch.setattr(importing, "list_cases_for_batch", fake_list_cases)
    monkeypatch.setattr(importing, "update_batch_suite_title", fake_update_title)

    result = await importing.import_markdown(
        SimpleNamespace(),
        ImportMarkdownIn(requirement_item_id=7, filename="cases.md", content=retitled_content),
    )

    assert result.mode == "no_changes"
    assert result.suite_title == "学习方法 V2 测试用例"
    assert synced == ["学习方法 V2 测试用例"]


def test_moved_case_can_still_lock_to_original_case() -> None:
    original = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 原模块
    - 原功能
      - 原测试点
        - 测试标题：点击更多跳转详情页
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页、点击更多
              - 预期结果：进入详情页
"""
    )
    moved = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 新模块
    - 新功能
      - 新测试点
        - 测试标题：点击更多跳转详情页
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页、点击更多
              - 预期结果：进入详情页
"""
    )

    review = case_matching.build_import_review(moved, existing_rows(original))

    assert review["review_count"] == 1
    assert review["delete_count"] == 0
    assert review["review_items"][0]["primary_old_case_id"] == 1


def test_same_title_but_different_intent_stays_unlocked_and_keeps_old_delete_choice() -> None:
    original = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
"""
    )
    changed = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：后台已下架全部课程
            - 操作步骤：打开管理后台，删除课程配置，再打开学习页
              - 预期结果：页面展示空态和刷新入口
"""
    )

    review = case_matching.build_import_review(changed, existing_rows(original))

    assert review["review_count"] == 1
    assert review["review_items"][0].get("primary_old_case_id") is None
    assert review["delete_count"] == 1
    assert review["delete_items"][0]["old_case_id"] == 1


def test_one_old_case_can_only_be_locked_by_one_incoming_case() -> None:
    original = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：点击更多跳转详情页
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页、点击更多
              - 预期结果：进入详情页
"""
    )
    changed = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：点击更多跳转详情页1
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页、点击更多
              - 预期结果：进入详情页
        - 测试标题：点击更多跳转详情页2
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页、点击更多
              - 预期结果：进入详情页
"""
    )

    review = case_matching.build_import_review(changed, existing_rows(original))
    locked_items = [item for item in review["review_items"] if item.get("primary_old_case_id")]
    unlocked_items = [item for item in review["review_items"] if not item.get("primary_old_case_id")]

    assert review["review_count"] == 2
    assert review["delete_count"] == 0
    assert len(locked_items) == 1
    assert locked_items[0]["primary_old_case_id"] == 1
    assert len(unlocked_items) == 1


def test_commit_decision_rejects_replace_with_non_primary_case() -> None:
    original = parse_cases(BASE_TWO_CASES)
    changed = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片1
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
        - 测试标题：点击更多跳转详情页2
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页、点击更多
              - 预期结果：进入详情页
"""
    )
    review = case_matching.build_import_review(changed, existing_rows(original))
    first, second = review["review_items"]

    with pytest.raises(ValueError, match="1:1 主候选"):
        importing.validate_import_review_decisions(
            review,
            [
                {
                    "incoming_key": first["incoming_key"],
                    "old_case_id": second["primary_old_case_id"],
                    "action": "replace",
                },
                {
                    "incoming_key": second["incoming_key"],
                    "old_case_id": second["primary_old_case_id"],
                    "action": "replace",
                },
            ],
        )


def test_commit_decision_rejects_delete_case_outside_delete_candidates() -> None:
    original = parse_cases(BASE_TWO_CASES)
    changed = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
"""
    )
    review = case_matching.build_import_review(changed, existing_rows(original))

    with pytest.raises(ValueError, match="不属于本次碰撞"):
        importing.validate_import_review_decisions(
            review,
            [
                {"old_case_id": review["delete_items"][0]["old_case_id"], "action": "keep"},
                {"old_case_id": 999, "action": "delete"},
            ],
        )


@pytest.mark.asyncio
async def test_apply_import_review_keep_old_case_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = parse_cases(
        """
- 学习方法 V1.1 测试用例
  - 模块A
    - 功能A
      - 测试点A
        - 测试标题：进入页面显示卡片
          - 前置条件：用户已登录
            - 操作步骤：打开 App、进入学习页
              - 预期结果：展示学习卡片
"""
    )
    batch = SimpleNamespace(
        id=10,
        requirement_item_id=3,
        source_name="cases.md",
        suite_title="旧测试集",
        imported_at=None,
        case_count=2,
        raw_metadata={},
    )
    old_case = SimpleNamespace(id=2, batch_id=10, ordinal=2)
    ensure_calls: list[tuple[list[int], list[int]]] = []

    class _ScalarOneOrNoneResult:
        def __init__(self, value: object) -> None:
            self.value = value

        def scalar_one_or_none(self) -> object:
            return self.value

    class _ScalarOneResult:
        def __init__(self, value: int) -> None:
            self.value = value

        def scalar_one(self) -> int:
            return self.value

    class _Session:
        def __init__(self) -> None:
            self.execute_count = 0
            self.deleted: list[object] = []
            self.committed = False

        async def execute(self, _statement: object) -> object:
            self.execute_count += 1
            if self.execute_count == 1:
                return _ScalarOneOrNoneResult(batch)
            if self.execute_count in {2, 3}:
                return _ScalarOneResult(2)
            return None

        async def get(self, model: object, identity: int) -> object | None:
            if model is CaseAsset and identity == 2:
                return old_case
            return None

        async def delete(self, value: object) -> None:
            self.deleted.append(value)

        async def commit(self) -> None:
            self.committed = True

        async def refresh(self, _value: object) -> None:
            return None

    async def fake_reorder(*_args: object) -> None:
        return None

    async def fake_ensure(
        _session: object,
        *,
        case_ids: list[int] | set[int] | None = None,
        changed_case_ids: list[int] | set[int] | None = None,
    ) -> None:
        ensure_calls.append((list(case_ids or []), list(changed_case_ids or [])))

    monkeypatch.setattr(importing, "reorder_batch_cases_from_parsed", fake_reorder)
    monkeypatch.setattr(importing, "ensure_case_work_items", fake_ensure)

    session = _Session()
    result = await importing.apply_import_review(
        session,  # type: ignore[arg-type]
        parsed,
        3,
        [{"old_case_id": 2, "action": "keep"}],
    )

    assert result is batch
    assert batch.raw_metadata["applied"]["keep"] == 1
    assert ensure_calls == [([], [])]
    assert session.deleted == []
    assert session.committed is True
