"""执行策略调用日志服务（检查点 7）。

每一路 submit 在前台创建一条 `compiling` 日志并拿到 `call_id`；后台任务完成
提交后标记 `submitted`，或在编译/提交失败时标记 `compile_failed`/`submit_failed`。
Function Map 编译明细（`function_map_result` / `effective_context`）留给检查点 6。
"""
from __future__ import annotations

import uuid
from typing import Any

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_assets import AIPhoneExecutionBatch
from app.models.execution_call_log import ExecutionStrategyCallLog
from app.models.quick import QuickExecutionBatch, QuickSession
from app.models.requirements import RequirementItem, User


async def create_call_log(
    session: AsyncSession,
    *,
    mode: str,
    executor: str,
    entry: str,
    case_ids: list[int],
    scope: str | None = None,
    requirement_item_id: int | None = None,
    quick_session_id: str | None = None,
    trigger_user_id: int | None = None,
    request_group_id: str | None = None,
    input: dict[str, Any] | None = None,
) -> ExecutionStrategyCallLog:
    """创建一条 compiling 状态的调用日志并 flush 出主键；提交由调用方负责。"""
    log = ExecutionStrategyCallLog(
        call_id=uuid.uuid4().hex,
        request_group_id=request_group_id,
        mode=mode,
        scope=scope or ("batch" if len(case_ids) > 1 else "single"),
        entry=entry,
        executor=executor,
        requirement_item_id=requirement_item_id,
        quick_session_id=quick_session_id,
        case_ids=list(case_ids),
        trigger_user_id=trigger_user_id,
        input=input or {},
        status="compiling",
    )
    session.add(log)
    await session.flush()
    return log


async def mark_submitted(
    session: AsyncSession,
    call_log_id: int,
    *,
    execution_batch_id: int | None,
    submission_id: str | None,
) -> None:
    log = await session.get(ExecutionStrategyCallLog, call_log_id)
    if log is None:
        return
    log.status = "submitted"
    log.execution_batch_id = execution_batch_id
    log.submission_id = submission_id
    log.failure_reason = None


async def mark_failed(
    session: AsyncSession,
    call_log_id: int,
    *,
    status: str,
    reason: str,
) -> None:
    """status 取 compile_failed（编译阶段）或 submit_failed（提交执行器阶段）。"""
    log = await session.get(ExecutionStrategyCallLog, call_log_id)
    if log is None:
        return
    log.status = status
    log.failure_reason = reason


@dataclass
class CallLogMeta:
    """列表页附带的解析结果：触发人名、关联对象标题、以及本次实际提交的 functionMapContext。"""

    user_names: dict[int, str] = field(default_factory=dict)
    requirement_titles: dict[int, str] = field(default_factory=dict)
    quick_titles: dict[str, str] = field(default_factory=dict)
    # 按 call log id → 该次提交批次 raw_request 里的 functionMapContext（没有则不放键）
    submitted_context: dict[int, str] = field(default_factory=dict)


async def list_call_logs(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    executor: str | None = None,
    mode: str | None = None,
) -> tuple[list[ExecutionStrategyCallLog], int, CallLogMeta]:
    """执行流水台账：按时间倒序分页，支持状态/执行器/模式筛选。
    返回 (本页日志, 总数, meta)；meta 含触发人名、关联标题、本次实际提交的 functionMapContext。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    filters = []
    if status:
        filters.append(ExecutionStrategyCallLog.status == status)
    if executor:
        filters.append(ExecutionStrategyCallLog.executor == executor)
    if mode:
        filters.append(ExecutionStrategyCallLog.mode == mode)

    count_stmt = select(func.count()).select_from(ExecutionStrategyCallLog)
    for condition in filters:
        count_stmt = count_stmt.where(condition)
    total = int((await session.execute(count_stmt)).scalar() or 0)

    stmt = select(ExecutionStrategyCallLog)
    for condition in filters:
        stmt = stmt.where(condition)
    stmt = (
        stmt.order_by(ExecutionStrategyCallLog.created_at.desc(), ExecutionStrategyCallLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = list((await session.execute(stmt)).scalars().all())

    meta = CallLogMeta()

    user_ids = {log.trigger_user_id for log in logs if log.trigger_user_id}
    if user_ids:
        rows = await session.execute(
            select(User.id, User.display_name).where(User.id.in_(user_ids))
        )
        meta.user_names = {int(uid): name for uid, name in rows.all()}

    item_ids = {log.requirement_item_id for log in logs if log.requirement_item_id}
    if item_ids:
        rows = await session.execute(
            select(RequirementItem.id, RequirementItem.title).where(RequirementItem.id.in_(item_ids))
        )
        meta.requirement_titles = {int(iid): title for iid, title in rows.all()}

    quick_ids = {log.quick_session_id for log in logs if log.quick_session_id}
    if quick_ids:
        rows = await session.execute(
            select(QuickSession.session_id, QuickSession.suite_title).where(
                QuickSession.session_id.in_(quick_ids)
            )
        )
        meta.quick_titles = {sid: title for sid, title in rows.all()}

    # 本次实际提交的 functionMapContext：按日志的批次去批量取 raw_request（标准/快速两张表分开）
    std_batch_ids = {
        log.execution_batch_id
        for log in logs
        if log.execution_batch_id and log.mode != "quick"
    }
    quick_batch_ids = {
        log.execution_batch_id
        for log in logs
        if log.execution_batch_id and log.mode == "quick"
    }
    std_ctx: dict[int, str] = {}
    quick_ctx: dict[int, str] = {}
    if std_batch_ids:
        rows = await session.execute(
            select(AIPhoneExecutionBatch.id, AIPhoneExecutionBatch.raw_request).where(
                AIPhoneExecutionBatch.id.in_(std_batch_ids)
            )
        )
        for bid, raw in rows.all():
            ctx = (raw or {}).get("functionMapContext")
            if ctx:
                std_ctx[int(bid)] = ctx
    if quick_batch_ids:
        rows = await session.execute(
            select(QuickExecutionBatch.id, QuickExecutionBatch.raw_request).where(
                QuickExecutionBatch.id.in_(quick_batch_ids)
            )
        )
        for bid, raw in rows.all():
            ctx = (raw or {}).get("functionMapContext")
            if ctx:
                quick_ctx[int(bid)] = ctx
    for log in logs:
        if not log.execution_batch_id:
            continue
        ctx = (quick_ctx if log.mode == "quick" else std_ctx).get(log.execution_batch_id)
        if ctx:
            meta.submitted_context[log.id] = ctx

    return logs, total, meta
