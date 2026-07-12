import pytest

from app.report_readers.html import _ReportHTMLParser, classify_failure_type, read_report


def test_assert_failed_takes_priority_over_business_words() -> None:
    text = (
        "状态原因：assert_failed 失败步骤：步骤 #9 断言内容：期望进入学习方法卡片；"
        "实际页面仍在全部 Tab 下方。模型分析：可能属于业务功能异常。"
    )

    assert classify_failure_type(text) == "assertion_failed"


def test_business_failure_when_no_assertion_evidence() -> None:
    text = "接口返回错误，服务端业务异常，账号权限不足。"

    assert classify_failure_type(text) == "business_failure"


def test_html_parser_builds_ordered_text_and_image_blocks() -> None:
    parser = _ReportHTMLParser("https://example.com/reports/run.html")
    parser.feed(
        """
        <html><body>
          <div>步骤 #1 打开课程管理</div>
          <img src="before.png" />
          <p>断言失败：预期不存在系统课程，实际存在系统课程。</p>
          <img src="/shots/fail.png" />
        </body></html>
        """
    )

    logs_text = parser.text()
    blocks = parser.limited_blocks(image_limit=None)

    assert "[截图#0]" in logs_text
    assert "[截图#1]" in logs_text
    assert [block.kind for block in blocks] == ["text", "image", "text", "image"]
    assert blocks[0].text == "步骤 #1 打开课程管理"
    assert blocks[1].image_index == 0
    assert blocks[1].url == "https://example.com/reports/before.png"
    assert blocks[2].text == "断言失败：预期不存在系统课程，实际存在系统课程。"
    assert blocks[3].image_index == 1
    assert blocks[3].url == "https://example.com/shots/fail.png"


def test_html_parser_default_keeps_images_beyond_legacy_200_limit() -> None:
    parser = _ReportHTMLParser("https://example.com/reports/run.html")
    parser.feed(
        "<html><body>"
        + "".join(f"<div>步骤 {idx}</div><img src='shots/{idx}.png' />" for idx in range(205))
        + "</body></html>"
    )

    blocks = parser.limited_blocks(image_limit=None)
    image_blocks = [block for block in blocks if block.kind == "image"]

    assert len(parser.image_urls) == 205
    assert len(image_blocks) == 205
    assert image_blocks[-1].image_index == 204
    assert image_blocks[-1].url == "https://example.com/reports/shots/204.png"


@pytest.mark.asyncio
async def test_ai_hybrid_reader_does_not_fallback_to_plain_html(monkeypatch) -> None:
    class FakeResponse:
        url = "https://example.com/hybrid.html"
        headers = {"content-type": "text/html"}
        text = "<html><body><p>普通 HTML 文字</p><img src='shot.png' /></body></html>"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str):
            return FakeResponse()

    monkeypatch.setattr("app.report_readers.html.httpx.AsyncClient", FakeClient)

    report = await read_report("https://example.com/hybrid.html", executor="ai_hybrid")

    assert report.available is False
    assert report.reader == "ai_hybrid"
    assert report.error == "missing_hybrid_evidence"
    assert report.logs_text == ""
    assert report.image_urls == []
