from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.main import create_app


@pytest.mark.asyncio
async def test_home_and_workbench_cases_load_from_formal_api() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        users = (await client.get("/api/v1/users")).json()
        if not users:
            pytest.skip("无用户（飞书未拉取前为空），跳过数据相关断言")
        user_id = users[0]["id"]

        home = await client.get("/api/v1/home", params={"user_id": user_id})
        assert home.status_code == 200
        home_payload = home.json()
        # 契约：summary 字段齐全；不再依赖 mock 种子数据的具体条数。
        assert {"requirements", "case_count", "not_run", "passed", "failed"} <= set(home_payload["summary"])

        requirements = home_payload["requirements"]
        if not requirements:
            return
        requirement_id = requirements[0]["requirement_item_id"]
        cases = await client.get("/api/v1/workbench-cases", params={"requirement_item_id": requirement_id})
        assert cases.status_code == 200
        case_payload = cases.json()
        assert len(case_payload) == requirements[0]["case_count"]
        if case_payload:
            assert {"id", "clean_title", "execution_status", "execution_target"} <= set(case_payload[0])

    await engine.dispose()
