from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.executor_platforms import (
    executor_callback_base_url,
    normalize_executor_device,
    normalize_executor_platform,
)
from app.services.executions import (
    _build_submitted_units,
    _device_effective_status,
    _device_identity,
    _normalize_busy_device,
    _platforms_from_device_alias_pools,
)


def test_aiweb_busy_device_supports_camel_case_status_fields() -> None:
    device = {
        "alias": "chrome-2",
        "platform": "chrome",
        "effectiveStatus": "busy",
        "screenWidth": 1440,
        "screenHeight": 900,
        "lastSeenAt": "2026-06-27T12:00:00Z",
        "lock": {"holderType": "submission"},
    }

    assert _device_identity(device) == "chrome-2"
    assert _device_effective_status(device) == "busy"
    assert _normalize_busy_device(device) == {
        "serial": "chrome-2",
        "alias": "chrome-2",
        "platform": "chrome",
        "brand": "",
        "model": "",
        "osVersion": "",
        "screenWidth": 1440,
        "screenHeight": 900,
        "lastSeenAt": "2026-06-27T12:00:00Z",
        "occupancy": "busy",
        "lockHolderType": "submission",
    }


def test_aiphone_busy_device_still_supports_snake_case_status_fields() -> None:
    device = {
        "serial": "android-1",
        "alias": "A1",
        "platform": "android",
        "effective_status": "busy",
        "os_version": "15",
        "screen_width": 1080,
        "screen_height": 2400,
        "last_seen_at": "2026-06-27T12:00:00Z",
        "lock": {"holder_type": "submission"},
    }

    assert _device_identity(device) == "android-1"
    assert _device_effective_status(device) == "busy"
    normalized = _normalize_busy_device(device)
    assert normalized["serial"] == "android-1"
    assert normalized["osVersion"] == "15"
    assert normalized["screenWidth"] == 1080
    assert normalized["screenHeight"] == 2400
    assert normalized["lastSeenAt"] == "2026-06-27T12:00:00Z"
    assert normalized["lockHolderType"] == "submission"


def test_aiweb_platform_aliases_normalize_to_case_flow_lanes() -> None:
    assert normalize_executor_platform("webkit", "ai_web") == "safari"
    assert normalize_executor_platform("safari", "ai_web") == "safari"
    assert normalize_executor_platform("chromium", "ai_web") == "chrome"
    assert normalize_executor_platform("firefox", "ai_web") == "firefox"
    assert normalize_executor_platform("ios", "ai_phone") == "ios"


def test_aiweb_device_webkit_is_exposed_as_safari() -> None:
    device = {"alias": "Safari #1", "platform": "webkit", "brand": "WebKit"}

    normalized = normalize_executor_device(device, "ai_web")

    assert normalized["platform"] == "safari"
    assert normalized["alias"] == "Safari #1"


def test_aiweb_platforms_follow_selected_browser_lanes() -> None:
    pools = {
        "chrome": ["Chrome #1"],
        "webkit": ["Safari #1"],
        "firefox": ["Firefox #1"],
    }

    assert _platforms_from_device_alias_pools(pools, "chrome", "ai_web") == [
        "chrome",
        "safari",
        "firefox",
    ]


def test_aiweb_response_webkit_item_is_stored_as_safari() -> None:
    units = _build_submitted_units(
        {"items": [{"caseId": "cf-1", "platform": "webkit", "state": "queued"}]},
        [{"_case_id": 1, "caseId": "cf-1"}],
        "chrome",
        "ai_web",
    )

    assert units[0]["platform"] == "safari"


def test_aiweb_callback_uses_dedicated_base_url() -> None:
    settings = SimpleNamespace(
        public_base_url="http://case-flow-phone.local",
        aiweb_callback_base_url="http://case-flow-web.local/",
    )

    assert executor_callback_base_url(settings, "ai_web", "AI Web") == "http://case-flow-web.local"


def test_aiweb_callback_requires_dedicated_base_url() -> None:
    settings = SimpleNamespace(public_base_url="http://case-flow-phone.local", aiweb_callback_base_url="")

    with pytest.raises(ValueError, match="CASE_FLOW_AIWEB_CALLBACK_BASE_URL"):
        executor_callback_base_url(settings, "ai_web", "AI Web")


def test_aiphone_callback_keeps_public_base_url() -> None:
    settings = SimpleNamespace(
        public_base_url="http://case-flow-phone.local/",
        aiweb_callback_base_url="http://case-flow-web.local",
    )

    assert executor_callback_base_url(settings, "ai_phone", "AI Phone") == "http://case-flow-phone.local"
