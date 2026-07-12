from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.executions import _case_to_run_content as standard_run_content
from app.services.quick_executions import _case_to_run_content as quick_run_content


def test_standard_execution_rebuilds_all_four_markdown_field_labels() -> None:
    case = SimpleNamespace(raw_title="【改造】多层弹层返回")
    body = SimpleNamespace(
        preconditions="有两层弹层",
        steps_text="连续点击返回",
        expected_result="逐层关闭直至退页",
    )

    assert standard_run_content(case, body) == (
        "测试标题：【改造】多层弹层返回\n\n"
        "前置条件：有两层弹层\n\n"
        "操作步骤：连续点击返回\n\n"
        "预期结果：逐层关闭直至退页"
    )


def test_quick_execution_rebuilds_all_four_markdown_field_labels() -> None:
    case = SimpleNamespace(raw_title="【改造】多层弹层返回")
    body = SimpleNamespace(
        preconditions="有两层弹层",
        steps_text="连续点击返回",
        expected_result="逐层关闭直至退页",
    )

    assert quick_run_content(case, body) == (
        "测试标题：【改造】多层弹层返回\n\n"
        "前置条件：有两层弹层\n\n"
        "操作步骤：连续点击返回\n\n"
        "预期结果：逐层关闭直至退页"
    )


@pytest.mark.parametrize("render", [standard_run_content, quick_run_content])
def test_execution_stops_instead_of_hiding_a_missing_title(render) -> None:
    case = SimpleNamespace(raw_title="")
    body = SimpleNamespace(preconditions="", steps_text="", expected_result="")

    with pytest.raises(ValueError, match="缺少完整测试标题"):
        render(case, body)
