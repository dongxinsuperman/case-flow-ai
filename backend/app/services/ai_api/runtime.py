from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal


ExecutionMode = Literal["standard", "quick"]


@dataclass
class _BatchRuntime:
    case_ids: set[int]
    batch_task: asyncio.Task[object] | None = None
    item_tasks: dict[int, asyncio.Task[object]] = field(default_factory=dict)
    stopped_case_ids: set[int] = field(default_factory=set)


_runtimes: dict[tuple[ExecutionMode, str], _BatchRuntime] = {}


def start(mode: ExecutionMode, submission_id: str, case_ids: list[int]) -> None:
    """登记本进程内运行句柄；它不承载业务状态，也不跨进程生效。"""
    _runtimes[(mode, submission_id)] = _BatchRuntime(case_ids=set(case_ids))


def set_batch_task(mode: ExecutionMode, submission_id: str, task: asyncio.Task[object]) -> None:
    runtime = _runtimes.get((mode, submission_id))
    if runtime is not None:
        runtime.batch_task = task


def set_item_task(
    mode: ExecutionMode,
    submission_id: str,
    case_id: int,
    task: asyncio.Task[object],
) -> None:
    runtime = _runtimes.get((mode, submission_id))
    if runtime is not None:
        runtime.item_tasks[case_id] = task


def clear_item_task(mode: ExecutionMode, submission_id: str, case_id: int) -> None:
    runtime = _runtimes.get((mode, submission_id))
    if runtime is not None:
        runtime.item_tasks.pop(case_id, None)


def is_stopped(mode: ExecutionMode, submission_id: str, case_id: int) -> bool:
    runtime = _runtimes.get((mode, submission_id))
    return runtime is not None and case_id in runtime.stopped_case_ids


def stop(mode: ExecutionMode, submission_id: str, case_ids: list[int] | None = None) -> list[int]:
    """停止本进程指定执行单元；空 case_ids 表示该 submission 的全部单元。"""
    runtime = _runtimes.get((mode, submission_id))
    if runtime is None:
        return []

    requested = runtime.case_ids if not case_ids else {int(case_id) for case_id in case_ids}
    targets = runtime.case_ids & requested
    runtime.stopped_case_ids.update(targets)

    for case_id in targets:
        task = runtime.item_tasks.get(case_id)
        if task is not None and not task.done():
            task.cancel()

    # 全部单元都被停止时，连同批次协程一起中断，避免它继续写 batch 终态。
    if runtime.case_ids and runtime.case_ids <= runtime.stopped_case_ids:
        task = runtime.batch_task
        if task is not None and not task.done():
            task.cancel()

    return sorted(targets)


def finish(mode: ExecutionMode, submission_id: str) -> None:
    _runtimes.pop((mode, submission_id), None)
