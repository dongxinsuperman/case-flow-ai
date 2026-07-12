"""飞书项目 MCP 第二通道。

只用于 bug 描述中的富文本图片渲染。首次建单、字段、报告人仍走
`feishu_project.FeishuProjectClient` 的 OpenAPI 路径。
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.core.settings import get_settings


class FeishuProjectMCPError(RuntimeError):
    pass


class FeishuProjectMCPClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._rpc_id = 0

    @property
    def enabled(self) -> bool:
        return bool(self._settings.feishu_project_mcp_token)

    def _url(self) -> str:
        return self._settings.feishu_project_mcp_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "X-Mcp-Token": self._settings.feishu_project_mcp_token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    async def call_tool(
        self,
        client: httpx.AsyncClient,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled:
            raise FeishuProjectMCPError("飞书项目 MCP token 未配置")
        self._rpc_id += 1
        resp = await client.post(
            self._url(),
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": self._rpc_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        payload = _json_response(resp, "MCP tools/call")
        if resp.status_code >= 400:
            raise FeishuProjectMCPError(_error_message("MCP tools/call HTTP 失败", payload, resp))
        if payload.get("error"):
            raise FeishuProjectMCPError(_error_message("MCP tools/call 失败", payload, resp))

        result = payload.get("result")
        if not isinstance(result, dict):
            raise FeishuProjectMCPError(_error_message("MCP tools/call 返回缺少 result", payload, resp))
        if result.get("isError"):
            raise FeishuProjectMCPError(_error_message("MCP tools/call 返回错误", payload, resp))

        text = _first_content_text(result)
        if _looks_like_tool_error(text):
            raise FeishuProjectMCPError(_error_message(f"MCP 工具 {name} 返回错误：{text}", payload, resp))
        parsed = _parse_tool_text(text)
        if isinstance(parsed, dict):
            if parsed.get("isError") or _looks_like_tool_error(json.dumps(parsed, ensure_ascii=False)):
                raise FeishuProjectMCPError(_error_message(f"MCP 工具 {name} 返回错误", parsed, resp))
            return parsed
        return {"text": text}

    async def upload_rich_text_image(
        self,
        client: httpx.AsyncClient,
        local_path: str | Path,
        project_key: str,
        work_item_id: int | str,
        work_item_type: str = "issue",
    ) -> tuple[str, str]:
        path = Path(local_path)
        raw = path.read_bytes()
        if not raw:
            raise FeishuProjectMCPError(f"图片为空：{path}")
        mime = _image_mime(path)
        upload_info = await self.call_tool(
            client,
            "upload_file",
            {
                "size": len(raw),
                "project_key": project_key,
                "work_item_id": str(work_item_id),
                "work_item_type": work_item_type,
                "file_name": path.name,
                "mime_type": mime,
                "resource_type": 16,
            },
        )
        sign = str(upload_info.get("sign") or "")
        upload_url = str(upload_info.get("upload_url") or "")
        if not sign or not upload_url:
            raise FeishuProjectMCPError(f"MCP upload_file 未返回 sign/upload_url：{upload_info}")

        upload_result = await self._signed_upload(
            client,
            upload_url,
            sign,
            raw,
            mime,
            bool(upload_info.get("is_multipart")),
        )
        file_url = str((upload_result.get("data") or {}).get("file_url") or "")
        file_token = str((upload_result.get("data") or {}).get("file_token") or "")
        if not file_url or not file_token:
            raise FeishuProjectMCPError(f"签名上传未返回 file_url/file_token：{upload_result}")
        return file_url, file_token

    async def _signed_upload(
        self,
        client: httpx.AsyncClient,
        upload_url: str,
        sign: str,
        raw: bytes,
        mime: str,
        multipart: bool,
    ) -> dict[str, Any]:
        chunks = _upload_chunks(raw) if multipart else [raw]
        last_payload: dict[str, Any] | None = None
        for index, chunk in enumerate(chunks):
            url = upload_url.replace(":part_number", str(index))
            resp = await client.post(
                url,
                headers={"X-Meego-File-Sign": sign, "Content-Type": mime},
                content=chunk,
            )
            payload = _json_response(resp, "MCP signed upload")
            if resp.status_code >= 400 or payload.get("code") not in (0, None):
                raise FeishuProjectMCPError(_error_message("MCP signed upload 失败", payload, resp))
            last_payload = payload
        if last_payload is None:
            raise FeishuProjectMCPError("MCP signed upload 没有上传任何分片")
        return last_payload


def _upload_chunks(raw: bytes) -> list[bytes]:
    part_size = 4 * 1024 * 1024
    return [raw[index : index + part_size] for index in range(0, len(raw), part_size)]


def _image_mime(path: Path) -> str:
    guessed = (mimetypes.guess_type(path.name)[0] or "").lower()
    return guessed if guessed.startswith("image/") else "image/png"


def _json_response(resp: httpx.Response, action: str) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError as exc:
        raise FeishuProjectMCPError(
            f"{action} 返回非 JSON：HTTP {resp.status_code} {resp.text[:200]}"
        ) from exc
    return payload if isinstance(payload, dict) else {"payload": payload}


def _first_content_text(result: dict[str, Any]) -> str:
    content = result.get("content") or []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("text") is not None:
                return str(item.get("text") or "")
    return ""


def _parse_tool_text(text: str) -> Any:
    clean = str(text or "").strip()
    if not clean:
        return {}
    try:
        return json.loads(clean)
    except ValueError:
        return clean


def _looks_like_tool_error(text: str) -> bool:
    lower = str(text or "").lower()
    return (
        ("code=" in lower and "message=" in lower)
        or "data not found" in lower
        or "user has not enabled this mcp feature" in lower
        or "invalid multi text img src" in lower
    )


def _error_message(prefix: str, payload: dict[str, Any], resp: httpx.Response | None = None) -> str:
    logid = ""
    if resp is not None:
        logid = (
            resp.headers.get("x-tt-logid")
            or resp.headers.get("x-lark-log-id")
            or resp.headers.get("x-request-id")
            or ""
        )
    suffix = f" logid={logid}" if logid else ""
    return f"{prefix}{suffix}：{json.dumps(payload, ensure_ascii=False)[:800]}"
