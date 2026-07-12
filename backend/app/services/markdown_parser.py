from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.markdown_import_config import (
    CORE_ROLES,
    MarkdownImportConfig,
    get_markdown_import_config,
    trim_configured_prefix,
)
from app.services.markdown_text import append_inline_text, collapse_inline_text, extract_title_tags


class ParseError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class ParsedCase:
    ordinal: int
    suite_title: str
    path_nodes: list[dict[str, Any]]
    core_nodes: dict[str, dict[str, Any]]
    core_labels: dict[str, str]
    module_name: str
    product_feature: str
    test_feature: str
    raw_title: str
    clean_title: str
    scenario_tags: list[str]
    manual: bool
    preconditions: str
    steps_text: str
    step_items: list[str]
    expected_result: str


@dataclass(frozen=True)
class ParsedMarkdown:
    source_name: str
    suite_title: str
    cases: list[ParsedCase]
    warnings: list[str]


LIST_RE = re.compile(r"^(?P<indent> *)-\s+(?P<text>.+?)\s*$")
def parse_markdown_file(path: str | Path) -> ParsedMarkdown:
    source = Path(path)
    if source.suffix.lower() != ".md":
        raise ParseError([f"仅支持 .md 文件：{source}"])
    if not source.exists():
        raise ParseError([f"文件不存在：{source}"])
    return parse_markdown(source.read_text(encoding="utf-8"), source.name)


def parse_markdown(
    content: str,
    source_name: str = "uploaded.md",
    config: MarkdownImportConfig | None = None,
) -> ParsedMarkdown:
    config = config or get_markdown_import_config()
    nodes: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    for line_no, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        match = LIST_RE.match(line)
        if not match:
            if nodes:
                nodes[-1]["text"] = append_inline_text(nodes[-1]["text"], line)
            else:
                errors.append(f"第 {line_no} 行不是 Markdown 嵌套列表格式：{line}")
            continue
        indent = len(match.group("indent"))
        if indent % 2 != 0:
            errors.append(f"第 {line_no} 行缩进不是 2 空格倍数：{line}")
            continue
        level = indent // 2 + 1
        text = collapse_inline_text(match.group("text"))
        if level < 1 or level > config.level_count:
            errors.append(f"第 {line_no} 行层级超出 1-{config.level_count}：Level {level}")
            continue
        nodes.append({"line": line_no, "level": level, "text": text})

    if errors:
        raise ParseError(errors)
    if not nodes:
        raise ParseError(["文件为空或没有可解析的列表节点"])

    roots = [node for node in nodes if node["level"] == 1]
    if len(roots) != 1:
        raise ParseError([f"MD 必须只有一个 Level 1 用例集标题，当前数量：{len(roots)}"])

    suite_title = roots[0]["text"]
    stack: dict[int, dict[str, Any]] = {}
    cases: list[ParsedCase] = []

    for index, node in enumerate(nodes):
        level = node["level"]
        stack[level] = node
        for old_level in list(stack.keys()):
            if old_level > level:
                del stack[old_level]

        next_node = nodes[index + 1] if index + 1 < len(nodes) else None
        is_leaf = next_node is None or int(next_node["level"]) <= level
        if not is_leaf:
            continue
        if level != config.level_count:
            errors.append(
                f"第 {node['line']} 行是分支叶子节点，"
                f"但层级不是配置的 Level {config.level_count}：Level {level}"
            )
            continue
        missing_levels = [
            required_level
            for required_level in range(1, config.level_count + 1)
            if required_level not in stack
        ]
        if missing_levels:
            errors.append(f"第 {node['line']} 行 case 分支缺少上级层级：{missing_levels}")
            continue
        cases.append(_case_from_stack(stack, len(cases) + 1, config, suite_title))

    if not cases:
        errors.append(f"没有解析到任何完整 Level {config.level_count} case")
    if errors:
        raise ParseError(errors)

    return ParsedMarkdown(
        source_name=source_name,
        suite_title=suite_title,
        cases=cases,
        warnings=warnings,
    )


def _case_from_stack(
    stack: dict[int, dict[str, Any]],
    ordinal: int,
    config: MarkdownImportConfig,
    suite_title: str,
) -> ParsedCase:
    path_nodes = [
        {
            "level": level.index,
            "label": level.display_label,
            "rawText": stack[level.index]["text"],
            "displayText": stack[level.index]["text"],
        }
        for level in config.path_levels
    ]
    core_nodes: dict[str, dict[str, Any]] = {}
    for level in config.core_levels:
        raw_text = stack[level.index]["text"]
        display_text, separator = trim_configured_prefix(
            raw_text,
            level.display_label,
            config.trim_separators,
        )
        core_nodes[level.role] = {
            "level": level.index,
            "label": level.display_label,
            "rawText": raw_text,
            "displayText": display_text,
            "trimmed": separator is not None,
            "separator": separator,
        }

    title_text = _display_text(core_nodes, "case_title")
    tags = extract_title_tags(title_text)
    steps_text = _display_text(core_nodes, "steps")
    legacy_path = [str(node.get("displayText") or "") for node in path_nodes]
    module_text = legacy_path[0] if legacy_path else ""
    return ParsedCase(
        ordinal=ordinal,
        suite_title=suite_title,
        path_nodes=path_nodes,
        core_nodes=core_nodes,
        core_labels={role: config.core_label(role) for role in CORE_ROLES},
        module_name=module_text,
        product_feature=legacy_path[1] if len(legacy_path) > 1 else "",
        test_feature=legacy_path[2] if len(legacy_path) > 2 else "",
        raw_title=title_text,
        clean_title=title_text,
        scenario_tags=[tag for tag in tags if tag != "人工"],
        manual="人工" in tags,
        preconditions=_display_text(core_nodes, "preconditions"),
        steps_text=steps_text,
        step_items=_split_steps(steps_text),
        expected_result=_display_text(core_nodes, "expected"),
    )


def _display_text(core_nodes: dict[str, dict[str, Any]], role: str) -> str:
    return str((core_nodes.get(role) or {}).get("displayText") or "")


def _case_from_raw(raw: dict[str, Any], ordinal: int) -> ParsedCase:
    raw_title = collapse_inline_text(raw["raw_title"])
    tags = extract_title_tags(raw_title)
    steps_text = raw.get("steps_text") or ""
    return ParsedCase(
        ordinal=ordinal,
        suite_title=raw["suite_title"],
        path_nodes=raw.get("path_nodes") or [],
        core_nodes=raw.get("core_nodes") or {},
        core_labels=raw.get("core_labels") or {},
        module_name=raw["module_name"],
        product_feature=raw["product_feature"],
        test_feature=raw["test_feature"],
        raw_title=raw_title,
        clean_title=raw_title,
        scenario_tags=[tag for tag in tags if tag != "人工"],
        manual="人工" in tags,
        preconditions=raw.get("preconditions") or "",
        steps_text=steps_text,
        step_items=_split_steps(steps_text),
        expected_result=raw.get("expected_result") or "",
    )


def _split_steps(steps_text: str) -> list[str]:
    return [item.strip() for item in steps_text.split("、") if item.strip()]
