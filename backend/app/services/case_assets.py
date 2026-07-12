from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_assets import (
    CaseAsset,
    CaseBody,
    CaseRawNode,
    CaseStep,
    CaseWorkItem,
    ImportBatch,
)
from app.models.requirements import RequirementAssignee
from app.schemas.workbench import (
    CaseAssetCreateIn,
    CaseAssetMutationOut,
    CaseAssetUpdateIn,
    CaseSuiteMutationOut,
)
from app.services.execution_reset import (
    clear_standard_case_execution_artifacts,
    reset_standard_work_item_execution,
)
from app.services.markdown_text import collapse_inline_text, extract_title_tags


async def create_case_asset(
    session: AsyncSession,
    payload: CaseAssetCreateIn,
) -> CaseAssetMutationOut | None:
    batch = await session.get(ImportBatch, payload.batch_id)
    if batch is None or int(batch.requirement_item_id) != int(payload.requirement_item_id):
        return None

    requested_path_nodes = _normalize_input_path_nodes(payload.path_nodes)
    if not requested_path_nodes:
        raise ValueError("新增 Case 必须选择已有层级")

    rows = await session.execute(
        select(CaseAsset, CaseRawNode)
        .outerjoin(CaseRawNode, CaseRawNode.case_id == CaseAsset.id)
        .where(CaseAsset.batch_id == batch.id)
        .order_by(CaseAsset.ordinal, CaseAsset.id)
    )
    existing_rows = rows.all()
    if not existing_rows:
        raise ValueError("当前测试集没有可选择的层级")

    path_key = _path_key(requested_path_nodes)
    template_case: CaseAsset | None = None
    template_payload: dict[str, Any] = {}
    max_ordinal = 0
    for case_asset, raw_node in existing_rows:
        max_ordinal = max(max_ordinal, int(case_asset.ordinal or 0))
        if template_case is None and _path_key(_case_path_nodes(case_asset)) == path_key:
            template_case = case_asset
            if raw_node and isinstance(raw_node.raw_payload, dict):
                template_payload = raw_node.raw_payload

    if template_case is None:
        raise ValueError("新增 Case 只能选择当前测试集里已存在的完整层级")

    path_nodes = _case_path_nodes(template_case)
    raw_title = collapse_inline_text(payload.raw_title)
    title_tags = extract_title_tags(raw_title)
    if not raw_title:
        raise ValueError("测试标题不能为空")
    preconditions = collapse_inline_text(payload.preconditions)
    steps_text = collapse_inline_text(payload.steps_text)
    expected_result = collapse_inline_text(payload.expected_result)
    path_texts = _path_node_texts(path_nodes)

    case_asset = CaseAsset(
        batch_id=batch.id,
        ordinal=max_ordinal + 1,
        suite_title=batch.suite_title,
        path_nodes=path_nodes,
        module_name=path_texts[0] if len(path_texts) > 0 else None,
        product_feature=path_texts[1] if len(path_texts) > 1 else None,
        test_feature=path_texts[2] if len(path_texts) > 2 else None,
        raw_title=raw_title,
        clean_title=raw_title,
        scenario_tags=[tag for tag in title_tags if tag != "人工"],
        manual="人工" in title_tags,
        source_requirement_item_id=payload.requirement_item_id,
    )
    session.add(case_asset)
    await session.flush()

    body = CaseBody(
        case_id=case_asset.id,
        goal=raw_title,
        preconditions=preconditions,
        steps_text=steps_text,
        expected_result=expected_result,
    )
    session.add(body)
    session.add_all(
        CaseStep(case_id=case_asset.id, step_order=index, step_text=step)
        for index, step in enumerate(_split_steps(steps_text), start=1)
    )
    session.add(
        CaseRawNode(
            case_id=case_asset.id,
            raw_payload={
                "suite_title": batch.suite_title,
                "module_name": case_asset.module_name,
                "path_nodes": path_nodes,
                "core_nodes": _updated_core_nodes(template_payload, case_asset, body),
                "core_labels": _core_labels_from_payload(template_payload),
                "product_feature": case_asset.product_feature,
                "test_feature": case_asset.test_feature,
                "raw_title": raw_title,
                "preconditions": preconditions,
                "steps_text": steps_text,
                "expected_result": expected_result,
            },
        )
    )
    session.add(
        CaseWorkItem(
            case_id=case_asset.id,
            assigned_user_id=await _first_assignee_id(session, payload.requirement_item_id),
            execution_status="not_run",
            lifecycle_state="待人工干预",
            attention_reason="变更待确认",
            case_type="changed",
            execution_target="manual",
            tag_source="manual_create",
            tag_reason="手动新增 Case，未触发执行端识别。",
            tag_confidence=0,
            run_enabled=True,
        )
    )
    batch.case_count = len(existing_rows) + 1
    await session.commit()
    return CaseAssetMutationOut(case_id=case_asset.id, message="Case 已新增")


