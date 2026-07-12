from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.api.v1.routes import quick as quick_routes
from app.main import create_app


@pytest.mark.parametrize(
    ("path", "handler_name"),
    [
        ("/api/v1/quick/aiphone/callback/bad-token", "apply_aiphone_callback"),
        ("/api/v1/quick/aiweb/callback/bad-token", "apply_aiweb_callback"),
    ],
)
def test_quick_callbacks_return_400_for_business_errors(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    handler_name: str,
) -> None:
    async def raise_value_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ValueError("callback mapping not found")

    monkeypatch.setattr(quick_routes.quick_executions, handler_name, raise_value_error)

    client = TestClient(create_app())
    response = client.post(path, json={"submissionId": "sub-1"})

    assert response.status_code == 400
    assert response.json()["detail"] == "callback mapping not found"
