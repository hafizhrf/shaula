import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskState(Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RiskLevel(Enum):
    SAFE = "SAFE"
    MEDIUM = "MEDIUM"
    DANGEROUS = "DANGEROUS"


_DANGEROUS_KEYWORDS = {
    # English
    "rm", "remove", "delete", "drop", "truncate", "format", "destroy",
    "prune", "--force", "wipe", "nuke", "purge", "unlink", "shred",
    "docker system prune", "rm -rf",
    # Indonesian
    "hapus", "hilangkan", "hancurkan", "buang", "bersihkan semua",
    "reset database", "drop table", "drop database",
}
_MEDIUM_KEYWORDS = {
    "npm", "pip", "install", "upgrade", "update", "docker restart",
    "docker stop", "docker start", "git pull", "git push", "git merge",
    "git reset", "migrate", "deploy", "restart",
}


def classify_risk(description: str) -> RiskLevel:
    lower = description.lower()
    for kw in _DANGEROUS_KEYWORDS:
        if kw in lower:
            return RiskLevel.DANGEROUS
    for kw in _MEDIUM_KEYWORDS:
        if kw in lower:
            return RiskLevel.MEDIUM
    return RiskLevel.SAFE


@dataclass
class TaskRecord:
    task_id: str
    description: str
    creator_id: int
    guild_id: int
    channel_id: int
    state: TaskState = TaskState.PENDING
    risk_level: RiskLevel = RiskLevel.SAFE
    plan_text: Optional[str] = None
    diff_text: Optional[str] = None
    output_lines: list = field(default_factory=list)
    project_dir: str = ""
    process_pid: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    session_id: Optional[str] = None
    error_text: str = ""
    # Context size (input tokens) of the last API call this run, from the stream-json
    # `result` usage. Drives Shaula's auto-compaction trigger. 0 = unknown/not captured.
    context_tokens: int = 0


_tasks: dict[str, TaskRecord] = {}


def prune_old_tasks(limit: int = 50) -> None:
    """Keep in-memory task store bounded so long-running bots don't leak RAM."""
    if len(_tasks) <= limit:
        return
    terminal_ids = [
        tid for tid, t in _tasks.items()
        if t.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)
    ]
    to_remove = len(_tasks) - limit
    for tid in terminal_ids[:to_remove]:
        _tasks.pop(tid, None)


def create_task(
    description: str,
    creator_id: int,
    guild_id: int,
    channel_id: int,
    project_dir: str = "",
) -> TaskRecord:
    prune_old_tasks(50)
    task_id = str(uuid.uuid4())
    record = TaskRecord(
        task_id=task_id,
        description=description,
        creator_id=creator_id,
        guild_id=guild_id,
        channel_id=channel_id,
        risk_level=classify_risk(description),
        project_dir=project_dir,
    )
    _tasks[task_id] = record
    return record


def get_task(task_id: str) -> Optional[TaskRecord]:
    return _tasks.get(task_id)


def update_state(task_id: str, state: TaskState) -> None:
    if task_id in _tasks:
        _tasks[task_id].state = state


def list_running() -> list[TaskRecord]:
    return [t for t in _tasks.values() if t.state == TaskState.RUNNING]


def kill_all_running() -> list[int]:
    import psutil
    import signal

    pids = []
    for task in list_running():
        if task.process_pid:
            pids.append(task.process_pid)
            try:
                proc = psutil.Process(task.process_pid)
                for child in proc.children(recursive=True):
                    try:
                        child.send_signal(signal.SIGTERM)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                proc.send_signal(signal.SIGTERM)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            except Exception:
                pass
        update_state(task.task_id, TaskState.CANCELLED)
    return pids
