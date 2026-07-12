from __future__ import annotations

from types import SimpleNamespace

from app.services.workbench import _case_to_out, _display_numbers


def test_display_numbers_single_suite_plain() -> None:
    # 单测试集：纯数字 1,2,3
    assert _display_numbers([7, 7, 7], [1, 2, 3]) == ["1", "2", "3"]


def test_display_numbers_multi_suite_banded() -> None:
    # 多测试集：集号-集内序号，集号按出现顺序，集内沿用各自序号
    assert _display_numbers([7, 7, 9, 9], [1, 2, 1, 2]) == ["1-1", "1-2", "2-1", "2-2"]


def test_workbench_card_exposes_failure_classification() -> None:
    case = SimpleNamespace(
        id=1,
        ordinal=1,
        suite_title="测试集",
        status="imported",
        module_name="模块",
        product_feature="功能点",
        test_feature="测试功能点",
        raw_title="原始标题",
        clean_title="测试标题",
        scenario_tags=[],
        manual=False,
    )
    body = SimpleNamespace(
        preconditions="前置条件",
        steps_text="操作步骤",
        expected_result="预期结果",
    )
    work_item = SimpleNamespace(
        execution_status="failed",
        lifecycle_state="待人工干预",
        attention_reason="执行失败",
        case_type="auto",
        execution_target="app",
        tag_source="content_rule",
        tag_reason="",
        tag_confidence=80,
        run_enabled=True,
        report_url="http://127.0.0.1/report.html",
        failure_type="business_failure",
        failure_summary="模型分析认为可能是业务失败",
        bug_url=None,
        external_submission_id=None,
        execution_started_at=None,
        execution_finished_at=None,
    )
    batch = SimpleNamespace(id=7, source_name="case.md")

    item = _case_to_out(case, body, work_item, batch)

    assert item.batch_id == 7
    assert item.execution_status == "failed"
    # 新语义：attention 只表示“变更待确认”，失败统一走 failure_type，不再以 attention 表达。
    assert item.attention_reason is None
    assert item.failure_type == "业务失败"


def test_workbench_card_hides_non_evidence_failure_classification() -> None:
    case = SimpleNamespace(
        id=1,
        ordinal=1,
        suite_title="测试集",
        status="imported",
        module_name="模块",
        product_feature="功能点",
        test_feature="测试功能点",
        raw_title="原始标题",
        clean_title="测试标题",
        scenario_tags=[],
        manual=False,
    )
    body = SimpleNamespace(
        preconditions="前置条件",
        steps_text="操作步骤",
        expected_result="预期结果",
    )
    work_item = SimpleNamespace(
        execution_status="failed",
        lifecycle_state="待人工干预",
        attention_reason="执行失败",
        case_type="auto",
        execution_target="app",
        tag_source="content_rule",
        tag_reason="",
        tag_confidence=80,
        run_enabled=True,
        report_url=None,
        failure_type="model_unavailable",
        failure_summary="模型不可用",
        bug_url=None,
        external_submission_id=None,
        execution_started_at=None,
        execution_finished_at=None,
    )
    batch = SimpleNamespace(id=7, source_name="case.md")

    item = _case_to_out(case, body, work_item, batch)

    assert item.execution_status == "failed"
    assert item.failure_type is None
