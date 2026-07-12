from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ReportImageEvidence:
    index: int
    url: str
    context: str = ""
    # 端标签（hybrid 多端混合报告用：每张证据图各属于哪个端，如「安卓端」「Web端」「接口」）。
    # 单端执行器（ai_phone/ai_web）留空，端信息由报告整体的 primary_platform 承载。
    platform: str = ""


@dataclass(frozen=True)
class ReportBlock:
    index: int
    kind: str
    text: str = ""
    image_index: int | None = None
    url: str = ""


@dataclass(frozen=True)
class ReportEvidence:
    available: bool
    reader: str
    url: str
    failure_type: str
    summary: str
    logs_text: str
    image_urls: list[str]
    image_evidence: list[ReportImageEvidence] = field(default_factory=list)
    blocks: list[ReportBlock] = field(default_factory=list)
    quality: dict[str, object] | None = None
    error: str | None = None


class ReportReader(Protocol):
    key: str

    async def read(self, report_url: str) -> ReportEvidence:
        """Read executor report data into a normalized evidence object."""
