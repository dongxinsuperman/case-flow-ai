from __future__ import annotations

import pytest

from app.services.ai_hybrid import report_observer

_HTML = """
<!doctype html>
<html>
<body>
  <div class="hd-title"><h1>获取web课程平台知识点管理下第一个「有理数引入」课程的ID</h1></div>
  <div class="reason success">知识点管理下第一个「有理数引入」课程的ID为37eaeece0-57f7-11e7-9139-d35a4f1315a7</div>
  <img src="shots/1.png" />
  <div class="tl-action">finished(content='课程的ID已获取')</div>
  <img src="shots/2.png" />
  <script>var noise = 'should be ignored';</script>
</body>
</html>
"""

_BASE = "http://127.0.0.1:8009/files/reports/demo/report.html"


def _index() -> dict:
    return report_observer.build_index(_HTML, base_url=_BASE, executor="ai_web")


def test_build_index_orders_text_and_image_blocks() -> None:
    index = _index()

    assert index["available"] is True
    kinds = [block["kind"] for block in index["blocks"]]
    # 文字块和截图块按原文顺序交错排列
    assert "text" in kinds and kinds.count("image") == 2
    assert index["stats"]["image_count"] == 2
    # 噪声被剔除，关键正文保留
    joined = " ".join(b["text"] for b in index["blocks"] if b["kind"] == "text")
    assert "37eaeece0-57f7-11e7-9139-d35a4f1315a7" in joined
    assert "should be ignored" not in joined
    # 单脑：不做关键字状态判定 / 事实抽取
    assert "status_hint" not in index
    assert "key_facts" not in index
    # 截图块带稳定 imgNo 与绝对地址
    images = [block for block in index["blocks"] if block["kind"] == "image"]
    assert [img["imgNo"] for img in images] == [0, 1]
    assert images[0]["url"].endswith("shots/1.png")


def test_outline_lists_toc_with_stable_indices() -> None:
    outline = report_observer.outline(_index())

    assert outline["observation_mode"] == "outline"
    assert outline["strategy"] == "failure_tail_first"
    assert outline["stats"]["image_count"] == 2
    image_entries = [entry for entry in outline["toc"] if entry["kind"] == "image"]
    assert {entry["imgNo"] for entry in image_entries} == {0, 1}
    assert outline["latest_image"]["imgNo"] == 1


def test_outline_prioritizes_tail_for_long_reports() -> None:
    html = (
        "<html><body>"
        + "".join(f"<div>步骤 {idx}</div><img src='shot{idx}.png' />" for idx in range(80))
        + "</body></html>"
    )
    index = report_observer.build_index(html, base_url=_BASE, executor="ai_phone")

    outline = report_observer.outline(index)

    assert outline["stats"]["block_count"] == 160
    assert outline["head_toc"][0]["preview"] == "步骤 0"
    assert outline["tail_toc"][0]["i"] == 120
    assert any(entry.get("preview") == "步骤 79" for entry in outline["tail_toc"])
    assert outline["latest_image"]["imgNo"] == 79
    assert outline["omitted_range"] == {"from": 5, "to": 120, "count": 115}
    assert not any(entry.get("preview") == "步骤 20" for entry in outline["toc"])


def test_read_returns_block_range_text_and_image_markers() -> None:
    result = report_observer.read(_index(), 0, None)

    assert result["observation_mode"] == "read"
    assert "37eaeece0" in result["text"]
    assert "[图#0]" in result["text"]
    assert {img["imgNo"] for img in result["images"]} == {0, 1}


def test_search_locates_matching_blocks() -> None:
    result = report_observer.search(_index(), "37eaeece0")

    assert result["match_count"] == 1
    assert result["matches"][0]["i"] >= 0
    assert "37eaeece0" in result["matches"][0]["snippet"]


@pytest.mark.asyncio
async def test_read_image_returns_data_uri_and_neighbor_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str, **_kwargs: object) -> str:
        return "data:image/png;base64,ZmFrZQ=="

    monkeypatch.setattr(report_observer, "_fetch_data_uri", fake_fetch)

    result = await report_observer.read_image(_index(), 0)

    assert result["observation_mode"] == "image"
    assert result["imgNo"] == 0
    assert result["image_data_uri"].startswith("data:image/png;base64,")
    # 上/下最近文字块作为上下文一并给出
    assert result["context_before"] or result["context_after"]


@pytest.mark.asyncio
async def test_read_image_missing_index() -> None:
    result = await report_observer.read_image(_index(), 99)
    assert result["error"] == "image_not_found"


def test_build_index_empty_html_has_no_blocks() -> None:
    index = report_observer.build_index("<html><body></body></html>", base_url="http://local/r.html")
    assert index["blocks"] == []
    assert index["stats"]["block_count"] == 0
