"""Contract tests: invalid inputs fail before an adapter can run."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_runtime.core.models import (
    AgentCapabilities,
    CancellationToken,
    ErrorKind,
    ExecutionContext,
    NormalizedError,
    Task,
    TaskResult,
    TaskStatus,
)


def test_task_copies_mutable_inputs_and_resolves_workspace(tmp_path):
    constraints = ["read only"]
    requested = {"read_workspace"}
    task = Task(
        id="task-1",
        instructions="Inspect",
        workspace=tmp_path / ".." / tmp_path.name,
        constraints=constraints,
        requested_capabilities=requested,
    )
    constraints.append("mutated")
    requested.add("write_workspace")
    assert task.workspace == tmp_path.resolve()
    assert task.constraints == ("read only",)
    assert task.requested_capabilities == frozenset({"read_workspace"})
    with pytest.raises(FrozenInstanceError):
        task.instructions = "Changed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", ""),
        ("id", " "),
        ("instructions", ""),
        ("instructions", " "),
        ("deadline", datetime(2026, 10, 27)),
    ],
)
def test_task_rejects_invalid_inputs(field, value, tmp_path):
    values = {"id": "task-1", "instructions": "Inspect", "workspace": tmp_path, field: value}
    with pytest.raises((ValueError, TypeError)):
        Task(**values)


def test_task_accepts_timezone_aware_deadline(tmp_path):
    task = Task(
        id="t",
        instructions="Inspect",
        workspace=tmp_path,
        deadline=datetime(2026, 10, 27, tzinfo=UTC),
    )
    assert task.deadline.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("status", [TaskStatus.CREATED, TaskStatus.RUNNING])
def test_result_rejects_nonterminal_status(status):
    with pytest.raises(ValueError):
        TaskResult(task_id="t", status=status)


@pytest.mark.parametrize(
    "status,kind",
    [
        (TaskStatus.FAILED, ErrorKind.ADAPTER_ERROR),
        (TaskStatus.CANCELLED, ErrorKind.CANCELLED),
        (TaskStatus.TIMED_OUT, ErrorKind.TIMEOUT),
    ],
)
def test_unsuccessful_result_requires_error(status, kind):
    with pytest.raises(ValueError):
        TaskResult(task_id="t", status=status)
    error = NormalizedError(kind=kind, message="Safe diagnostic")
    assert TaskResult(task_id="t", status=status, normalized_error=error).normalized_error == error


def test_success_cannot_carry_error():
    with pytest.raises(ValueError):
        TaskResult(
            task_id="t",
            status=TaskStatus.SUCCEEDED,
            normalized_error=NormalizedError(ErrorKind.ADAPTER_ERROR, "failed"),
        )


def test_result_copies_metadata_and_paths():
    metadata = {"source": "fake"}
    paths = [Path("report.txt")]
    result = TaskResult(
        task_id="t",
        status=TaskStatus.SUCCEEDED,
        artifacts=paths,
        changed_files=paths,
        raw_metadata=metadata,
    )
    metadata["source"] = "mutated"
    paths.clear()
    assert result.artifacts == (Path("report.txt"),)
    assert result.changed_files == (Path("report.txt"),)
    assert result.raw_metadata["source"] == "fake"
    with pytest.raises(TypeError):
        result.raw_metadata["source"] = "mutated"


def test_result_rejects_non_string_metadata():
    with pytest.raises(TypeError):
        TaskResult(task_id="t", status=TaskStatus.SUCCEEDED, raw_metadata={"nested": []})


def test_result_rejects_mistyped_status_and_error():
    with pytest.raises(TypeError):
        TaskResult(task_id="t", status="succeeded")
    with pytest.raises(TypeError):
        TaskResult(task_id="t", status=TaskStatus.FAILED, normalized_error="failed")


def test_capabilities_and_permissions_are_explicit_and_copied():
    operations = {"read_workspace"}
    grants = {"read_workspace"}
    capabilities = AgentCapabilities(operations=operations)
    context = ExecutionContext(permissions=grants)
    operations.clear()
    grants.clear()
    assert capabilities.operations == frozenset({"read_workspace"})
    assert context.permissions == frozenset({"read_workspace"})
    assert ExecutionContext().permissions == frozenset()
    assert context.trace_id


@pytest.mark.asyncio
async def test_cancellation_tokens_are_independent_and_idempotent():
    first, second = CancellationToken(), CancellationToken()
    first.cancel()
    first.cancel()
    await first.wait()
    assert first.is_cancelled
    assert not second.is_cancelled
    assert ExecutionContext().cancellation is not ExecutionContext().cancellation
