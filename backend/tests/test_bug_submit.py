"""bug 提交核心逻辑回归测试（纯函数，不依赖飞书/DB/LLM）。

覆盖之前真机踩坑、最容易裂的三块：
- _format_value：飞书字段值格式（单选 {value}、多选 [{value}]、多用户 [keys]、关联 id、文本）。
- _pairs_from_fields：弹窗字段 → field_value_pairs，并按"建单即落库 / 单选多选要后写"拆两组。
- _normalize_meta：飞书建单 meta → 规整字段（只取可见、带选项/必填/默认）。
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from app.services import bug_submit
from app.services.bug_submit import (
    SpaceIssueConfig,
    _build_fields,
    _current_month_label,
    _current_month_zh_label,
    _format_value,
    load_issue_config,
    _model_target_keys,
    _normalize_meta,
    _pairs_from_fields,
    _prefill_choices,
)


def _issue_cfg(**overrides) -> SpaceIssueConfig:
    data = {
        "project_key": "example_project",
        "work_item_type": "issue",
        "template_id": 1,
        "title_field": "name",
        "description_field": "description",
        "description_include": [],
        "attachment_field": None,
        "link_requirement_field": "_field_linked_story",
        "link_requirement_field_type": "work_item_related_select",
        "field_sources": {},
    }
    data.update(overrides)
    return SpaceIssueConfig(**data)


class _UploadFile:
    def __init__(self, filename: str, content_type: str, raw: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._raw = raw

    async def read(self) -> bytes:
        return self._raw


def test_format_value_select_uses_value_object() -> None:
    # 单选必须是 {"value": option_id}（{"option_id"} 飞书不落库，这是踩过的坑）
    assert _format_value("select", "2") == {"value": "2"}


@pytest.mark.asyncio
async def test_upload_bug_images_returns_media_path_that_submit_channel_can_read(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bug_submit,
        "get_settings",
        lambda: SimpleNamespace(repair_image_dir=str(tmp_path)),
    )

    images = await bug_submit.upload_bug_images([
        _UploadFile("shot.png", "image/png", b"img"),
    ])

    assert len(images) == 1
    assert images[0]["platform"] == "手动补图"
    assert images[0]["image"].startswith("/media/bug_")
    local = bug_submit._media_local_path(images[0]["image"])  # type: ignore[attr-defined]
    assert local is not None
    assert local.read_bytes() == b"img"


@pytest.mark.asyncio
async def test_append_rich_text_images_updates_description_without_bare_image_markdown(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"img")
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeMCP:
        enabled = True

        async def upload_rich_text_image(
            self,
            _http: httpx.AsyncClient,
            local_path,
            project_key: str,
            work_item_id: int,
            work_item_type: str,
        ) -> tuple[str, str]:
            assert local_path == image
            assert project_key == "proj"
            assert work_item_id == 456
            assert work_item_type == "issue"
            return "https://file.local/shot.png", "file-token"

        async def call_tool(self, _http: httpx.AsyncClient, name: str, arguments: dict[str, object]) -> dict:
            calls.append((name, arguments))
            return {}

    monkeypatch.setattr(bug_submit.fpmcp, "FeishuProjectMCPClient", FakeMCP)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200))) as http:
        await bug_submit.append_rich_text_images_to_bug(
            http,
            "proj",
            "issue",
            456,
            "description",
            "复现说明\n![旧图](https://old.local/old.png)",
            [["安卓端", image]],
            log_context="test",
        )

    assert calls and calls[0][0] == "update_field"
    field_value = calls[0][1]["fields"][0]["field_value"]  # type: ignore[index]
    assert "![旧图]" not in field_value
    assert "旧图：https://old.local/old.png" in field_value
    assert "安卓端：![](https://file.local/shot.png)<!--file-token-->" in field_value


def test_format_value_multi_select_list_of_value_objects() -> None:
    assert _format_value("multi_select", ["a", "b"]) == [{"value": "a"}, {"value": "b"}]
    # 兼容传入 dict 元素
    assert _format_value("multi_select", [{"value": "x"}]) == [{"value": "x"}]


def test_format_value_users_and_relations() -> None:
    assert _format_value("multi_user", ["k1", "k2"]) == ["k1", "k2"]
    assert _format_value("user", "k1") == ["k1"]
    assert _format_value("work_item_related_select", "123") == 123
    assert _format_value("work_item_related_multi_select", ["1", "2"]) == [1, 2]
    assert _format_value("text", "hello") == "hello"


def test_format_value_empty_is_none() -> None:
    for empty in (None, "", [], {}):
        assert _format_value("select", empty) is None


def test_current_month_label_uses_short_year_month() -> None:
    assert _current_month_label(datetime(2026, 6, 26)) == "26-06"


def test_current_month_zh_label_uses_short_year_chinese_month() -> None:
    assert _current_month_zh_label(datetime(2026, 6, 26)) == "26年6月"


def test_load_issue_config_supports_multi_requirement_link(tmp_path) -> None:
    cfg_path = tmp_path / "feishu_issue.json"
    cfg_path.write_text(json.dumps({
        "spaces": [{
            "project_key": "example_project",
            "work_item_type": "issue",
            "template_id": 1,
            "title_field": "name",
            "description_field": "description",
            "description_include": ["diagnosis"],
            "link_requirement_field": "field_linked_requirement",
            "link_requirement_field_type": "work_item_related_multi_select",
        }],
    }), encoding="utf-8")

    cfg = load_issue_config(cfg_path)
    example = cfg["example_project"]
    assert example.link_requirement_field == "field_linked_requirement"
    assert example.link_requirement_field_type == "work_item_related_multi_select"


def test_build_fields_prefills_requirement_link_when_meta_contains_field() -> None:
    cfg = _issue_cfg()
    ctx = SimpleNamespace(
        cfg=cfg,
        pool=SimpleNamespace(title="示例需求", external_key="10001"),
    )

    fields = _build_fields(
        ctx,  # type: ignore[arg-type]
        [{
            "field_key": "_field_linked_story",
            "label": "关联需求",
            "type": "work_item_related_select",
            "required": True,
            "options": [],
            "default": None,
        }],
        {},
    )

    assert len(fields) == 1
    field = fields[0]
    assert field["field_key"] == "_field_linked_story"
    assert field["display"] == "示例需求（10001）"
    assert field["submit_value"] == 10001
    assert field["editable"] is False


def test_build_fields_appends_requirement_link_when_meta_omits_field() -> None:
    cfg = _issue_cfg()
    ctx = SimpleNamespace(
        cfg=cfg,
        pool=SimpleNamespace(title="示例需求", external_key="10001"),
    )

    fields = _build_fields(ctx, [], {})  # type: ignore[arg-type]

    assert len(fields) == 1
    assert fields[0]["field_key"] == "_field_linked_story"
    assert fields[0]["submit_value"] == 10001


def test_build_fields_uses_meta_type_for_multi_requirement_link() -> None:
    cfg = _issue_cfg(
        link_requirement_field="field_linked_requirement",
        link_requirement_field_type="work_item_related_select",
    )
    ctx = SimpleNamespace(
        cfg=cfg,
        pool=SimpleNamespace(title="另一个示例需求", external_key="10002"),
    )

    fields = _build_fields(
        ctx,  # type: ignore[arg-type]
        [{
            "field_key": "field_linked_requirement",
            "label": "关联需求",
            "type": "work_item_related_multi_select",
            "required": True,
            "options": [],
            "default": None,
        }],
        {},
    )

    assert fields[0]["type"] == "work_item_related_multi_select"
    assert fields[0]["submit_value"] == [10002]


def test_build_fields_keeps_submitter_as_current_user_only() -> None:
    cfg = _issue_cfg(field_sources={"issue_reporter": {"source": "current_user"}})
    current_user = SimpleNamespace(feishu_user_key="u1", display_name="张三", name="zhangsan")
    other_user = SimpleNamespace(feishu_user_key="u2", display_name="李四", name="lisi")
    ctx = SimpleNamespace(
        cfg=cfg,
        user=current_user,
        users=[other_user],
        pool=SimpleNamespace(title="需求", external_key="1"),
    )

    fields = _build_fields(
        ctx,  # type: ignore[arg-type]
        [{
            "field_key": "issue_reporter",
            "label": "报告人",
            "type": "multi_user",
            "required": True,
            "options": [],
            "default": None,
        }],
        {},
    )

    assert fields[0]["editable"] is False
    assert fields[0]["display"] == "右上角当前用户"
    assert fields[0]["submit_value"] is None
    assert fields[0]["options"] == []


def test_build_fields_infers_submitter_semantic_without_source_config() -> None:
    cfg = _issue_cfg(field_sources={})
    current_user = SimpleNamespace(feishu_user_key="u1", display_name="张三", name="zhangsan")
    ctx = SimpleNamespace(
        cfg=cfg,
        user=current_user,
        users=[],
        pool=SimpleNamespace(title="需求", external_key="1"),
    )

    fields = _build_fields(
        ctx,  # type: ignore[arg-type]
        [{
            "field_key": "field_creator",
            "label": "创建人",
            "type": "multi_user",
            "required": True,
            "options": [],
            "default": None,
        }],
        {},
    )

    assert fields[0]["editable"] is False
    assert fields[0]["display"] == "右上角当前用户"
    assert fields[0]["submit_value"] is None


def test_build_fields_uses_requirement_relation_as_selected_option() -> None:
    cfg = _issue_cfg(field_sources={"planning_sprint": {"source": "requirement_field", "from": "planning_sprint"}})
    ctx = SimpleNamespace(
        cfg=cfg,
        pool=SimpleNamespace(
            title="需求",
            external_key="1",
            source_payload={
                "fields": [{"field_key": "planning_sprint", "field_value": [123]}],
                "_card": {"sprints": [{"id": "123", "name": "26-06 迭代"}]},
            },
        ),
    )

    fields = _build_fields(
        ctx,  # type: ignore[arg-type]
        [{
            "field_key": "planning_sprint",
            "label": "规划迭代",
            "type": "work_item_related_multi_select",
            "required": True,
            "options": [],
            "default": None,
        }],
        {},
    )

    assert fields[0]["editable"] is True
    assert fields[0]["selected"] == ["123"]
    assert fields[0]["options"] == [{"id": "123", "name": "26-06 迭代"}]


def test_build_fields_uses_sprint_name_overrides() -> None:
    cfg = _issue_cfg(field_sources={"planning_sprint": {"source": "requirement_field", "from": "planning_sprint"}})
    ctx = SimpleNamespace(
        cfg=cfg,
        pool=SimpleNamespace(
            title="需求",
            external_key="1",
            source_payload={"fields": [{"field_key": "planning_sprint", "field_value": [123]}]},
        ),
    )

    fields = _build_fields(
        ctx,  # type: ignore[arg-type]
        [{
            "field_key": "planning_sprint",
            "label": "规划迭代",
            "type": "work_item_related_multi_select",
            "required": True,
            "options": [],
            "default": None,
        }],
        {},
        {"planning_sprint": {"123": "26-06 迭代"}},
    )

    assert fields[0]["selected"] == ["123"]
    assert fields[0]["options"] == [{"id": "123", "name": "26-06 迭代"}]


def test_pairs_split_select_vs_create() -> None:
    fields = [
        {"field_key": "priority", "type": "select", "editable": True,
         "selected": "P2", "options": [{"name": "P2", "id": "2"}]},
        {"field_key": "tags", "type": "multi_select", "editable": True,
         "selected": ["UI"], "options": [{"name": "UI", "id": "u1"}]},
        {"field_key": "issue_operator", "type": "multi_user", "editable": True,
         "selected": ["k1", "k2"], "options": [{"name": "甲", "id": "k1"}, {"name": "乙", "id": "k2"}]},
        {"field_key": "field_expected_text", "type": "multi_text", "editable": True, "selected": "预期文本"},
    ]
    create_pairs, select_pairs = _pairs_from_fields(fields)
    # 单选/多选 → 后写组（建单会被模板覆盖）
    assert {"field_key": "priority", "field_value": {"value": "2"}} in select_pairs
    assert {"field_key": "tags", "field_value": [{"value": "u1"}]} in select_pairs
    # 多用户/文本 → 建单即落库组
    assert {"field_key": "issue_operator", "field_value": ["k1", "k2"]} in create_pairs
    assert {"field_key": "field_expected_text", "field_value": "预期文本"} in create_pairs


def test_pairs_readonly_uses_submit_value() -> None:
    fields = [
        {"field_key": "issue_reporter", "type": "multi_user", "editable": False,
         "submit_value": ["me"]},
        {"field_key": "_field_linked_story", "type": "work_item_related_select",
         "editable": False, "submit_value": 7023},
    ]
    create_pairs, select_pairs = _pairs_from_fields(fields)
    assert {"field_key": "issue_reporter", "field_value": ["me"]} in create_pairs
    assert {"field_key": "_field_linked_story", "field_value": 7023} in create_pairs
    assert select_pairs == []


def test_pairs_injects_submitter_from_current_user_at_submit_time() -> None:
    cfg = _issue_cfg(field_sources={"issue_reporter": {"source": "current_user"}})
    current_user = SimpleNamespace(feishu_user_key="u1")
    fields = [
        {"field_key": "issue_reporter", "label": "报告人", "type": "multi_user",
         "editable": False, "submit_value": None},
    ]

    create_pairs, select_pairs = _pairs_from_fields(
        fields,
        cfg,
        current_user,  # type: ignore[arg-type]
    )

    assert {"field_key": "issue_reporter", "field_value": ["u1"]} in create_pairs
    assert select_pairs == []


def test_pairs_empty_selection_skipped() -> None:
    fields = [
        {"field_key": "priority", "type": "select", "editable": True, "selected": "", "options": []},
        {"field_key": "issue_operator", "type": "multi_user", "editable": True, "selected": []},
    ]
    create_pairs, select_pairs = _pairs_from_fields(fields)
    assert create_pairs == []
    assert select_pairs == []


def test_normalize_meta_keeps_visible_with_options_and_required() -> None:
    meta = [
        {
            "is_visibility": 1, "field_key": "priority", "field_type_key": "select",
            "is_required": 1, "field_name": "优先级",
            "options": [{"label": "P2", "value": "2"}, {"label": "P0", "value": "0"}],
            "default_value": {"value": {"label": "P2", "value": "2"}},
        },
        {  # 不可见 → 应被过滤
            "is_visibility": 2, "field_key": "description", "field_type_key": "multi_text",
            "is_required": 1, "field_name": "描述", "default_value": {"value": ""},
        },
    ]
    out = _normalize_meta(meta)
    assert len(out) == 1
    f = out[0]
    assert f["field_key"] == "priority"
    assert f["required"] is True
    assert f["type"] == "select"
    assert {"name": "P2", "id": "2"} in f["options"]


def test_model_target_keys_include_required_text_fields() -> None:
    cfg = _issue_cfg(field_sources={
        "model_text": {"source": "model"},
        "fixed_select": {"source": "fixed", "value": "p2"},
    })
    ctx = SimpleNamespace(cfg=cfg)
    meta = [
        {"field_key": "required_text", "type": "text", "required": True, "default": None},
        {"field_key": "required_multi_text", "type": "multi_text", "required": True, "default": None},
        {
            "field_key": "required_select",
            "type": "select",
            "required": True,
            "default": None,
            "options": [{"name": "P2", "id": "p2"}],
        },
        {"field_key": "model_text", "type": "text", "required": False, "default": None},
        {"field_key": "optional_text", "type": "text", "required": False, "default": None},
        {"field_key": "fixed_select", "type": "select", "required": True, "default": None},
        {"field_key": "description", "type": "multi_text", "required": True, "default": None},
    ]

    targets = _model_target_keys(ctx, meta)  # type: ignore[arg-type]

    assert targets == ["required_text", "required_multi_text", "required_select", "model_text"]


@pytest.mark.asyncio
async def test_model_choice_does_not_fallback_to_first_option(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bug_submit, "get_settings", lambda: type(
        "Settings",
        (),
        {"llm_api_key": "", "llm_base_url": "", "llm_model": ""},
    )())
    choices = await _prefill_choices(
        session=None,  # type: ignore[arg-type]
        ctx=None,  # type: ignore[arg-type]
        meta_by_key={
            "priority": {
                "type": "select",
                "options": [{"name": "P2", "id": "2"}, {"name": "P0", "id": "0"}],
            }
        },
        target_keys=["priority"],
    )

    assert choices == {}
