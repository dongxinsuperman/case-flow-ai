"""子报告索引器（单脑 / 分层读取）。

任意执行器的 HTML 报告进来，都先**确定性**归一化成一份「有序块」索引：
    blocks = [ {i, kind:"text", text}, {i, kind:"image", imgNo, url}, ... ]
文字和截图按原文顺序排好、各自带稳定编号。之后主编排 agent 通过 4 个动作分层探查
这份索引，而不是每次去抓 HTML：

- outline：看报告结构 + 故障优先目录（先做，知道报告多大、尾部现场在哪、末图是哪张）
- read(from,to)：按块区间读正文（翻页，几十轮也塞得下）
- search(q)：按关键词定位命中块（找断言/报错）
- image(imgNo)：把指定截图取来编成 base64 data URI，**直接作为图片交给主 agent 自己看**

**不做任何关键字状态判定 / 事实抽取 / 二级 LLM**——语义理解全是主 agent 的活。
截图不预先转文字，也不预先全部下载；只有主 agent 点名某张图时才现取 base64。
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import html
import re
from collections.abc import Iterator
from contextlib import contextmanager
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import httpx

_IGNORE_TAGS = {"script", "style", "noscript", "svg"}
_BLOCK_TAGS = {"br", "p", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "h4"}

# 缓存作用域限定在一次 run 内：索引按 report_url、图片 base64 按图 url。
# 用 contextvar 而非模块级字典，避免常驻进程里缓存只增不减（尤其图片 base64）——
# run 结束即释放，不落库、也不做进程级悬挂缓存。作用域外（scope 未开）则不缓存、每次现取。
_index_cache_var: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = contextvars.ContextVar(
    "hybrid_index_cache", default=None
)
_image_cache_var: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "hybrid_image_cache", default=None
)


@contextmanager
def run_cache_scope() -> Iterator[None]:
    """把索引/截图缓存限定在一次 run 内；退出即释放。"""
    index_token = _index_cache_var.set({})
    image_token = _image_cache_var.set({})
    try:
        yield
    finally:
        _index_cache_var.reset(index_token)
        _image_cache_var.reset(image_token)


# ---------------------------------------------------------------------------
# 1) 确定性解析：HTML -> 有序块索引
# ---------------------------------------------------------------------------
class _IndexParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self._ignored_depth = 0
        self._buffer: list[str] = []
        self.items: list[tuple[str, str]] = []

    def _flush_text(self) -> None:
        text = normalize_text("".join(self._buffer))
        self._buffer = []
        if text:
            self.items.append(("text", text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IGNORE_TAGS:
            self._ignored_depth += 1
            return
        attr_map = {key.lower(): value for key, value in attrs if value}
        if tag == "img" and attr_map.get("src"):
            self._flush_text()
            self.items.append(("image", urljoin(self.base_url, attr_map["src"])))
        if tag in _BLOCK_TAGS:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORE_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._buffer.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = normalize_text(data)
        if text:
            self._buffer.append(text + " ")

    def finish(self) -> list[tuple[str, str]]:
        self._flush_text()
        return self.items


def build_index(html_text: str, *, base_url: str, executor: str = "") -> dict[str, Any]:
    parser = _IndexParser(base_url)
    parser.feed(str(html_text or ""))
    items = parser.finish()

    blocks: list[dict[str, Any]] = []
    img_no = 0
    for kind, value in items:
        i = len(blocks)
        if kind == "text":
            blocks.append({"i": i, "kind": "text", "text": value})
        else:
            blocks.append({"i": i, "kind": "image", "imgNo": img_no, "url": value})
            img_no += 1

    text_chars = sum(len(block["text"]) for block in blocks if block["kind"] == "text")
    return {
        "available": True,
        "url": base_url,
        "executor": executor,
        "blocks": blocks,
        "stats": {"block_count": len(blocks), "text_chars": text_chars, "image_count": img_no},
    }


# ---------------------------------------------------------------------------
# 2) 拉取 + 缓存索引
# ---------------------------------------------------------------------------
async def fetch_index(report_url: str, *, executor: str = "") -> dict[str, Any]:
    if not report_url:
        return _unavailable("missing_report", report_url, executor, "缺少子报告地址。")
    cache = _index_cache_var.get()
    if cache is not None and report_url in cache:
        return cache[report_url]
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(report_url)
            response.raise_for_status()
    except Exception as exc:
        return _unavailable("report_unreadable", report_url, executor, f"子报告读取失败：{exc}")

    index = build_index(response.text, base_url=str(response.url), executor=executor)
    if not index["blocks"]:
        return _unavailable("empty_report", str(response.url), executor, "子报告可访问，但没有解析出内容。")
    if cache is not None:
        cache[report_url] = index
    return index


# ---------------------------------------------------------------------------
# 3) 四个读取动作（纯函数 / 现取图片）
# ---------------------------------------------------------------------------
OUTLINE_HEAD_BLOCKS = 5
OUTLINE_TAIL_BLOCKS = 40
OUTLINE_PREVIEW_CHARS = 160
READ_CHAR_BUDGET = 60000
SEARCH_RADIUS = 300
SEARCH_MAX_HITS = 30


def outline(
    index: dict[str, Any],
    *,
    head_blocks: int = OUTLINE_HEAD_BLOCKS,
    tail_blocks: int = OUTLINE_TAIL_BLOCKS,
    preview: int = OUTLINE_PREVIEW_CHARS,
) -> dict[str, Any]:
    blocks = index["blocks"]
    total = len(blocks)
    head_count = max(0, head_blocks)
    tail_count = max(0, tail_blocks)
    head_slice = blocks[: min(head_count, total)]
    tail_start = max(len(head_slice), total - tail_count)
    tail_slice = blocks[tail_start:]
    head_toc = _toc_entries(head_slice, preview=preview)
    tail_toc = _toc_entries(tail_slice, preview=preview)
    omitted_start = len(head_slice)
    omitted_end = tail_start
    toc: list[dict[str, Any]] = [*head_toc]
    if omitted_end > omitted_start:
        toc.append(
            {
                "kind": "omitted_range",
                "from": omitted_start,
                "to": omitted_end,
                "count": omitted_end - omitted_start,
                "hint": "如尾部证据不足，可 read 这个区间或 search 失败关键词。",
            }
        )
    toc.extend(tail_toc)
    latest_image = _latest_image_entry(blocks)
    return {
        "observation_mode": "outline",
        "url": index["url"],
        "executor": index["executor"],
        "stats": index["stats"],
        "strategy": "failure_tail_first",
        "reading_advice": (
            "测试执行报告优先从尾部失败现场看；先看 tail_toc 和 latest_image，"
            "如果尾部不足，再 search 失败原因/断言关键词，或 read 更早的 block 区间。"
        ),
        "toc": toc,
        "head_toc": head_toc,
        "tail_toc": tail_toc,
        "latest_image": latest_image,
        "omitted_range": {"from": omitted_start, "to": omitted_end, "count": max(0, omitted_end - omitted_start)},
        "truncated": omitted_end > omitted_start,
    }


def read(
    index: dict[str, Any],
    frm: Any = 0,
    to: Any = None,
    *,
    char_budget: int = READ_CHAR_BUDGET,
) -> dict[str, Any]:
    blocks = index["blocks"]
    n = len(blocks)
    start = _clamp(_int(frm, 0), 0, n)
    end = n if to is None else _clamp(_int(to, n), start, n)
    if end <= start:
        end = min(n, start + 40)

    parts: list[str] = []
    images: list[dict[str, Any]] = []
    used = 0
    next_from: int | None = None
    for block in blocks[start:end]:
        if block["kind"] == "text":
            parts.append(f"[块{block['i']}] {block['text']}")
            used += len(block["text"])
        else:
            parts.append(f"[块{block['i']}][图#{block['imgNo']}]")
            images.append({"i": block["i"], "imgNo": block["imgNo"]})
        if used >= char_budget and block["i"] + 1 < end:
            next_from = block["i"] + 1
            break
    return {
        "observation_mode": "read",
        "url": index["url"],
        "executor": index["executor"],
        "from": start,
        "to": end,
        "next_from": next_from,
        "block_count": n,
        "text": "\n".join(parts),
        "images": images,
    }


def search(index: dict[str, Any], query: str, *, radius: int = SEARCH_RADIUS, max_hits: int = SEARCH_MAX_HITS) -> dict[str, Any]:
    q = str(query or "").strip()
    matches: list[dict[str, Any]] = []
    if q:
        lowered = q.lower()
        for block in index["blocks"]:
            if block["kind"] != "text":
                continue
            pos = block["text"].lower().find(lowered)
            if pos < 0:
                continue
            start = max(0, pos - radius)
            end = min(len(block["text"]), pos + len(q) + radius)
            matches.append({"i": block["i"], "snippet": block["text"][start:end]})
            if len(matches) >= max_hits:
                break
    return {
        "observation_mode": "search",
        "url": index["url"],
        "executor": index["executor"],
        "query": q,
        "match_count": len(matches),
        "matches": matches,
    }


def _toc_entries(blocks: list[dict[str, Any]], *, preview: int) -> list[dict[str, Any]]:
    toc: list[dict[str, Any]] = []
    for block in blocks:
        if block["kind"] == "text":
            toc.append({"i": block["i"], "kind": "text", "preview": block["text"][:preview]})
        else:
            toc.append({"i": block["i"], "kind": "image", "imgNo": block["imgNo"]})
    return toc


def _latest_image_entry(blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for block in reversed(blocks):
        if block["kind"] == "image":
            return {"i": block["i"], "kind": "image", "imgNo": block["imgNo"]}
    return None


async def read_image(index: dict[str, Any], img_no: Any, *, radius: int = 240) -> dict[str, Any]:
    number = _int(img_no, -1)
    block = _image_block(index, number)
    if block is None:
        return {
            "observation_mode": "image",
            "url": index["url"],
            "executor": index["executor"],
            "error": "image_not_found",
            "imgNo": number,
            "image_count": index["stats"]["image_count"],
        }
    before, after = _neighbor_text(index, block["i"], radius=radius)
    data_uri = await _fetch_data_uri(str(block.get("url") or ""))
    result: dict[str, Any] = {
        "observation_mode": "image",
        "url": index["url"],
        "executor": index["executor"],
        "imgNo": number,
        "block": block["i"],
        "image_url": block.get("url"),
        "context_before": before,
        "context_after": after,
    }
    if data_uri:
        result["image_data_uri"] = data_uri
        result["note"] = "该截图已作为图片附在本轮消息中，请直接看图判断。"
    else:
        result["error"] = "image_download_failed"
    return result


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _unavailable(reason: str, url: str, executor: str, message: str) -> dict[str, Any]:
    return {
        "available": False,
        "url": url,
        "executor": executor,
        "error": reason,
        "message": message,
        "blocks": [],
        "stats": {"block_count": 0, "text_chars": 0, "image_count": 0},
    }


def _image_block(index: dict[str, Any], img_no: int) -> dict[str, Any] | None:
    for block in index["blocks"]:
        if block["kind"] == "image" and int(block.get("imgNo", -1)) == img_no:
            return block
    return None


def _neighbor_text(index: dict[str, Any], block_i: int, *, radius: int) -> tuple[str, str]:
    blocks = index["blocks"]
    before = ""
    after = ""
    for block in reversed(blocks[:block_i]):
        if block["kind"] == "text":
            before = block["text"][-radius:]
            break
    for block in blocks[block_i + 1 :]:
        if block["kind"] == "text":
            after = block["text"][:radius]
            break
    return before, after


async def _fetch_data_uri(url: str) -> str | None:
    if not url:
        return None
    cache = _image_cache_var.get()
    if cache is not None and url in cache:
        return cache[url]
    data_uri = await asyncio.to_thread(_download_data_uri, url)
    if data_uri and cache is not None:
        cache[url] = data_uri
    return data_uri


def _download_data_uri(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=15, trust_env=False, follow_redirects=True)
        response.raise_for_status()
    except Exception:
        return None
    content_type = (response.headers.get("content-type") or "").lower()
    lowered = url.lower().split("?")[0]
    if "png" in content_type or lowered.endswith(".png"):
        mime = "image/png"
    elif "webp" in content_type or lowered.endswith(".webp"):
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(response.content).decode()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
