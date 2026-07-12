from __future__ import annotations

import json

import pytest

from app.report_readers.base import ReportBlock, ReportEvidence
from app.services.case_repair import RepairInput, _renumber_report_blocks, _round1_payload
from app.services.executions import _aiphone_case_status, _worst_wins
from app.services.repair_orchestrator import (
    RepairDiagnosisInput,
    _report_failure_reason,
    _report_evidence_windows,
    _root_cause_initial_payload,
    _select_requested_windows,
    run_backend_repair_diagnosis,
    selected_key_image_url,
)


def test_worst_wins_waits_for_all_then_takes_worst() -> None:
    # 任一端还在跑 → 整体执行中（等齐）。
    assert _worst_wins(["passed", "running"]) == "running"
    assert _worst_wins(["failed", "running"]) == "running"
    # 全部终态、有失败 → 失败（先败后成不会被成功掩盖）。
    assert _worst_wins(["failed", "passed"]) == "failed"
    assert _worst_wins(["passed", "failed"]) == "failed"
    # 全部通过 → 通过。
    assert _worst_wins(["passed", "passed"]) == "passed"
    # 空 → 执行中。
    assert _worst_wins([]) == "running"


def test_aiphone_case_status_maps_terminal_states() -> None:
    assert _aiphone_case_status("success") == "passed"
    assert _aiphone_case_status("failed") == "failed"
    assert _aiphone_case_status("timeout") == "failed"
    assert _aiphone_case_status("queued") == "running"


def _make_report() -> ReportEvidence:
    return ReportEvidence(
        available=True,
        reader="ai_phone",
        url="https://example.com/report/android",
        failure_type="execution_failed",
        summary="Android 端：未找到登录按钮。",
        logs_text="步骤1 打开应用\n[截图#0]\n步骤2 点击登录\n[截图#1]",
        image_urls=["https://example.com/a/0.png", "https://example.com/a/1.png"],
    )


def _make_input(
    other_end_reports: tuple[dict[str, str], ...] = (),
    executor_failure_reason: str = "",
) -> RepairInput:
    return RepairInput(
        case_id=1,
        case_title="验证登录后进入首页",
        path="登录 / 账号 / 手机号",
        preconditions="已在登录页",
        steps_text="输入账号\n点击登录",
        expected_result="进入首页",
        report_url="https://example.com/report/android",
        executor_failure_reason=executor_failure_reason,
        function_map="",
        other_end_reports=other_end_reports,
    )


def test_round1_payload_single_end_has_no_multi_end_note() -> None:
    payload = json.loads(_round1_payload(_make_input(), _make_report()))
    assert "other_failed_ends" not in payload
    assert "multi_end_note" not in payload
    assert payload["report"]["url"] == "https://example.com/report/android"
    assert payload["report"]["failure_reason"] == "Android 端：未找到登录按钮。"


def test_round1_payload_uses_executor_failure_reason_as_anchor() -> None:
    payload = json.loads(
        _round1_payload(
            _make_input(executor_failure_reason="预期不存在，实际存在系统课程。"),
            _make_report(),
        )
    )
    assert payload["report"]["failure_reason"] == "预期不存在，实际存在系统课程。"


def test_round1_payload_multi_end_adds_note() -> None:
    payload = json.loads(_round1_payload(_make_input(), _make_report(), multi_end=True))
    # 多端时给出“只能给一份修复”的统一指令；轨迹/截图编号已在合并报告里处理。
    assert "multi_end_note" in payload
    assert "一份" in payload["multi_end_note"]
    assert payload["report"]["image_index_range"] == [0, 1]


def test_renumber_report_blocks_offsets_images_and_keeps_end_label() -> None:
    report = ReportEvidence(
        available=True,
        reader="ai_web",
        url="https://example.com/web",
        failure_type="assertion_failed",
        summary="失败",
        logs_text="步骤 #1\n[截图#0]",
        image_urls=["https://example.com/0.png"],
        blocks=[
            ReportBlock(index=0, kind="text", text="步骤 #1\n操作后 [截图#0]"),
            ReportBlock(index=1, kind="image", image_index=0, url="https://example.com/0.png"),
        ],
    )

    blocks = _renumber_report_blocks("Chrome端", report, image_offset=3, block_offset=10)

    assert blocks[0].index == 10
    assert blocks[0].text == "【Chrome端】\n步骤 #1\n操作后 [截图#3]"
    assert blocks[1].index == 11
    assert blocks[1].image_index == 3
    assert blocks[1].url == "https://example.com/0.png"


