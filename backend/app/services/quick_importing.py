from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quick import (
    QuickBugDraft,
    QuickCase,
    QuickCaseBody,
    QuickCaseStep,
    QuickCaseWorkItem,
    QuickRepairDraft,
    QuickSession,
)
from app.schemas.quick import (
    QuickCaseItemOut,
    QuickCaseMutationOut,
    QuickCaseUpdateIn,
    QuickClearOut,
    QuickCoverageOut,
    QuickCoverageUpdateIn,
    QuickExportOut,
    QuickFunctionFileIn,
    QuickFunctionFileOut,
    QuickImportIn,
    QuickImportOut,
    QuickSessionOut,
    QuickSessionPatchIn,
    QuickSessionSummaryOut,
    QuickWorkItemUpdateIn,
    QuickWorkItemUpdateOut,
)
from app.services import case_tagging
from app.services.execution_reset import (
    clear_quick_case_execution_artifacts,
    reset_quick_work_item_execution,
)
from app.services.markdown_text import collapse_inline_text, extract_title_tags
from app.services.quick_markdown import (
    QuickParsedCase,
    parse_quick_markdown,
    render_quick_markdown,
)

COVERAGE_LANES = {"android", "ios", "harmony", "chrome", "safari", "firefox"}
COVERAGE_STATES = {"none", "passed", "failed"}
FAILURE_TYPE_LABELS = {
    "assertion_failed": "断言失败",
    "business_failure": "业务失败",
    "execution_failed": "执行失败",
    "environment_failure": "执行失败",
    "case_step_failure": "步骤问题",
    "flaky_failure": "偶发波动",
}
VISIBLE_FAILURE_TYPE_LABELS = set(FAILURE_TYPE_LABELS.values())


async def create_session_from_markdown(session: AsyncSession, payload: QuickImportIn) -> QuickImportOut:
    parsed = parse_quick_markdown(payload.content, payload.filename or "uploaded.md")
    session_id = await _new_session_id(session)
    quick_session = QuickSession(
        session_id=session_id,
        source_name=payload.filename or "uploaded.md",
        suite_title=parsed.suite_title,
        function_files=_function_files(payload.function_files),
        status="active",
    )
    session.add(quick_session)
    await session.flush()

    tags = await case_tagging.classify_cases([_tagging_input(case) for case in parsed.cases])
    for case, tag in zip(parsed.cases, tags, strict=True):
        await _insert_case(session, quick_session.session_id, case, tag)

    await session.commit()
    return QuickImportOut(
        session=await _session_summary(session, quick_session.session_id),
        cases=await list_cases(session, quick_session.session_id),
        warnings=parsed.warnings,
    )


async def get_session_detail(session: AsyncSession, session_id: str) -> QuickSessionOut | None:
    quick_session = await session.get(QuickSession, session_id)
    if quick_session is None:
        return None
    return QuickSessionOut(
        session=await _session_summary(session, session_id),
        cases=await list_cases(session, session_id),
    )


async def update_session(
    session: AsyncSession,
    session_id: str,
    payload: QuickSessionPatchIn,
) -> QuickSessionOut | None:
    quick_session = await session.get(QuickSession, session_id)
    if quick_session is None:
        return None
    clear_bug_drafts = False
    if payload.function_files is not None:
        quick_session.function_files = _function_files(payload.function_files)
        clear_bug_drafts = True
    if payload.feishu_requirement_url is not None:
        next_url = payload.feishu_requirement_url.strip() or None
        clear_bug_drafts = clear_bug_drafts or next_url != quick_session.feishu_requirement_url
        quick_session.feishu_requirement_url = next_url
        quick_session.feishu_target = {}
    if payload.feishu_bug_url is not None:
        quick_session.feishu_bug_url = payload.feishu_bug_url.strip() or None
    if payload.current_user_id is not None:
        next_user_id = payload.current_user_id if payload.current_user_id > 0 else None
        clear_bug_drafts = clear_bug_drafts or next_user_id != quick_session.current_user_id
        quick_session.current_user_id = next_user_id
    if clear_bug_drafts:
        case_ids = select(QuickCase.id).where(QuickCase.session_id == session_id)
        await session.execute(delete(QuickBugDraft).where(QuickBugDraft.case_id.in_(case_ids)))
    quick_session.updated_at = func.now()
    await session.commit()
    return await get_session_detail(session, session_id)


