from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.case_assets import CaseAsset, CaseBody, CaseRawNode, CaseStep, CaseWorkItem, ImportBatch
from app.models.requirements import RequirementAssignee, RequirementItem
from app.schemas.importing import ImportMarkdownIn, ImportMarkdownOut, ImportReviewCommitIn
from app.services import case_matching, case_tagging, import_jobs
from app.services.execution_reset import (
    clear_standard_case_execution_artifacts,
    reset_standard_work_item_execution,
)
from app.services.markdown_parser import ParsedCase, ParsedMarkdown, parse_markdown


async def import_markdown(session: AsyncSession, payload: ImportMarkdownIn) -> ImportMarkdownOut:
    parsed = parse_markdown(payload.content, _source_name(payload.filename))
    await _ensure_requirement_exists(session, payload.requirement_item_id)
    existing_batch = await get_import_batch_by_source(
        session,
        payload.requirement_item_id,
        parsed.source_name,
    )

    if existing_batch:
        existing_cases = await list_cases_for_batch(session, existing_batch.id)
        # build_import_review 内部会调模型（同步阻塞网络），放到线程里跑，避免卡住事件循环导致健康检查饿死。
        review = await asyncio.to_thread(case_matching.build_import_review, parsed, existing_cases)
        if review["review_count"] == 0 and review["delete_count"] == 0:
            title_changed = await update_batch_suite_title(session, existing_batch, parsed.suite_title)
            return ImportMarkdownOut(
                mode="no_changes",
                message=(
                    "本次导入与当前文件测试集 case 内容完全一致，测试集标题已同步。"
                    if title_changed
                    else "本次导入与当前文件测试集完全一致，无需入库。"
                ),
                suite_title=parsed.suite_title,
                case_count=len(parsed.cases),
                warnings=parsed.warnings,
            )
        return ImportMarkdownOut(
            mode="collision_review",
            message="检测到同文件二次导入，请处理全部新增、替代、删除差异后再落库。",
            existing_batch=_batch_to_dict(existing_batch),
            review=review,
            warnings=parsed.warnings,
        )

    has_existing_batches = await requirement_has_batches(session, payload.requirement_item_id)
    if has_existing_batches and not payload.confirm_independent:
        return ImportMarkdownOut(
            mode="independent_confirm_required",
            message=(
                "当前二级需求下已有其他测试集。本次文件名不同，"
                "将作为独立测试集新增，不做增删改查碰撞。"
            ),
            suite_title=parsed.suite_title,
            case_count=len(parsed.cases),
            warnings=parsed.warnings,
        )

    batch = await replace_import(session, parsed, payload.requirement_item_id, mark_changed=False)
    return ImportMarkdownOut(
        mode="imported",
        batch=_batch_to_dict(batch),
        suite_title=parsed.suite_title,
        case_count=len(parsed.cases),
        warnings=parsed.warnings,
    )


async def start_import_markdown(session: AsyncSession, payload: ImportMarkdownIn) -> ImportMarkdownOut:
    """路由入口：同文件二次导入（碰撞）走后台任务 + 轮询，其余快路径同步返回。

    碰撞要调模型 1-3 分钟，如果挂在这个请求里会被网关 ~60s 掐断（502）。所以只把碰撞计算
    丢到后台任务，立刻返回 collision_pending + task_id，前端拿 task_id 轮询结果。
    """
    parsed = parse_markdown(payload.content, _source_name(payload.filename))
    await _ensure_requirement_exists(session, payload.requirement_item_id)
    existing_batch = await get_import_batch_by_source(
        session,
        payload.requirement_item_id,
        parsed.source_name,
    )
    if existing_batch is None:
        # 非碰撞路径（独立测试集确认 / 直接入库）不调碰撞模型，同步返回即可。
        return await import_markdown(session, payload)

    job_id = import_jobs.start(lambda: _run_import_markdown_job(payload))
    return ImportMarkdownOut(
        mode="collision_pending",
        task_id=job_id,
        message="正在进行二次导入碰撞判断，请稍候…",
        suite_title=parsed.suite_title,
        case_count=len(parsed.cases),
        warnings=parsed.warnings,
    )


