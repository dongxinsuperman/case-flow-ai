from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.markdown_text import append_inline_text, collapse_inline_text, extract_title_tags


class QuickParseError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class QuickParsedCase:
    ordinal: int
    suite_title: str
    path_nodes: list[dict[str, Any]]
    core_nodes: dict[str, dict[str, Any]]
    raw_title: str
    clean_title: str
    scenario_tags: list[str]
    manual: bool
    preconditions: str
    steps_text: str
    step_items: list[str]
    expected_result: str


@dataclass(frozen=True)
class QuickParsedMarkdown:
    source_name: str
    suite_title: str
    cases: list[QuickParsedCase]
    warnings: list[str]


LIST_RE = re.compile(r"^(?P<indent> *)-\s+(?P<text>.+?)\s*$")
CORE_ROLES = ("case_title", "preconditions", "steps", "expected")
CORE_LABELS = {
    "case_title": "测试标题",
    "preconditions": "前置条件",
    "steps": "操作步骤",
    "expected": "预期结果",
}
TRIM_SEPARATORS = ("：", ":")


def parse_quick_markdown(content: str, source_name: str = "uploaded.md") -> QuickParsedMarkdown:
    nodes: list[dict[str, Any]] = []
    errors: list[str] = []

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
        nodes.append({
            "line": line_no,
            "level": indent // 2 + 1,
            "text": collapse_inline_text(match.group("text")),
        })

    if errors:
        raise QuickParseError(errors)
    if not nodes:
        raise QuickParseError(["文件为空或没有可解析的列表节点"])

    roots = [node for node in nodes if node["level"] == 1]
    if len(roots) != 1:
        raise QuickParseError([f"MD 必须只有一个 Level 1 测试集标题，当前数量：{len(roots)}"])
    if nodes[0]["level"] != 1:
        raise QuickParseError(["第一条列表必须是 Level 1 测试集标题"])

    suite_title = str(roots[0]["text"])
    stack: dict[int, dict[str, Any]] = {}
    cases: list[QuickParsedCase] = []

    for index, node in enumerate(nodes):
        level = int(node["level"])
        stack[level] = node
        for old_level in list(stack.keys()):
            if old_level > level:
                del stack[old_level]

        next_node = nodes[index + 1] if index + 1 < len(nodes) else None
        is_leaf = next_node is None or int(next_node["level"]) <= level
        if not is_leaf:
            continue
        if level < 5:
            errors.append(f"第 {node['line']} 行分支不足 5 级，无法按最后四级解析为完整 case")
            continue
        missing = [required for required in range(1, level + 1) if required not in stack]
        if missing:
            errors.append(f"第 {node['line']} 行 case 分支缺少上级层级：{missing}")
            continue
        cases.append(_case_from_stack(stack, level, len(cases) + 1, suite_title))

    if not cases:
        errors.append("没有解析到任何完整 case")
    if errors:
        raise QuickParseError(errors)

    return QuickParsedMarkdown(
        source_name=source_name,
        suite_title=suite_title,
        cases=cases,
        warnings=[],
    )


def _case_from_stack(
    stack: dict[int, dict[str, Any]],
    leaf_level: int,
    ordinal: int,
    suite_title: str,
) -> QuickParsedCase:
    core_start = leaf_level - 3
    path_nodes = [
        {
            "level": level,
            "label": f"层级{level - 1}",
            "rawText": str(stack[level]["text"]),
            "displayText": str(stack[level]["text"]),
        }
        for level in range(2, core_start)
    ]
    core_nodes: dict[str, dict[str, Any]] = {}
    for offset, role in enumerate(CORE_ROLES):
        level = core_start + offset
        raw_text = str(stack[level]["text"])
        display_text, separator = _trim_core_prefix(raw_text, CORE_LABELS[role])
        core_nodes[role] = {
            "level": level,
            "label": CORE_LABELS[role],
            "rawText": raw_text,
            "displayText": display_text,
            "trimmed": separator is not None,
            "separator": separator,
        }
    title = _display_text(core_nodes, "case_title")
    tags = extract_title_tags(title)
    steps_text = _display_text(core_nodes, "steps")
    return QuickParsedCase(
        ordinal=ordinal,
        suite_title=suite_title,
        path_nodes=path_nodes,
        core_nodes=core_nodes,
        raw_title=title,
        clean_title=title,
        scenario_tags=[tag for tag in tags if tag != "人工"],
        manual="人工" in tags,
        preconditions=_display_text(core_nodes, "preconditions"),
        steps_text=steps_text,
        step_items=_split_steps(steps_text),
        expected_result=_display_text(core_nodes, "expected"),
    )


def render_quick_markdown(
    suite_title: str,
    cases: list[dict[str, Any]],
) -> str:
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
        raise ValueError("导出 Quick Case 缺少完整原始标题 raw_title")
    values = {
        "case_title": raw_title,
        "preconditions": case.get("preconditions") or "",
        "steps": case.get("steps_text") or "",
        "expected": case.get("expected_result") or "",
    }
    current_depth = depth
    for role in CORE_ROLES:
        node = core_nodes.get(role) if isinstance(core_nodes, dict) else None
        text = _core_export_text(role, values[role], node)
        lines.append(f"{'  ' * current_depth}- {text}")
        current_depth += 1


def _core_export_text(role: str, value: str, node: dict[str, Any] | None) -> str:
    value = collapse_inline_text(value)
    if isinstance(node, dict) and node.get("trimmed"):
        label = str(node.get("label") or CORE_LABELS[role])
        separator = str(node.get("separator") or "：")
        return f"{label}{separator}{value}"
    return value


def _trim_core_prefix(text: str, label: str) -> tuple[str, str | None]:
    raw = collapse_inline_text(text)
    for separator in TRIM_SEPARATORS:
        prefix = f"{label}{separator}"
        if raw.startswith(prefix):
            return raw.removeprefix(prefix).strip(), separator
    return raw, None


def _display_text(core_nodes: dict[str, dict[str, Any]], role: str) -> str:
    return str((core_nodes.get(role) or {}).get("displayText") or "")


def _node_text(node: dict[str, Any]) -> str:
    return collapse_inline_text(node.get("displayText") or node.get("rawText") or "")


def _split_steps(steps_text: str) -> list[str]:
    steps = [line.strip() for line in str(steps_text or "").splitlines() if line.strip()]
    return steps or ([steps_text.strip()] if steps_text.strip() else [])
