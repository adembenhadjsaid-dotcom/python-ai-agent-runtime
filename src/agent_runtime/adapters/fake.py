"""Deterministic offline adapter for examples and contract tests."""

from __future__ import annotations

import asyncio

from agent_runtime.core.models import (
    AgentCapabilities,
    ExecutionContext,
    Task,
    TaskResult,
    TaskStatus,
)


class FakeAgent:
    """Return a fixed result, raise a fixed error, or await an explicit event gate.

    started/cleaned_up and calls are observations for deterministic test coordination.
    Use a fresh instance for each gated test. The default agent is reusable.
    """

    name = "fake"
    capabilities = AgentCapabilities(supports_cancel=True)

    def __init__(
        self,
        *,
        result: TaskResult | None = None,
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        if result is not None and error is not None:
            raise ValueError("Choose either result or error")
        self._result = result
        self._error = error
        self._gate = gate
        self.started = asyncio.Event()
        self.cleaned_up = asyncio.Event()
        self.calls: list[tuple[Task, ExecutionContext]] = []

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        self.calls.append((task, context))
        self.started.set()
        try:
            if self._gate is not None:
                await self._gate.wait()
            if self._error is not None:
                raise self._error
            if self._result is not None:
                return self._result
            return TaskResult(
                task_id=task.id, status=TaskStatus.SUCCEEDED, summary="Fake task completed."
            )
        finally:
            self.cleaned_up.set()
