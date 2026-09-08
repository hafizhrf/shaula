"""
Track Claude account rate-limit windows (5-hour + weekly).

Source of truth = the `rate_limit_event` emitted in Claude Code's stream-json output.
It exposes per-window {status, resetsAt, rateLimitType} but NOT an exact remaining count
(the detailed % bars only exist in the interactive `/usage` TUI). We cache whatever flows
by during normal task runs (free) and only fire a cheap probe request when the cache is stale.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import config
from services.process_cleanup import communicate_with_timeout

logger = logging.getLogger(__name__)

_WIB = timezone(timedelta(hours=7))
_LABELS = {
    "five_hour": "5 jam",
    "seven_day": "Mingguan",
    "seven_day_oauth": "Mingguan",
    "seven_day_opus": "Mingguan (Opus)",
}

# rateLimitType -> {"status", "resetsAt", "seen" (monotonic), "wall" (epoch)}
_cache: dict[str, dict] = {}


def record(info: dict) -> None:
    """Store a rate_limit_info dict seen in any stream-json output (called for free during tasks)."""
    if not info:
        return
    rt = info.get("rateLimitType")
    if not rt:
        return
    _cache[rt] = {
        "status": info.get("status"),
        "resetsAt": info.get("resetsAt"),
        "seen": time.monotonic(),
        "wall": time.time(),
    }


def snapshot_age() -> float | None:
    """Seconds since the freshest cached window, or None if cache is empty."""
    if not _cache:
        return None
    return time.monotonic() - max(v["seen"] for v in _cache.values())


def _fmt_reset(ts) -> str:
    try:
        ts = int(float(ts))
    except (ValueError, TypeError):
        return ""
    clock = datetime.fromtimestamp(ts, _WIB).strftime("%d %b %H:%M WIB")
    mins = max(0, round((ts - time.time()) / 60))
    if mins >= 60:
        rel = f"±{mins // 60} jam {mins % 60} menit lagi"
    else:
        rel = f"±{mins} menit lagi"
    return f"{clock} ({rel})"


def format_report() -> str | None:
    """Human-readable status of each known window, or None if nothing cached yet."""
    if not _cache:
        return None
    lines = []
    for rt in sorted(_cache):
        v = _cache[rt]
        label = _LABELS.get(rt, rt.replace("_", " "))
        ok = v.get("status") == "allowed"
        icon = "✅" if ok else "🔴"
        state = "masih bisa dipakai" if ok else "udah abis"
        reset = _fmt_reset(v.get("resetsAt"))
        lines.append(f"{icon} **{label}**: {state}" + (f" — reset {reset}" if reset else ""))
    if not any("day" in rt or "week" in rt for rt in _cache):
        lines.append("🗓️ *Window mingguan belum dilaporin API (biasanya baru muncul pas udah mendekati batas).*")
    age = snapshot_age()
    if age and age > 90:
        lines.append(f"\n_(data ~{round(age / 60)} menit lalu)_")
    return "\n".join(lines)


async def probe(timeout: int = 60) -> bool:
    """Fire a tiny request just to capture fresh rate_limit_event(s). Returns True if any seen."""
    cmd = [
        config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose",
        "--max-budget-usd", "0.10", "ok",
    ]
    env = {**os.environ}
    if config.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd="/tmp",
            env=env,
            start_new_session=True,
        )
        out, _ = await communicate_with_timeout(proc, timeout=timeout, label="Claude limit probe")
    except Exception as e:
        logger.warning("claude_limits probe failed: %s", e)
        return False
    found = False
    for line in out.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "rate_limit_event":
            record(event.get("rate_limit_info", {}))
            found = True
    return found