def _make_orchestrator_input(function_map: str = "") -> RepairDiagnosisInput:
    return RepairDiagnosisInput(
        case_id=1,
        case_title="验证课程管理菜单项",
        path="课程管理 / 菜单",
        preconditions="使用后台。",
        steps_text="查看课程管理下是否存在系统课程菜单。",
        expected_result="不存在",
        report_url="https://example.com/report/web",
        executor_failure_reason="预期课程管理下不存在系统课程菜单，实际存在系统课程菜单。",
        function_map=function_map,
    )


def _make_orchestrator_report() -> ReportEvidence:
    logs_text = (
        "测试标题：验证课程管理菜单项\n"
        "步骤 #1\nopen_url\n操作前\n[截图#0]\n操作后\n[截图#1]\n"
        "步骤 #2\nclick\n思考\n现在需要点击「入口A」进入另一个后台。\n"
        "动作\nclick(point='<point>10 10</point>')\n操作前\n[截图#2]\n操作后\n[截图#3]\n"
        "步骤 #3\nassert_fail\n思考\n课程管理下确实存在系统课程菜单。\n"
        "动作\nassert_fail(content='预期课程管理下不存在系统课程菜单，实际存在系统课程菜单。')\n操作前\n[截图#4]\n"
    )
    return ReportEvidence(
        available=True,
        reader="ai_web",
        url="https://example.com/report/web",
        failure_type="assertion_failed",
        summary="预期不存在，实际存在。",
        logs_text=logs_text,
        image_urls=[f"https://example.com/{idx}.png" for idx in range(5)],
        blocks=[
            ReportBlock(index=0, kind="text", text="测试标题：验证课程管理菜单项\n步骤 #1\nopen_url"),
            ReportBlock(index=1, kind="image", image_index=0, url="https://example.com/0.png"),
            ReportBlock(index=2, kind="image", image_index=1, url="https://example.com/1.png"),
            ReportBlock(
                index=3,
                kind="text",
                text="步骤 #2\nclick\n思考\n现在需要点击「入口A」进入另一个后台。\n动作\nclick(point='<point>10 10</point>')",
            ),
            ReportBlock(index=4, kind="image", image_index=2, url="https://example.com/2.png"),
            ReportBlock(index=5, kind="image", image_index=3, url="https://example.com/3.png"),
            ReportBlock(
                index=6,
                kind="text",
                text=(
                    "步骤 #3\nassert_fail\n思考\n课程管理下确实存在系统课程菜单。\n"
                    "动作\nassert_fail(content='预期课程管理下不存在系统课程菜单，实际存在系统课程菜单。')"
                ),
            ),
            ReportBlock(index=7, kind="image", image_index=4, url="https://example.com/4.png"),
        ],
    )


def test_report_assert_reason_wins_over_synced_report_summary() -> None:
    report = _make_orchestrator_report()
    assert _report_failure_reason(report, report.summary) == "预期课程管理下不存在系统课程菜单，实际存在系统课程菜单。"


def test_non_hybrid_report_without_blocks_does_not_fallback_to_logs_text() -> None:
    report = ReportEvidence(
        available=True,
        reader="ai_web",
        url="https://example.com/report/web",
        failure_type="assertion_failed",
        summary="预期不存在，实际存在。",
        logs_text="步骤 #1\n这里是旧粗文本。\n[截图#0]\n步骤 #2\nassert_fail(content='旧文本不该进窗口')",
        image_urls=["https://example.com/0.png"],
    )

    windows = _report_evidence_windows(report)
    payload = _root_cause_initial_payload(
        _make_orchestrator_input(),
        report,
        {"failure_anchor": "旧文本不该进窗口"},
        windows,
    )

    assert windows == []
    assert "trajectory_excerpt" not in payload["report"]
    assert "window_catalog" not in payload["report"]


