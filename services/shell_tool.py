"""Central shell-command policy + execution for Emilia.

One source of truth for: running a command, the strict safe-check (untrusted /
Hermes-emitted commands), and the fatal-check (catastrophic commands that are
blocked for EVERY command, even ones marked `trusted`).

Per Apis's full-auto preference, `is_fatal` only blocks truly catastrophic
operations — a targeted `rm -rf /tmp/foo` is allowed; `rm -rf /` is not.
"""

import asyncio
import re

from services.process_cleanup import communicate_with_timeout

# --- Strict gate: applied to UNTRUSTED commands (Hermes emitted them) ----------
# Block shell injection / chaining metacharacters and known dangerous verbs.
_SHELL_INJECT = frozenset("|>&;$`!")
_SHELL_BLOCKED_RE = re.compile(
    r'\b(rm|rmdir|dd|mkfs|fdisk|parted|shred|wipefs|poweroff|reboot|shutdown|init|'
    r'passwd|useradd|userdel|visudo|mkswap|format)\b',
    re.IGNORECASE,
)


def is_safe(cmd: str) -> bool:
    """Blacklist gate for untrusted commands: block metacharacters + dangerous verbs."""
    cmd = cmd.strip()
    if not cmd:
        return False
    if any(c in cmd for c in _SHELL_INJECT):
        return False
    if _SHELL_BLOCKED_RE.search(cmd):
        return False
    return True


# --- Fatal gate: applied to ALL commands, even trusted ones --------------------
# Only genuinely catastrophic patterns. Targeted deletes (rm -rf /tmp/x) pass.
_FATAL_PATTERNS = (
    # rm targeting root / home / critical system dirs (with or without trailing slash/glob)
    re.compile(r'\brm\b[^\n]*\s(-[a-z]*\s+)*(/|/\*|~|\$HOME|/etc|/var|/usr|/boot|/home|/lib|/bin|/sbin|/root)(/\*|/)?(\s|$)', re.IGNORECASE),
    # disk / filesystem destroyers
    re.compile(r'\b(dd|mkfs\w*|wipefs|shred|fdisk|parted|mkswap|format)\b', re.IGNORECASE),
    # writing to a raw block device
    re.compile(r'(>\s*/dev/sd|of=/dev/)', re.IGNORECASE),
    # fork bomb
    re.compile(r':\(\)\s*\{'),
    # recursive chmod/chown on root
    re.compile(r'\bch(mod|own)\b[^\n]*\s-R[^\n]*\s/(\s|$|\*)', re.IGNORECASE),
    # power state changes
    re.compile(r'\b(poweroff|reboot|shutdown|halt)\b', re.IGNORECASE),
    re.compile(r'\binit\s+[06]\b', re.IGNORECASE),
)


def is_fatal(cmd: str) -> bool:
    """True if the command is catastrophic — blocked even when marked trusted."""
    cmd = (cmd or "").strip()
    if not cmd:
        return False
    return any(p.search(cmd) for p in _FATAL_PATTERNS)


# --- Execution -----------------------------------------------------------------
async def run(cmd: str, timeout: int = 10) -> tuple[str, int]:
    """Run a shell command, returning (combined_output, returncode)."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    stdout, _ = await communicate_with_timeout(proc, timeout=timeout, label="shell command")
    return stdout.decode("utf-8", errors="replace").strip(), proc.returncode
