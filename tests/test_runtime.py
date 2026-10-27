"""Runtime contract tests, with event gates instead of arbitrary sleeps."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_runtime import finish_cleanup
from agent_runtime.adapters.fake import FakeAgent
from agent_runtime.core.models import (
    AgentCapabilities,
    ErrorKind,
    ExecutionContext,
    NormalizedError,
    Task,
    TaskResult,
    TaskStatus,
)
from agent_runtime.core.runtime import Runtime


@pytest.fixture
def task(tmp_path):
    return Task(id="task-1", instructions="Inspect the project", workspace=tmp_path)


async def complete(awaitable):
    """Fail a broken cancellation test instead of hanging the test process."""
    return await asyncio.wait_for(awaitable, timeout=2)


@pytest.mark.asyncio
async def test_success_preserves_adapter_output_and_adds_runtime_timing(task):
    adapter = FakeAgent(
        result=TaskResult(
            task_id=task.id,
            status=TaskStatus.SUCCEEDED,
            summary="Inspected",
            artifacts=(Path("report.txt"),),
            changed_files=(Path("a.py"),),
            raw_metadata={"backend": "fake"},
        )
    )
    context = ExecutionContext(trace_id="trace-1")
    result = await Runtime().execute(task, adapter, context=context)
    assert result.status is TaskStatus.SUCCEEDED
    assert result.task_id == task.id
    assert result.summary == "Inspected"
    assert result.artifacts == (Path("report.txt"),)
    assert result.changed_files == (Path("a.py"),)
    assert result.raw_metadata == {"backend": "fake"}
    assert result.trace_id == "trace-1"
    assert result.started_at <= result.finished_at
    assert result.started_at.tzinfo is UTC
    assert result.elapsed_seconds >= 0
    assert adapter.calls == [(task, context)]
    assert adapter.cleaned_up.is_set()


@pytest.mark.asyncio
async def test_exception_maps_to_safe_error_without_retry_or_secret_leak(task, caplog):
    adapter = FakeAgent(error=RuntimeError("api-key=SUPER_SECRET"))
    with caplog.at_level(logging.INFO, logger="agent_runtime"):
        result = await Runtime().execute(task, adapter)
    assert result.status is TaskStatus.FAILED
    assert result.normalized_error.kind is ErrorKind.ADAPTER_ERROR
    assert len(adapter.calls) == 1
    assert adapter.cleaned_up.is_set()
    assert "SUPER_SECRET" not in repr(result)
    assert "SUPER_SECRET" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_adapter_normalized_failure_is_preserved(task):
    error = NormalizedError(ErrorKind.ADAPTER_ERROR, "Safe adapter diagnostic")
    adapter = FakeAgent(
        result=TaskResult(
            task_id=task.id,
            status=TaskStatus.FAILED,
            normalized_error=error,
            raw_metadata={"code": "42"},
        )
    )
    result = await Runtime().execute(task, adapter)
    assert result.normalized_error == error
    assert result.raw_metadata == {"code": "42"}


@pytest.mark.asyncio
async def test_runtime_accepts_an_independent_structural_adapter(task):
    class OtherAdapter:
        name = "other"
        capabilities = AgentCapabilities()

        async def execute(self, task, context):
            return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED, summary="Other")

    result = await Runtime().execute(task, OtherAdapter())
    assert result.summary == "Other"
    assert result.status is TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_log_transitions_are_ordered_and_correlated(task, caplog):
    with caplog.at_level(logging.INFO, logger="agent_runtime"):
        result = await Runtime().execute(
            task, FakeAgent(), context=ExecutionContext(trace_id="trace-17")
        )
    assert [record.status for record in caplog.records] == ["created", "running", "succeeded"]
    for record in caplog.records:
        assert record.task_id == task.id
        assert record.trace_id == result.trace_id == "trace-17"
        assert record.adapter == "fake"
        assert record.timestamp.tzinfo is UTC
        assert task.instructions not in record.getMessage()


@pytest.mark.asyncio
async def test_timeout_cancels_active_execution_and_waits_for_cleanup(task):
    adapter = FakeAgent(gate=asyncio.Event())
    context = ExecutionContext()
    result = await complete(Runtime().execute(task, adapter, context=context, timeout=0.02))
    assert adapter.started.is_set()
    assert adapter.cleaned_up.is_set()
    assert context.cancellation.is_cancelled
    assert result.status is TaskStatus.TIMED_OUT
    assert result.normalized_error.kind is ErrorKind.TIMEOUT


@pytest.mark.asyncio
async def test_expired_deadline_never_starts_adapter(tmp_path, caplog):
    task = Task(
        id="expired",
        instructions="Inspect",
        workspace=tmp_path,
        deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    adapter = FakeAgent()
    with caplog.at_level(logging.INFO, logger="agent_runtime"):
        result = await Runtime().execute(task, adapter)
    assert result.status is TaskStatus.TIMED_OUT
    assert adapter.calls == []
    assert [r.status for r in caplog.records] == ["created", "timed_out"]


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,deadline_offset", [(20, 0.02), (0.02, 20)])
async def test_earlier_deadline_or_timeout_wins(tmp_path, budget, deadline_offset):
    task = Task(
        id="bounded",
        instructions="Inspect",
        workspace=tmp_path,
        deadline=datetime.now(UTC) + timedelta(seconds=deadline_offset),
    )
    adapter = FakeAgent(gate=asyncio.Event())
    result = await complete(Runtime().execute(task, adapter, timeout=budget))
    assert result.status is TaskStatus.TIMED_OUT
    assert adapter.cleaned_up.is_set()


@pytest.mark.asyncio
async def test_zero_timeout_never_starts_adapter(task):
    adapter = FakeAgent()
    result = await Runtime().execute(task, adapter, timeout=0)
    assert result.status is TaskStatus.TIMED_OUT
    assert adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [-1, float("nan"), float("inf"), -float("inf"), True, "1"])
async def test_invalid_timeout_rejected_before_adapter_starts(task, budget):
    adapter = FakeAgent()
    with pytest.raises((ValueError, TypeError)):
        await Runtime().execute(task, adapter, timeout=budget)
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_token_cancellation_returns_cancelled_after_cleanup(task):
    adapter = FakeAgent(gate=asyncio.Event())
    context = ExecutionContext()
    execution = asyncio.create_task(Runtime().execute(task, adapter, context=context))
    await complete(adapter.started.wait())
    context.cancellation.cancel()
    result = await complete(execution)
    assert result.status is TaskStatus.CANCELLED
    assert result.normalized_error.kind is ErrorKind.CANCELLED
    assert adapter.cleaned_up.is_set()


@pytest.mark.asyncio
async def test_precancelled_context_never_starts_adapter(task):
    adapter = FakeAgent()
    context = ExecutionContext()
    context.cancellation.cancel()
    result = await Runtime().execute(task, adapter, context=context)
    assert result.status is TaskStatus.CANCELLED
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_native_caller_cancel_propagates_after_cleanup_and_logs_once(task, caplog):
    adapter = FakeAgent(gate=asyncio.Event())
    context = ExecutionContext()
    with caplog.at_level(logging.INFO, logger="agent_runtime"):
        execution = asyncio.create_task(Runtime().execute(task, adapter, context=context))
        await complete(adapter.started.wait())
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await complete(execution)
    assert adapter.cleaned_up.is_set()
    assert context.cancellation.is_cancelled
    assert [r.status for r in caplog.records] == ["created", "running", "cancelled"]


class SlowCleanupAdapter:
    name = "slow-cleanup"
    capabilities = AgentCapabilities(supports_cancel=True)

    def __init__(self):
        self.started = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.cleaned = asyncio.Event()

    async def execute(self, task, context):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaning.set()
            await self.release_cleanup.wait()
            self.cleaned.set()


@pytest.mark.asyncio
async def test_repeated_caller_cancel_does_not_interrupt_adapter_cleanup(task):
    adapter = SlowCleanupAdapter()
    execution = asyncio.create_task(Runtime().execute(task, adapter))
    await complete(adapter.started.wait())
    execution.cancel()
    await complete(adapter.cleaning.wait())
    execution.cancel()
    assert not execution.done()
    adapter.release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await complete(execution)
    assert adapter.cleaned.is_set()


@pytest.mark.asyncio
async def test_timeout_waits_for_async_cleanup_before_return(task):
    adapter = SlowCleanupAdapter()
    execution = asyncio.create_task(Runtime().execute(task, adapter, timeout=0.02))
    await complete(adapter.cleaning.wait())
    assert not execution.done()
    adapter.release_cleanup.set()
    result = await complete(execution)
    assert result.status is TaskStatus.TIMED_OUT
    assert adapter.cleaned.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        "plain text",
        TaskResult(task_id="wrong-task", status=TaskStatus.SUCCEEDED),
    ],
)
async def test_invalid_adapter_result_fails_closed(task, payload):
    class BadAdapter:
        name = "bad"
        capabilities = AgentCapabilities()

        async def execute(self, task, context):
            return payload

    result = await Runtime().execute(task, BadAdapter())
    assert result.status is TaskStatus.FAILED
    assert result.normalized_error.kind is ErrorKind.INVALID_RESULT


@pytest.mark.asyncio
async def test_adapter_timeout_exception_is_failure_not_runtime_deadline(task):
    result = await Runtime().execute(task, FakeAgent(error=TimeoutError("upstream")))
    assert result.status is TaskStatus.FAILED
    assert result.normalized_error.kind is ErrorKind.ADAPTER_ERROR


@pytest.mark.asyncio
async def test_adapter_self_cancellation_is_a_cancelled_result(task):
    class SelfCancelled:
        name = "self-cancelled"
        capabilities = AgentCapabilities()

        async def execute(self, task, context):
            raise asyncio.CancelledError

    result = await Runtime().execute(task, SelfCancelled())
    assert result.status is TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_runtime_and_adapter_reuse_do_not_leak_state_or_context(task):
    runtime, adapter = Runtime(), FakeAgent()
    first = await runtime.execute(task, adapter)
    second = await runtime.execute(task, adapter)
    assert first.status is second.status is TaskStatus.SUCCEEDED
    assert first.trace_id != second.trace_id
    assert adapter.calls[0][1].cancellation is not adapter.calls[1][1].cancellation


@pytest.mark.asyncio
async def test_runtime_leaves_no_background_tasks_after_timeout(task):
    before = asyncio.all_tasks()
    await Runtime().execute(task, FakeAgent(gate=asyncio.Event()), timeout=0.01)
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_cancellation_wins_when_adapter_returns_in_same_cycle(task):
    class CancelsAndReturns:
        name = "cancel-and-return"
        capabilities = AgentCapabilities(supports_cancel=True)

        async def execute(self, task, context):
            context.cancellation.cancel()
            return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED)

    result = await Runtime().execute(task, CancelsAndReturns())
    assert result.status is TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancellation_wins_over_execution_exception_in_same_cycle(task):
    class CancelsAndFails:
        name = "cancel-and-fail"
        capabilities = AgentCapabilities(supports_cancel=True)

        async def execute(self, task, context):
            context.cancellation.cancel()
            raise RuntimeError("ordinary execution failure")

    result = await Runtime().execute(task, CancelsAndFails())
    assert result.status is TaskStatus.CANCELLED
    assert result.normalized_error.kind is ErrorKind.CANCELLED


@pytest.mark.asyncio
async def test_adapter_cannot_convert_runtime_timeout_into_success(task):
    class SuppressesCancellation:
        name = "suppresses-cancel"
        capabilities = AgentCapabilities()

        async def execute(self, task, context):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED)

    result = await Runtime().execute(task, SuppressesCancellation(), timeout=0.01)
    assert result.status is TaskStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_cleanup_failure_surfaces_safely_instead_of_silent_timeout(task, caplog):
    class BrokenCleanup:
        name = "broken-cleanup"
        capabilities = AgentCapabilities()

        async def execute(self, task, context):
            try:
                await asyncio.Event().wait()
            finally:
                raise RuntimeError("cleanup-secret=DO_NOT_DISCLOSE")

    with caplog.at_level(logging.INFO, logger="agent_runtime"):
        result = await Runtime().execute(task, BrokenCleanup(), timeout=0.01)
    assert result.status is TaskStatus.FAILED
    assert result.normalized_error.kind is ErrorKind.ADAPTER_ERROR
    assert result.raw_metadata["interrupted_status"] == "timed_out"
    assert "DO_NOT_DISCLOSE" not in repr(result) + caplog.text
    assert [r.status for r in caplog.records] == ["created", "running", "failed"]


@pytest.mark.asyncio
async def test_concurrent_calls_have_independent_cancellation(task):
    runtime = Runtime()
    first_agent, second_agent = FakeAgent(gate=asyncio.Event()), FakeAgent()
    context = ExecutionContext()
    first = asyncio.create_task(runtime.execute(task, first_agent, context=context))
    await complete(first_agent.started.wait())
    second = await runtime.execute(task, second_agent)
    context.cancellation.cancel()
    cancelled = await complete(first)
    assert cancelled.status is TaskStatus.CANCELLED
    assert second.status is TaskStatus.SUCCEEDED
    assert cancelled.trace_id != second.trace_id


class ProtectedCleanupAdapter(SlowCleanupAdapter):
    def __init__(self, *, wait_for_token):
        super().__init__()
        self.wait_for_token = wait_for_token

    async def execute(self, task, context):
        async def cleanup():
            self.cleaning.set()
            await self.release_cleanup.wait()
            self.cleaned.set()

        try:
            self.started.set()
            if self.wait_for_token:
                await context.cancellation.wait()
            return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED)
        finally:
            await finish_cleanup(cleanup())


@pytest.mark.asyncio
async def test_token_aware_adapter_cleanup_is_not_abandoned(task):
    adapter = ProtectedCleanupAdapter(wait_for_token=True)
    context = ExecutionContext()
    execution = asyncio.create_task(Runtime().execute(task, adapter, context=context))
    await complete(adapter.started.wait())
    context.cancellation.cancel()
    await complete(adapter.cleaning.wait())
    assert not execution.done()
    adapter.release_cleanup.set()
    result = await complete(execution)
    assert result.status is TaskStatus.CANCELLED
    assert adapter.cleaned.is_set()


@pytest.mark.asyncio
async def test_timeout_during_normal_completion_waits_for_protected_cleanup(task):
    adapter = ProtectedCleanupAdapter(wait_for_token=False)
    context = ExecutionContext()
    execution = asyncio.create_task(Runtime().execute(task, adapter, context=context, timeout=0.02))
    await complete(adapter.cleaning.wait())
    await complete(context.cancellation.wait())  # Deadline observed; teardown is still held.
    assert not execution.done()
    adapter.release_cleanup.set()
    result = await complete(execution)
    assert result.status is TaskStatus.TIMED_OUT
    assert adapter.cleaned.is_set()


@pytest.mark.asyncio
async def test_native_cancel_during_normal_cleanup_waits_for_teardown(task):
    adapter = ProtectedCleanupAdapter(wait_for_token=False)
    execution = asyncio.create_task(Runtime().execute(task, adapter))
    await complete(adapter.cleaning.wait())
    execution.cancel()
    assert not execution.done()
    adapter.release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await complete(execution)
    assert adapter.cleaned.is_set()