def test_ai_hybrid_report_keeps_dedicated_evidence_window_from_logs_text() -> None:
    report = ReportEvidence(
        available=True,
        reader="ai_hybrid",
        url="https://example.com/report/hybrid",
        failure_type="assertion_failed",
        summary="Hybrid 总结：双端均未找到 uid 对应知识点。",
        logs_text=(
            "最终结论：双端均未找到 uid 对应知识点。\n"
            "证据：\n- cli 查到 uid\n- web 暂无数据\n- app 暂无结果\n"
            "错误证据：\n【Chrome端】[截图#0]\n【安卓端】[截图#1]"
        ),
        image_urls=["https://example.com/web.png", "https://example.com/app.png"],
    )

    windows = _report_evidence_windows(report)
    payload = _root_cause_initial_payload(
        _make_orchestrator_input(),
        report,
        {"failure_anchor": "双端均未找到 uid 对应知识点。"},
        windows,
    )

    assert [window.window_id for window in windows] == ["hybrid_total_report"]
    assert windows[0].image_indices == (0, 1)
    assert "双端均未找到 uid" in windows[0].text
    assert payload["report"]["reading_mode"] == "ai_hybrid_structured_summary"
    assert "双端均未找到 uid" in payload["report"]["evidence_text"]
    assert "trajectory_excerpt" not in payload["report"]
    assert payload["report"]["window_catalog"][0]["window_id"] == "hybrid_total_report"
    assert "AI Hybrid" in payload["report"]["request_hint"]


def test_ai_hybrid_image_only_report_uses_images_without_fake_text() -> None:
    report = ReportEvidence(
        available=True,
        reader="ai_hybrid",
        url="https://example.com/report/hybrid",
        failure_type="execution_failed",
        summary="",
        logs_text="",
        image_urls=["https://example.com/api.png", "https://example.com/web.png"],
    )

    windows = _report_evidence_windows(report)
    payload = _root_cause_initial_payload(
        _make_orchestrator_input(),
        report,
        {"failure_anchor": "AI Hybrid 只有截图证据。"},
        windows,
    )

    assert [window.window_id for window in windows] == ["hybrid_total_report"]
    assert windows[0].text == ""
    assert windows[0].image_indices == (0, 1)
    assert "未提供可补充的文字轨迹" not in payload["report"]["evidence_text"]
    assert "关联截图 #0, #1" in payload["report"]["evidence_text"]


@pytest.mark.asyncio
async def test_ai_hybrid_need_more_reuses_hybrid_total_report_window() -> None:
    calls: list[list[dict[str, object]]] = []
    report = ReportEvidence(
        available=True,
        reader="ai_hybrid",
        url="https://example.com/report/hybrid",
        failure_type="assertion_failed",
        summary="Hybrid 总结：双端均未找到 uid 对应知识点。",
        logs_text=(
            "最终结论：双端均未找到 uid 对应知识点。\n"
            "证据：\n- cli 查到 uid\n- web 暂无数据\n- app 暂无结果\n"
            "错误证据：\n【Chrome端】[截图#0]\n【安卓端】[截图#1]"
        ),
        image_urls=["https://example.com/web.png", "https://example.com/app.png"],
    )

    async def fake_call(messages: list[dict[str, object]]) -> str:
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "need_more": True,
                    "question": "请复核 uid 双端搜索证据。",
                    "request_images": [],
                    "request_windows": ["hybrid_total_report"],
                    "root_cause_type": "insufficient_evidence",
                    "root_cause_point": "需要复核 Hybrid 总报告证据。",
                    "failure_type": "assertion_failed",
                    "key_image_index": [0, 1],
                    "confidence": 30,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "need_more": False,
                "root_cause_type": "business_failure",
                "root_cause_point": "AI Hybrid 总报告显示 web 和 app 均未找到该 uid 对应知识点。",
                "failure_type": "assertion_failed",
                "evidence": "hybrid_total_report 内的 cli/web/app 证据一致。",
                "key_image_index": [0, 1],
                "confidence": 80,
            },
            ensure_ascii=False,
        )

    async def fake_image(url: str) -> dict[str, object]:
        return {"type": "image_url", "image_url": {"url": url}}

    result = await run_backend_repair_diagnosis(
        _make_orchestrator_input(),
        report,
        "fake-model",
        fake_call,
        fake_image,
        first_round_image_indices=[0, 1],
    )

    evidence_rounds = [step for step in result["process"] if step.get("stage") == "root_cause"]
    assert len(calls) == 2
    assert result["repairable"] is False
    assert evidence_rounds[0]["selected_windows"] == ["hybrid_total_report"]
    assert evidence_rounds[1]["shown_windows"] == ["hybrid_total_report"]


