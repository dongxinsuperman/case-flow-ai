"""导入碰撞结果的内存快照。

碰撞只在展示阶段计算一次，结果连同批次签名、内容哈希一起保存；确认入库时按
``review_id`` 取回同一份快照，不再重算、不再调碰撞模型。展示和落库因此基于同一张清单。

【硬约束：单 worker + 单 Pod】
和 ``import_jobs`` 一样，快照只存在本进程内存里。多 worker / 多 Pod 或服务重启会取不到
快照；此时明确要求用户重新导入碰撞，失败即阻断，不做静默重算或兜底提交。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

_TTL_SECONDS = 1800
_MAX_ENTRIES = 200

_REVIEWS: dict[str, dict[str, Any]] = {}


def store(
    *,
    requirement_item_id: int,
    source_name: str,
    content_hash: str,
    batch_signature: str,
    review: dict[str, Any],
) -> str:
    """保存一份碰撞快照，返回供确认入库使用的 review_id。"""
    _gc()
    review_id = uuid.uuid4().hex
    _REVIEWS[review_id] = {
        "requirement_item_id": requirement_item_id,
        "source_name": source_name,
        "content_hash": content_hash,
        "batch_signature": batch_signature,
        "review": review,
        "created_at": time.time(),
    }
    return review_id


def get(review_id: str) -> dict[str, Any] | None:
    """取回有效快照；不存在或过期时返回 None。"""
    entry = _REVIEWS.get(review_id)
    if entry is None:
        return None
    if time.time() - entry["created_at"] > _TTL_SECONDS:
        _REVIEWS.pop(review_id, None)
        return None
    return entry


def discard(review_id: str) -> None:
    """成功入库后一次性消费快照，禁止同一审批重复提交。"""
    _REVIEWS.pop(review_id, None)


def _gc() -> None:
    now = time.time()
    expired = [review_id for review_id, entry in _REVIEWS.items() if now - entry["created_at"] > _TTL_SECONDS]
    for review_id in expired:
        _REVIEWS.pop(review_id, None)
    if len(_REVIEWS) <= _MAX_ENTRIES:
        return
    oldest = sorted(_REVIEWS, key=lambda review_id: _REVIEWS[review_id]["created_at"])
    for review_id in oldest[: len(_REVIEWS) - _MAX_ENTRIES]:
        _REVIEWS.pop(review_id, None)
