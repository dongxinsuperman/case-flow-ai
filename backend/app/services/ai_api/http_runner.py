from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.services.ai_api.schemas import (
    AIAPIExecutionPlan,
    AIAPISecurityConfig,
    APIRequestPlan,
    HTTPExchange,
    SecurityValidationResult,
)


async def execute_http_plan(
    plan: AIAPIExecutionPlan,
    security: SecurityValidationResult,
    config: AIAPISecurityConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HTTPExchange:
    if plan.request is None:
        return HTTPExchange(
            method=security.method,
            url=security.url,
            request_headers={},
            request_body=None,
            error="执行计划缺少 request",
        )
    return await execute_http_request(plan.request, security, config, transport=transport)


async def execute_http_request(
    request_plan: APIRequestPlan,
    security: SecurityValidationResult,
    config: AIAPISecurityConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HTTPExchange:
    timeout = min(int(request_plan.timeout_seconds or config.max_timeout_seconds), config.max_timeout_seconds)
    headers = security.headers
    params = request_plan.query or {}
    body_kwargs = _body_kwargs(request_plan.body_type, request_plan.body)
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    request_body = request_plan.body
    request_bytes = _estimate_request_bytes(request_plan.body_type, request_plan.body)

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=config.follow_redirects,
            transport=transport,
        ) as client:
            request = client.build_request(
                security.method,
                security.url,
                headers=headers,
                params=params,
                **body_kwargs,
            )
            request_bytes = _request_content_length(request, request_bytes)
            response = await client.send(request, stream=True)
            content, truncated = await _read_limited(response, config.max_response_bytes)
            await response.aclose()
    except Exception as exc:
        finished_at = datetime.now(UTC)
        return HTTPExchange(
            method=security.method,
            url=security.url,
            request_headers=headers,
            request_body=request_body,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            request_bytes=request_bytes,
            error=str(exc),
            started_at=started_at,
            finished_at=finished_at,
        )

    text = _decode_response_text(content, response)
    finished_at = datetime.now(UTC)
    return HTTPExchange(
        method=security.method,
        url=str(response.url),
        request_headers=headers,
        request_body=request_body,
        status_code=response.status_code,
        response_headers=dict(response.headers),
        response_text=text,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        request_bytes=request_bytes,
        response_bytes=len(content),
        truncated=truncated,
        started_at=started_at,
        finished_at=finished_at,
    )


def _body_kwargs(body_type: str, body: Any) -> dict[str, Any]:
    if body_type == "json":
        return {"json": body}
    if body_type == "form":
        return {"data": body or {}}
    if body_type == "raw":
        if isinstance(body, (dict, list)):
            return {"content": json.dumps(body, ensure_ascii=False)}
        return {"content": "" if body is None else str(body)}
    return {}


def _estimate_request_bytes(body_type: str, body: Any) -> int:
    if body_type == "none" or body is None:
        return 0
    if body_type == "json":
        return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    if body_type == "raw":
        if isinstance(body, (dict, list)):
            return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        return len(str(body).encode("utf-8"))
    if body_type == "form":
        if isinstance(body, dict):
            return len("&".join(f"{key}={value}" for key, value in body.items()).encode("utf-8"))
    return 0


def _request_content_length(request: httpx.Request, fallback: int) -> int:
    try:
        return len(request.content)
    except Exception:
        return fallback


async def _read_limited(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    if max_bytes <= 0:
        return await response.aread(), False
    chunks: list[bytes] = []
    total = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        remaining = max_bytes - total
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), truncated


def _decode_response_text(content: bytes, response: httpx.Response) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")