async def update_case_asset(
    session: AsyncSession,
    case_id: int,
    payload: CaseAssetUpdateIn,
) -> CaseAssetMutationOut | None:
    case_asset = await session.get(CaseAsset, case_id)
    body = await session.get(CaseBody, case_id)
    work_item = await session.get(CaseWorkItem, case_id)
    if case_asset is None or body is None:
        return None

    if payload.module_name is not None:
        case_asset.module_name = payload.module_name.strip() or None
    if payload.product_feature is not None:
        case_asset.product_feature = payload.product_feature.strip() or None
    if payload.test_feature is not None:
        case_asset.test_feature = payload.test_feature.strip() or None
    _sync_existing_path_nodes(case_asset)
    if payload.clean_title is not None and payload.raw_title is None:
        raise ValueError("更新测试标题必须提交完整标题 raw_title")
    if payload.raw_title is not None:
        raw_title = collapse_inline_text(payload.raw_title)
        title_tags = extract_title_tags(raw_title)
        if raw_title:
            case_asset.raw_title = raw_title
            case_asset.clean_title = raw_title
            case_asset.scenario_tags = [tag for tag in title_tags if tag != "人工"]
            case_asset.manual = "人工" in title_tags
            body.goal = raw_title
    if payload.preconditions is not None:
        body.preconditions = collapse_inline_text(payload.preconditions)
    if payload.steps_text is not None:
        body.steps_text = collapse_inline_text(payload.steps_text)
        await _replace_steps(session, case_id, body.steps_text)
    if payload.expected_result is not None:
        body.expected_result = collapse_inline_text(payload.expected_result)

    case_asset.updated_at = func.now()
    await _replace_raw_node(session, case_asset, body)
    if work_item:
        reset_standard_work_item_execution(work_item, status="not_run")
    await clear_standard_case_execution_artifacts(session, [case_id])
    await session.commit()
    return CaseAssetMutationOut(case_id=case_id, message="Case 已更新")


async def delete_case_asset(session: AsyncSession, case_id: int) -> CaseAssetMutationOut | None:
    case_asset = await session.get(CaseAsset, case_id)
    if case_asset is None:
        return None
    batch_id = case_asset.batch_id
    await session.delete(case_asset)
    await session.flush()

    count_result = await session.execute(
        select(func.count(CaseAsset.id)).where(CaseAsset.batch_id == batch_id)
    )
    remaining = int(count_result.scalar_one() or 0)
    deleted_batch_id: int | None = None
    if remaining == 0:
        batch = await session.get(ImportBatch, batch_id)
        if batch is not None:
            await session.delete(batch)
            deleted_batch_id = batch_id
    else:
        batch = await session.get(ImportBatch, batch_id)
        if batch is not None:
            batch.case_count = remaining
    await session.commit()
    return CaseAssetMutationOut(case_id=case_id, message="Case 已删除", deleted_batch_id=deleted_batch_id)


async def delete_case_suite(
    session: AsyncSession,
    requirement_item_id: int,
    batch_id: int,
) -> CaseSuiteMutationOut | None:
    statement = select(ImportBatch).where(
        ImportBatch.requirement_item_id == requirement_item_id,
        ImportBatch.id == batch_id,
    )
    batch = await session.scalar(statement.order_by(ImportBatch.id).limit(1))
    if batch is None:
        return None

    case_count_result = await session.execute(
        select(func.count(CaseAsset.id)).where(CaseAsset.batch_id == batch.id)
    )
    running_count_result = await session.execute(
        select(func.count(CaseWorkItem.case_id))
        .join(CaseAsset, CaseAsset.id == CaseWorkItem.case_id)
        .where(
            CaseAsset.batch_id == batch.id,
            CaseWorkItem.execution_status == "running",
        )
    )
    deleted_case_count = int(case_count_result.scalar_one() or 0)
    deleted_running_count = int(running_count_result.scalar_one() or 0)
    deleted_batch_id = int(batch.id)
    deleted_suite_title = batch.suite_title

    await session.delete(batch)
    await session.commit()
    return CaseSuiteMutationOut(
        requirement_item_id=requirement_item_id,
        batch_id=deleted_batch_id,
        suite_title=deleted_suite_title,
        deleted_case_count=deleted_case_count,
        deleted_running_count=deleted_running_count,
        deleted_batch_id=deleted_batch_id,
        message="测试集已删除",
    )


async def _replace_steps(session: AsyncSession, case_id: int, steps_text: str) -> None:
    await session.execute(delete(CaseStep).where(CaseStep.case_id == case_id))
    steps = _split_steps(steps_text)
    session.add_all(
        CaseStep(case_id=case_id, step_order=index, step_text=step)
        for index, step in enumerate(steps, start=1)
    )


