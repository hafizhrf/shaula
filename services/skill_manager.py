"""
Dynamic skill manager.
- corrections.json: rules Emilia learns from user feedback, injected into every Hermes prompt
- index.json: registry of skill scripts Emilia has created
"""
import asyncio
import json
import logging
import os

from services.process_cleanup import communicate_with_timeout

logger = logging.getLogger(__name__)

_BASE = os.path.join(os.path.dirname(__file__), '..', 'skills')
_CORRECTIONS_FILE = os.path.join(_BASE, 'corrections.json')
_INDEX_FILE = os.path.join(_BASE, 'index.json')


def _load(path: str, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Corrections ──────────────────────────────────────────────────────────────

def get_corrections() -> list[str]:
    return _load(_CORRECTIONS_FILE, [])


def add_correction(rule: str) -> bool:
    """Append a correction rule. Returns False if duplicate."""
    rules = _load(_CORRECTIONS_FILE, [])
    rule = rule.strip()
    if not rule or rule in rules:
        return False
    rules.append(rule)
    _save(_CORRECTIONS_FILE, rules)
    logger.info("Saved correction: %s", rule)
    return True


def remove_correction(index: int) -> str | None:
    """Remove correction by 1-based index. Returns removed rule or None."""
    rules = _load(_CORRECTIONS_FILE, [])
    if 1 <= index <= len(rules):
        removed = rules.pop(index - 1)
        _save(_CORRECTIONS_FILE, rules)
        return removed
    return None


def list_corrections() -> list[str]:
    return _load(_CORRECTIONS_FILE, [])


# ── Skills ────────────────────────────────────────────────────────────────────

def register_skill(name: str, description: str, filename: str) -> None:
    index = _load(_INDEX_FILE, {})
    index[name] = {"description": description, "file": filename}
    _save(_INDEX_FILE, index)
    logger.info("Registered skill: %s (%s)", name, filename)


def list_skills() -> dict:
    return _load(_INDEX_FILE, {})


def skills_summary() -> str:
    skills = list_skills()
    if not skills:
        return ""
    return "\n".join(f"- {n}: {v['description']}" for n, v in skills.items())


async def run_skill(name: str, args: list[str] | None = None) -> tuple[str, int]:
    index = _load(_INDEX_FILE, {})
    if name not in index:
        return f"Skill '{name}' tidak ditemukan.", 1
    path = os.path.join(_BASE, index[name].get('file', f'{name}.py'))
    if not os.path.exists(path):
        return f"File skill '{path}' tidak ada.", 1
    proc = await asyncio.create_subprocess_exec(
        'python3', path, *(args or []),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    stdout, _ = await communicate_with_timeout(proc, timeout=30, label=f"skill {name}")
    return stdout.decode('utf-8', errors='replace').strip(), proc.returncode
