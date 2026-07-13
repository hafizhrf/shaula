"""
Persistent run-history log (SQLite, stdlib only).

Unlike `task_store` (in-memory live registry for busy-guards / kill-all, wiped on
restart), this is a durable record of every Claude/Shaula execution so runs stay
debuggable after the fact. One row per turn; rows are keyed by `task_id` and carry the
stable `session_id` (which also names the on-disk Claude transcript and the thread title)
so a Discord thread → journald logs → DB row is one lookup.

Everything here is fail-safe: a DB error is logged and swallowed. This is a debug aid,
never on the critical path — a broken DB must never stop a task from running. SQLite
calls are blocking, so they run in a worker thread via `asyncio.to_thread`.
"""
import asyncio
import logging
import os
import sqlite3
from datetime import datetime

import config

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    task_id           TEXT PRIMARY KEY,
    session_id        TEXT,
    channel_id        INTEGER,
    origin_channel_id INTEGER,
    description       TEXT,
    state             TEXT,
    risk_level        TEXT,
    account           TEXT,
    turn              INTEGER,
    cost_usd          REAL,
    error_text        TEXT,
    created_at        TEXT,
    finished_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
"""

_COLUMNS = [
    "task_id", "session_id", "channel_id", "origin_channel_id", "description",
    "state", "risk_level", "account", "turn", "cost_usd", "error_text",
    "created_at", "finished_at",
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.RUN_DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def init_db() -> None:
    """Create the data dir + table (idempotent). Call once on startup. Fail-safe."""
    try:
        os.makedirs(os.path.dirname(config.RUN_DB_PATH), exist_ok=True)
        with _connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            # A fresh process means nothing is actually executing — any row still marked
            # RUNNING is a leftover from a crash or a mid-task restart (e.g. the bot being
            # restarted while a task ran). Reconcile so it doesn't orphan as RUNNING forever.
            cur = conn.execute(
                "UPDATE runs SET state='INTERRUPTED', finished_at=? WHERE state='RUNNING'",
                (_now(),),
            )
            if cur.rowcount:
                logger.info("Reconciled %d orphaned RUNNING run(s) → INTERRUPTED", cur.rowcount)
        logger.info("Run-history DB ready at %s", config.RUN_DB_PATH)
    except Exception as e:
        logger.error("run_store.init_db failed: %s", e)


# ── blocking workers (run inside asyncio.to_thread) ───────────────────────────

def _start_sync(row: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(task_id, session_id, channel_id, origin_channel_id, description, state, "
            " risk_level, account, turn, cost_usd, error_text, created_at, finished_at) "
            "VALUES (:task_id, :session_id, :channel_id, :origin_channel_id, :description, "
            " :state, :risk_level, :account, :turn, :cost_usd, :error_text, :created_at, NULL)",
            row,
        )


def _finish_sync(task_id: str, state: str, cost_usd: float, error_text: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE runs SET state=?, cost_usd=?, error_text=?, finished_at=? WHERE task_id=?",
            (state, cost_usd, error_text, _now(), task_id),
        )


def _recent_sync(limit: int) -> list[dict]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]


def _get_sync(id_prefix: str) -> list[dict]:
    like = id_prefix + "%"
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM runs WHERE task_id LIKE ? OR session_id LIKE ? "
            "ORDER BY turn ASC, created_at ASC",
            (like, like),
        )
        return [dict(r) for r in cur.fetchall()]


# ── async public API (all fail-safe) ──────────────────────────────────────────

async def start_run(task, session_id, channel_id, origin_channel_id, account, turn) -> None:
    row = {
        "task_id": task.task_id,
        "session_id": session_id,
        "channel_id": channel_id,
        "origin_channel_id": origin_channel_id,
        "description": (task.description or "")[:2000],
        "state": "RUNNING",
        "risk_level": getattr(task.risk_level, "value", str(task.risk_level)),
        "account": account,
        "turn": turn,
        "cost_usd": 0.0,
        "error_text": "",
        "created_at": _now(),
    }
    try:
        await asyncio.to_thread(_start_sync, row)
    except Exception as e:
        logger.error("run_store.start_run failed for %s: %s", task.task_id[:8], e)


async def finish_run(task_id: str, state: str, cost_usd: float, error_text: str) -> None:
    try:
        await asyncio.to_thread(_finish_sync, task_id, state, cost_usd or 0.0, error_text or "")
    except Exception as e:
        logger.error("run_store.finish_run failed for %s: %s", task_id[:8], e)


async def recent(limit: int = 10) -> list[dict]:
    try:
        return await asyncio.to_thread(_recent_sync, max(1, min(limit, 25)))
    except Exception as e:
        logger.error("run_store.recent failed: %s", e)
        return []


async def get(id_prefix: str) -> list[dict]:
    if not id_prefix:
        return []
    try:
        return await asyncio.to_thread(_get_sync, id_prefix.strip())
    except Exception as e:
        logger.error("run_store.get failed: %s", e)
        return []
