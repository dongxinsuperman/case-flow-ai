from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.sources import feishu_project_mcp


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        feishu_project_mcp_url="https://mcp.local/mcp_server/v1",
        feishu_project_mcp_token="mcp-token",
    )


@pytest.mark.asyncio
async def test_upload_rich_text_image_uses_mcp_upload_and_signed_post(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feishu_project_mcp, "get_settings", _settings)
    image = tmp_path / "shot.png"
    image.write_bytes(b"png-bytes")
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mcp.local":
            body = json.loads(request.content)
            calls.append(body)
            assert request.headers["X-Mcp-Token"] == "mcp-token"
            assert body["params"]["name"] == "upload_file"
            assert body["params"]["arguments"]["resource_type"] == 16
            return httpx.Response(
                200,
                json={
                    "result": {
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "is_multipart": False,
                                        "sign": "signed",
                                        "upload_url": "https://upload.local/upload/16/:part_number",
                                    }
                                )
                            }
                        ]
                    }
                },
            )
        assert request.url.host == "upload.local"
        assert str(request.url).endswith("/upload/16/0")
        assert request.headers["X-Meego-File-Sign"] == "signed"
        assert request.content == b"png-bytes"
        return httpx.Response(
            200,
            json={"code": 0, "data": {"file_url": "https://file.local/shot.png", "file_token": "token-1"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = feishu_project_mcp.FeishuProjectMCPClient()
        file_url, file_token = await client.upload_rich_text_image(http, image, "proj", 123, "issue")

    assert file_url == "https://file.local/shot.png"
    assert file_token == "token-1"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_call_tool_raises_on_mcp_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feishu_project_mcp, "get_settings", _settings)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"result": {"content": [{"text": "code=123 message=invalid multi text img src"}]}},
            headers={"x-tt-logid": "log-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = feishu_project_mcp.FeishuProjectMCPClient()
        with pytest.raises(feishu_project_mcp.FeishuProjectMCPError) as exc:
            await client.call_tool(http, "update_field", {})

    assert "invalid multi text img src" in str(exc.value)
    assert "log-1" in str(exc.value)
