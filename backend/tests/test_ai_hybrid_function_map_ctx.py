from __future__ import annotations

from app.services.ai_hybrid import function_map_ctx


_MAPS = [
    {
        "asset_id": 12,
        "title": "账号设备绑定",
        "description": "老师和学生账号对应固定设备",
        "targets": ["app"],
        "source": "一级目录显式挂载",
        "content": "老师账号 → 别名 teacher-device；学生账号 → 别名 student-device。",
    },
    {
        "asset_id": 34,
        "title": "后台配置",
        "description": "Web 后台配置",
        "targets": ["web"],
        "source": "二级需求显式挂载",
        "content": "后台打开功能开关。",
    },
]


def test_catalog_is_structured_and_excludes_body() -> None:
    catalog = function_map_ctx.build_catalog(_MAPS)
    assert catalog == [
        {
            "asset_id": 12,
            "title": "账号设备绑定",
            "description": "老师和学生账号对应固定设备",
            "targets": ["app"],
        },
        {
            "asset_id": 34,
            "title": "后台配置",
            "description": "Web 后台配置",
            "targets": ["web"],
        },
    ]


def test_read_block_returns_complete_body_and_legacy_text_is_not_lost() -> None:
    assert function_map_ctx.read_block(_MAPS, "", "12")["content"].endswith("student-device。")  # type: ignore[index]

    context = "--- FUNCTION MAP: 额外映射 ---\n资产 ID: 99\n来源: 外部\n\n账号 → 别名 extra-device"
    ids = [str(item["asset_id"]) for item in function_map_ctx.build_catalog(_MAPS, context)]
    assert ids == ["12", "34", "99"]
    assert function_map_ctx.read_block(_MAPS, context, "99")["content"] == "账号 → 别名 extra-device"  # type: ignore[index]

    plain = "无边界的旧 functionMap 文本"
    legacy = function_map_ctx.read_block([], plain, "functionMap")
    assert legacy is not None
    assert legacy["content"] == plain
