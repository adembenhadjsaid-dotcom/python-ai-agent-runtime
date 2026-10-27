"""Run with: python examples/run_task.py (after installing the package)."""

import asyncio
import logging
from pathlib import Path

from agent_runtime import ExecutionContext, Runtime, Task
from agent_runtime.adapters.fake import FakeAgent


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(task_id)s %(trace_id)s %(adapter)s %(status)s",
    )
    task = Task(id="example-1", instructions="Demonstrate the runtime", workspace=Path.cwd())
    result = await Runtime().execute(
        task, FakeAgent(), context=ExecutionContext(trace_id="example-trace"), timeout=5
    )
    print(f"{result.status.value}: {result.summary}")


if __name__ == "__main__":
    asyncio.run(main())
