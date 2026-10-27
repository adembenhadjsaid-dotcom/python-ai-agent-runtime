# Python AI Agent Runtime Framework

M1: core contracts and minimal runtime. A reusable Python library that runs a
typed task through a harness-neutral adapter. The included Fake Agent is fully
offline: no model keys, network access or external agent binaries are needed.

Python 3.11+; **no runtime dependencies**. Package naming and Python version policy
are provisional. License and public API stability remain open; this is not a release.

## Setup

From this directory, using standard Python packaging:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
ruff format --check .
python examples/run_task.py
```

Or with uv (useful when the system Python has no pip):

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python examples/run_task.py
```

Installation downloads development/build tools. Once installed, the runtime,
example and entire test suite run offline. Tests use asyncio event gates for
coordination; only deadline tests wait for short timers.

## Execute a task

```python
import asyncio
from pathlib import Path

from agent_runtime import Runtime, Task
from agent_runtime.adapters.fake import FakeAgent


async def main():
    task = Task(id="inspect-1", instructions="Inspect this project", workspace=Path.cwd())
    result = await Runtime().execute(task, FakeAgent(), timeout=5)
    print(result.status.value, result.summary, result.trace_id)


asyncio.run(main())
```

The Fake Agent simulates execution without inspecting or changing the workspace.
It can return a supplied TaskResult, raise an exception, or wait on an asyncio.Event:
`FakeAgent(result=...)`, `FakeAgent(error=...)`, `FakeAgent(gate=...)`.

## Architecture

```text
Application
    → Runtime.execute(task, adapter, *, context=None, timeout=None)
        → AgentAdapter.execute(task, context) → TaskResult
            ├── FakeAgent               (implemented)
            ├── independent ACP adapter (M2)
            └── independent CLI relay   (M3, Delegate Skills)
```

- `core/models.py`: frozen Task, TaskResult, AgentCapabilities, ExecutionContext,
  statuses and normalized errors. Collection inputs are copied to immutable
  containers. CancellationToken is an intentionally mutable, one-way signal.
- `core/protocols.py`: structural AgentAdapter interface, with name, capabilities
  and async execute. Adapters need no inheritance and own their per-call resources.
- `core/runtime.py`: task lifecycle, timing, deadlines, cancellation, safe exception
  normalization and correlated standard-library logs.
- `adapters/fake.py`: deterministic testing adapter.
- `core/cleanup.py`: `finish_cleanup`, a cancellation-resistant await for adapter
  resource teardown.

Core imports no SDKs, CLIs or concrete adapters. ACP and Delegate Skills will be
separate adapter modules implementing this same interface, with no dependence on
each other. Discovery, optional streaming, session protocols, delegation and
review services are deferred.

Each execution has `created → running → succeeded | failed | cancelled | timed_out`.
Pre-cancelled and expired tasks go directly from created to their terminal status.
There is no queue and no externally mutable state machine. Each call emits one
terminal lifecycle log and returns a TaskResult, except native caller cancellation.
The runtime stores no shared run state and does not retry.

TaskResult includes task ID, status, summary, artifacts, changed files,
normalized_error, raw_metadata, trace ID, UTC timestamps and monotonic elapsed
seconds. Runtime replaces adapter timing/correlation with its own values.
Paths in output are inert references, not verified artifacts or approved changes.
Diagnostic metadata is an immutable string-to-string mapping.

## Deadlines, cancellation and cleanup

`Task.deadline` is an absolute timezone-aware datetime; `timeout` is a finite,
nonnegative duration in seconds. The earlier applies. Zero timeout or an expired
deadline returns timed_out without launching the adapter.

To request cancellation and receive a typed result:

```python
from agent_runtime import ExecutionContext

context = ExecutionContext()
execution = asyncio.create_task(Runtime().execute(task, adapter, context=context))
context.cancellation.cancel()
result = await execution  # TaskStatus.CANCELLED
```

