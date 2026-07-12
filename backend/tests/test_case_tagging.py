from __future__ import annotations

import pytest

from app.services import case_tagging


def _case(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "module_name": "学习",
        "path_nodes": [
            {"displayText": "学习"},
            {"displayText": "课程"},
            {"displayText": "记录"},
        ],
        "product_feature": "课程",
        "test_feature": "记录",
        "raw_title": "查看学习记录",
        "clean_title": "查看学习记录",
        "scenario_tags": [],
        "manual": False,
        "preconditions": "用户已登录",
        "steps_text": "进入页面查看记录",
        "expected_result": "记录展示正常",
    }
    data.update(overrides)
    return data


def test_classify_case_keeps_api_rule() -> None:
    result = case_tagging.classify_case(
        _case(
            raw_title="登录校验",
            clean_title="登录校验",
            steps_text="POST /api/login，检查 HTTP 状态码和 JSON 响应",
        )
    )

    assert result["execution_target"] == "api"
    assert result["case_type"] == "auto"
    assert result["tag_source"] == "content_rule"


@pytest.mark.parametrize(
    "keyword",
    [
        "平台",
        "后台",
        "管理端",
        "控制台",
        "站点",
        "网站",
        "cb",
        "vm",
        "视频后台",
        "课程后台",
        "cb平台系统课搜索知识点",
        "vm后台配置",
    ],
)
def test_classify_case_marks_web_business_terms(keyword: str) -> None:
    result = case_tagging.classify_case(
        _case(
            raw_title=f"{keyword}可见性校验",
            clean_title=f"{keyword}可见性校验",
        )
    )

    assert result["execution_target"] == "web"
    assert result["case_type"] == "auto"
    assert result["tag_source"] == "content_rule"


def test_classify_case_does_not_treat_course_alone_as_web() -> None:
    result = case_tagging.classify_case(
        _case(
            raw_title="查看课程学习记录",
            clean_title="查看课程学习记录",
            preconditions="用户已登录",
        )
    )

    assert result["execution_target"] == "manual"
    assert result["tag_source"] == "manual_fallback"


def test_classify_case_keeps_manual_backend_precedence() -> None:
    result = case_tagging.classify_case(
        _case(
            raw_title="运营后台审核学习记录",
            clean_title="运营后台审核学习记录",
            preconditions="存在待审核记录",
        )
    )

    assert result["execution_target"] == "manual"
    assert result["case_type"] == "manual"
    assert result["tag_source"] == "content_rule"


def test_classify_case_falls_back_to_manual_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASE_FLOW_LLM_API_KEY", "configured-but-unused")

    result = case_tagging.classify_case(_case())

    assert result["execution_target"] == "manual"
    assert result["case_type"] == "manual"
    assert result["tag_source"] == "manual_fallback"
    assert result["tag_confidence"] == 35


@pytest.mark.asyncio
async def test_classify_cases_enriches_only_rule_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[int, dict[str, object]]] = []

    async def fake_model(items: list[tuple[int, dict[str, object]]]) -> dict[int, str]:
        seen.extend(items)
        return {1: "api"}

    monkeypatch.setattr(case_tagging, "_classify_fallbacks_with_model", fake_model)

    results = await case_tagging.classify_cases(
        [
            _case(
                raw_title="登录校验",
                clean_title="登录校验",
                steps_text="POST /api/login，检查 HTTP 状态码和 JSON 响应",
            ),
            _case(raw_title="查看记录", clean_title="查看记录", preconditions="用户已登录"),
        ]
    )

    assert [item[0] for item in seen] == [1]
    assert results[0]["tag_source"] == "content_rule"
    assert results[0]["execution_target"] == "api"
    assert results[1]["tag_source"] == "model"
    assert results[1]["execution_target"] == "api"
    assert results[1]["tag_confidence"] == 70


def test_model_prompt_uses_only_title_and_preconditions() -> None:
    prompt = case_tagging._build_model_prompt(
        [
            (
                7,
                _case(
                    raw_title="原始标题",
                    clean_title="展示标题",
                    preconditions="用户已登录",
                    steps_text="不要发送步骤",
                    expected_result="不要发送预期",
                ),
            )
        ]
    )

    assert '"id":7' in prompt
    assert '"title":"原始标题"' in prompt
    assert '"pre":"用户已登录"' in prompt
    assert "不要发送步骤" not in prompt
    assert "不要发送预期" not in prompt
    assert "hybrid" not in prompt.lower()


def test_model_batches_are_split_across_three_requests() -> None:
    items = [(index, _case(raw_title=f"case {index}")) for index in range(4)]

    batches = case_tagging._split_even(items, 3)

    assert [len(batch) for batch in batches] == [2, 1, 1]
    assert [[index for index, _case_data in batch] for batch in batches] == [[0, 1], [2], [3]]