@pytest.mark.asyncio
async def test_ai_hybrid_need_more_without_request_stops_as_insufficient_evidence() -> None:
    calls: list[list[dict[str, object]]] = []
    report = ReportEvidence(
        available=True,
        reader="ai_hybrid",
        url="https://example.com/report/hybrid",
        failure_type="assertion_failed",
        summary="Hybrid 总结。",
        logs_text="最终结论：失败。\n证据：\n- web 未找到数据\n错误证据：\n【Chrome端】[截图#0]",
        image_urls=["https://example.com/web.png"],
    )

    async def fake_call(messages: list[dict[str, object]]) -> str:
        calls.append(messages)
        return json.dumps(
            {
                "need_more": True,
                "question": "需要更多证据。",
                "request_images": [],
                "request_windows": [],
                "root_cause_type": "insufficient_evidence",
                "failure_type": "assertion_failed",
                "confidence": 20,
            },
            ensure_ascii=False,
        )

    async def fake_image(url: str) -> dict[str, object]:
        return {"type": "image_url", "image_url": {"url": url}}

    result = await run_backend_repair_diagnosis(
        _make_orchestrator_input(),
        report,
        "fake-model",
        fake_call,
        fake_image,
        first_round_image_indices=[0],
    )

    evidence_rounds = [step for step in result["process"] if step.get("stage") == "root_cause"]
    assert len(calls) == 1
    assert result["repairable"] is False
    assert "没有点名有效文字窗口或截图" in result["reason"]
    assert evidence_rounds[0]["decision"] == "need_more_empty"
    assert evidence_rounds[0]["selected_windows"] == []


def test_initial_indexed_payload_carries_tail_text_and_image_indices_without_images() -> None:
    blocks: list[ReportBlock] = []
    image_urls: list[str] = []
    for idx in range(120):
        text = f"普通步骤 {idx}：继续执行。"
        if idx == 119:
            text = "尾部失败步骤 119：assert_fail，期望不存在，实际存在。"
        blocks.append(ReportBlock(index=len(blocks), kind="text", text=text))
        image_urls.append(f"https://example.com/{idx}.png")
        blocks.append(
            ReportBlock(
                index=len(blocks),
                kind="image",
                image_index=idx,
                url=f"https://example.com/{idx}.png",
            )
        )
    report = ReportEvidence(
        available=True,
        reader="ai_phone",
        url="https://example.com/report/android",
        failure_type="assertion_failed",
        summary="尾部失败。",
        logs_text="旧全文不作为首轮证据。",
        image_urls=image_urls,
        blocks=blocks,
    )

    windows = _report_evidence_windows(report)
    payload = _root_cause_initial_payload(
        _make_orchestrator_input(),
        report,
        {"failure_anchor": "尾部失败步骤 119"},
        windows,
    )

    report_payload = payload["report"]
    assert report_payload["reading_mode"] == "indexed_failure_tail_first"
    assert "尾部失败步骤 119" in report_payload["evidence_text"]
    assert "普通步骤 0" not in report_payload["evidence_text"]
    assert "普通步骤 79" not in report_payload["evidence_text"]
    assert "普通步骤 80" in report_payload["evidence_text"]
    assert report_payload["stats"]["text_window_count"] == 120
    assert report_payload["stats"]["tail_window_count"] == 40
    assert report_payload["stats"]["selected_text_window_count"] == 40
    assert report_payload["text_menu"]["available_text_window_range"] == ["block_0", "block_238"]
    assert report_payload["text_menu"]["tail_window_ids"][0] == "block_160"
    assert report_payload["text_menu"]["tail_window_ids"][-1] == "block_238"
    assert {"image_index": 119, "block_index": 239, "near_text_window": "block_238"} in report_payload["image_catalog"]
    assert "[截图#119]" in report_payload["evidence_text"]