If the caller cancels the asyncio task with `execution.cancel()`, Runtime awaits
adapter cleanup and re-raises CancelledError. It also signals the context token.
Cancellation wins if observed in the same wait cycle as completion.
Use a fresh context/token for an independent execution; tokens cannot be reset.
All cancellation APIs belong to the execution's event-loop thread.

Adapters must release resources in try/finally, await subprocess/session teardown,
and propagate CancelledError. Asynchronous teardown must use `finish_cleanup` (or
an equivalent cancellation-resistant pattern), since cancellation can arrive after
normal execution or token handling has already entered finally:

```python
from agent_runtime import finish_cleanup


async def execute(self, task, context):
    resource = await self.open_resource(task)
    try:
        return await self.run_with_resource(resource, task, context)
    finally:
        await finish_cleanup(resource.aclose())
```

This is an adapter-author pattern; open_resource/run_with_resource are the adapter's
own implementation. Shielding alone is insufficient: the helper also awaits teardown
before propagating cancellation. Synchronous cleanup needs no helper.
Runtime cancels and awaits the execution coroutine and its cancellation waiter.
Repeated caller cancellation is shielded while draining them. A timeout bounds
the execution coroutine; cleanup may take longer.

An adapter that blocks the event loop or never finishes cleanup cannot be stopped
reliably by an in-process runtime. M1 assumes cooperative adapters. Real transport
adapters must add their own bounded process/session teardown strategies in M2/M3.
A success returned while suppressing runtime cancellation cannot override the
runtime's timeout/cancel outcome.

Exceptions become failed/adapter_error results with generic safe text; an adapter's
own TimeoutError is an adapter failure, not proof of the runtime deadline expiring.
Malformed or mismatched results become failed/invalid_result. Cleanup exceptions
arising while Runtime stops an unfinished execution after timeout/token cancellation
become failed/adapter_error, with
`raw_metadata["interrupted_status"]` retaining the interrupted status. Caller
cancellation still propagates and its terminal log flags cleanup failure.
An execution exception already completed alongside token cancellation is not
classified as a cleanup failure; cancellation takes precedence in that cycle.

## Logs and permission boundary

The library configures no handlers. Enable INFO logging for `agent_runtime` to
receive records with `task_id`, `trace_id`, `adapter`, `status`, `timestamp`
and `cleanup_failed`. Runtime never logs instructions, outputs, metadata,
permission grants, exception messages or tracebacks. Use non-secret IDs/names.
Adapters must sanitize their explicit summaries, normalized errors and metadata;
M1 does not claim to identify arbitrary secrets in caller-supplied data.

ExecutionContext defaults to no permission grants. M1 executes no tools, shells,
network operations, commits, pushes or deletions. Permission labels, constraints,
requested capabilities and resolved workspace paths are data: they do not provide
an OS sandbox or enforce tool policy on arbitrary Python code. Policy enforcement
and scoped tool/MCP execution belong to M5; actual process isolation remains open.

## Project documentation and next work

All six source documents were read completely through the connected Drive plugin.
Local snapshots with source links are in [docs/sources](docs/sources/).
[Decision record](docs/decisions.md) distinguishes accepted decisions, proposals,
provisional implementation choices and open questions.
[Implementation plan](docs/superpowers/plans/2026-10-27-m1-core.md) tracks this M1 scope.

Remaining: M2 ACP SDK/agent verification and adapter; M3 pinned Delegate Skills
relay and supervisor; M4 serial delegation/review; M5 MCP/permission policy;
M6 recovery/isolation/retries/parallelism; M7 stable public API and release.
Before public release, decide license, naming and support policy. Before real
execution, validate upstream compatibility, permissions and transport cleanup.

[Verification record](docs/verification.md) records actual local test/tool results.
The included GitHub Actions workflow runs tests, lint and formatting checks on
Python 3.11 and 3.14 after pushes and pull requests.
