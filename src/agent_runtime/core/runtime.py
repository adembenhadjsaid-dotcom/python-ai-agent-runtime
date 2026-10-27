"""Single-task lifecycle, deadlines and cancellation without transport knowledge."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic

from agent_runtime.core.models import (
    ErrorKind,
    ExecutionContext,
    NormalizedError,
    Task,
    TaskResult,
    TaskStatus,
)
from agent_runtime.core.protocols import AgentAdapter

logger = logging.getLogger("agent_runtime")


def _error(task: Task, status: TaskStatus, kind: ErrorKind, message: str) -> TaskResult:
    return TaskResult(
        task_id=task.id,
        status=status,
        normalized_error=NormalizedError(kind, message),
    )


def _log(
    task: Task,
    context: ExecutionContext,
    adapter: AgentAdapter,
    status: TaskStatus,
    *,
    cleanup_failed: bool = False,
) -> None:
    # Never log instructions, outputs, permission values, metadata or exception text.
    logger.info(
        "Task state changed",
        extra={
            "task_id": task.id,
            "trace_id": context.trace_id,
            "adapter": adapter.name,
            "status": status.value,
            "timestamp": datetime.now(UTC),
            "cleanup_failed": cleanup_failed,
        },
    )


class Runtime:
    """Reusable stateless runtime. Each execute call owns its lifecycle and helpers."""

    async def execute(
        self,
        task: Task,
        adapter: AgentAdapter,
        *,
        context: ExecutionContext | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 -- the runtime owns deadline normalization
    ) -> TaskResult:
        """Execute once, await cleanup, then return one normalized terminal result.

        The earlier of timeout (seconds) and task.deadline applies. Zero/expired
        budgets skip execution. An explicit cancellation token returns CANCELLED;
        native caller cancellation propagates CancelledError after cleanup.
        """
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a finite number of seconds")
            if not math.isfinite(timeout) or timeout < 0:
                raise ValueError("timeout must be finite and nonnegative")
        context = context if context is not None else ExecutionContext()
        started_at, start = datetime.now(UTC), monotonic()
        budget = timeout
        if task.deadline is not None:
            remaining = (task.deadline - started_at).total_seconds()
            budget = remaining if budget is None else min(budget, remaining)

        def finish(result: TaskResult) -> TaskResult:
            _log(task, context, adapter, result.status)
            return replace(
                result,
                trace_id=context.trace_id,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                elapsed_seconds=monotonic() - start,
            )

        _log(task, context, adapter, TaskStatus.CREATED)
        if context.cancellation.is_cancelled:
            return finish(
                _error(task, TaskStatus.CANCELLED, ErrorKind.CANCELLED, "Task cancelled.")
            )
        if budget is not None and budget <= 0:
            return finish(
                _error(task, TaskStatus.TIMED_OUT, ErrorKind.TIMEOUT, "Deadline exceeded.")
            )

        _log(task, context, adapter, TaskStatus.RUNNING)

        async def invoke() -> TaskResult:
            # Wrapping also captures invalid synchronous execute implementations.
            return await adapter.execute(task, context)

        worker = asyncio.create_task(invoke())
        cancelled = asyncio.create_task(context.cancellation.wait())
        caller_cancelled = False
        result: TaskResult | None = None
        try:
            done, _ = await asyncio.wait(
                (worker, cancelled), timeout=budget, return_when=asyncio.FIRST_COMPLETED
            )
            if context.cancellation.is_cancelled:
                result = _error(task, TaskStatus.CANCELLED, ErrorKind.CANCELLED, "Task cancelled.")
            elif worker in done:
                if worker.cancelled():
                    context.cancellation.cancel()
                    result = _error(
                        task, TaskStatus.CANCELLED, ErrorKind.CANCELLED, "Task cancelled."
                    )
                else:
                    try:
                        candidate = worker.result()
                    except Exception:
                        result = _error(
                            task,
                            TaskStatus.FAILED,
                            ErrorKind.ADAPTER_ERROR,
                            "Adapter execution failed.",
                        )
                    else:
                        if not isinstance(candidate, TaskResult) or candidate.task_id != task.id:
                            result = _error(
                                task,
                                TaskStatus.FAILED,
                                ErrorKind.INVALID_RESULT,
                                "Adapter returned an invalid or mismatched result.",
                            )
                        else:
                            result = candidate
            else:
                context.cancellation.cancel()
                result = _error(task, TaskStatus.TIMED_OUT, ErrorKind.TIMEOUT, "Deadline exceeded.")
        except asyncio.CancelledError:
            caller_cancelled = True
            context.cancellation.cancel()
        finally:
            # Cancel exactly once: repeated cancellation must not interrupt async finally blocks.
            interrupted_worker = not worker.done()
            for child in (worker, cancelled):
                if not child.done():
                    child.cancel()
            cleanup = asyncio.gather(worker, cancelled, return_exceptions=True)
            while True:
                try:
                    await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    caller_cancelled = True
                    context.cancellation.cancel()

        cleanup_failed = (
            interrupted_worker and not worker.cancelled() and worker.exception() is not None
        )
        if caller_cancelled:
            _log(task, context, adapter, TaskStatus.CANCELLED, cleanup_failed=cleanup_failed)
            raise asyncio.CancelledError
        assert result is not None
        if cleanup_failed and result.status in (TaskStatus.CANCELLED, TaskStatus.TIMED_OUT):
            result = TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                normalized_error=NormalizedError(
                    ErrorKind.ADAPTER_ERROR, "Adapter failed while stopping execution."
                ),
                raw_metadata={"interrupted_status": result.status.value},
            )
        return finish(result)