def test_requested_windows_accepts_block_ranges() -> None:
    windows = _report_evidence_windows(_make_orchestrator_report())

    selected = _select_requested_windows(windows, ["block_3-block_6"])

    assert [window.window_id for window in selected] == ["block_3", "block_6"]


def test_selected_key_image_url_requires_explicit_valid_index() -> None:
    images = ["https://example.com/0.png", "https://example.com/last.png"]

    assert selected_key_image_url(images, 1) == "https://example.com/last.png"
    assert selected_key_image_url(images, None) == ""
    assert selected_key_image_url(images, 99) == ""
    assert selected_key_image_url(images, [0, 1]) == ""


@pytest.mark.asyncio
async def test_backend_orchestrator_rejects_empty_need_more_request() -> None:
    calls: list[list[dict[str, object]]] = []

    async def fake_call(messages: list[dict[str, object]]) -> str:
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "need_more": True,
                    "question": "需要确认点击「入口A」后是否发生跳转，以及断言现场看到什么",
                    "request_images": [],
                },
                ensure_ascii=False,
            )
        raise AssertionError("空补证据请求不应进入第二轮模型调用")

    async def fake_image(url: str) -> dict[str, object]:
        return {"type": "image_url", "image_url": {"url": url}}

    result = await run_backend_repair_diagnosis(
        _make_orchestrator_input(),
        _make_orchestrator_report(),
        "fake-model",
        fake_call,
        fake_image,
        first_round_image_indices=[3, 4],
    )

    assert len(calls) == 1
    assert result["repairable"] is False
    assert result["failure_type"] == "assertion_failed"
    evidence_rounds = [step for step in result["process"] if step.get("stage") == "root_cause"]
    assert evidence_rounds[0]["shown_images"] == [3, 4]
    assert evidence_rounds[0]["raw_request_images"] == []
    assert evidence_rounds[0]["selected_windows"] == []
    assert evidence_rounds[0]["request_images"] == []
    assert evidence_rounds[0]["decision"] == "need_more_empty"


@pytest.mark.asyncio
async def test_backend_orchestrator_uses_indexed_report_blocks_for_need_more() -> None:
    calls: list[list[dict[str, object]]] = []
    report = ReportEvidence(
        available=True,
        reader="ai_web",
        url="https://example.com/report/web",
        failure_type="assertion_failed",
        summary="预期不存在，实际存在。",
        logs_text="粗略全文仍保留，但诊断应优先用 blocks。\n[截图#0]\n[截图#1]",
        image_urls=["https://example.com/0.png", "https://example.com/1.png"],
        blocks=[
            ReportBlock(index=0, kind="text", text="步骤 #1 打开课程管理。"),
            ReportBlock(index=1, kind="image", image_index=0, url="https://example.com/0.png"),
            ReportBlock(index=2, kind="text", text="步骤 #2 搜索系统课程菜单。"),
            ReportBlock(index=3, kind="text", text="断言失败：预期不存在系统课程菜单，实际存在系统课程菜单。"),
            ReportBlock(index=4, kind="image", image_index=1, url="https://example.com/1.png"),
        ],
    )

    async def fake_call(messages: list[dict[str, object]]) -> str:
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "need_more": True,
                    "question": "需要确认断言失败时系统课程菜单实际是否存在",
                    "request_images": [],
                    "request_windows": ["block_3"],
                },
                ensure_ascii=False,
            )
        evidence_payload = json.loads(calls[-1][-1]["content"][0]["text"])  # type: ignore[index]
        assert evidence_payload["stage"] == "evidence_window"
        assert "block_3" in [window["window_id"] for window in evidence_payload["windows"]]
        assert any("断言失败" in window["text"] for window in evidence_payload["windows"])
        assert evidence_payload["provided_images"] == []
        return json.dumps(
            {
                "need_more": False,
                "root_cause_type": "product_mismatch",
                "root_cause_point": "断言现场显示系统课程菜单实际存在。",
                "failure_type": "assertion_failed",
                "evidence": "文字块#3 与截图#1 对应断言现场。",
                "key_image_index": 1,
                "confidence": 90,
            },
            ensure_ascii=False,
        )

    async def fake_image(url: str) -> dict[str, object]:
        return {"type": "image_url", "image_url": {"url": url}}

    result = await run_backend_repair_diagnosis(
        _make_orchestrator_input(),
        report,
        "fake-model",
        fake_call,
        fake_image,
        first_round_image_indices=[0],
    )

    assert len(calls) == 2
    assert result["repairable"] is False
    evidence_rounds = [step for step in result["process"] if step.get("stage") == "root_cause"]
    assert "block_3" in evidence_rounds[0]["selected_windows"]
    assert evidence_rounds[0]["request_images"] == []