async def export_session(session: AsyncSession, session_id: str, *, clear: bool) -> QuickExportOut | None:
    quick_session = await session.get(QuickSession, session_id)
    if quick_session is None:
        return None
    rows = await session.execute(
        select(QuickCase, QuickCaseBody)
        .join(QuickCaseBody, QuickCaseBody.case_id == QuickCase.id)
        .where(QuickCase.session_id == session_id)
        .order_by(QuickCase.ordinal, QuickCase.id)
    )
    cases = [
        {
            "id": case.id,
            "ordinal": case.ordinal,
            "path_nodes": _case_path_nodes(case),
            "core_nodes": case.core_nodes or {},
            "raw_title": case.raw_title,
            "preconditions": body.preconditions,
            "steps_text": body.steps_text,
            "expected_result": body.expected_result,
        }
        for case, body in rows.all()
    ]
    content = render_quick_markdown(quick_session.suite_title, cases)
    filename = _export_filename(quick_session.source_name)
    if clear:
        await session.delete(quick_session)
        await session.commit()
    return QuickExportOut(filename=filename, content=content, cleared=clear)


async def export_and_clear_session(session: AsyncSession, session_id: str) -> QuickExportOut | None:
    return await export_session(session, session_id, clear=True)


async def clear_session(session: AsyncSession, session_id: str) -> QuickClearOut:
    quick_session = await session.get(QuickSession, session_id)
    if quick_session is not None:
        await session.delete(quick_session)
        await session.commit()
    return QuickClearOut(session_id=session_id, cleared=True)


async def list_cases(session: AsyncSession, session_id: str) -> list[QuickCaseItemOut]:
    rows = await session.execute(
        select(QuickCase, QuickCaseBody, QuickCaseWorkItem, QuickSession)
        .join(QuickCaseBody, QuickCaseBody.case_id == QuickCase.id)
        .join(QuickCaseWorkItem, QuickCaseWorkItem.case_id == QuickCase.id)
        .join(QuickSession, QuickSession.session_id == QuickCase.session_id)
        .where(QuickCase.session_id == session_id)
        .order_by(QuickCase.ordinal, QuickCase.id)
    )
    outs = [
        _case_to_out(case, body, work_item, quick_session)
        for case, body, work_item, quick_session in rows.all()
    ]
    for index, out in enumerate(outs, start=1):
        out.display_no = str(index)
    await _apply_ready_flags(session, outs)
    _kick_pending_diagnoses(outs)
    return outs


async def get_case(session: AsyncSession, case_id: int) -> QuickCaseItemOut | None:
    row = (
        await session.execute(
            select(QuickCase, QuickCaseBody, QuickCaseWorkItem, QuickSession)
            .join(QuickCaseBody, QuickCaseBody.case_id == QuickCase.id)
            .join(QuickCaseWorkItem, QuickCaseWorkItem.case_id == QuickCase.id)
            .join(QuickSession, QuickSession.session_id == QuickCase.session_id)
            .where(QuickCase.id == case_id)
        )
    ).one_or_none()
    if row is None:
        return None
    out = _case_to_out(*row)
    await _apply_ready_flags(session, [out])
    return out


