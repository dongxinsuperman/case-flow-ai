from __future__ import annotations

import asyncio
from typing import Any

_pending: dict[str, dict[str, Any]] = {}


def register(token: str, base_url: str = "") -> asyncio.Event:
    event = asyncio.Event()
    _pending[token] = {"event": event, "result": None, "base_url": str(base_url or "")}
    return event


def resolve(token: str, result: dict[str, Any]) -> bool:
    slot = _pending.get(token)
    if not slot:
        return False
    slot["result"] = result
    event = slot.get("event")
    if isinstance(event, asyncio.Event):
        event.set()
    return True


def take_result(token: str) -> dict[str, Any] | None:
    slot = _pending.pop(token, None)
    if not slot:
        return None
    result = slot.get("result")
    return result if isinstance(result, dict) else None


def base_url_for(token: str) -> str:
    slot = _pending.get(token)
    return str((slot or {}).get("base_url") or "")


def forget(token: str) -> None:
    _pending.pop(token, None)
