import json

from app.services.sources.feishu_project import (
    FeishuSourceConfig,
    SpaceConfig,
    _extract_role_owners,
    build_card,
    lifecycle_for,
    load_source_config,
    map_work_item,
    user_keys_for_concept,
)


def test_build_card():
    raw = {
        "id": 10001,
        "name": "示例功能基础操作",
        "simple_name": "demo_project",
        "work_item_type_key": "story",
        "created_at": 1781772364929,
        "current_nodes": [{"id": "state_3", "name": "测试中"}],
        "fields": [
            {"field_key": "auto_number", "field_value": 101},
            {"field_key": "planning_sprint", "field_value": [20001]},
            {
                "field_key": "role_owners",
                "field_type_key": "role_owners",
                "field_value": [
                    {"role": "role_tester", "owners": ["user_tester"]},
                    {"role": "role_frontend", "owners": ["user_frontend"]},
                ],
            },
        ],
    }
    space = SpaceConfig(
        project_key="demo_project",
        name="示例项目",
        role_map={"tester": ["role_tester"], "frontend": ["role_frontend"], "backend": ["role_backend"]},
        sprint_field="planning_sprint",
    )
    card = build_card(
        map_work_item(raw),
        space,
        {"user_tester": "测试人员甲", "user_frontend": "开发人员乙"},
        "https://project.feishu.cn",
        {"20001": "迭代A（26.06.16~26.06.29）"},
    )
    assert card["number"] == "10001"  # 使用工作项 id，与链接一致
    assert card["status"] == "测试中"
    assert card["link"] == "https://project.feishu.cn/demo_project/story/detail/10001"
    labels = {r["label"]: r["names"] for r in card["roles"]}
    assert labels["测试"] == ["测试人员甲"]
    assert labels["前端"] == ["开发人员乙"]
    assert "后端" not in labels  # 无后端负责人则不出现
    assert card["sprints"] == [{"id": "20001", "name": "迭代A（26.06.16~26.06.29）"}]


def test_map_basic_fields():
    raw = {
        "id": 10002,
        "name": "示例功能 V1.1 列表页改版",
        "work_item_status": {"state_key": "test", "name": "测试中"},
        "created_at": 1781772364929,
        "created_by": "u_zhangsan",
    }
    proj = map_work_item(raw)
    assert proj.external_key == "10002"
    assert proj.title == "示例功能 V1.1 列表页改版"
    assert proj.state_key == "test"
    assert proj.created_at_ms == 1781772364929
    assert proj.created_by == "u_zhangsan"
    assert proj.raw is raw


def test_string_status():
    raw = {"id": 1, "name": "登录链路优化", "work_item_status": "started"}
    proj = map_work_item(raw)
    assert proj.state_key == "started"


def test_extract_role_owners_from_fields():
    raw = {
        "id": 42,
        "name": "需求 X",
        "fields": [
            {
                "field_key": "role_owners",
                "field_type_key": "role_owners",
                "field_value": [
                    {"role": "role_tester", "owners": ["user_tester"]},
                    {"role": "role_frontend", "owners": ["user_fe1", "user_fe2"]},
                ],
            }
        ],
    }
    owners = _extract_role_owners(raw)
    assert owners["role_tester"] == ["user_tester"]
    assert owners["role_frontend"] == ["user_fe1", "user_fe2"]
    proj = map_work_item(raw)
    assert proj.role_owners["role_tester"] == ["user_tester"]


def test_user_field_map_supports_field_based_people():
    raw = {
        "id": 43,
        "name": "示例业务需求",
        "fields": [
            {
                "field_key": "field_assignees",
                "field_type_key": "multi_user",
                "field_value": ["uk_dev1", "uk_dev2"],
            }
        ],
    }
    space = SpaceConfig(
        project_key="example_business",
        name="示例业务",
        user_field_map={"tester": ["field_assignees"], "backend": ["field_assignees"]},
    )
    proj = map_work_item(raw)
    assert user_keys_for_concept(raw, proj.role_owners, space, "tester") == ["uk_dev1", "uk_dev2"]
    assert user_keys_for_concept(raw, proj.role_owners, space, "backend") == ["uk_dev1", "uk_dev2"]


def test_lifecycle_can_match_current_node_name():
    cfg = FeishuSourceConfig(
        work_item_type="story",
        default_sprint_field="planning_sprint",
        spaces=[SpaceConfig(
            project_key="example_project",
            name="示例项目",
            status_in_testing_node_names=["9.测试中"],
        )],
    )
    assert lifecycle_for(
        "example_project",
        "doing",
        {"current_nodes": [{"id": "state_9", "name": "9.测试中"}]},
        cfg,
    ) == "测试中"
    assert lifecycle_for(
        "example_project",
        "doing",
        {"current_nodes": [{"id": "state_5", "name": "4.待排期"}]},
        cfg,
    ) == "其他"


def test_load_source_config_from_file(tmp_path):
    cfg_path = tmp_path / "feishu_project.json"
    cfg_path.write_text(
        json.dumps({
            "work_item_type": "story",
            "default_sprint_field": "planning_sprint",
            "pull": {"created_after": "2026-01-01"},
            "spaces": [{
                "project_key": "example_project",
                "name": "示例项目",
                "role_map": {"tester": ["role_tester"]},
            }],
        }),
        encoding="utf-8",
    )

    cfg = load_source_config(cfg_path)
    assert cfg is not None
    assert cfg.work_item_type == "story"
    assert cfg.default_sprint_field == "planning_sprint"
    example = next(sp for sp in cfg.spaces if sp.project_key == "example_project")
    assert example.role_map["tester"] == ["role_tester"]
    assert example.work_item_type is None
    assert example.sprint_field == "planning_sprint"
    assert cfg.created_after_ms is not None


def test_default_sprint_field_can_be_overridden_or_disabled(tmp_path):
    cfg_path = tmp_path / "feishu_project.json"
    cfg_path.write_text(
        json.dumps({
            "work_item_type": "story",
            "default_sprint_field": "planning_sprint",
            "spaces": [
                {"project_key": "a", "name": "A", "role_map": {}},
                {"project_key": "b", "name": "B", "role_map": {}, "sprint_field": "custom_sprint"},
                {"project_key": "c", "name": "C", "role_map": {}, "sprint_field": None},
            ],
        }),
        encoding="utf-8",
    )

    cfg = load_source_config(cfg_path)
    assert cfg is not None
    by_key = {sp.project_key: sp for sp in cfg.spaces}
    assert by_key["a"].sprint_field == "planning_sprint"
    assert by_key["b"].sprint_field == "custom_sprint"
    assert by_key["c"].sprint_field is None


def test_space_config_defaults():
    sp = SpaceConfig(project_key="x", name="X")
    assert sp.work_item_type is None
    assert sp.work_item_url_type is None
    assert sp.role_map == {}
    assert sp.user_field_map == {}
    assert sp.status_in_testing_state_keys == []
    assert sp.status_in_testing_node_names == []
    assert sp.status_in_testing_node_ids == []
