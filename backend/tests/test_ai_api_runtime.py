from __future__ import annotations

import asyncio

import pytest

from app.services.ai_api import runtime


@pytest.fixture(autouse=True)
def clear_runtime() -> None:
    runtime._runtimes.clear()
    yield
    runtime._runtimes.clear()


@pytest.mark.asyncio
async def test_stop_selected_case_cancels_current_task_without_stopping_other_cases() -> None:
    waiting = asyncio.Event()

    async def wait_forever() -> None:
        await waiting.wait()

    runtime.start("standard", "submission-1", [11, 12])
    current = asyncio.create_task(wait_forever())
    runtime.set_item_task("standard", "submission-1", 11, current)

    assert runtime.stop("standard", "submission-1", [11]) == [11]
    with pytest.raises(asyncio.CancelledError):
        await current

    assert runtime.is_stopped("standard", "submission-1", 11) is True
    assert runtime.is_stopped("standard", "submission-1", 12) is False


@pytest.mark.asyncio
async def test_stop_all_quick_cases_cancels_batch_task_and_prevents_later_calls() -> None:
    waiting = asyncio.Event()

    async def wait_forever() -> None:
        await waiting.wait()

    runtime.start("quick", "submission-2", [21, 22])
    batch_task = asyncio.create_task(wait_forever())
    runtime.set_batch_task("quick", "submission-2", batch_task)

    assert runtime.stop("quick", "submission-2") == [21, 22]
    with pytest.raises(asyncio.CancelledError):
        await batch_task

    assert runtime.is_stopped("quick", "submission-2", 21) is True
    assert runtime.is_stopped("quick", "submission-2", 22) is True
