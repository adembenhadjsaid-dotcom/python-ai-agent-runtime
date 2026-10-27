# M1 scope and decision record

27 October 2026. All six version 0.1 project documents were read completely
using the connected Google Drive plugin before implementation. Source copies
and URLs are in [sources/](sources/). Drive originals were not edited.

## Accepted project decisions

D-001 Python; D-002 a general-purpose runtime with coding agents the initial
priority; D-003 harness-agnostic adapters; D-004 modular agent/MCP integrations.
Also accepted: explicit typed boundaries, least privilege, reliable lifecycle,
testability and minimal dependencies. The user's instruction authorizes M1
and independently isolates ACP and Delegate Skills.

## Proposals in the source documents

D-005 ACP SDK as the first real transport; D-006 optional Delegate Skills
integration; D-007 execution separate from review/approval. Detailed signatures,
capability categories, source tree and lifecycle names are sketches, not a
previously accepted public API. The roadmap is proposed, but the user explicitly
selected its M1 ticket. This implementation does not promote M2–M7 to accepted work.

## Provisional engineering choices for this implementation

These are builder choices to make M1 runnable, not newly accepted project decisions.

- Python >=3.11, standard-library asyncio, frozen dataclasses and Protocol.
- Local distribution name agent-runtime / import agent_runtime; no public release
  or compatibility guarantee. License remains OPEN; no license is invented.
- src layout, setuptools, pytest/pytest-asyncio and Ruff as development tools.
  No runtime dependencies.
- One in-memory execution per call, no durable runs, sessions, queue, registry,
  retry engine or persistence. Runtime can be reused; it stores no shared task state.
- created → running → one terminal status; a pre-cancelled or expired task skips
  running. queued is omitted because scheduling is explicitly deferred.
- Adapter execute(task, context) owns resources in try/finally. Runtime cancels and
  awaits it on timeout/cancellation; no separate global close() that could close a
  reusable adapter mid-run. Cancellation-suppressing adapters are unsupported.
  Timeouts bound execution, not cleanup time; cleanup must finish before return.
- Review refinement: asynchronous adapter cleanup must use finish_cleanup (or an
  equivalent cancellation-resistant pattern) inside finally. A plain await in finally
  can be interrupted if token cancellation or natural completion began teardown before
  Runtime injects CancelledError. This small helper shields AND awaits teardown; the
  runtime cannot protect a resource boundary an arbitrary adapter does not expose.
- A cleanup exception after token cancellation/timeout becomes a failed result
  with safe generic text and interrupted_status metadata. Native caller cancellation
  still propagates, with a cleanup_failed flag in the terminal lifecycle log.
  Only failures after the runtime interrupts an unfinished execution count as stopping
  failures; a simultaneous already-finished execution error loses to cancellation.
- Explicit context cancellation returns a cancelled TaskResult. Native caller
  Task.cancel() propagates CancelledError after cleanup, preserving asyncio semantics.
  Cancellation wins when observed in the same wait cycle as completion.
- Optional relative timeout plus timezone-aware absolute Task.deadline; the earlier
  applies. Expired deadlines and zero timeout never launch the adapter.
- TaskResult contains terminal status only, a matching task_id, normalized_error,
  inert artifact/changed-file paths, string diagnostic metadata and runtime timing.
  Invalid results fail closed. Exception text is excluded from results and logs
  because it may contain secrets; adapters own sanitizing any explicit result text.
- ExecutionContext carries trace ID, an explicit cancellation token and empty
  permission grants by default. M1 executes no tools, commands or external transports;
  these fields are data, not an authorization engine or an OS sandbox.
- Structured standard-library logging records only task_id, trace_id, adapter name,
  state and timestamp. Names/IDs must be non-secret correlation labels.
- No placeholder ACP or Delegate CLI implementation: each future module will
  independently implement AgentAdapter. Neither transport imports the other.

If these defaults change, update this record and affected contracts first.

## Open questions and remaining milestones

Package branding, license, API stability, Python support policy, tasks vs durable
runs/sessions, event persistence/schema stability, discovery/negotiation, interactive
vs durable approvals, cancellation of real subprocesses, pinned Delegate Skills
revision/commands, recursive delegation, MCP permissions/transport, isolation,
credentials and telemetry remain OPEN.

M2: verify/pin ACP SDK and one compatible agent, then implement sessions and teardown.
M3: independently verify/pin Delegate Skills CLI, supervise processes, normalize output.
M4: serial delegation and explicit review gates. M5: permission-enforced tools/MCP.
M6: retries with idempotency, recovery, isolation and parallelism. M7: stable API/release.
No real adapter may claim permission enforcement based only on M1 context fields.

## Upstream reference check

The ACP overview, SDK repository/quickstart and Delegate Skills repository were
opened on 27 October 2026. No upstream API or version is pinned or implemented by M1;
compatibility and exact commands must be rechecked in M2/M3.
