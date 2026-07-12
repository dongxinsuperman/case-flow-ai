from __future__ import annotations

from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api.v1.routes import agent as agent_routes
from app.api.v1.routes import health as health_routes
from app.main import create_app


def test_healthz() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_app_config_exposes_os_agent_flag(monkeypatch) -> None:
    monkeypatch.setattr(health_routes, "get_settings", lambda: SimpleNamespace(os_agent_enabled=True))

    client = TestClient(create_app())
    response = client.get("/api/v1/config")

    assert response.status_code == 200
    assert response.json()["os_agent_enabled"] is True


def test_os_agent_routes_are_disabled_by_feature_flag(monkeypatch) -> None:
    monkeypatch.setattr(agent_routes, "get_settings", lambda: SimpleNamespace(os_agent_enabled=False))

    client = TestClient(create_app())
    response = client.get("/api/v1/agent/session", params={"user_id": 1})

    assert response.status_code == 404
    assert response.json()["detail"] == "OS Agent 未启用"
