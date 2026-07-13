"""
Relay Discord messages as stdin to waiting subprocesses.

Flow:
  1. Subprocess prints a prompt ("Enter code:", "yes/no", etc.)
  2. claude_runner calls wait_for_input(channel_id) — suspends the coroutine
  3. Emilia sends a Discord message asking for input
  4. User types; on_message calls provide_input(channel_id, text) — resolves the Future
  5. claude_runner writes text to proc.stdin and continues
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

# channel_id → asyncio.Future[str]
_futures: dict[int, asyncio.Future] = {}


def is_waiting(channel_id: int) -> bool:
    return channel_id in _futures


async def wait_for_input(channel_id: int, timeout: float = 300.0) -> str:
    """Block the calling coroutine until the user provides input via Discord (5 min timeout)."""
    loop = asyncio.get_event_loop()
    fut: asyncio.Future[str] = loop.create_future()
    _futures[channel_id] = fut
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("stdin_relay timeout for channel %d", channel_id)
        raise
    finally:
        _futures.pop(channel_id, None)


def provide_input(channel_id: int, text: str) -> bool:
    """Called from on_message. Returns True if there was a waiting process."""
    fut = _futures.get(channel_id)
    if fut and not fut.done():
        fut.set_result(text)
        return True
    return False


def cancel(channel_id: int) -> bool:
    """Cancel a waiting relay (e.g. user typed 'cancel')."""
    fut = _futures.pop(channel_id, None)
    if fut and not fut.done():
        fut.cancel()
        return True
    return False
