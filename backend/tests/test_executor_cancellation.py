from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services import executions, executor_cancellation
from app.services.executor_cancellation import CancellationTarget


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _SnapshotSession:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.statements: list[object] = []

    async def execute(self, statement: object) -> _Rows:
        self.statements.append(statement)
        return _Rows(self.rows)


@pytest.mark.asyncio
async def test_snapshot_targets_keeps_executor_submission_case_and_platform() -> None:
    session = _SnapshotSession(
        [
            ("ai_phone", "phone-sub", "cf-7", "android", 7),
            ("ai_phone", "phone-sub", "cf-7", "android", 7),
            ("ai_web", "web-sub", "cf-7", "chrome", 7),
        ]
    )

    targets = await executor_cancellation.snapshot_standard_targets(
        session,  # type: ignore[arg-type]
        case_id=7,
        active_batch_id=12,
        external_submission_id="phone-sub",
    )

    assert targets == [
        CancellationTarget("ai_phone", "phone-sub", "cf-7", "android", 7),
        CancellationTarget("ai_web", "web-sub", "cf-7", "chrome", 7),
    ]
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_dispatch_routes_mixed_executors_without_consuming_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []
    stopped_aiapi: list[tuple[str, list[int] | None]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(
            self,
            url: str,
            *,
            params: dict[str, str] | None = None,
            json: dict[str, object] | None = None,
        ) -> Response:
            requests.append({"url": url, "params": params, "json": json})
            return Response()

    monkeypatch.setattr(
        executor_cancellation,
        "get_settings",
        lambda: SimpleNamespace(
            aiphone_base_url="http://phone.local",
            aiweb_base_url="http://web.local",
            aihybrid_base_url="http://hybrid.local/aihybrid",
        ),
    )
    monkeypatch.setattr(executor_cancellation.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        executions,
        "stop_aiapi_execution",
        lambda submission_id, case_ids=None: stopped_aiapi.append((submission_id, case_ids)),
    )

    await executor_cancellation.dispatch_cancellation(
        "standard",
        [
            CancellationTarget("ai_phone", "phone-sub", "cf-1", "android", 1),
            CancellationTarget("ai_web", "web-sub", "cf-2", "chrome", 2),
            CancellationTarget("ai_hybrid", "hybrid-sub", "cf-3", "mixed", 3),
            CancellationTarget("ai_hybrid", "hybrid-sub", "cf-4", "mixed", 4),
            CancellationTarget("ai_api", "api-sub", "cf-5", "api", 5),
            CancellationTarget("ai_api", "api-sub", "cf-6", "api", 6),
        ],
    )

    assert stopped_aiapi == [("api-sub", [5, 6])]
    assert {request["url"] for request in requests} == {
        "http://phone.local/api/submissions/phone-sub/cases/cf-1/cancel",
        "http://web.local/api/submissions/web-sub/cases/cf-2/cancel",
        "http://hybrid.local/aihybrid/api/submissions/hybrid-sub/cancel",
    }
    assert next(request for request in requests if request["url"].startswith("http://phone"))["params"] == {
        "platform": "android"
    }
    assert next(request for request in requests if request["url"].startswith("http://web"))["params"] == {
        "platform": "chrome"
    }
    assert next(request for request in requests if request["url"].startswith("http://hybrid"))["json"] == {
        "caseIds": ["cf-3", "cf-4"]
    }


@pytest.mark.asyncio
async def test_external_cancel_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, **_kwargs: object) -> object:
            calls.append(url)
            raise httpx.ConnectError("executor unavailable")

    monkeypatch.setattr(
        executor_cancellation,
        "get_settings",
        lambda: SimpleNamespace(
            aiphone_base_url="http://phone.local",
            aiweb_base_url="http://web.local",
            aihybrid_base_url="http://hybrid.local/aihybrid",
        ),
    )
    monkeypatch.setattr(executor_cancellation.httpx, "AsyncClient", Client)

    await executor_cancellation.dispatch_cancellation(
        "standard",
        [CancellationTarget("ai_phone", "phone-sub", "cf-1", "android", 1)],
    )

    assert calls == ["http://phone.local/api/submissions/phone-sub/cases/cf-1/cancel"]
