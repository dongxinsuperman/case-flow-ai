"""Hybrid 主脑读取完整参考 Function Map 的目录和正文。

结构化 maps 是首选输入；其中的 targets 明确每份 Map 的 app/web/api 适用端。兼容外部只传
functionMapContext 的旧调用；两者同时存在时合并去重，无法识别边界的旧文本整体保留为一个块，
绝不静默丢弃上下文。
"""
from __future__ import annotations

import re
from typing import Any

_HEADER_RE = re.compile(r"^--- FUNCTION MAP: (?P<title>.*?) ---\s*$", re.MULTILINE)
_ID_RE = re.compile(r"^资产 ID:\s*(?P<id>.*)$", re.MULTILINE)


def build_catalog(function_maps: list[dict[str, Any]] | None, context: str = "") -> list[dict[str, Any]]:
    """返回无正文的完整目录，供模型再逐份读取每个 Map。"""
    return [
        {
            "asset_id": entry.get("asset_id"),
            "title": entry.get("title"),
            "description": entry.get("description") or "",
            "targets": list(entry.get("targets") or []),
        }
        for entry in _entries(function_maps, context)
    ]


def read_block(
    function_maps: list[dict[str, Any]] | None, context: str, wanted: str
) -> dict[str, Any] | None:
    """按 asset_id 或标题返回一份完整正文与适用端，不截断。"""
    target = str(wanted or "").strip().lower()
    if not target:
        return None
    for entry in _entries(function_maps, context):
        asset_id = str(entry.get("asset_id") or "").strip().lower()
        title = str(entry.get("title") or "").strip().lower()
        if target in {asset_id, title}:
            return {
                "asset_id": entry.get("asset_id"),
                "title": entry.get("title"),
                "description": entry.get("description") or "",
                "targets": list(entry.get("targets") or []),
                "content": entry.get("content") or "",
            }
    return None


def _entries(function_maps: list[dict[str, Any]] | None, context: str) -> list[dict[str, Any]]:
    structured = [entry for entry in (function_maps or []) if isinstance(entry, dict)]
    merged = list(structured)
    seen = {_dedup_key(entry) for entry in structured}
    for block in _parse_context_blocks(context):
        key = _dedup_key(block)
        if key not in seen:
            seen.add(key)
            merged.append(block)
    return merged


def _dedup_key(entry: dict[str, Any]) -> tuple[str, str]:
    asset_id = entry.get("asset_id")
    if asset_id is not None and str(asset_id).strip():
        return ("id", str(asset_id).strip().lower())
    return ("title", str(entry.get("title") or "").strip().lower())


def _parse_context_blocks(context: str) -> list[dict[str, Any]]:
    text = str(context or "")
    if not text.strip():
        return []
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return [{"asset_id": None, "title": "functionMap", "content": text.strip()}]
    blocks: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        meta, content = _split_meta_content(text[start:end])
        id_match = _ID_RE.search(meta)
        blocks.append(
            {
                "asset_id": id_match.group("id").strip() if id_match else None,
                "title": match.group("title").strip(),
                "content": content,
            }
        )
    return blocks


def _split_meta_content(segment: str) -> tuple[str, str]:
    parts = segment.split("\n\n", 1)
    return parts[0], parts[1].strip() if len(parts) > 1 else ""
