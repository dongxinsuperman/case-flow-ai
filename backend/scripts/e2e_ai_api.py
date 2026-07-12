from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


class _JSONHandler(BaseHTTPRequestHandler):
    server_version = "CaseFlowE2E/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockAPIHandler(_JSONHandler):
    users: dict[str, dict[str, Any]] = {}

    def do_POST(self) -> None:
        payload = self._read_json()
        if self.path == "/orders/query":
            limit = int(payload.get("limit") or 0)
            if limit > 1000:
                self._send_json(400, {"message": "limit不能大于1000", "limit": limit})
                return
            self._send_json(200, {"items": [{"id": 1}], "limit": limit})
            return
        if self.path == "/users":
            user_id = str(payload.get("id") or "e2e-user-1")
            user = {"id": user_id, "name": str(payload.get("name") or "Alice")}
            self.users[user_id] = user
            self._send_json(201, user)
            return
        self._send_json(404, {"message": "not found"})

    def do_GET(self) -> None:
        if self.path.startswith("/users/"):
            user_id = self.path.rsplit("/", 1)[-1]
            user = self.users.get(user_id)
            if user is None:
                self._send_json(404, {"message": "not found"})
                return
            self._send_json(200, user)
            return
        self._send_json(404, {"message": "not found"})

    def do_PATCH(self) -> None:
        if self.path.startswith("/users/"):
            user_id = self.path.rsplit("/", 1)[-1]
            user = self.users.get(user_id)
            if user is None:
                self._send_json(404, {"message": "not found"})
                return
            payload = self._read_json()
            user = {**user, "name": str(payload.get("name") or user["name"])}
            self.users[user_id] = user
            self._send_json(200, user)
            return
        self._send_json(404, {"message": "not found"})

    def do_DELETE(self) -> None:
        if self.path.startswith("/users/"):
            user_id = self.path.rsplit("/", 1)[-1]
            self.users.pop(user_id, None)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(404, {"message": "not found"})


class FakeLLMHandler(_JSONHandler):
    def do_POST(self) -> None:
        if not self.path.endswith("/responses"):
            self._send_json(404, {"error": {"message": "not found"}})
            return
        payload = self._read_json()
        prompt = json.dumps(payload.get("input") or payload, ensure_ascii=False)
        plan = _crud_plan() if "增删改查" in prompt else _limit_plan()
        output_text = json.dumps(plan, ensure_ascii=False)
        response = {
            "id": f"resp_e2e_{int(time.time() * 1000)}",
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": "case-flow-e2e",
            "output": [
                {
                    "id": "msg_e2e",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": output_text,
                            "annotations": [],
                        }
                    ],
                }
            ],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        self._send_json(200, response)


def _limit_plan() -> dict[str, Any]:
    return {
        "executable": True,
        "reason": "识别为订单查询 limit 边界测试，需要验证 1000 成功、1001 被拒绝。",
        "scenarios": [
            {
                "id": "limit_eq_1000",
                "name": "limit 等于最大边界应成功",
                "intent": "验证 limit=1000 仍被接口接受",
                "expected_outcome": "accepted",
                "request": {
                    "method": "POST",
                    "path": "/orders/query",
                    "headers": {"Content-Type": "application/json"},
                    "body_type": "json",
                    "body": {"limit": 1000},
                    "timeout_seconds": 10,
                },
                "assertions": [{"type": "status_code", "expected": 200}],
                "required": True,
            },
            {
                "id": "limit_gt_1000",
                "name": "limit 超过最大边界应被拒绝",
                "intent": "验证 limit=1001 被接口拦截",
                "expected_outcome": "rejected",
                "request": {
                    "method": "POST",
                    "path": "/orders/query",
                    "headers": {"Content-Type": "application/json"},
                    "body_type": "json",
                    "body": {"limit": 1001},
                    "timeout_seconds": 10,
                },
                "assertions": [
                    {"type": "status_code", "expected": 400},
                    {"type": "body_contains", "contains": "limit不能大于1000"},
                ],
                "required": True,
            },
        ],
        "notes": ["1001 是非法越界值，被拒绝代表该 scenario 通过。"],
    }


