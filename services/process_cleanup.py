"""Safe lifecycle helpers for subprocesses started by the bot.

Every managed command runs in its own process session.  This lets a timeout or
cancellation stop the command *and* any children it launched, without touching
the bot's own process group.
"""

import asyncio
import logging
import os
import signal

logger = logging.getLogger(__name__)


async def terminate_process_group(
    proc: asyncio.subprocess.Process,
    *,
    label: str,
    grace_seconds: float = 5.0,
) -> None:
    """Stop a subprocess session, escalating from SIGTERM to SIGKILL if needed."""
    if proc.returncode is not None:
        return

    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    except Exception as exc:
        logger.warning("Could not terminate %s (pid %s): %s", label, proc.pid, exc)

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        logger.warning("%s (pid %s) ignored SIGTERM; sending SIGKILL", label, proc.pid)
    except ProcessLookupError:
        return

    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        return
    except Exception as exc:
        logger.warning("Could not kill %s (pid %s): %s", label, proc.pid, exc)
        return

    try:
        await proc.wait()
    except ProcessLookupError:
        pass


async def communicate_with_timeout(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float,
    label: str,
) -> tuple[bytes, bytes]:
    """Collect output until completion, never leaving a timed-out child behind."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await terminate_process_group(proc, label=label)
        # Let asyncio drain the pipes after the process tree exits.  The original
        # timeout/cancellation still propagates to the caller.
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            pass
        raise