async def update_case(
    session: AsyncSession,
    case_id: int,
    payload: QuickCaseUpdateIn,
) -> QuickCaseMutationOut | None:
    case = await session.get(QuickCase, case_id)
    body = await session.get(QuickCaseBody, case_id)
    work_item = await session.get(QuickCaseWorkItem, case_id)
    if case is None or body is None or work_item is None:
        return None
    quick_session = await session.get(QuickSession, case.session_id)
    if quick_session is None:
        return None

    if payload.clean_title is not None and payload.raw_title is None:
        raise ValueError("更新 Quick 测试标题必须提交完整标题 raw_title")
    if payload.raw_title is not None:
        raw_title = collapse_inline_text(payload.raw_title)
        title_tags = extract_title_tags(raw_title)
        if raw_title:
            case.raw_title = raw_title
            case.clean_title = raw_title
            case.scenario_tags = [tag for tag in title_tags if tag != "人工"]
            case.manual = "人工" in title_tags
            body.goal = raw_title
            _set_core_node(case, "case_title", raw_title)
    if payload.preconditions is not None:
        body.preconditions = collapse_inline_text(payload.preconditions)
        _set_core_node(case, "preconditions", body.preconditions)
    if payload.steps_text is not None:
        body.steps_text = collapse_inline_text(payload.steps_text)
        _set_core_node(case, "steps", body.steps_text)
        await _replace_steps(session, case.id, body.steps_text)
    if payload.expected_result is not None:
        body.expected_result = collapse_inline_text(payload.expected_result)
        _set_core_node(case, "expected", body.expected_result)

    case.updated_at = func.now()
    quick_session.updated_at = func.now()
    reset_quick_work_item_execution(work_item, status="not_run")
    await clear_quick_case_execution_artifacts(session, [case.id])
    await session.commit()
    return QuickCaseMutationOut(case_id=case.id, message="Quick case 已更新")


async def update_case_work_item(
    session: AsyncSession,
    payload: QuickWorkItemUpdateIn,
) -> QuickWorkItemUpdateOut | None:
    work_item = await session.get(QuickCaseWorkItem, payload.case_id)
    if work_item is None:
        return None
    should_clear_artifacts = payload.execution_status is not None or payload.execution_target is not None
    if payload.execution_status is not None:
        reset_quick_work_item_execution(work_item, status=payload.execution_status)
    elif payload.execution_target is not None:
        reset_quick_work_item_execution(work_item, status="not_run")
    if payload.execution_target is not None:
        work_item.execution_target = payload.execution_target
    if payload.run_enabled is not None:
        work_item.run_enabled = payload.run_enabled
    if not should_clear_artifacts:
        work_item.updated_at = func.now()
    if should_clear_artifacts:
        await clear_quick_case_execution_artifacts(session, [payload.case_id])
    await session.commit()
    return QuickWorkItemUpdateOut(
        case_id=work_item.case_id,
        execution_status=work_item.execution_status,
        execution_target=work_item.execution_target,
        run_enabled=work_item.run_enabled,
    )


async def set_case_coverage(
    session: AsyncSession,
    payload: QuickCoverageUpdateIn,
) -> QuickCoverageOut | None:
    lane = str(payload.lane).lower()
    state = str(payload.state).lower()
    if lane not in COVERAGE_LANES:
        raise ValueError(f"非法覆盖泳道：{payload.lane}")
    if state not in COVERAGE_STATES:
        raise ValueError(f"非法覆盖状态：{payload.state}")
    work_item = await session.get(QuickCaseWorkItem, payload.case_id)
    if work_item is None:
        return None
    coverage = {str(k): str(v) for k, v in (work_item.coverage or {}).items()}
    if state == "none":
        coverage.pop(lane, None)
    else:
        coverage[lane] = state
    work_item.coverage = coverage
    work_item.updated_at = func.now()
    await session.commit()
    return QuickCoverageOut(case_id=payload.case_id, coverage=coverage)


async def _insert_case(
    session: AsyncSession,
    session_id: str,
    parsed: QuickParsedCase,
    tag: dict[str, Any],
) -> int:
    case = QuickCase(
        session_id=session_id,
        ordinal=parsed.ordinal,
        suite_title=parsed.suite_title,
        path_nodes=parsed.path_nodes,
        core_nodes=parsed.core_nodes,
        raw_title=parsed.raw_title,
        clean_title=parsed.clean_title,
        scenario_tags=parsed.scenario_tags,
        manual=parsed.manual,
    )
    session.add(case)
    await session.flush()

    body = QuickCaseBody(
        case_id=case.id,
        goal=parsed.raw_title,
        preconditions=parsed.preconditions,
        steps_text=parsed.steps_text,
        expected_result=parsed.expected_result,
    )
    session.add(body)
    session.add_all(
        QuickCaseStep(case_id=case.id, step_order=index, step_text=step)
        for index, step in enumerate(parsed.step_items, start=1)
    )
    session.add(
        QuickCaseWorkItem(
            case_id=case.id,
            execution_status="not_run",
            lifecycle_state="待验证",
            case_type=tag["case_type"],
            execution_target=tag["execution_target"],
            tag_source=tag["tag_source"],
            tag_reason=tag["tag_reason"],
            tag_confidence=tag["tag_confidence"],
            run_enabled=True,
        )
    )
    return int(case.id)


