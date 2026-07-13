"""
Intent router using local Ollama (Hermes).
Classifies user messages before hitting Claude Code, so simple chat/questions
don't waste Claude budget.
"""
import json
import logging
import urllib.request
import urllib.error
from enum import Enum
from typing import Optional

import config

logger = logging.getLogger(__name__)


class Intent(Enum):
    CHAT = "chat"           # casual question, general info → answer locally
    CODE = "code"           # coding task → send to Claude Code
    DEPLOY = "deploy"       # deployment request → /deploy flow
    MONITOR = "monitor"     # server/docker status question → monitoring tools
    UNKNOWN = "unknown"     # fallback → Claude Code


ROUTER_PROMPT = """\
You are an intent classifier for a DevOps Discord bot. Classify the user message into exactly one of:
- chat: general conversation, questions, greetings, non-technical requests
- code: coding tasks, file editing, script writing, debugging
- deploy: deploying applications, Cloudflare, VPS, Docker Compose
- monitor: checking server health, memory, CPU, disk, Docker container status

Respond with ONLY a JSON object: {"intent": "<one of the above>", "confidence": <0.0-1.0>}

User message: {message}
"""


def _is_ollama_running() -> bool:
    try:
        req = urllib.request.urlopen(f"{config.OLLAMA_HOST}/api/tags", timeout=2)
        return req.status == 200
    except Exception:
        return False


async def classify_intent(message: str) -> tuple[Intent, float]:
    """Returns (Intent, confidence). Falls back to UNKNOWN if Ollama unavailable."""
    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _blocking_classify(message))


def _blocking_classify(message: str) -> tuple[Intent, float]:
    if not _is_ollama_running():
        logger.debug("Ollama not running — defaulting to UNKNOWN intent")
        return Intent.UNKNOWN, 0.0

    payload = json.dumps({
        "model": config.OLLAMA_MODEL,
        "prompt": ROUTER_PROMPT.format(message=message[:500]),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 64},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{config.OLLAMA_HOST}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            raw = result.get("response", "{}")
            parsed = json.loads(raw)
            intent_str = parsed.get("intent", "unknown").lower()
            confidence = float(parsed.get("confidence", 0.5))
            intent = Intent(intent_str) if intent_str in Intent._value2member_map_ else Intent.UNKNOWN
            return intent, confidence
    except Exception as e:
        logger.warning("Ollama classification failed: %s", e)
        return Intent.UNKNOWN, 0.0


async def local_chat(message: str) -> Optional[str]:
    """Handle simple chat locally without Claude Code."""
    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _blocking_chat(message))


def _blocking_chat(message: str) -> Optional[str]:
    if not _is_ollama_running():
        return None

    payload = json.dumps({
        "model": config.OLLAMA_MODEL,
        "prompt": message,
        "stream": False,
        "system": (
            "You are a helpful DevOps assistant in a Discord server. "
            "Be concise and direct. Format responses for Discord (use markdown)."
        ),
        "options": {"temperature": 0.7, "num_predict": 512},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{config.OLLAMA_HOST}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result.get("response", "").strip()
    except Exception as e:
        logger.warning("Ollama chat failed: %s", e)
        return None
