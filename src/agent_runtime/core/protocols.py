"""Structural boundary shared by independent ACP, CLI and future adapters."""

from typing import Protocol

from agent_runtime.core.models import AgentCapabilities, ExecutionContext, Task, TaskResult


class AgentAdapter(Protocol):
    @property
    def name(self) -> str:
        """A non-secret correlation label for this adapter."""
        ...

    @property
    def capabilities(self) -> AgentCapabilities:
        """Advertised behavior; never a permission grant."""
        ...

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        """Return a matching terminal result, releasing per-call resources in finally.

        Async finally teardown must use finish_cleanup (or an equivalent pattern):
        a plain await in finally may be interrupted if cancellation arrives mid-cleanup.
        Cancellation must propagate after cleanup. Implementations must not suppress
        CancelledError or leave subprocesses/sessions running after this call ends.
        The runtime cannot enforce an OS boundary on arbitrary Python adapters.
        """
        ...