def _tagging_input(parsed: QuickParsedCase) -> dict[str, Any]:
    return {
        "module_name": None,
        "path_nodes": parsed.path_nodes,
        "product_feature": None,
        "test_feature": None,
        "raw_title": parsed.raw_title,
        "clean_title": parsed.clean_title,
        "scenario_tags": parsed.scenario_tags,
        "manual": parsed.manual,
        "preconditions": parsed.preconditions,
        "steps_text": parsed.steps_text,
        "expected_result": parsed.expected_result,
    }


async def _session_summary(session: AsyncSession, session_id: str) -> QuickSessionSummaryOut:
    quick_session = await session.get(QuickSession, session_id)
    if quick_session is None:
        raise ValueError("quick session 不存在")
    count = int((
        await session.execute(select(func.count(QuickCase.id)).where(QuickCase.session_id == session_id))
    ).scalar_one() or 0)
    return QuickSessionSummaryOut(
        session_id=quick_session.session_id,
        source_name=quick_session.source_name,
        suite_title=quick_session.suite_title,
        case_count=count,
        function_files=_function_files_out(quick_session.function_files or []),
        feishu_requirement_url=quick_session.feishu_requirement_url,
        feishu_bug_url=quick_session.feishu_bug_url,
        current_user_id=quick_session.current_user_id,
        created_at=quick_session.created_at,
        updated_at=quick_session.updated_at,
    )


async def _new_session_id(session: AsyncSession) -> str:
    for _ in range(10):
        session_id = secrets.token_urlsafe(18)
        if await session.get(QuickSession, session_id) is None:
            return session_id
    raise ValueError("生成 quick session ID 失败，请重试")


