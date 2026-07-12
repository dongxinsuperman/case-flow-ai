from __future__ import annotations

import os
from typing import Any

from app.core.settings import get_settings


def llm_client(settings: Any | None = None) -> tuple[Any | None, Any]:
    settings = settings or get_settings()
    api_key = (
        getattr(settings, "llm_api_key", "")
        or os.environ.get("ARK_API_KEY")
        or os.environ.get("CASE_FLOW_LLM_API_KEY")
    )
    if not api_key:
        return None, settings
    try:
        from openai import OpenAI
    except Exception:
        return None, settings
    kwargs: dict[str, Any] = {"api_key": api_key}
    if getattr(settings, "llm_base_url", ""):
        kwargs["base_url"] = settings.llm_base_url
    return OpenAI(**kwargs), settings