async def _replace_raw_node(session: AsyncSession, case_asset: CaseAsset, body: CaseBody) -> None:
    existing = await session.scalar(select(CaseRawNode).where(CaseRawNode.case_id == case_asset.id).limit(1))
    previous_payload = existing.raw_payload if existing and isinstance(existing.raw_payload, dict) else {}
    await session.execute(delete(CaseRawNode).where(CaseRawNode.case_id == case_asset.id))
    session.add(
        CaseRawNode(
            case_id=case_asset.id,
            raw_payload={
                "suite_title": case_asset.suite_title,
                "module_name": case_asset.module_name,
                "path_nodes": _case_path_nodes(case_asset),
                "core_nodes": _updated_core_nodes(previous_payload, case_asset, body),
                "core_labels": previous_payload.get("core_labels") or _default_core_labels(),
                "product_feature": case_asset.product_feature,
                "test_feature": case_asset.test_feature,
                "raw_title": case_asset.raw_title,
                "preconditions": body.preconditions,
                "steps_text": body.steps_text,
                "expected_result": body.expected_result,
            },
        )
    )


def _updated_core_nodes(
    previous_payload: dict[str, Any],
    case_asset: CaseAsset,
    body: CaseBody,
) -> dict[str, dict[str, Any]]:
    previous = previous_payload.get("core_nodes") if isinstance(previous_payload, dict) else {}
    if not isinstance(previous, dict):
        previous = {}
    labels = previous_payload.get("core_labels") if isinstance(previous_payload, dict) else {}
    if not isinstance(labels, dict):
        labels = {}
    values = {
        "case_title": case_asset.raw_title,
        "preconditions": body.preconditions,
        "steps": body.steps_text,
        "expected": body.expected_result,
    }
    result: dict[str, dict[str, Any]] = {}
    for index, (role, value) in enumerate(values.items(), start=1):
        old_node = previous.get(role) if isinstance(previous.get(role), dict) else {}
        label = str(old_node.get("label") or labels.get(role) or _default_core_labels()[role])
        trimmed = bool(old_node.get("trimmed", True))
        separator = old_node.get("separator")
        if separator is None and trimmed:
            separator = "："
        display_text = collapse_inline_text(value)
        raw_text = f"{label}{separator}{display_text}" if trimmed and separator else display_text
        result[role] = {
            "level": old_node.get("level") or index,
            "label": label,
            "rawText": raw_text,
            "displayText": display_text,
            "trimmed": trimmed,
            "separator": separator,
        }
    return result


def _default_core_labels() -> dict[str, str]:
    return {
        "case_title": "测试标题",
        "preconditions": "前置条件",
        "steps": "操作步骤",
        "expected": "预期结果",
    }


def _split_steps(steps_text: str) -> list[str]:
    steps = [line.strip() for line in steps_text.splitlines() if line.strip()]
    return steps or ([steps_text.strip()] if steps_text.strip() else [])


def _sync_existing_path_nodes(case_asset: CaseAsset) -> None:
    nodes = _case_path_nodes(case_asset)
    path_values = [case_asset.module_name, case_asset.product_feature, case_asset.test_feature]
    for index, value in enumerate(path_values):
        if index >= len(nodes) or value is None:
            continue
        nodes[index]["rawText"] = value
        nodes[index]["displayText"] = value
    case_asset.path_nodes = nodes


def _case_path_nodes(case_asset: CaseAsset) -> list[dict[str, Any]]:
    nodes = getattr(case_asset, "path_nodes", None)
    return [dict(node) for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _core_labels_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    labels = payload.get("core_labels") if isinstance(payload, dict) else {}
    if isinstance(labels, dict) and labels:
        return {str(key): str(value) for key, value in labels.items()}
    return _default_core_labels()


def _normalize_input_path_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        label = collapse_inline_text(node.get("label") or "层级")
        raw_text = collapse_inline_text(
            node.get("rawText")
            or node.get("raw_text")
            or node.get("displayText")
            or node.get("display_text")
            or ""
        )
        display_text = collapse_inline_text(
            node.get("displayText")
            or node.get("display_text")
            or raw_text
        )
        if not display_text:
            continue
        normalized_node: dict[str, Any] = {
            "label": label,
            "rawText": raw_text or display_text,
            "displayText": display_text,
        }
        level = node.get("level")
        if level is not None:
            try:
                normalized_node["level"] = int(level)
            except (TypeError, ValueError):
                pass
        normalized.append(normalized_node)
    return normalized


def _path_key(nodes: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            str(node.get("label") or ""),
            str(
                node.get("displayText")
                or node.get("display_text")
                or node.get("rawText")
                or node.get("raw_text")
                or ""
            ),
        )
        for node in nodes
        if isinstance(node, dict)
    )


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
        and (node.get("displayText") or node.get("display_text") or node.get("rawText") or node.get("raw_text"))
    ]


async def _first_assignee_id(session: AsyncSession, requirement_item_id: int) -> int | None:
    result = await session.execute(
        select(RequirementAssignee.user_id)
        .where(RequirementAssignee.requirement_item_id == requirement_item_id)
        .order_by(RequirementAssignee.user_id)
        .limit(1)
    )
    return result.scalar_one_or_none()