def _crud_plan() -> dict[str, Any]:
    user_id = "e2e-user-1"
    return {
        "executable": True,
        "reason": "识别为用户接口 CRUD 测试，按创建、查询、修改、删除、删除后查询拆分。",
        "scenarios": [
            {
                "id": "create_user",
                "name": "创建用户",
                "intent": "创建测试用户 Alice",
                "expected_outcome": "changed",
                "request": {
                    "method": "POST",
                    "path": "/users",
                    "headers": {"Content-Type": "application/json"},
                    "body_type": "json",
                    "body": {"id": user_id, "name": "Alice"},
                    "timeout_seconds": 10,
                },
                "assertions": [
                    {"type": "status_code", "expected": 201},
                    {"type": "json_path_equals", "path": "$.id", "expected": user_id},
                ],
                "required": True,
            },
            {
                "id": "query_created_user",
                "name": "查询已创建用户",
                "intent": "确认创建后的用户可查询",
                "expected_outcome": "accepted",
                "request": {"method": "GET", "path": f"/users/{user_id}", "timeout_seconds": 10},
                "assertions": [
                    {"type": "status_code", "expected": 200},
                    {"type": "json_path_equals", "path": "$.name", "expected": "Alice"},
                ],
                "required": True,
            },
            {
                "id": "update_user",
                "name": "修改用户",
                "intent": "把用户名称改为 Bob",
                "expected_outcome": "changed",
                "request": {
                    "method": "PATCH",
                    "path": f"/users/{user_id}",
                    "headers": {"Content-Type": "application/json"},
                    "body_type": "json",
                    "body": {"name": "Bob"},
                    "timeout_seconds": 10,
                },
                "assertions": [
                    {"type": "status_code", "expected": 200},
                    {"type": "json_path_equals", "path": "$.name", "expected": "Bob"},
                ],
                "required": True,
            },
            {
                "id": "query_updated_user",
                "name": "查询修改后用户",
                "intent": "确认用户名称已经变更",
                "expected_outcome": "accepted",
                "request": {"method": "GET", "path": f"/users/{user_id}", "timeout_seconds": 10},
                "assertions": [
                    {"type": "status_code", "expected": 200},
                    {"type": "json_path_equals", "path": "$.name", "expected": "Bob"},
                ],
                "required": True,
            },
            {
                "id": "delete_user",
                "name": "删除用户",
                "intent": "删除测试用户",
                "expected_outcome": "changed",
                "request": {"method": "DELETE", "path": f"/users/{user_id}", "timeout_seconds": 10},
                "assertions": [{"type": "status_code", "expected": 204}],
                "required": True,
            },
            {
                "id": "query_deleted_user",
                "name": "删除后查询应不存在",
                "intent": "确认删除后再次查询会被拒绝",
                "expected_outcome": "rejected",
                "request": {"method": "GET", "path": f"/users/{user_id}", "timeout_seconds": 10},
                "assertions": [
                    {"type": "status_code", "expected": 404},
                    {"type": "body_contains", "contains": "not found"},
                ],
                "required": True,
            },
        ],
        "notes": ["删除后 404 是本场景的预期结果。"],
    }


class _Server:
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


async def main() -> None:
    api_server = _Server(MockAPIHandler)
    llm_server = _Server(FakeLLMHandler)
    api_server.start()
    llm_server.start()
    report_dir = tempfile.mkdtemp(prefix="case-flow-aiapi-e2e-")
    os.environ.update(
        {
            "CASE_FLOW_AIAPI_ALLOWED_HOSTS": "127.0.0.1",
            "CASE_FLOW_AIAPI_ALLOWED_BASE_URLS": api_server.base_url,
            "CASE_FLOW_AIAPI_DEFAULT_BASE_URL": api_server.base_url,
            "CASE_FLOW_AIAPI_ALLOWED_METHODS": "GET,POST,PUT,PATCH,DELETE",
            "CASE_FLOW_AIAPI_ALLOW_PRIVATE_NETWORKS": "true",
            "CASE_FLOW_AIAPI_MAX_RESPONSE_BYTES": "0",
            "CASE_FLOW_LLM_BASE_URL": f"{llm_server.base_url}/v1",
            "CASE_FLOW_LLM_API_KEY": "case-flow-e2e-key",
            "CASE_FLOW_LLM_MODEL": "case-flow-e2e-model",
            "CASE_FLOW_PUBLIC_BASE_URL": "http://testserver",
            "CASE_FLOW_REPAIR_IMAGE_DIR": report_dir,
        }
    )
    try:
        await _run_e2e(api_server.base_url, report_dir)
    finally:
        api_server.close()
        llm_server.close()


