from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.execution_logs import ExecutionCallLogOut, ExecutionCallLogPage
from app.services import execution_call_log

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/execution-strategy-call-logs", response_model=ExecutionCallLogPage)
async def list_execution_strategy_call_logs(
    session: SessionDep,
    status: Annotated[str | None, Query()] = None,
    executor: Annotated[str | None, Query()] = None,
    mode: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ExecutionCallLogPage:
    logs, total, meta = await execution_call_log.list_call_logs(
        session,
        page=page,
        page_size=page_size,
        status=status,
        executor=executor,
        mode=mode,
    )
    items = [
        ExecutionCallLogOut(
            id=log.id,
            call_id=log.call_id,
            request_group_id=log.request_group_id,
            mode=log.mode,
            scope=log.scope,
            entry=log.entry,
            executor=log.executor,
            requirement_item_id=log.requirement_item_id,
            quick_session_id=log.quick_session_id,
            case_ids=list(log.case_ids or []),
            execution_batch_id=log.execution_batch_id,
            submission_id=log.submission_id,
            trigger_user_id=log.trigger_user_id,
            trigger_user_name=meta.user_names.get(log.trigger_user_id) if log.trigger_user_id else None,
            requirement_item_title=(
                meta.requirement_titles.get(log.requirement_item_id)
                if log.requirement_item_id
                else None
            ),
            quick_session_title=(
                meta.quick_titles.get(log.quick_session_id) if log.quick_session_id else None
            ),
            submitted_function_map_context=meta.submitted_context.get(log.id),
            input=log.input or {},
            function_map_result=log.function_map_result,
            effective_context=log.effective_context,
            status=log.status,
            failure_reason=log.failure_reason,
            created_at=log.created_at,
            updated_at=log.updated_at,
        )
        for log in logs
    ]
    return ExecutionCallLogPage(items=items, total=total, page=page, page_size=page_size)
