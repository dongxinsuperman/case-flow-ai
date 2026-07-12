from __future__ import annotations

from typing import Any

AI_PHONE_PLATFORMS = {"android", "ios", "harmony"}
AI_WEB_PLATFORMS = {"chrome", "safari", "firefox"}
COVERAGE_EXEC_LANES = {
    "ai_phone": AI_PHONE_PLATFORMS,
    "ai_web": AI_WEB_PLATFORMS,
}

_AI_WEB_PLATFORM_ALIASES = {
    "chrome": "chrome",
    "chromium": "chrome",
    "firefox": "firefox",
    "safari": "safari",
    "webkit": "safari",
}


def normalize_executor_platform(platform: Any, executor_key: str | None = None) -> str:
    value = str(platform or "").strip().lower()
    if executor_key == "ai_web":
        if not value:
            return ""
        return _AI_WEB_PLATFORM_ALIASES.get(value, value or "chrome")
    return value


def normalize_device_alias_pools(
    device_alias_pools: dict[str, list[str]] | None,
    executor_key: str,
) -> dict[str, list[str]] | None:
    if not device_alias_pools:
        return None
    normalized: dict[str, list[str]] = {}
    for platform, aliases in device_alias_pools.items():
        if aliases is not None and not isinstance(aliases, list):
            continue
        key = normalize_executor_platform(platform, executor_key)
        if not key:
            continue
        normalized.setdefault(key, [])
        normalized[key].extend([str(alias) for alias in (aliases or [])])
    return normalized or None


def normalize_executor_device(device: dict[str, Any], executor_key: str) -> dict[str, Any]:
    if executor_key != "ai_web":
        return device
    normalized = dict(device)
    normalized["platform"] = normalize_executor_platform(normalized.get("platform"), executor_key)
    return normalized


def executor_callback_base_url(settings: Any, executor_key: str, label: str) -> str:
    if executor_key == "ai_web":
        base = str(getattr(settings, "aiweb_callback_base_url", "") or "").strip()
        if not base:
            raise ValueError(f"{label} 回调地址未配置：请设置 CASE_FLOW_AIWEB_CALLBACK_BASE_URL")
        return base.rstrip("/")
    return str(getattr(settings, "public_base_url", "") or "").rstrip("/")
