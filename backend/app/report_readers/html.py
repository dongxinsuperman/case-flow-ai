from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from app.report_readers.base import ReportBlock, ReportEvidence, ReportImageEvidence

_HYBRID_EVIDENCE_RE = re.compile(
    r'<script[^>]*id="hybrid-evidence"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)
_BLOCK_TAGS = {"p", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "h4"}


class _ReportHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self._ignored_depth = 0
        self._chunks: list[str] = []
        self._text_buffer: list[str] = []
        self.image_urls: list[str] = []
        self.blocks: list[ReportBlock] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        attr_map = {key.lower(): value for key, value in attrs if value}
        if tag in _BLOCK_TAGS:
            self._flush_text_block()
            self._chunks.append("\n")
        if tag == "img" and attr_map.get("src"):
            # 在文本里按顺序内联 [截图#N] 标记，N=该图在 image_urls 里的 index，
            # 让模型能把“失败那一步”和对应截图对上（GUI agent 轨迹通用）。
            self._flush_text_block()
            image_index = len(self.image_urls)
            url = urljoin(self.base_url, attr_map["src"])
            self._chunks.append(f"\n[截图#{image_index}]\n")
            self.image_urls.append(url)
            self.blocks.append(
                ReportBlock(index=len(self.blocks), kind="image", image_index=image_index, url=url)
            )
        if tag == "br":
            self._chunks.append("\n")
            self._text_buffer.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")
            self._flush_text_block()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = normalize_text(data)
        if text:
            self._chunks.append(text)
            self._text_buffer.append(text)

    def text(self) -> str:
        self._flush_text_block()
        return normalize_text("\n".join(self._chunks))

    def limited_blocks(self, image_limit: int | None) -> list[ReportBlock]:
        self._flush_text_block()
        if image_limit is None:
            return list(self.blocks)
        return [
            block
            for block in self.blocks
            if block.kind != "image" or (block.image_index is not None and block.image_index < image_limit)
        ]

    def _flush_text_block(self) -> None:
        text = normalize_text(" ".join(self._text_buffer))
        self._text_buffer.clear()
        if text:
            self.blocks.append(ReportBlock(index=len(self.blocks), kind="text", text=text))


async def read_report(
    report_url: str, executor: str | None = None, image_limit: int | None = None
) -> ReportEvidence:
    """读执行报告为归一化证据。

    默认不截断截图目录：诊断/提 bug 需要知道真实末图在哪里，但只有被点名的截图才会下载成
    base64 发给模型。image_limit 只保留给特殊调用方做显式裁剪。
    """
    if not report_url:
        return ReportEvidence(
            available=False,
            reader=executor or "html",
            url="",
            failure_type="missing_report",
            summary="当前 case 没有关联执行报告。",
            logs_text="",
            image_urls=[],
            image_evidence=[],
            blocks=[],
            quality={"text_chars": 0, "image_count": 0, "block_count": 0},
            error="missing_report",
        )
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(report_url)
            response.raise_for_status()
    except Exception as exc:
        return ReportEvidence(
            available=False,
            reader=executor or "html",
            url=report_url,
            failure_type="report_unreadable",
            summary=f"执行报告读取失败：{exc}",
            logs_text="",
            image_urls=[],
            image_evidence=[],
            blocks=[],
            quality={"text_chars": 0, "image_count": 0, "block_count": 0},
            error=str(exc),
        )

    content_type = response.headers.get("content-type", "")
    text = response.text

    # AI Hybrid 总报告：内嵌了结构化证据（agent 实际看过的图 + 逐图端标签 + 带 [截图#N] 的轨迹），
    # 无损解析它，而不是当普通 HTML 抓 img（那样丢端标签、还会抓进无关图）。
    if (executor or "").lower() == "ai_hybrid":
        hybrid = _hybrid_report_evidence(str(response.url), text)
        if hybrid is None:
            return ReportEvidence(
                available=False,
                reader="ai_hybrid",
                url=str(response.url),
                failure_type="report_unreadable",
                summary="AI Hybrid 总报告缺少内嵌结构化证据，不能生成错误修复候选。",
                logs_text="",
                image_urls=[],
                image_evidence=[],
                blocks=[],
                quality={"text_chars": 0, "image_count": 0, "block_count": 0},
                error="missing_hybrid_evidence",
            )
        return hybrid

    if "html" in content_type.lower() or "<html" in text[:500].lower():
        parser = _ReportHTMLParser(str(response.url))
        parser.feed(text)
        logs_text = parser.text()
        # 不去重：截图按轨迹顺序，index 要与文本里的 [截图#N] 标记对齐。
        image_urls = parser.image_urls if image_limit is None else parser.image_urls[:image_limit]
        image_evidence = image_contexts(logs_text, image_urls)
        blocks = parser.limited_blocks(image_limit)
    else:
        logs_text = normalize_text(text)
        image_urls = []
        image_evidence = []
        blocks = [ReportBlock(index=0, kind="text", text=logs_text)] if logs_text else []

    if not logs_text:
        return ReportEvidence(
            available=False,
            reader=executor or "html",
            url=str(response.url),
            failure_type="report_unreadable",
            summary="执行报告可访问，但没有解析出可用于判断的文本。",
            logs_text="",
            image_urls=image_urls,
            image_evidence=image_evidence,
            blocks=blocks,
            quality={"text_chars": 0, "image_count": len(image_urls), "block_count": len(blocks)},
            error="empty_report_text",
        )

    return ReportEvidence(
        available=True,
        reader=executor or "html",
        url=str(response.url),
        failure_type=classify_failure_type(logs_text),
        summary=summarize_text(logs_text),
        logs_text=logs_text[:60000],
        image_urls=image_urls,
        image_evidence=image_evidence,
        blocks=blocks,
        quality={"text_chars": len(logs_text), "image_count": len(image_urls), "block_count": len(blocks)},
    )


def _hybrid_report_evidence(url: str, html_text: str) -> ReportEvidence | None:
    """从 AI Hybrid 总报告里读内嵌的结构化证据 → ReportEvidence。解析失败返回 None。"""
    match = _HYBRID_EVIDENCE_RE.search(html_text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1).replace("<\\/", "</"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    images = [img for img in (data.get("images") or []) if isinstance(img, dict) and img.get("image_url")]
    image_urls = [str(img["image_url"]) for img in images]
    image_evidence = [
        ReportImageEvidence(
            index=i,
            url=str(img["image_url"]),
            context=str(img.get("context") or ""),
            platform=str(img.get("platform") or ""),
        )
        for i, img in enumerate(images)
    ]

    logs_text = _hybrid_logs_text(data)
    if not logs_text and not image_urls:
        return None

    summary = str(data.get("summary") or "").strip()
    return ReportEvidence(
        available=True,
        reader="ai_hybrid",
        url=url,
        failure_type=classify_failure_type(logs_text),
        summary=summary[:600],
        logs_text=logs_text,
        image_urls=image_urls,
        image_evidence=image_evidence,
        blocks=[],
        quality={"text_chars": len(logs_text), "image_count": len(image_urls)},
    )


def _hybrid_logs_text(data: dict) -> str:
    parts: list[str] = []
    summary = str(data.get("summary") or "").strip()
    attribution = str(data.get("attribution") or "").strip()
    if summary:
        parts.append(f"最终结论：{summary}")
    if attribution and attribution != summary:
        parts.append(f"归因：{attribution}")
    evidence = [str(x).strip() for x in (data.get("evidence") or []) if str(x).strip()]
    if evidence:
        parts.append("证据：\n" + "\n".join(f"- {x}" for x in evidence))
    suggestions = [str(x).strip() for x in (data.get("suggestions") or []) if str(x).strip()]
    if suggestions:
        parts.append("建议：\n" + "\n".join(f"- {x}" for x in suggestions))
    trajectory = str(data.get("trajectory_text") or "").strip()
    if trajectory:
        parts.append("【ReAct 轨迹】\n" + trajectory)
    return normalize_text("\n\n".join(parts))


def hybrid_end_key_indices(report: ReportEvidence) -> list[tuple[str, int]]:
    """按端分组证据图，每端取末图 index → [(端标签, index)]。

    供 hybrid 单份总报告复用多端诊断链路：每端各留一张关键截图、首轮各喂一张。
    无端标签（非 hybrid 或无图）则返回空。
    """
    last_by_label: dict[str, int] = {}
    for ev in report.image_evidence or []:
        label = str(getattr(ev, "platform", "") or "").strip()
        if label:
            last_by_label[label] = ev.index
    return list(last_by_label.items())


def classify_failure_type(text: str) -> str:
    lowered = text.lower()
    assertion_markers = (
        "assert_failed",
        "assertion_failed",
        "assert",
        "断言失败",
        "断言不通过",
        "断言",
        "expected",
        "actual",
        "期望",
        "实际",
        "校验失败",
        "结果不符合",
    )
    business_markers = (
        "业务失败",
        "业务问题",
        "业务异常",
        "服务端",
        "后端",
        "接口返回错误",
        "接口异常",
        "数据异常",
        "权限不足",
        "账号异常",
        "余额不足",
        "库存不足",
        "业务规则",
        "不是case问题",
        "not a case issue",
    )
    execution_markers = (
        "element",
        "元素",
        "找不到",
        "未找到",
        "点击失败",
        "无法点击",
        "timeout",
        "超时",
        "未出现",
        "页面未",
        "步骤",
        "执行失败",
    )
    if any(marker in lowered for marker in assertion_markers):
        return "assertion_failed"
    if any(marker in lowered for marker in business_markers):
        return "business_failure"
    if any(marker in lowered for marker in execution_markers):
        return "execution_failed"
    return "unknown_failure"


def summarize_text(text: str, limit: int = 600) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    summary = "\n".join(lines[:12])
    return summary[:limit] + ("..." if len(summary) > limit else "")


def image_contexts(text: str, image_urls: list[str], radius: int = 500) -> list[ReportImageEvidence]:
    if not image_urls:
        return []
    markers = {
        int(match.group(1)): match.start()
        for match in re.finditer(r"\[截图#(\d+)\]", text)
        if match.group(1).isdigit()
    }
    result: list[ReportImageEvidence] = []
    for index, url in enumerate(image_urls):
        position = markers.get(index)
        if position is None:
            context = ""
        else:
            start = max(0, position - radius)
            end = min(len(text), position + radius)
            context = normalize_text(text[start:end])
        result.append(ReportImageEvidence(index=index, url=url, context=context))
    return result


def normalize_text(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", str(text or ""))
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