@pytest.mark.asyncio
async def test_backend_orchestrator_shows_last_image_first_for_single_indexed_report() -> None:
    calls: list[list[dict[str, object]]] = []
    report = ReportEvidence(
        available=True,
        reader="ai_phone",
        url="https://example.com/report/android",
        failure_type="execution_failed",
        summary="末尾失败。",
        logs_text="旧全文不作为首轮证据。",
        image_urls=["https://example.com/0.png", "https://example.com/1.png", "https://example.com/2.png"],
        blocks=[
            ReportBlock(index=0, kind="text", text="步骤 #1 正常执行。"),
            ReportBlock(index=1, kind="image", image_index=0, url="https://example.com/0.png"),
            ReportBlock(index=2, kind="text", text="步骤 #2 正常执行。"),
            ReportBlock(index=3, kind="image", image_index=1, url="https://example.com/1.png"),
            ReportBlock(index=4, kind="text", text="步骤 #3 失败：未找到登录按钮。"),
            ReportBlock(index=5, kind="image", image_index=2, url="https://example.com/2.png"),
        ],
    )

    async def fake_call(messages: list[dict[str, object]]) -> str:
        calls.append(messages)
        return json.dumps(
            {
                "need_more": False,
                "root_cause_type": "case_repairable",
                "root_cause_point": "步骤缺少进入登录页动作。",
                "failure_type": "execution_failed",
                "evidence": "文字块#4 与截图#2 指向失败现场。",
                "key_image_index": 2,
                "confidence": 88,
            },
            ensure_ascii=False,
        )

    async def fake_image(url: str) -> dict[str, object]:
        return {"type": "image_url", "image_url": {"url": url}}

    result = await run_backend_repair_diagnosis(
        _make_orchestrator_input(),
        report,
        "fake-model",
        fake_call,
        fake_image,
    )

    assert len(calls) == 2
    evidence_rounds = [step for step in result["process"] if step.get("stage") == "root_cause"]
    assert evidence_rounds[0]["shown_images"] == [2]


@pytest.mark.asyncio
async def test_backend_orchestrator_blocks_trajectory_only_requirement_terms() -> None:
    calls: list[list[dict[str, object]]] = []

    async def fake_call(messages: list[dict[str, object]]) -> str:
        calls.append(messages)
        return json.dumps(
            {
                "need_more": False,
                "root_cause_type": "case_repairable",
                "root_cause_point": "点击「入口A」后未进入正确后台，导致在错误页面验证。",
                "failure_type": "execution_failed",
                "evidence": "执行轨迹思考提到需要进入「入口A」，截图显示仍在原页面。",
                "key_image_index": 4,
                "confidence": 88,
            },
            ensure_ascii=False,
        )

    async def fake_image(url: str) -> dict[str, object]:
        return {"type": "image_url", "image_url": {"url": url}}

    result = await run_backend_repair_diagnosis(
        _make_orchestrator_input(),
        _make_orchestrator_report(),
        "fake-model",
        fake_call,
        fake_image,
        first_round_image_indices=[3, 4],
    )

    assert len(calls) == 1
    assert result["repairable"] is False
    assert result["repair_channel"] == "none"
    assert result["proposed_steps"] == ""
    assert "未声明" in result["reason"]
