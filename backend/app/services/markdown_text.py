from __future__ import annotations

import re
from typing import Any


TITLE_TAG_RE = re.compile(r"^(?:【[^】]+】)+")
ONE_TITLE_TAG_RE = re.compile(r"【([^】]+)】")


def collapse_inline_text(value: Any) -> str:
    """Normalize Markdown case field values to a single physical line."""
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    return " ".join(lines)


def append_inline_text(base: Any, extra: Any) -> str:
    return collapse_inline_text(f"{base or ''}\n{extra or ''}")


def extract_title_tags(value: Any) -> list[str]:
    """Copy leading ``【tag】`` markers as metadata without altering the title."""
    title = collapse_inline_text(value)
    match = TITLE_TAG_RE.match(title)
    if not match:
        return []
    return ONE_TITLE_TAG_RE.findall(match.group(0))
