"""Cancellation-resistant asynchronous resource teardown for adapter authors."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def finish_cleanup(cleanup: Awaitable[None]) -> None:
    """Await teardown to completion, then propagate any incoming cancellation.

    Use inside an adapter's finally block. Shielding alone can leave teardown running
    after execute returns; this helper also drains it, including after repeated cancel().
    Cleanup must finish on its own and must not depend on its caller's cancellation.
    Cleanup exceptions propagate to the runtime for safe normalization.
    """
    pending = asyncio.ensure_future(cleanup)
    interrupted: asyncio.CancelledError | None = None
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError as error:
            if pending.cancelled():
                raise
            interrupted = error
    pending.result()
    if interrupted is not None:
        raise interrupted
