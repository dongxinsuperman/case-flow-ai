from __future__ import annotations

import pytest

from app.services.markdown_export import render_standard_markdown
from app.services.markdown_parser import parse_markdown


def test_standard_markdown_export_restores_matched_prefixes_without_duplication() -> None:
    content = render_standard_markdown(
        "登录测试",
        [
            {
                "path_nodes": [
                    {"label": "模块", "displayText": "账号"},
                    {"label": "功能点", "displayText": "验证码登录"},
                    {"label": "测试功能点", "displayText": "成功路径"},
                ],
                "core_nodes": {
                    "case_title": {"label": "测试标题", "rawText": "测试标题：登录成功"},
                    "preconditions": {"label": "前置条件", "rawText": "前置条件:用户已注册"},
                    "steps": {"label": "操作步骤", "rawText": "操作步骤：打开 App"},
                    "expected": {"label": "预期结果", "rawText": "预期结果：进入首页"},
                },
                "raw_title": "登录成功后进入首页",
                "clean_title": "登录成功后进入首页",
                "preconditions": "前置条件：用户已注册且有验证码",
                "steps_text": "打开 App、输入手机号、输入验证码",
                "expected_result": "进入首页",
            }
        ],
    )

    assert content == (
        "- 登录测试\n"
        "  - 账号\n"
        "    - 验证码登录\n"
        "      - 成功路径\n"
        "        - 测试标题：登录成功后进入首页\n"
        "          - 前置条件:用户已注册且有验证码\n"
        "            - 操作步骤：打开 App、输入手机号、输入验证码\n"
        "              - 预期结果：进入首页\n"
    )


def test_standard_markdown_export_keeps_leading_title_tags() -> None:
    content = render_standard_markdown(
        "返回测试",
        [
            {
                "path_nodes": [{"label": "模块", "displayText": "完成页"}],
                "core_nodes": {
                    "case_title": {"label": "测试标题", "rawText": "测试标题：【改造】逐层返回"},
                    "preconditions": {"label": "前置条件", "rawText": "前置条件：有两层弹层"},
                    "steps": {"label": "操作步骤", "rawText": "操作步骤：连续返回"},
                    "expected": {"label": "预期结果", "rawText": "预期结果：逐层关闭"},
                },
                "raw_title": "【改造】逐层返回",
                "clean_title": "逐层返回",
                "preconditions": "有两层弹层",
                "steps_text": "连续返回",
                "expected_result": "逐层关闭",
            }
        ],
    )

    assert "测试标题：【改造】逐层返回" in content


def test_standard_markdown_export_rejects_missing_raw_title() -> None:
    with pytest.raises(ValueError, match="缺少完整原始标题"):
        render_standard_markdown(
            "返回测试",
            [{"path_nodes": [], "core_nodes": {}, "clean_title": "逐层返回"}],
        )


def test_standard_markdown_export_does_not_add_prefix_when_source_had_none() -> None:
    content = render_standard_markdown(
        "登录测试",
        [
            {
                "path_nodes": [{"label": "模块", "displayText": "账号"}],
                "core_nodes": {
                    "case_title": {"label": "测试标题", "rawText": "登录成功", "trimmed": False},
                    "preconditions": {"label": "前置条件", "rawText": "用户已注册", "trimmed": False},
                    "steps": {"label": "操作步骤", "rawText": "打开 App", "trimmed": False},
                    "expected": {"label": "预期结果", "rawText": "进入首页", "trimmed": False},
                },
                "raw_title": "登录成功后进入首页",
                "clean_title": "登录成功后进入首页",
                "preconditions": "用户已注册且有验证码",
                "steps_text": "打开 App、输入手机号、输入验证码",
                "expected_result": "进入首页",
            }
        ],
    )

    assert "    - 登录成功后进入首页\n" in content
    assert "      - 用户已注册且有验证码\n" in content
    assert "测试标题：" not in content
    assert "前置条件：" not in content


def test_standard_markdown_import_folds_continuation_lines_into_core_fields() -> None:
    parsed = parse_markdown(
        """
- 学习方法V1.1测试用例
  - 接口
    - 站点
      - 创建
        - 测试标题：验证创建结构
          - 前置条件：参数结构为
{
  "name": "CRUD测试站点",
  "url": "https://example.com"
}
            - 操作步骤：提交创建请求
              - 预期结果：200，可创建
""",
        "cases.md",
    )

    case = parsed.cases[0]
    assert case.preconditions == '参数结构为 { "name": "CRUD测试站点", "url": "https://example.com" }'
    assert case.steps_text == "提交创建请求"


def test_standard_markdown_export_folds_multiline_fields_and_round_trips() -> None:
    content = render_standard_markdown(
        "登录测试",
        [
            {
                "path_nodes": [
                    {"label": "模块", "displayText": "账号\n登录"},
                    {"label": "功能点", "displayText": "验证码"},
                    {"label": "测试功能点", "displayText": "成功路径"},
                ],
                "core_nodes": {
                    "case_title": {"label": "测试标题", "rawText": "测试标题：登录成功"},
                    "preconditions": {"label": "前置条件", "rawText": "前置条件：用户已注册"},
                    "steps": {"label": "操作步骤", "rawText": "操作步骤：打开 App"},
                    "expected": {"label": "预期结果", "rawText": "预期结果：进入首页"},
                },
                "raw_title": "登录\n成功",
                "clean_title": "登录\n成功",
                "preconditions": '参数结构为\n{\n  "name": "CRUD测试站点"\n}',
                "steps_text": "打开 App\n提交登录",
                "expected_result": "进入首页\n展示欢迎语",
            }
        ],
    )

    assert all(line.lstrip().startswith("- ") for line in content.splitlines() if line.strip())

    parsed = parse_markdown(content, "roundtrip.md")
    case = parsed.cases[0]
    assert case.path_nodes[0]["displayText"] == "账号 登录"
    assert case.path_nodes[1]["displayText"] == "验证码"
    assert case.path_nodes[2]["displayText"] == "成功路径"
    assert case.clean_title == "登录 成功"
    assert case.preconditions == '参数结构为 { "name": "CRUD测试站点" }'
    assert case.steps_text == "打开 App 提交登录"
    assert case.expected_result == "进入首页 展示欢迎语"
