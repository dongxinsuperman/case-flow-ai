from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExecutionCase:
    case_id: int
    title: str
    preconditions: str
    steps_text: str
    expected_result: str


@dataclass(frozen=True)
class ExecutionRequest:
    executor_key: str
    cases: list[ExecutionCase]
    callback_url: str | None = None


@dataclass(frozen=True)
class ExecutionSubmission:
    external_id: str
    status: str


class ExecutorProvider(Protocol):
    key: str

    async def submit(self, request: ExecutionRequest) -> ExecutionSubmission:
        """Submit cases to an executor and return the external submission identity."""

