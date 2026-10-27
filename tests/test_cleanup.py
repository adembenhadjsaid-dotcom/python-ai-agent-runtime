"""Cancellation-resistant teardown is an explicit adapter responsibility."""

import asyncio

import pytest

from agent_runtime import finish_cleanup


@pytest.mark.asyncio
async def test_cleanup_finishes_before_repeated_cancellation_propagates():
    started, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def cleanup():
        started.set()
        await release.wait()
        cleaned.set()

    operation = asyncio.create_task(finish_cleanup(cleanup()))
    await asyncio.wait_for(started.wait(), timeout=2)
    operation.cancel()
    await asyncio.sleep(0)  # Yield to deliver the first cancellation, without a timer.
    operation.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=2)
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_cleanup_exception_is_not_hidden():
    async def cleanup():
        raise RuntimeError("resource close failed")

    with pytest.raises(RuntimeError, match="resource close failed"):
        await finish_cleanup(cleanup())


@pytest.mark.asyncio
async def test_self_cancelled_cleanup_propagates_without_hanging():
    async def cleanup():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(finish_cleanup(cleanup()), timeout=2)
