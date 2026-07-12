from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse

from app.services.ai_api.schemas import (
    AIAPIExecutionPlan,
    AIAPISecurityConfig,
    APIRequestPlan,
    SecurityValidationResult,
)


def validate_plan_security(
    plan: AIAPIExecutionPlan,
    config: AIAPISecurityConfig,
) -> SecurityValidationResult:
    if not plan.executable or plan.request is None:
        return SecurityValidationResult(allowed=False, reason="执行计划不可执行或缺少 request")

    return validate_request_security(plan.request, config)


def validate_request_security(
    request: APIRequestPlan,
    config: AIAPISecurityConfig,
) -> SecurityValidationResult:
    method = request.method.upper()
    if method not in {item.upper() for item in config.allowed_methods}:
        return SecurityValidationResult(
            allowed=False,
            method=method,
            reason=f"HTTP method {method} 未在允许列表内",
        )

    url = _resolve_url(request.url, request.path, config.default_base_url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return SecurityValidationResult(allowed=False, method=method, url=url, reason="只允许 http/https")
    if not parsed.hostname:
        return SecurityValidationResult(allowed=False, method=method, url=url, reason="URL 缺少 host")

    host = parsed.hostname.lower()
    if not config.allow_private_networks and _is_private_or_local_host(host):
        return SecurityValidationResult(
            allowed=False,
            method=method,
            url=url,
            reason=f"host {host} 属于本机或私网地址，当前配置禁止访问",
        )

    if _has_allowlist(config) and not _matches_allowlist(url, host, config):
        return SecurityValidationResult(
            allowed=False,
            method=method,
            url=url,
            reason="URL 未命中 AI API allowlist",
        )

    headers = _safe_headers({**config.default_headers, **request.headers})
    return SecurityValidationResult(allowed=True, method=method, url=url, headers=headers)


def _resolve_url(url: str | None, path: str | None, default_base_url: str) -> str:
    if url:
        return str(url).strip()
    base = str(default_base_url or "").strip()
    if not base:
        return str(path or "").strip()
    return urljoin(base.rstrip("/") + "/", str(path or "").lstrip("/"))


def _matches_allowlist(url: str, host: str, config: AIAPISecurityConfig) -> bool:
    for allowed in config.allowed_hosts:
        if _host_matches(host, allowed):
            return True
    parsed = urlparse(url)
    for base in config.allowed_base_urls:
        base_parsed = urlparse(base)
        if parsed.scheme != base_parsed.scheme or parsed.netloc.lower() != base_parsed.netloc.lower():
            continue
        base_path = (base_parsed.path or "/").rstrip("/") + "/"
        path = (parsed.path or "/").rstrip("/") + "/"
        if path.startswith(base_path):
            return True
    return False


def _has_allowlist(config: AIAPISecurityConfig) -> bool:
    return bool(config.allowed_hosts or config.allowed_base_urls)


def _host_matches(host: str, pattern: str) -> bool:
    normalized = str(pattern or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("*."):
        suffix = normalized[1:]
        return host.endswith(suffix) and host != normalized[2:]
    return host == normalized


def _is_private_or_local_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = {"host", "content-length", "transfer-encoding", "connection"}
    safe: dict[str, str] = {}
    for key, value in headers.items():
        normalized = str(key or "").strip()
        if not normalized or normalized.lower() in blocked:
            continue
        safe[normalized] = str(value)
    return safe
