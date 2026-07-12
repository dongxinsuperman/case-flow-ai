from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_assets import CaseAsset, CaseBody, CaseRawNode, ImportBatch
from app.schemas.workbench import CaseSuiteMarkdownExportOut
from app.services.markdown_import_config import CORE_ROLES, get_markdown_import_config
from app.services.markdown_text import collapse_inline_text


async def export_case_suite_markdown(
    session: AsyncSession,
    requirement_item_id: int,
    batch_id: int,
) -> CaseSuiteMarkdownExportOut | None:
    statement = select(ImportBatch).where(
        ImportBatch.requirement_item_id == requirement_item_id,
        ImportBatch.id == batch_id,
    )
    batch = await session.scalar(statement.order_by(ImportBatch.id).limit(1))
    if batch is None:
        return None

    rows = await session.execute(
        select(CaseAsset, CaseBody, CaseRawNode)
        .join(CaseBody, CaseBody.case_id == CaseAsset.id)
        .outerjoin(CaseRawNode, CaseRawNode.case_id == CaseAsset.id)
        .where(CaseAsset.batch_id == batch.id)
        .order_by(CaseAsset.ordinal, CaseAsset.id)
    )
    cases = [
        {
            "id": case.id,
            "ordinal": case.ordinal,
            "path_nodes": _case_path_nodes(case),
            "core_nodes": _core_nodes(raw_node),
            "raw_title": case.raw_title,
            "preconditions": body.preconditions,
            "steps_text": body.steps_text,
            "expected_result": body.expected_result,
        }
        for case, body, raw_node in rows.all()
    ]
    return CaseSuiteMarkdownExportOut(
        batch_id=batch.id,
        suite_title=batch.suite_title,
        filename=_export_filename(batch.source_name),
        content=render_standard_markdown(batch.suite_title, cases),
        case_count=len(cases),
    )


def render_standard_markdown(suite_title: str, cases: list[dict[str, Any]]) -> str:
    lines = [f"- {suite_title}"]
    root: list[dict[str, Any]] = []
    root_map: dict[str, dict[str, Any]] = {}

    for case in cases:
        groups = root
        group_map = root_map
        current_group: dict[str, Any] | None = None
        for node in case.get("path_nodes") or []:
            text = _node_text(node)
            if not text:
                continue
            key = f"{node.get('label') or ''}\u0000{text}"
            group = group_map.get(key)
            if group is None:
                group = {"text": text, "children": [], "child_map": {}, "cases": []}
                group_map[key] = group
                groups.append(group)
            current_group = group
            groups = group["children"]
            group_map = group["child_map"]
        if current_group is None:
            root.append({"case": case})
        else:
            current_group["cases"].append(case)

    for entry in root:
        _render_entry(lines, entry, 1)
    return "\n".join(lines) + "\n"


def _render_entry(lines: list[str], entry: dict[str, Any], depth: int) -> None:
    if "case" in entry:
        _render_case(lines, entry["case"], depth)
        return
    lines.append(f"{'  ' * depth}- {collapse_inline_text(entry['text'])}")
    for child in entry["children"]:
        _render_entry(lines, child, depth + 1)
    for case in entry["cases"]:
        _render_case(lines, case, depth + 1)


def _render_case(lines: list[str], case: dict[str, Any], depth: int) -> None:
    core_nodes = case.get("core_nodes") or {}
    raw_title = collapse_inline_text(case.get("raw_title"))
    if not raw_title:
        raise ValueError("导出 Case 缺少完整原始标题 raw_title")
    values = {
        "case_title": raw_title,
        "preconditions": case.get("preconditions") or "",
        "steps": case.get("steps_text") or "",
        "expected": case.get("expected_result") or "",
    }
    current_depth = depth
    for role in CORE_ROLES:
        node = core_nodes.get(role) if isinstance(core_nodes, dict) else None
        text = _core_export_text(role, values[role], node if isinstance(node, dict) else None)
        lines.append(f"{'  ' * current_depth}- {text}")
        current_depth += 1


def _core_export_text(role: str, value: str, node: dict[str, Any] | None) -> str:
    value = collapse_inline_text(value)
    if not isinstance(node, dict):
        return value
    label = str(node.get("label") or _configured_core_label(role))
    raw_text = str(node.get("rawText") or node.get("raw_text") or "")
    separator = _matched_separator(raw_text, label) or node.get("separator")
    if not separator and node.get("trimmed"):
        separator = "："
    if not separator:
        return value
    clean_value = _strip_existing_prefix(value, label, str(separator))
    return f"{label}{separator}{clean_value}"


def _strip_existing_prefix(value: str, label: str, preferred_separator: str) -> str:
    raw = collapse_inline_text(value)
    separators = tuple(dict.fromkeys((preferred_separator, *_configured_trim_separators())))
    for separator in separators:
        prefix = f"{label}{separator}"
        if raw.startswith(prefix):
            return raw.removeprefix(prefix).strip()
    return raw


def _matched_separator(raw_text: str, label: str) -> str | None:
    raw = collapse_inline_text(raw_text)
    for separator in _configured_trim_separators():
        if raw.startswith(f"{label}{separator}"):
            return separator
    return None


def _configured_core_label(role: str) -> str:
    return get_markdown_import_config().core_label(role)


def _configured_trim_separators() -> tuple[str, ...]:
    return get_markdown_import_config().trim_separators


def _case_path_nodes(case: CaseAsset) -> list[dict[str, Any]]:
    nodes = getattr(case, "path_nodes", None)
    return [dict(node) for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _core_nodes(raw_node: CaseRawNode | None) -> dict[str, Any]:
    raw_payload = raw_node.raw_payload if raw_node and isinstance(raw_node.raw_payload, dict) else {}
    nodes = raw_payload.get("core_nodes") if isinstance(raw_payload, dict) else {}
    return dict(nodes) if isinstance(nodes, dict) else {}


def _node_text(node: dict[str, Any]) -> str:
    return collapse_inline_text(
        node.get("displayText")
        or node.get("display_text")
        or node.get("rawText")
        or node.get("raw_text")
        or ""
    )


def _export_filename(source_name: str) -> str:
    source = (source_name or "case-suite.md").strip()
    if source.lower().endswith(".md"):
        return source
    return f"{source}.md"
