from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.settings import get_settings

CORE_ROLES = ("case_title", "preconditions", "steps", "expected")


class MarkdownImportConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MarkdownLevelConfig:
    index: int
    role: str
    display_label: str


@dataclass(frozen=True)
class MarkdownImportConfig:
    levels: tuple[MarkdownLevelConfig, ...]
    trim_separators: tuple[str, ...]

    @property
    def level_count(self) -> int:
        return len(self.levels)

    @property
    def path_levels(self) -> tuple[MarkdownLevelConfig, ...]:
        return self.levels[1:-4]

    @property
    def core_levels(self) -> tuple[MarkdownLevelConfig, ...]:
        return self.levels[-4:]

    def level(self, index: int) -> MarkdownLevelConfig:
        return self.levels[index - 1]

    def core_label(self, role: str) -> str:
        for level in self.core_levels:
            if level.role == role:
                return level.display_label
        return role


DEFAULT_MARKDOWN_IMPORT_CONFIG: dict[str, Any] = {
    "levels": [
        {"index": 1, "role": "suite", "displayLabel": "测试集"},
        {"index": 2, "role": "path", "displayLabel": "模块"},
        {"index": 3, "role": "path", "displayLabel": "功能点"},
        {"index": 4, "role": "path", "displayLabel": "测试功能点"},
        {"index": 5, "role": "case_title", "displayLabel": "测试标题"},
        {"index": 6, "role": "preconditions", "displayLabel": "前置条件"},
        {"index": 7, "role": "steps", "displayLabel": "操作步骤"},
        {"index": 8, "role": "expected", "displayLabel": "预期结果"},
    ],
    "trimSeparators": ["：", ":"],
}


@lru_cache
def get_markdown_import_config() -> MarkdownImportConfig:
    settings = get_settings()
    config_path = Path(settings.markdown_import_config_path)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    if config_path.exists():
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        payload = DEFAULT_MARKDOWN_IMPORT_CONFIG
    return parse_markdown_import_config(payload)


def parse_markdown_import_config(payload: dict[str, Any]) -> MarkdownImportConfig:
    errors: list[str] = []
    raw_levels = payload.get("levels")
    if not isinstance(raw_levels, list):
        raise MarkdownImportConfigError("Markdown 导入配置缺少 levels 数组")
    if len(raw_levels) < 5:
        errors.append("levels.length 必须 >= 5")

    levels: list[MarkdownLevelConfig] = []
    for offset, raw in enumerate(raw_levels, start=1):
        if not isinstance(raw, dict):
            errors.append(f"levels[{offset}] 必须是对象")
            continue
        index = raw.get("index")
        role = str(raw.get("role") or "").strip()
        label = str(raw.get("displayLabel") or "").strip()
        if index != offset:
            errors.append(f"levels[{offset}] 的 index 必须等于 {offset}")
        if not role:
            errors.append(f"levels[{offset}] 缺少 role")
        if not label:
            errors.append(f"levels[{offset}] 缺少 displayLabel")
        levels.append(MarkdownLevelConfig(index=offset, role=role, display_label=label))

    if levels:
        if levels[0].role != "suite":
            errors.append("Level 1 role 必须是 suite")
        for level in levels[1:-4]:
            if level.role != "path":
                errors.append(f"Level {level.index} role 必须是 path")
        tail_roles = tuple(level.role for level in levels[-4:])
        if tail_roles != CORE_ROLES:
            errors.append("最后四级 role 必须依次是 case_title、preconditions、steps、expected")

    separators = payload.get("trimSeparators")
    if separators is None and "trimSeparator" in payload:
        separators = [payload.get("trimSeparator")]
    if not isinstance(separators, list) or not separators:
        errors.append("trimSeparators 必须是非空数组")
        separators = []
    clean_separators: list[str] = []
    for separator in separators:
        text = str(separator or "")
        if not text:
            errors.append("trimSeparators 不能包含空字符串")
            continue
        clean_separators.append(text)

    if errors:
        raise MarkdownImportConfigError("；".join(errors))
    return MarkdownImportConfig(levels=tuple(levels), trim_separators=tuple(clean_separators))


def trim_configured_prefix(text: str, label: str, separators: tuple[str, ...]) -> tuple[str, str | None]:
    raw = str(text or "").strip()
    for separator in separators:
        prefix = f"{label}{separator}"
        if raw.startswith(prefix):
            return raw.removeprefix(prefix).strip(), separator
    return raw, None
