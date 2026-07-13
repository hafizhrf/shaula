"""
Per-channel persistent Claude Code sessions.

Each Discord channel can hold ONE live Claude session. The first task starts it
(`claude --session-id <uuid>`); follow-up messages continue it (`claude --resume
<uuid>`), preserving full conversation context, until the user stops it or it
goes idle.

Sessions are cheap: we do NOT hold a process open. Each prompt spawns a
short-lived `claude` that exits; continuity comes from the on-disk session
transcript that `--resume` rehydrates (so it even survives a bot restart).
"""
import logging
import os
import time
import uuid

import config

logger = logging.getLogger(__name__)

# Auto-close a session after this many seconds of inactivity.
IDLE_TIMEOUT = int(os.getenv("CLAUDE_SESSION_IDLE_SECONDS", "1800"))  # 30 min


class Session:
    def __init__(self, channel_id: int, project_dir: str):
        self.channel_id = channel_id  # the Discord channel/thread the session lives in
        self.session_id = str(uuid.uuid4())
        self.project_dir = project_dir
        self.started = False          # True once the session exists on disk (after turn 1)
        self.busy = False             # True while a prompt is executing (prevents concurrent --resume)
        self.current_task = None      # the TaskRecord currently executing (so stop can kill its process)
        self.config_dir = None        # CLAUDE_CONFIG_DIR for an alternate account (None = default)
        self.turns = 0
        self.is_thread = False        # True if channel_id is a dedicated session thread
        self.origin_channel_id = None # parent channel the thread was spun off from
        self.active_msg = None        # the latest "🟢 Session aktif" notice (carries the Stop button)
        self.last_context_tokens = 0  # input-token size of the last turn (drives auto-compaction)
        self.last_active = time.monotonic()

    def touch(self) -> None:
        self.last_active = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_active


_sessions: dict[int, Session] = {}


def get(channel_id: int) -> Session | None:
    return _sessions.get(channel_id)


def is_active(channel_id: int) -> bool:
    s = _sessions.get(channel_id)
    return s is not None and s.idle_seconds < IDLE_TIMEOUT


def get_or_start(channel_id: int) -> Session:
    """Return the channel's session, creating it (with a stable project dir) if absent."""
    s = _sessions.get(channel_id)
    if s is None:
        project_dir = os.path.join(config.PROJECTS_BASE_DIR, f"session-{channel_id}")
        os.makedirs(project_dir, exist_ok=True)
        s = Session(channel_id, project_dir)
        _sessions[channel_id] = s
        logger.info("Started Claude session %s for channel %s", s.session_id[:8], channel_id)
    return s


def stop(channel_id: int) -> Session | None:
    s = _sessions.pop(channel_id, None)
    if s:
        logger.info("Stopped Claude session %s for channel %s (%d turns)",
                    s.session_id[:8], channel_id, s.turns)
    return s


def kill_session_task(sess) -> bool:
    """If a prompt is currently running in this session, terminate its Claude process.

    Returns True if a running task was actually killed. Shared by the `stop session`
    text command and the Stop-session button so both behave identically.
    """
    import signal

    from services import task_store

    if not sess or not sess.busy:
        return False
    task = sess.current_task
    if not task or not task.process_pid:
        return False
    try:
        os.kill(task.process_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    task_store.update_state(task.task_id, task_store.TaskState.CANCELLED)
    return True


def sweep_idle() -> list[Session]:
    """Drop sessions idle past IDLE_TIMEOUT. Returns the closed sessions."""
    closed = []
    for cid, s in list(_sessions.items()):
        if not s.busy and s.idle_seconds >= IDLE_TIMEOUT:
            closed.append(_sessions.pop(cid))
    return closed
