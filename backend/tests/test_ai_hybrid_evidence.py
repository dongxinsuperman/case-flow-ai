from __future__ import annotations

from app.report_readers.base import ReportEvidence
from app.report_readers.html import _hybrid_report_evidence, hybrid_end_key_indices
from app.services.ai_hybrid.evidence import build_evidence, collect_evidence, end_label
from app.services.ai_hybrid.reporter import build_hybrid_report
from app.services.ai_hybrid.schemas import HybridRunResult


def _failed_web_result() -> HybridRunResult:
    """线上那次的形态：agent 只调 ai_web，凭「暂无数据」文字判失败，没主动看任何图。"""
    return HybridRunResult(
        status="failed",
        status_reason="execution_failed",
        final_summary="课程平台知识点管理未找到该 uid 对应知识点",
        input_payload={"title": "跨端验证", "preconditions": "测试账号", "steps_text": "查", "expected_result": "有数据"},
        child_results_payload=[
            {"tool": "cli", "status": "success", "reason": "cli_success", "raw": {}},
            {
                "tool": "ai_web",
                "status": "failed",
                "reason": "暂无数据",
                "report_url": "http://exec/web-report.html",
                "raw": {"execution_strategy": {"platform": "chrome"}},
            },
        ],
        reasoning_trace=[
            {"phase": "step", "index": 1, "tool": "cli", "status": "success", "reason": "cli_success", "thought": "查 uid"},
            {"phase": "step", "index": 2, "tool": "ai_web", "status": "failed", "reason": "暂无数据", "thought": "去后台搜"},
            {"phase": "finish", "index": 3, "verdict": "failed", "thought": "未找到，判失败",
             "attribution": "知识点管理无对应数据", "evidence": ["搜索结果暂无数据"], "suggestions": ["排查数据"]},
        ],
    )


def _fake_read_report(*urls_images):
    async def _reader(report_url: str, executor: str | None = None, image_limit: int | None = 40) -> ReportEvidence:
        return ReportEvidence(
            available=True, reader=executor or "html", url=report_url, failure_type="execution_failed",
            summary="知识点管理搜索结果：暂无数据",
            logs_text="...", image_urls=["http://exec/s1.png", "http://exec/s2.png"], image_evidence=[],
        )

    return _reader


def test_end_label_prefers_platform_then_executor() -> None:
    assert end_label("ai_phone", "android") == "安卓端"
    assert end_label("ai_web", "chrome") == "Chrome端"
    assert end_label("ai_web", "") == "Web端"
    assert end_label("ai_api", "") == "接口"
    assert end_label("", "") == "该端"


def test_collect_evidence_has_no_images_only_trajectory() -> None:
    ev = collect_evidence(_failed_web_result())
    assert ev["images"] == []  # 错误图由 build_evidence 按失败端补齐，collect 不收图
    assert "去后台搜" in ev["trajectory_text"]
    assert "暂无数据" in ev["trajectory_text"]


async def test_build_evidence_takes_failed_end_last_screenshot(monkeypatch) -> None:
    monkeypatch.setattr("app.report_readers.html.read_report", _fake_read_report())
    ev = await build_evidence(_failed_web_result(), settings=object())
    assert len(ev["images"]) == 1
    img = ev["images"][0]
    assert img["platform"] == "Chrome端"
    assert img["image_url"] == "http://exec/s2.png"  # 末图=失败截图
    assert "[截图#0]" in ev["trajectory_text"]
    assert "失败端" in ev["trajectory_text"]


async def test_build_evidence_uses_unlimited_read_for_true_last_image(monkeypatch) -> None:
    """子报告超 40 张图时，必须取真·末图，而非被截断的第 40 张。"""
    captured: dict[str, object] = {}

    async def reader(report_url: str, executor: str | None = None, image_limit: int | None = 40):
        captured["image_limit"] = image_limit
        urls = [f"http://exec/shot{i}.png" for i in range(80)]
        if image_limit is not None:
            urls = urls[:image_limit]
        return ReportEvidence(
            available=True, reader=executor or "html", url=report_url, failure_type="execution_failed",
            summary="失败", logs_text="...", image_urls=urls, image_evidence=[],
        )

    monkeypatch.setattr("app.report_readers.html.read_report", reader)
    ev = await build_evidence(_failed_web_result(), settings=object())
    assert captured["image_limit"] is None  # hybrid 取图不截断
    assert ev["images"][0]["image_url"] == "http://exec/shot79.png"  # 真·末图（第80张）
    assert ev["images"][0]["img_no"] == 79


async def test_build_evidence_skips_passed_end(monkeypatch) -> None:
    monkeypatch.setattr("app.report_readers.html.read_report", _fake_read_report())
    result = HybridRunResult(
        status="failed",
        status_reason="execution_failed",
        final_summary="App 失败",
        child_results_payload=[
            {"tool": "ai_web", "status": "success", "reason": "ok", "report_url": "http://exec/web.html",
             "raw": {"execution_strategy": {"platform": "chrome"}}},
            {"tool": "ai_phone", "status": "failed", "reason": "元素未出现", "report_url": "http://exec/phone.html",
             "raw": {"execution_strategy": {"platform": "android"}}},
        ],
        reasoning_trace=[{"phase": "finish", "index": 1, "verdict": "failed", "thought": "App 失败"}],
    )
    ev = await build_evidence(result, settings=object())
    # 只取失败端（安卓端），通过端（Web）不进证据
    assert len(ev["images"]) == 1
    assert ev["images"][0]["platform"] == "安卓端"


