"""导入碰撞的后台任务注册表。

打磨碰撞要调模型（1-3 分钟），不能挂在一个 HTTP 请求里——网关会在 ~60s 掐断（502）。
这里把碰撞计算放进后台 asyncio 任务，前端拿 task_id 轮询结果，每次轮询都是短请求，
不会触发网关超时。失败一律显式记录到任务状态里返回给前端，不静默吞异常。

【硬约束：必须单 worker + 单 Pod】
任务状态只存在本进程内存里。一旦跑多 uvicorn worker（--workers>1）或把 Deployment 副本
扩到多 Pod，创建任务的进程和处理轮询的进程可能不是同一个，轮询就会拿到 404。
因此本方案要求 case-flow 后端保持单 worker、单副本（当前部署即如此：docker/backend.Dockerfile
启动命令未带 --workers、Deployment replicas=1）。

配合 importing.py 把模型调用放进线程（asyncio.to_thread），单 worker 也不会被健康检查饿死，
无需靠加 worker 兜底。将来若确实要扩 worker/Pod，必须先把任务状态迁到共享存储（如 Redis/DB），
否则轮询必然错进程。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

# 结果保留时长：跑完后 30 分钟内可轮询，之后回收。
_JOB_TTL_SECONDS = 1800
# 内存里最多保留的任务数，超出后回收最旧的已结束任务（进行中的不动）。
_MAX_JOBS = 200

_JOBS: dict[str, dict[str, Any]] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}


def start(factory: Callable[[], Awaitable[dict[str, Any]]]) -> str:
    """登记一个后台任务并立刻调度，返回可用于轮询的 task_id。"""
    _gc()
    job_id = uuid.uuid4().hex
    now = time.time()
    _JOBS[job_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    task = asyncio.create_task(_run(job_id, factory))
    _TASKS[job_id] = task
    task.add_done_callback(_on_task_done)
    return job_id


def status(job_id: str) -> dict[str, Any] | None:
    """查询任务状态；不存在或已回收返回 None。"""
    job = _JOBS.get(job_id)
    if job is None:
        return None
    elapsed_ms = int((job["updated_at"] - job["created_at"]) * 1000)
    if job["status"] == "pending":
        elapsed_ms = int((time.time() - job["created_at"]) * 1000)
    return {
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
        "elapsed_ms": elapsed_ms,
    }


async def _run(job_id: str, factory: Callable[[], Awaitable[dict[str, Any]]]) -> None:
    try:
        result = await factory()
    except Exception as exc:  # noqa: BLE001 - 把真实失败原因显式带回前端，不静默降级
        _update(job_id, status="error", error=str(exc) or exc.__class__.__name__)
        return
    _update(job_id, status="done", result=result)


def _update(job_id: str, **changes: Any) -> None:
    job = _JOBS.get(job_id)
    if job is None:
        return
    job.update(changes)
    job["updated_at"] = time.time()


def _on_task_done(task: asyncio.Task[None]) -> None:
    for job_id, tracked in list(_TASKS.items()):
        if tracked is task:
            _TASKS.pop(job_id, None)
            break
    if not task.cancelled():
        task.exception()  # 消费异常，避免 "Task exception never retrieved" 告警


def _gc() -> None:
    now = time.time()
    expired = [
        job_id
        for job_id, job in _JOBS.items()
        if job["status"] != "pending" and now - job["updated_at"] > _JOB_TTL_SECONDS
    ]
    for job_id in expired:
        _JOBS.pop(job_id, None)

    if len(_JOBS) <= _MAX_JOBS:
        return
    finished = sorted(
        (job_id for job_id, job in _JOBS.items() if job["status"] != "pending"),
        key=lambda job_id: _JOBS[job_id]["updated_at"],
    )
    for job_id in finished[: len(_JOBS) - _MAX_JOBS]:
        _JOBS.pop(job_id, None)
