from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.main import create_app


@pytest.mark.asyncio
async def test_public_startup_and_quick_import_without_model() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/api/v1/healthz")
        assert health.status_code == 200

        imported = await client.post(
            "/api/v1/quick/sessions/import",
            json={
                "filename": "public-smoke.md",
                "content": (
                    "- 示例测试集\n"
                    "  - 示例模块\n"
                    "    - 测试标题：查看基础信息\n"
                    "      - 前置条件：用户已登录\n"
                    "        - 操作步骤：进入页面查看信息\n"
                    "          - 预期结果：信息展示正常\n"
                ),
            },
        )

        assert imported.status_code == 200
        payload = imported.json()
        assert len(payload["cases"]) == 1
        assert payload["cases"][0]["execution_target"] == "manual"
        assert payload["cases"][0]["tag_source"] == "manual_fallback"

    await engine.dispose()