async def start_commit_import_review(
    session: AsyncSession,
    payload: ImportReviewCommitIn,
) -> dict[str, Any]:
    """路由入口：落库前会重算碰撞（同样要调模型），因此也走后台任务 + 轮询。"""
    parsed = parse_markdown(payload.content, _source_name(payload.filename))
    existing_batch = await get_import_batch_by_source(
        session,
        payload.requirement_item_id,
        parsed.source_name,
    )
    if existing_batch is None:
        raise ValueError("同文件测试集不存在，无法确认碰撞结果")

    job_id = import_jobs.start(lambda: _run_commit_import_review_job(payload))
    return {
        "mode": "commit_pending",
        "task_id": job_id,
        "message": "正在写入导入结果，请稍候…",
    }


async def _run_import_markdown_job(payload: ImportMarkdownIn) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        result = await import_markdown(session, payload)
        return result.model_dump()


async def _run_commit_import_review_job(payload: ImportReviewCommitIn) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        return await commit_import_review(session, payload)


async def commit_import_review(session: AsyncSession, payload: ImportReviewCommitIn) -> dict[str, Any]:
    parsed = parse_markdown(payload.content, _source_name(payload.filename))
    existing_batch = await get_import_batch_by_source(
        session,
        payload.requirement_item_id,
        parsed.source_name,
    )
    if existing_batch is None:
        raise ValueError("同文件测试集不存在，无法确认碰撞结果")

    existing_cases = await list_cases_for_batch(session, existing_batch.id)
    review = await asyncio.to_thread(case_matching.build_import_review, parsed, existing_cases)
    decisions = [decision.model_dump(exclude_none=True) for decision in payload.decisions]
    final_decisions = validate_import_review_decisions(review, decisions)
    batch = await apply_import_review(session, parsed, payload.requirement_item_id, final_decisions)
    return {
        "mode": "review_committed",
        "message": "打磨碰撞处理已落库。",
        "batch": _batch_to_dict(batch),
        "review": review,
        "warnings": parsed.warnings,
    }