def _function_files(files: list[QuickFunctionFileIn]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in files:
        filename = item.filename.strip()
        content = item.content
        if not filename or not content.strip():
            continue
        normalized.append({
            "filename": filename,
            "content": content,
            "char_count": len(content),
        })
    return normalized


def _function_files_out(files: list[dict[str, Any]]) -> list[QuickFunctionFileOut]:
    return [
        QuickFunctionFileOut(
            filename=str(item.get("filename") or ""),
            content=str(item.get("content") or ""),
            char_count=int(item.get("char_count") or len(str(item.get("content") or ""))),
        )
        for item in files
        if isinstance(item, dict) and item.get("filename")
    ]


def _case_to_out(
    case: QuickCase,
    body: QuickCaseBody,
    work_item: QuickCaseWorkItem,
    quick_session: QuickSession,
) -> QuickCaseItemOut:
    path_nodes = _case_path_nodes(case)
    return QuickCaseItemOut(
        id=case.id,
        ordinal=case.ordinal,
        suite_title=quick_session.suite_title,
        source_name=quick_session.source_name,
        asset_status=case.status,
        raw_title=case.raw_title,
        clean_title=case.clean_title,
        path=" / ".join(_path_node_texts(path_nodes)),
        path_nodes=path_nodes,
        scenario_tags=list(case.scenario_tags or []),
        manual=case.manual,
        execution_status=work_item.execution_status,
        coverage={str(k): str(v) for k, v in (work_item.coverage or {}).items()},
        lifecycle_state=work_item.lifecycle_state,
        attention_reason=_visible_attention_reason(work_item.attention_reason),
        case_type=work_item.case_type,
        execution_target=work_item.execution_target,
        tag_source=work_item.tag_source,
        tag_reason=work_item.tag_reason,
        tag_confidence=work_item.tag_confidence,
        run_enabled=work_item.run_enabled,
        report_url=work_item.report_url,
        failure_type=_failure_type_label(work_item.failure_type),
        failure_summary=work_item.failure_summary,
        bug_url=work_item.bug_url,
        bugs=[
            {"url": str(b.get("url") or ""), "id": str(b.get("id") or "")}
            for b in (work_item.bugs or [])
            if isinstance(b, dict) and b.get("url")
        ],
        external_submission_id=work_item.external_submission_id,
        execution_started_at=work_item.execution_started_at,
        execution_finished_at=work_item.execution_finished_at,
        preconditions=body.preconditions,
        steps_text=body.steps_text,
        expected_result=body.expected_result,
        core_nodes=case.core_nodes or {},
    )


async def _apply_ready_flags(session: AsyncSession, outs: list[QuickCaseItemOut]) -> None:
    case_ids = [out.id for out in outs]
    if not case_ids:
        return
    diag_ids = set(
        (await session.execute(
            select(QuickRepairDraft.case_id).where(QuickRepairDraft.case_id.in_(case_ids))
        )).scalars().all()
    )
    bug_ids = set(
        (await session.execute(
            select(QuickBugDraft.case_id).where(QuickBugDraft.case_id.in_(case_ids))
        )).scalars().all()
    )
    for out in outs:
        out.diagnosis_ready = out.id in diag_ids
        out.bug_draft_ready = out.id in bug_ids


def _kick_pending_diagnoses(outs: list[QuickCaseItemOut]) -> None:
    try:
        import asyncio

        from app.services.quick_repair import auto_diagnose_case
    except Exception:
        return
    for out in outs:
        if out.execution_status == "failed" and out.report_url and not out.diagnosis_ready:
            task = asyncio.create_task(auto_diagnose_case(out.id))
            task.add_done_callback(lambda t: t.exception())


async def _replace_steps(session: AsyncSession, case_id: int, steps_text: str) -> None:
    await session.execute(delete(QuickCaseStep).where(QuickCaseStep.case_id == case_id))
    steps = [line.strip() for line in steps_text.splitlines() if line.strip()]
    if not steps and steps_text.strip():
        steps = [steps_text.strip()]
    session.add_all(
        QuickCaseStep(case_id=case_id, step_order=index, step_text=step)
        for index, step in enumerate(steps, start=1)
    )


def _set_core_node(case: QuickCase, role: str, display_text: str) -> None:
    display_text = collapse_inline_text(display_text)
    nodes = dict(case.core_nodes or {})
    node = dict(nodes.get(role) or {})
    node["displayText"] = display_text
    if node.get("trimmed"):
        label = str(node.get("label") or role)
        separator = str(node.get("separator") or "：")
        node["rawText"] = f"{label}{separator}{display_text}"
    else:
        node["rawText"] = display_text
    nodes[role] = node
    case.core_nodes = nodes


def _normalize_path_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, node in enumerate(nodes, start=2):
        if not isinstance(node, dict):
            continue
        text = str(node.get("displayText") or node.get("rawText") or "").strip()
        if not text:
            continue
        normalized.append({
            "level": int(node.get("level") or index),
            "label": str(node.get("label") or f"层级{index - 1}"),
            "rawText": str(node.get("rawText") or text),
            "displayText": text,
        })
    return normalized


def _case_path_nodes(case: QuickCase) -> list[dict[str, Any]]:
    nodes = getattr(case, "path_nodes", None)
    return [dict(node) for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _path_node_texts(nodes: list[dict[str, Any]]) -> list[str]:
    return [
        str(
            node.get("displayText")
            or node.get("display_text")
            or node.get("rawText")
            or node.get("raw_text")
            or ""
        )
        for node in nodes
        if isinstance(node, dict)
        and (
            node.get("displayText")
            or node.get("display_text")
            or node.get("rawText")
            or node.get("raw_text")
        )
    ]


def _failure_type_label(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if text in VISIBLE_FAILURE_TYPE_LABELS:
        return text
    return FAILURE_TYPE_LABELS.get(text)


def _visible_attention_reason(value: str | None) -> str | None:
    return value if value == "变更待确认" else None


def _export_filename(source_name: str) -> str:
    source = (source_name or "quick-cases.md").strip()
    if source.lower().endswith(".md"):
        return source
    return f"{source}.md"