async def test_build_evidence_no_images_when_passed() -> None:
    result = HybridRunResult(
        status="success",
        status_reason="all_required_steps_passed",
        final_summary="通过",
        child_results_payload=[
            {"tool": "ai_web", "status": "success", "reason": "ok", "report_url": "http://exec/web.html",
             "raw": {"execution_strategy": {"platform": "chrome"}}},
        ],
        reasoning_trace=[{"phase": "finish", "index": 1, "verdict": "success", "thought": "ok"}],
    )
    ev = await build_evidence(result, settings=object())
    assert ev["images"] == []


async def test_report_renders_error_gallery_and_read_report_round_trip(monkeypatch) -> None:
    monkeypatch.setattr("app.report_readers.html.read_report", _fake_read_report())
    result = _failed_web_result()
    evidence = await build_evidence(result, settings=object())

    html = build_hybrid_report(result, evidence)
    assert "错误证据截图" in html
    assert "Chrome端" in html
    assert "http://exec/s2.png" in html
    assert 'id="hybrid-evidence"' in html

    report = _hybrid_report_evidence("http://local/hybrid.html", html)
    assert report is not None
    assert report.reader == "ai_hybrid"
    assert report.image_urls == ["http://exec/s2.png"]
    assert report.image_evidence[0].platform == "Chrome端"
    assert "[截图#0]" in report.logs_text
    assert hybrid_end_key_indices(report) == [("Chrome端", 0)]


async def test_full_chain_hybrid_report_to_bug_draft_per_end_images(monkeypatch, tmp_path) -> None:
    """完整链路：hybrid 双端失败报告 → read_report(ai_hybrid) → hybrid_end_key_indices
    → 逐端 key_images → 提 bug 逐端取本地图（含叉掉过滤）。全程不碰 DB/模型。"""
    from types import SimpleNamespace

    from app.services import bug_submit

    # 1) 造一份「Web端 + 安卓端」都失败、agent 未主动看图的 hybrid run
    result = HybridRunResult(
        status="failed",
        status_reason="execution_failed",
        final_summary="Web 后台可见但 App 未同步",
        child_results_payload=[
            {"tool": "ai_web", "status": "failed", "reason": "字段缺失", "report_url": "http://exec/web.html",
             "raw": {"execution_strategy": {"platform": "chrome"}}},
            {"tool": "ai_phone", "status": "failed", "reason": "列表为空", "report_url": "http://exec/phone.html",
             "raw": {"execution_strategy": {"platform": "android"}}},
        ],
        reasoning_trace=[
            {"phase": "step", "index": 1, "tool": "ai_web", "status": "failed", "reason": "字段缺失", "thought": "查后台"},
            {"phase": "step", "index": 2, "tool": "ai_phone", "status": "failed", "reason": "列表为空", "thought": "查 App"},
            {"phase": "finish", "index": 3, "verdict": "failed", "thought": "App 未同步"},
        ],
    )

    async def reader(report_url: str, executor: str | None = None, image_limit: int | None = 40):
        name = "web" if "web" in report_url else "phone"
        return ReportEvidence(
            available=True, reader=executor or "html", url=report_url, failure_type="execution_failed",
            summary=f"{name} 失败", logs_text="...",
            image_urls=[f"http://exec/{name}_1.png", f"http://exec/{name}_last.png"], image_evidence=[],
        )

    monkeypatch.setattr("app.report_readers.html.read_report", reader)

    # 2) 收敛 → 写报告 → read_report(ai_hybrid) 读回 → 逐端末图索引
    evidence = await build_evidence(result, settings=object())
    html = build_hybrid_report(result, evidence)
    parsed = _hybrid_report_evidence("http://local/hybrid.html", html)
    assert parsed is not None
    ends = hybrid_end_key_indices(parsed)
    assert sorted(label for label, _ in ends) == ["Chrome端", "安卓端"]

    # 3) 模拟 case_repair 逐端下载后写入 diagnosis.key_images（图落 repair_image_dir）
    (tmp_path / "web.png").write_bytes(b"w")
    (tmp_path / "phone.png").write_bytes(b"p")
    label_to_file = {"Chrome端": "/media/web.png", "安卓端": "/media/phone.png"}
    diagnosis = {"key_images": [{"platform": label, "image": label_to_file[label]} for label, _ in ends]}

    monkeypatch.setattr(bug_submit, "get_settings", lambda: SimpleNamespace(repair_image_dir=str(tmp_path)))

    # 4) 提 bug 逐端取本地图
    paths = bug_submit._key_image_local_paths(diagnosis)
    assert sorted(label for label, _ in paths) == ["Chrome端", "安卓端"]
    assert all(p.exists() for _, p in paths)


def test_report_without_images_has_no_gallery() -> None:
    result = HybridRunResult(
        status="failed",
        status_reason="execution_failed",
        final_summary="纯数据前置失败",
        reasoning_trace=[{"phase": "finish", "index": 1, "verdict": "failed", "thought": "数据不满足"}],
    )
    html = build_hybrid_report(result)  # 无 evidence 参数 → collect_evidence，images 空
    assert "错误证据截图" not in html