def validate_import_review_decisions(
    review: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions_by_key = {
        str(item["incoming_key"]): item
        for item in decisions
        if item.get("incoming_key")
    }
    delete_decisions_by_id = {
        int(item["old_case_id"]): item
        for item in decisions
        if item.get("old_case_id") and item.get("action") in {"delete", "keep"}
    }

    required_keys = {item["incoming_key"] for item in review["review_items"]}
    unknown_keys = sorted(set(decisions_by_key) - required_keys)
    if unknown_keys:
        raise ValueError(f"包含不属于本次碰撞的新 case 决策：{unknown_keys}")

    missing = sorted(required_keys - set(decisions_by_key))
    if missing:
        raise ValueError(f"仍有未处理差异，不能落库：{missing}")

    review_item_by_key = {str(item["incoming_key"]): item for item in review["review_items"]}
    for incoming_key, decision in decisions_by_key.items():
        action = str(decision.get("action") or "")
        if action not in {"add", "replace", "skip"}:
            raise ValueError(f"新导入 case 不能使用处理动作：{action}")
        if action == "replace":
            old_case_id = int(decision.get("old_case_id") or 0)
            primary_old_case_id = int(review_item_by_key[incoming_key].get("primary_old_case_id") or 0)
            if not old_case_id:
                raise ValueError("替代旧 case 必须指定 old_case_id")
            if old_case_id != primary_old_case_id:
                raise ValueError("替代旧 case 必须使用当前新 case 锁定的 1:1 主候选")

    replacement_old_ids = {
        int(item.get("old_case_id") or 0)
        for item in decisions_by_key.values()
        if item.get("action") == "replace" and item.get("old_case_id")
    }
    allowed_delete_ids = {int(item["old_case_id"]) for item in review["delete_items"]}
    unknown_delete_ids = sorted(set(delete_decisions_by_id) - allowed_delete_ids)
    if unknown_delete_ids:
        raise ValueError(f"包含不属于本次碰撞的旧 case 删除/保留决策：{unknown_delete_ids}")

    required_delete_ids = {
        int(item["old_case_id"])
        for item in review["delete_items"]
        if int(item["old_case_id"]) not in replacement_old_ids
    }
    missing_delete_ids = sorted(required_delete_ids - set(delete_decisions_by_id))
    if missing_delete_ids:
        raise ValueError(f"仍有未处理删除候选，不能落库：{missing_delete_ids}")

    final_decisions = [decisions_by_key[key] for key in sorted(required_keys)]
    final_decisions.extend(delete_decisions_by_id[case_id] for case_id in sorted(required_delete_ids))
    return final_decisions


async def get_import_batch_by_source(
    session: AsyncSession,
    requirement_item_id: int,
    source_name: str,
) -> ImportBatch | None:
    result = await session.execute(
        select(ImportBatch).where(
            ImportBatch.requirement_item_id == requirement_item_id,
            ImportBatch.source_name == source_name,
        )
    )
    return result.scalar_one_or_none()


async def update_batch_suite_title(
    session: AsyncSession,
    batch: ImportBatch,
    suite_title: str,
) -> bool:
    if batch.suite_title == suite_title:
        return False
    batch.suite_title = suite_title
    await session.execute(
        update(CaseAsset)
        .where(CaseAsset.batch_id == batch.id)
        .values(suite_title=suite_title, updated_at=func.now())
    )
    await session.commit()
    await session.refresh(batch)
    return True


async def requirement_has_batches(session: AsyncSession, requirement_item_id: int) -> bool:
    result = await session.execute(
        select(ImportBatch.id).where(ImportBatch.requirement_item_id == requirement_item_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def replace_import(
    session: AsyncSession,
    parsed: ParsedMarkdown,
    requirement_item_id: int,
    *,
    mark_changed: bool,
) -> ImportBatch:
    await session.execute(
        delete(ImportBatch).where(
            ImportBatch.requirement_item_id == requirement_item_id,
            ImportBatch.source_name == parsed.source_name,
        )
    )
    batch = ImportBatch(
        suite_title=parsed.suite_title,
        source_name=parsed.source_name,
        requirement_item_id=requirement_item_id,
        imported_at=datetime.now(UTC),
        case_count=len(parsed.cases),
    )
    session.add(batch)
    await session.flush()

    changed_case_ids: list[int] = []
    imported_case_ids: list[int] = []
    for case in parsed.cases:
        case_id = await insert_parsed_case(session, batch.id, case, requirement_item_id, case.ordinal)
        imported_case_ids.append(case_id)
        if mark_changed:
            changed_case_ids.append(case_id)

    await ensure_case_work_items(
        session,
        case_ids=imported_case_ids,
        changed_case_ids=changed_case_ids if mark_changed else None,
    )
    await session.commit()
    await session.refresh(batch)
    return batch


async def apply_import_review(
    session: AsyncSession,
    parsed: ParsedMarkdown,
    requirement_item_id: int,
    decisions: list[dict[str, Any]],
) -> ImportBatch:
    parsed_by_key = {}
    for case in parsed.cases:
        case_dict = case_matching.parsed_case_to_dict(case)
        digest = case_matching.case_digest(case_dict)
        parsed_by_key[case_matching.incoming_case_key(case, digest)] = case
    result = await session.execute(
        select(ImportBatch).where(
            ImportBatch.requirement_item_id == requirement_item_id,
            ImportBatch.source_name == parsed.source_name,
        )
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        raise ValueError("同文件测试集不存在，无法应用打磨碰撞结果")

    max_ordinal_result = await session.execute(
        select(func.coalesce(func.max(CaseAsset.ordinal), 0)).where(CaseAsset.batch_id == batch.id)
    )
    next_ordinal = int(max_ordinal_result.scalar_one() or 0) + 1
    changed_case_ids: set[int] = set()
    replaced_old_ids: set[int] = set()
    applied = {"add": 0, "replace": 0, "skip": 0, "delete": 0, "keep": 0}

    for decision in decisions:
        action = str(decision.get("action") or "")
        incoming_key = str(decision.get("incoming_key") or "")
        parsed_case = parsed_by_key.get(incoming_key)
        if action in {"add", "replace", "skip"} and parsed_case is None:
            raise ValueError(f"未知导入 case：{incoming_key}")

        if action == "add":
            new_case_id = await insert_parsed_case(
                session,
                batch.id,
                parsed_case,
                requirement_item_id,
                next_ordinal,
            )
            changed_case_ids.add(new_case_id)
            next_ordinal += 1
            applied["add"] += 1
        elif action == "replace":
            old_case_id = int(decision.get("old_case_id") or 0)
            if not old_case_id:
                raise ValueError("替代旧 case 必须指定 old_case_id")
            if old_case_id in replaced_old_ids:
                raise ValueError("同一个旧 case 同一轮只能被替代一次")
            old_case = await session.get(CaseAsset, old_case_id)
            if old_case is None or old_case.batch_id != batch.id:
                raise ValueError("被替代的旧 case 不属于当前测试集")
            old_ordinal = int(old_case.ordinal or next_ordinal)
            await session.delete(old_case)
            await session.flush()
            new_case_id = await insert_parsed_case(
                session,
                batch.id,
                parsed_case,
                requirement_item_id,
                old_ordinal,
            )
            changed_case_ids.add(new_case_id)
            replaced_old_ids.add(old_case_id)
            applied["replace"] += 1
        elif action == "skip":
            applied["skip"] += 1
        elif action == "delete":
            old_case_id = int(decision.get("old_case_id") or 0)
            if old_case_id in replaced_old_ids:
                continue
            old_case = await session.get(CaseAsset, old_case_id)
            if old_case is None or old_case.batch_id != batch.id:
                raise ValueError("被删除的旧 case 不属于当前测试集")
            await session.delete(old_case)
            applied["delete"] += 1
        elif action == "keep":
            old_case_id = int(decision.get("old_case_id") or 0)
            if old_case_id in replaced_old_ids:
                continue
            old_case = await session.get(CaseAsset, old_case_id)
            if old_case is None or old_case.batch_id != batch.id:
                raise ValueError("被保留的旧 case 不属于当前测试集")
            applied["keep"] += 1
        else:
            raise ValueError(f"未知处理动作：{action}")

    await reorder_batch_cases_from_parsed(session, batch.id, parsed)

    count_result = await session.execute(
        select(func.count(CaseAsset.id)).where(CaseAsset.batch_id == batch.id)
    )
    batch.source_name = parsed.source_name
    batch.suite_title = parsed.suite_title
    batch.imported_at = datetime.now(UTC)
    batch.case_count = int(count_result.scalar_one() or 0)
    batch.raw_metadata = {"applied": applied}
    await session.execute(
        update(CaseAsset)
        .where(CaseAsset.batch_id == batch.id)
        .values(suite_title=parsed.suite_title, updated_at=func.now())
    )

    await ensure_case_work_items(
        session,
        case_ids=list(changed_case_ids),
        changed_case_ids=list(changed_case_ids),
    )
    await session.commit()
    await session.refresh(batch)
    return batch


async def reorder_batch_cases_from_parsed(
    session: AsyncSession,
    batch_id: int,
    parsed: ParsedMarkdown,
) -> None:
    rows = await session.execute(
        select(CaseAsset, CaseBody)
        .join(CaseBody, CaseBody.case_id == CaseAsset.id)
        .where(CaseAsset.batch_id == batch_id)
        .order_by(CaseAsset.ordinal, CaseAsset.id)
    )
    current_by_digest: dict[str, list[CaseAsset]] = {}
    current_cases: list[CaseAsset] = []
    for case_asset, body in rows.all():
        current_cases.append(case_asset)
        digest = case_matching.case_digest(_case_dict(case_asset, body))
        current_by_digest.setdefault(digest, []).append(case_asset)

    ordered_case_ids: set[int] = set()
    next_ordinal = 1
    for parsed_case in parsed.cases:
        digest = case_matching.case_digest(case_matching.parsed_case_to_dict(parsed_case))
        matches = current_by_digest.get(digest) or []
        if not matches:
            continue
        case_asset = matches.pop(0)
        case_asset.ordinal = next_ordinal
        ordered_case_ids.add(int(case_asset.id))
        next_ordinal += 1

    for case_asset in current_cases:
        if int(case_asset.id) in ordered_case_ids:
            continue
        case_asset.ordinal = next_ordinal
        next_ordinal += 1


async def insert_parsed_case(
    session: AsyncSession,
    batch_id: int,
    case: ParsedCase,
    requirement_item_id: int,
    ordinal: int,
) -> int:
    case_asset = CaseAsset(
        batch_id=batch_id,
        ordinal=ordinal,
        suite_title=case.suite_title,
        path_nodes=case.path_nodes,
        module_name=case.module_name,
        product_feature=case.product_feature,
        test_feature=case.test_feature,
        raw_title=case.raw_title,
        clean_title=case.clean_title,
        scenario_tags=case.scenario_tags,
        manual=case.manual,
        source_requirement_item_id=requirement_item_id,
    )
    session.add(case_asset)
    await session.flush()

    session.add(
        CaseBody(
            case_id=case_asset.id,
            goal=case.raw_title,
            preconditions=case.preconditions,
            steps_text=case.steps_text,
            expected_result=case.expected_result,
        )
    )
    session.add_all(
        CaseStep(case_id=case_asset.id, step_order=index, step_text=step)
        for index, step in enumerate(case.step_items, start=1)
    )
    session.add(
        CaseRawNode(
            case_id=case_asset.id,
            raw_payload={
                "suite_title": case.suite_title,
                "path_nodes": case.path_nodes,
                "core_nodes": case.core_nodes,
                "core_labels": case.core_labels,
                "module_name": case.module_name,
                "product_feature": case.product_feature,
                "test_feature": case.test_feature,
                "raw_title": case.raw_title,
                "preconditions": case.preconditions,
                "steps_text": case.steps_text,
                "expected_result": case.expected_result,
            },
        )
    )
    return int(case_asset.id)


async def ensure_case_work_items(
    session: AsyncSession,
    *,
    case_ids: list[int] | set[int] | None = None,
    changed_case_ids: list[int] | set[int] | None = None,
) -> None:
    scoped_case_ids = set(case_ids or []) if case_ids is not None else None
    if scoped_case_ids is not None and not scoped_case_ids:
        return
    statement = (
        select(CaseAsset, CaseBody, CaseWorkItem)
        .join(CaseBody, CaseBody.case_id == CaseAsset.id)
        .outerjoin(CaseWorkItem, CaseWorkItem.case_id == CaseAsset.id)
    )
    if scoped_case_ids is not None:
        statement = statement.where(CaseAsset.id.in_(scoped_case_ids))
    rows = await session.execute(statement)
    changed = set(changed_case_ids or [])
    records = rows.all()
    case_inputs = [
        {
            "module_name": case_asset.module_name,
            "path_nodes": _case_path_nodes(case_asset),
            "product_feature": case_asset.product_feature,
            "test_feature": case_asset.test_feature,
            "raw_title": case_asset.raw_title,
            "clean_title": case_asset.clean_title,
            "scenario_tags": case_asset.scenario_tags,
            "manual": case_asset.manual,
            "preconditions": body.preconditions,
            "steps_text": body.steps_text,
            "expected_result": body.expected_result,
        }
        for case_asset, body, _work_item in records
    ]
    tags = await case_tagging.classify_cases(case_inputs)
    assignee_cache: dict[int, int | None] = {}

    for (case_asset, _body, work_item), tag in zip(records, tags, strict=True):
        requirement_item_id = int(case_asset.source_requirement_item_id)
        if requirement_item_id not in assignee_cache:
            assignee_cache[requirement_item_id] = await _first_assignee_id(session, requirement_item_id)
        first_assignee_id = assignee_cache[requirement_item_id]
        if work_item is None:
            work_item = CaseWorkItem(
                case_id=case_asset.id,
                assigned_user_id=first_assignee_id,
                execution_status="not_run",
                lifecycle_state="待验证",
                case_type=tag["case_type"],
                execution_target=tag["execution_target"],
                tag_source=tag["tag_source"],
                tag_reason=tag["tag_reason"],
                tag_confidence=tag["tag_confidence"],
                run_enabled=True,
            )
            session.add(work_item)
        else:
            work_item.execution_target = tag["execution_target"]
            work_item.tag_source = tag["tag_source"]
            work_item.tag_reason = tag["tag_reason"]
            work_item.tag_confidence = tag["tag_confidence"]
            if work_item.case_type != "changed":
                work_item.case_type = tag["case_type"]

        if case_asset.id in changed:
            reset_standard_work_item_execution(
                work_item,
                status="not_run",
                lifecycle_state="待人工干预",
                attention_reason="变更待确认",
            )
            work_item.case_type = "changed"
    await clear_standard_case_execution_artifacts(session, changed)


async def list_cases_for_batch(session: AsyncSession, batch_id: int) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(CaseAsset, CaseBody)
        .join(CaseBody, CaseBody.case_id == CaseAsset.id)
        .where(CaseAsset.batch_id == batch_id)
        .order_by(CaseAsset.ordinal, CaseAsset.id)
    )
    return [_case_dict(case_asset, body) for case_asset, body in rows.all()]


async def _ensure_requirement_exists(session: AsyncSession, requirement_item_id: int) -> None:
    if await session.get(RequirementItem, requirement_item_id) is None:
        raise ValueError("二级需求不存在")


async def _first_assignee_id(session: AsyncSession, requirement_item_id: int) -> int | None:
    result = await session.execute(
        select(RequirementAssignee.user_id)
        .where(RequirementAssignee.requirement_item_id == requirement_item_id)
        .order_by(RequirementAssignee.user_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


def _case_dict(case_asset: CaseAsset, body: CaseBody) -> dict[str, Any]:
    return {
        "id": case_asset.id,
        "ordinal": case_asset.ordinal,
        "suite_title": case_asset.suite_title,
        "path_nodes": _case_path_nodes(case_asset),
        "path": " / ".join(_path_node_texts(_case_path_nodes(case_asset))),
        "module_name": case_asset.module_name,
        "product_feature": case_asset.product_feature,
        "test_feature": case_asset.test_feature,
        "raw_title": case_asset.raw_title,
        "clean_title": case_asset.clean_title,
        "scenario_tags": list(case_asset.scenario_tags or []),
        "manual": case_asset.manual,
        "preconditions": body.preconditions,
        "steps_text": body.steps_text,
        "expected_result": body.expected_result,
    }


def _case_path_nodes(case_asset: CaseAsset) -> list[dict[str, Any]]:
    nodes = getattr(case_asset, "path_nodes", None)
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


def _batch_to_dict(batch: ImportBatch) -> dict[str, Any]:
    return {
        "id": batch.id,
        "suite_title": batch.suite_title,
        "source_name": batch.source_name,
        "requirement_item_id": batch.requirement_item_id,
        "imported_at": batch.imported_at.isoformat() if batch.imported_at else None,
        "case_count": batch.case_count,
        "raw_metadata": batch.raw_metadata or {},
    }


def _source_name(filename: str | None) -> str:
    return (filename or "uploaded.md").strip() or "uploaded.md"
