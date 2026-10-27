"""Immutable domain data; mutable cancellation is an explicit shared signal."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4


def _text(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _labels(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise TypeError(f"{field_name} must be a collection of strings")
    copied = tuple(values)
    for value in copied:
        _text(value, field_name)
    return copied


def _aware(value: datetime | None, field_name: str) -> None:
    if value is not None:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must include a timezone")


class TaskStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self not in (TaskStatus.CREATED, TaskStatus.RUNNING)


class ErrorKind(StrEnum):
    ADAPTER_ERROR = "adapter_error"
    INVALID_RESULT = "invalid_result"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class NormalizedError:
    kind: ErrorKind
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ErrorKind):
            raise TypeError("kind must be ErrorKind")
        _text(self.message, "message")


class CancellationToken:
    """One-way cooperative signal, used only on its execution's asyncio loop."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    instructions: str
    workspace: Path
    constraints: tuple[str, ...] = ()
    requested_capabilities: frozenset[str] = frozenset()
    deadline: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.id, "id")
        _text(self.instructions, "instructions")
        _aware(self.deadline, "deadline")
        object.__setattr__(self, "workspace", Path(self.workspace).resolve())
        object.__setattr__(self, "constraints", _labels(self.constraints, "constraints"))
        object.__setattr__(
            self,
            "requested_capabilities",
            frozenset(_labels(self.requested_capabilities, "requested_capabilities")),
        )


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Advertisements, not grants. Discovery/negotiation is deferred."""

    operations: frozenset[str] = frozenset()
    supports_streaming: bool = False
    supports_cancel: bool = False
    supports_tools: bool = False
    supports_sessions: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "operations", frozenset(_labels(self.operations, "operations")))


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    permissions: frozenset[str] = frozenset()
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _text(self.trace_id, "trace_id")
        if not isinstance(self.cancellation, CancellationToken):
            raise TypeError("cancellation must be CancellationToken")
        object.__setattr__(self, "permissions", frozenset(_labels(self.permissions, "permissions")))


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    status: TaskStatus
    summary: str = ""
    artifacts: tuple[Path, ...] = ()
    changed_files: tuple[Path, ...] = ()
    normalized_error: NormalizedError | None = None
    raw_metadata: Mapping[str, str] = field(default_factory=dict)
    trace_id: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float | None = None

    def __post_init__(self) -> None:
        _text(self.task_id, "task_id")
        if not isinstance(self.status, TaskStatus):
            raise TypeError("status must be TaskStatus")
        if not self.status.is_terminal:
            raise ValueError("TaskResult requires a terminal status")
        error = self.normalized_error
        if error is not None and not isinstance(error, NormalizedError):
            raise TypeError("normalized_error must be NormalizedError")
        if (self.status == TaskStatus.SUCCEEDED) != (error is None):
            raise ValueError("Only successful results may omit normalized_error")
        if self.status == TaskStatus.CANCELLED and error.kind != ErrorKind.CANCELLED:
            raise ValueError("cancelled result requires cancelled error")
        if self.status == TaskStatus.TIMED_OUT and error.kind != ErrorKind.TIMEOUT:
            raise ValueError("timed_out result requires timeout error")
        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string")
        metadata = dict(self.raw_metadata)
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items()):
            raise TypeError("raw_metadata keys and values must be strings")
        object.__setattr__(self, "raw_metadata", MappingProxyType(metadata))
        object.__setattr__(self, "artifacts", tuple(Path(p) for p in self.artifacts))
        object.__setattr__(self, "changed_files", tuple(Path(p) for p in self.changed_files))
        _aware(self.started_at, "started_at")
        _aware(self.finished_at, "finished_at")