async def _run_e2e(api_base_url: str, report_dir: str) -> None:
    import httpx
    from httpx import ASGITransport
    from sqlalchemy import func, select

    from app.core import database
    from app.main import create_app
    from app.models.case_assets import (
        AIPhoneExecutionItem,
        CaseAsset,
        CaseBody,
        CaseStep,
        CaseWorkItem,
        ImportBatch,
    )
    from app.models.quick import QuickCaseWorkItem, QuickExecutionItem
    from app.models.requirements import RequirementGroup, RequirementItem, RequirementPool

    unique = f"aiapi-e2e-{int(time.time())}"
    async with database.AsyncSessionLocal() as session:
        group = RequirementGroup(
            name=f"AI API E2E {unique}",
            function_map_files=[
                {
                    "filename": "api.md",
                    "content": (
                        f"base_url={api_base_url}\n"
                        "订单查询接口 POST /orders/query，字段 limit 最大 1000。\n"
                        "用户接口支持 POST /users、GET/PATCH/DELETE /users/{id}。"
                    ),
                }
            ],
        )
        pool = RequirementPool(
            external_key=unique,
            title=f"AI API E2E 标准需求 {unique}",
            description="标准模式 AI API 端到端测试数据",
            source_type="e2e",
            status="active",
            source_space="e2e",
            external_status="testing",
            source_payload={},
        )
        session.add_all([group, pool])
        await session.flush()
        requirement = RequirementItem(
            group_id=group.id,
            pool_id=pool.id,
            title=pool.title,
            description=pool.description,
            status="active",
            version="e2e",
            lifecycle_status="测试中",
        )
        session.add(requirement)
        await session.flush()
        import_batch = ImportBatch(
            suite_title="AI API E2E 标准测试集",
            source_name="ai-api-e2e-standard.md",
            version="e2e",
            requirement_item_id=requirement.id,
            case_count=2,
            raw_metadata={"source": "ai_api_e2e"},
        )
        session.add(import_batch)
        await session.flush()
        limit_case = CaseAsset(
            batch_id=import_batch.id,
            ordinal=1,
            suite_title=import_batch.suite_title,
            path_nodes=[
                {"level": 2, "label": "模块", "rawText": "订单", "displayText": "订单"}
            ],
            raw_title="订单查询接口 limit 边界测试",
            clean_title="订单查询接口 limit 边界测试",
            scenario_tags=[],
            manual=False,
            status="imported",
            source_requirement_item_id=requirement.id,
        )
        crud_case = CaseAsset(
            batch_id=import_batch.id,
            ordinal=2,
            suite_title=import_batch.suite_title,
            path_nodes=[
                {"level": 2, "label": "模块", "rawText": "用户", "displayText": "用户"}
            ],
            raw_title="用户接口增删改查测试",
            clean_title="用户接口增删改查测试",
            scenario_tags=[],
            manual=False,
            status="imported",
            source_requirement_item_id=requirement.id,
        )
        session.add_all([limit_case, crud_case])
        await session.flush()
        session.add_all(
            [
                CaseBody(
                    case_id=limit_case.id,
                    goal=limit_case.clean_title,
                    preconditions="订单查询接口 limit 参数不能大于 1000。",
                    steps_text="通过自然语言完成 limit 边界测试：请求 limit=1000 和 limit=1001。",
                    expected_result="limit=1000 应成功；limit=1001 应被接口拒绝，返回 limit不能大于1000。",
                ),
                CaseStep(case_id=limit_case.id, step_order=1, step_text="请求 limit=1000。"),
                CaseStep(case_id=limit_case.id, step_order=2, step_text="请求 limit=1001。"),
                CaseWorkItem(
                    case_id=limit_case.id,
                    execution_status="not_run",
                    coverage={},
                    lifecycle_state="待验证",
                    display_order=1,
                    case_type="auto",
                    execution_target="api",
                    tag_source="e2e",
                    tag_reason="自然语言 API case",
                    tag_confidence=100,
                    run_enabled=True,
                    bugs=[],
                ),
                CaseBody(
                    case_id=crud_case.id,
                    goal=crud_case.clean_title,
                    preconditions="用户接口支持创建、查询、修改、删除测试用户。",
                    steps_text="通过自然语言完成用户接口增删改查测试。",
                    expected_result="创建成功，先查到 Alice，修改后查到 Bob，删除后返回 not found。",
                ),
                CaseStep(case_id=crud_case.id, step_order=1, step_text="创建、查询、修改、复查、删除。"),
                CaseWorkItem(
                    case_id=crud_case.id,
                    execution_status="not_run",
                    coverage={},
                    lifecycle_state="待验证",
                    display_order=2,
                    case_type="auto",
                    execution_target="api",
                    tag_source="e2e",
                    tag_reason="自然语言 API case",
                    tag_confidence=100,
                    run_enabled=True,
                    bugs=[],
                ),
            ]
        )
        await session.commit()
        standard_case_ids = [int(limit_case.id), int(crud_case.id)]
        requirement_id = int(requirement.id)

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        standard_submit = await client.post(
            "/api/v1/executions/aiapi/submit",
            json={
                "case_ids": standard_case_ids,
                "submission_name": f"E2E AI API standard batch {unique}",
            },
        )
        standard_submit.raise_for_status()
        standard_submit_payload = standard_submit.json()
        assert standard_submit_payload["submitted_count"] == 2
        standard_batch_id = int(standard_submit_payload["batch_id"])
        standard_results = [
            await _wait_case_work_item(case_id, expected="passed")
            for case_id in standard_case_ids
        ]
        standard_report_html = "\n".join(
            [await _read_report(client, item["report_url"]) for item in standard_results]
        )
        assert "limit_gt_1000" in standard_report_html
        assert "limit不能大于1000" in standard_report_html
        assert "create_user" in standard_report_html
        assert "query_deleted_user" in standard_report_html

        quick_import = await client.post(
            "/api/v1/quick/sessions/import",
            json={
                "filename": "ai-api-e2e-quick.md",
                "content": _quick_markdown(),
                "function_files": [
                    {
                        "filename": "api.md",
                        "content": (
                            f"base_url={api_base_url}\n"
                            "订单查询接口 POST /orders/query，字段 limit 最大 1000。\n"
                            "用户接口支持 POST /users、GET/PATCH/DELETE /users/{id}。"
                        ),
                    }
                ],
            },
        )
        quick_import.raise_for_status()
        quick_payload = quick_import.json()
        quick_session_id = quick_payload["session"]["session_id"]
        quick_case_ids = [int(item["id"]) for item in quick_payload["cases"]]
        assert len(quick_case_ids) == 2
        for item in quick_payload["cases"]:
            if item["execution_target"] != "api":
                update_target = await client.post(
                    "/api/v1/quick/case-work-items/update",
                    json={"case_id": int(item["id"]), "execution_target": "api"},
                )
                update_target.raise_for_status()

        quick_submit = await client.post(
            "/api/v1/quick/executions/aiapi/submit",
            json={
                "session_id": quick_session_id,
                "case_ids": quick_case_ids,
                "submission_name": f"E2E AI API quick batch {unique}",
            },
        )
        quick_submit.raise_for_status()
        quick_submit_payload = quick_submit.json()
        assert quick_submit_payload["submitted_count"] == 2
        quick_batch_id = int(quick_submit_payload["batch_id"])
        quick_results = [
            await _wait_quick_work_item(case_id, expected="passed")
            for case_id in quick_case_ids
        ]
        quick_report_html = "\n".join(
            [await _read_report(client, item["report_url"]) for item in quick_results]
        )
        assert "create_user" in quick_report_html
        assert "query_deleted_user" in quick_report_html
        assert "not found" in quick_report_html
        assert "limit_gt_1000" in quick_report_html
        assert "limit不能大于1000" in quick_report_html

        for case_id in quick_case_ids:
            platform_results = await client.get(f"/api/v1/quick/cases/{case_id}/platform-results")
            platform_results.raise_for_status()
            assert platform_results.json()[0]["platform"] == "api"

    async with database.AsyncSessionLocal() as session:
        standard_items = [
            await session.get(CaseWorkItem, case_id)
            for case_id in standard_case_ids
        ]
        quick_items = [
            await session.get(QuickCaseWorkItem, case_id)
            for case_id in quick_case_ids
        ]
        standard_batches = {
            str(item.external_submission_id)
            for item in standard_items
            if item and item.external_submission_id
        }
        quick_batches = {
            str(item.external_submission_id)
            for item in quick_items
            if item and item.external_submission_id
        }
        assert len(standard_batches) == 1
        assert len(quick_batches) == 1
        standard_batch = next(iter(standard_batches))
        quick_batch = next(iter(quick_batches))
        assert standard_batch and standard_batch.startswith("local-aiapi-")
        assert quick_batch and quick_batch.startswith("local-quick-aiapi-")
        standard_item_count = (
            await session.execute(
                select(func.count(AIPhoneExecutionItem.id)).where(
                    AIPhoneExecutionItem.batch_id == standard_batch_id
                )
            )
        ).scalar_one()
        quick_item_count = (
            await session.execute(
                select(func.count(QuickExecutionItem.id)).where(
                    QuickExecutionItem.batch_id == quick_batch_id
                )
            )
        ).scalar_one()
        assert standard_item_count == 2
        assert quick_item_count == 2

        report_count = len(list(os.scandir(os.path.join(report_dir, "aiapi_reports"))))
        assert report_count >= 4

        print("AI API E2E passed")
        print(
            json.dumps(
                {
                    "standard": {
                        "requirement_id": requirement_id,
                        "case_ids": standard_case_ids,
                        "statuses": [item.execution_status if item else None for item in standard_items],
                        "report_urls": [item.report_url if item else None for item in standard_items],
                        "submission_id": standard_batch,
                    },
                    "quick": {
                        "session_id": quick_session_id,
                        "case_ids": quick_case_ids,
                        "statuses": [item.execution_status if item else None for item in quick_items],
                        "report_urls": [item.report_url if item else None for item in quick_items],
                        "submission_id": quick_batch,
                    },
                    "report_dir": report_dir,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    await database.engine.dispose()


def _quick_markdown() -> str:
    return "\n".join(
        [
            "- AI API Quick E2E 测试集",
            "  - 用户",
            "    - 接口",
            "      - 测试标题：通过自然语言完成用户接口增删改查测试",
            "        - 前置条件：用户接口支持创建、查询、修改、删除测试用户。",
            "          - 操作步骤：完成用户接口增删改查：创建、查询、修改、复查、删除。",
            "            - 预期结果：创建成功，先查到 Alice，修改后查到 Bob，删除后返回 not found。",
            "  - 订单",
            "    - 查询",
            "      - 测试标题：订单查询接口 limit 边界测试",
            "        - 前置条件：订单查询接口 limit 参数不能大于 1000。",
            "          - 操作步骤：请求 limit=1000 和 limit=1001，测试超过边界值是否被拒绝。",
            "            - 预期结果：limit=1000 应成功；limit=1001 应返回 limit不能大于1000。",
            "",
        ]
    )


async def _wait_case_work_item(case_id: int, *, expected: str) -> dict[str, Any]:
    from app.core import database
    from app.models.case_assets import CaseWorkItem

    deadline = time.monotonic() + 20
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        async with database.AsyncSessionLocal() as session:
            item = await session.get(CaseWorkItem, case_id)
            if item:
                last = {
                    "status": item.execution_status,
                    "report_url": item.report_url,
                    "failure_summary": item.failure_summary,
                }
                if item.execution_status == expected and item.report_url:
                    return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"standard case {case_id} did not reach {expected}: {last}")


async def _wait_quick_work_item(case_id: int, *, expected: str) -> dict[str, Any]:
    from app.core import database
    from app.models.quick import QuickCaseWorkItem

    deadline = time.monotonic() + 20
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        async with database.AsyncSessionLocal() as session:
            item = await session.get(QuickCaseWorkItem, case_id)
            if item:
                last = {
                    "status": item.execution_status,
                    "report_url": item.report_url,
                    "failure_summary": item.failure_summary,
                }
                if item.execution_status == expected and item.report_url:
                    return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"quick case {case_id} did not reach {expected}: {last}")


async def _read_report(client: Any, report_url: str | None) -> str:
    assert report_url, "missing report_url"
    path = urlparse(report_url).path
    response = await client.get(path)
    response.raise_for_status()
    return response.text


if __name__ == "__main__":
    asyncio.run(main())
