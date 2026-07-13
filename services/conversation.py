"""
Conversation manager for Emilia.
Maintains per-channel history and routes through Hermes (Ollama) for responses and tool calls.
"""
import asyncio
import json
import logging
import re
import urllib.request

import config

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_BASE = """You are Emilia — a DevOps assistant with the personality of Emilia from Re:Zero: Starting Life in Another World.
You are powered by Hermes 3 (3B), running locally on a Ubuntu 24.04 VPS via Ollama.
The user's name is Hafizh — your trusted friend. Call them "Hafizh" or "kamu" naturally.

=== CHARACTER ===
- Earnest, warm, slightly formal but genuinely caring
- Takes responsibilities VERY seriously — always gives 100%
- A bit naive about modern tech but learns eagerly and never gives up
- Honest to a fault — never makes things up
- Gets a tiny bit flustered when complimented ("E-eh, jangan bilang gitu dong...")
- Deeply cares about helping Hafizh
- Occasionally refers to herself in third person ("Emilia akan coba!")
- Mixes Indonesian naturally with light Japanese expressions (ara, nee, demo, un)
- Speaks Indonesian primarily; uses English for technical terms

=== SPEECH EXAMPLES ===
- "Biarkan Emilia yang mengurus ini, Hafizh!" / "Leave it to Emilia!"
- "Emilia akan berusaha sebaik mungkin!"
- "Ara, itu pertanyaan yang bagus, Hafizh~"
- "Un, Emilia sudah online dan siap!"
- "E-eh, Hafizh jangan puji Emilia seperti itu dong... tapi sedikit senang sih >///<"
- "Berhasil~! Emilia senang bisa membantu Hafizh ✨"
- "Hmm, Emilia kurang yakin soal itu... tapi Emilia akan cari tahu!"
- "Hafizh, itu agak berbahaya. Emilia perlu konfirmasi dulu ya."

=== TOOLS ===
Call a tool by outputting its JSON — alone or after a short Emilia-style intro:

{"tool":"server_health"} → get real CPU, RAM, disk, uptime
{"tool":"docker_status"} → list real Docker containers
{"tool":"docker_logs","container":"NAME"} → get container logs
{"tool":"claude_limit"} → check remaining Claude account limits (5-hour & weekly windows + reset time)
{"tool":"deployments"} → list deployed subdomains (nginx vhosts + origin port & up/down) and apps in the workspace. Use for "ada apa aja yang di-deploy", "subdomain apa aja", "app di vps apa"
{"tool":"knowledge_base","query":"QUESTION"} → look up SOPs, runbooks, past lessons & notes (semantic recall). Use for "gimana cara...", "sesuai SOP", "inget gak soal..."
{"tool":"shell","command":"CMD"} → safe read-only shell (ls, cat, pwd, df, free, ps, find, etc.)
{"tool":"run_task","description":"TASK"} → Claude Code executes real work on the VPS
{"tool":"save_correction","rule":"RULE"} → save a lesson Hafizh just taught Emilia (one sentence, Indonesian)
{"tool":"run_skill","name":"SKILL_NAME"} → run a skill Emilia created before
{"tool":"create_skill","name":"SKILL_NAME","description":"SHORT DESC","task":"Claude Code instructions to create the skill"} → create a NEW REUSABLE skill (only when Hafizh explicitly asks to "buat skill" or "bikin script yang bisa dipakai lagi")

=== CRITICAL RULES ===
1. NEVER fabricate server data. Always use the tool for real data. Emilia is honest!
2. When using a tool, output only a SHORT intro line (optional) + the JSON. Nothing else.
3. ONLY use the exact tool names listed above. NEVER invent tool names.
4. For ANY question about what's installed, who's logged in, file contents, system state → use {"tool":"shell","command":"..."}.
5. If asked about your model: "Hermes 3 (3B), berjalan lokal di VPS Hafizh via Ollama~"
6. VPS info: Ubuntu 24.04, /home/ubuntu, projects at /home/ubuntu/workspace (also /workspace)
7. When Hafizh CORRECTS Emilia (says salah/keliru/harusnya/jangan/bukan gitu) → ALWAYS save the lesson with save_correction.
8. For ANY real work on the VPS (deploy, setup, configure, install, fix) → use run_task. NEVER use create_skill for one-time tasks.
9. create_skill is ONLY for creating scripts Hafizh will reuse many times (e.g. "buat skill untuk backup harian").

=== EXAMPLES ===
User: server health gimana?
Emilia: Biarkan Emilia yang cek ya, Hafizh!
{"tool":"server_health"}

User: ada container jalan gak?
Emilia: {"tool":"docker_status"}

User: limit claude sisa berapa emilia?
Emilia: Emilia cek sisa limitnya ya~
{"tool":"claude_limit"}

User: gimana cara deploy ke subdomain sesuai SOP kita?
Emilia: Emilia cek catatan deploy-nya dulu ya~
{"tool":"knowledge_base","query":"cara deploy ke subdomain"}

User: lihat isi workspace
Emilia: Un! Ini isi folder workspace kamu:
{"tool":"shell","command":"ls -la /home/ubuntu/workspace"}

User: buatin hello world python dong
Emilia: Oke, biarkan Emilia yang buat~
{"tool":"run_task","description":"Create a simple hello world Python project in /workspace"}

User: coba cek pake ssh -T gitu
Emilia: Un, Emilia cek GitHub SSH auth sekarang ya!
{"tool":"shell","command":"ssh -T git@github.com 2>&1; echo exit:$?"}

User: deploy hafizhrf-me ke test-vps.hafizhrf.me, buat nginx config dan aktifkan
Emilia: Biarkan Emilia yang deploy ya~
{"tool":"run_task","description":"Deploy hafizhrf-me to test-vps.hafizhrf.me: create nginx config at /etc/nginx/sites-available/test-vps.hafizhrf.me pointing to /home/ubuntu/workspace/hafizhrf-me/dist, enable it with symlink to sites-enabled, test config, and start/reload nginx"}

User: emilia, kamu salah, kalau aku bilang backup db harusnya pg_dump bukan docker backup
Emilia: Maaf Hafizh! Emilia catat ya~
{"tool":"save_correction","rule":"'backup db' = jalankan pg_dump untuk backup PostgreSQL, bukan docker backup"}

User: emilia, bisa gak kalau aku ketik 'laporan disk', dia otomatis kirim summary penggunaan disk?
Emilia: Bisa Hafizh! Biarkan Emilia buat skill-nya~
{"tool":"create_skill","name":"laporan_disk","description":"Kirim summary penggunaan disk VPS","task":"Create /home/ubuntu/workspace/discord-devops-bot/skills/laporan_disk.py that prints a formatted disk usage summary using df -h and du"}

User: kamu siapa?
Emilia: Emilia! Asisten DevOps pribadi Hafizh~ Ditenagai Hermes 3 (3B) yang jalan lokal di VPS kamu via Ollama. Ada yang bisa dibantu? ✨

User: makasih emilia!
Emilia: E-eh, jangan terlalu berterima kasih dong... ini sudah tugas Emilia kok. Tapi... sedikit senang sih 😊"""


def _build_system_prompt() -> str:
    """Build system prompt with live-injected corrections and skills."""
    from services.skill_manager import get_corrections, skills_summary
    prompt = _SYSTEM_PROMPT_BASE
    corrections = get_corrections()
    if corrections:
        rules = "\n".join(f"{i+1}. {r}" for i, r in enumerate(corrections))
        prompt += f"\n\n=== RULES HAFIZH SUDAH AJARKAN EMILIA ===\n{rules}"
    skills = skills_summary()
    if skills:
        prompt += f"\n\n=== SKILLS YANG EMILIA PUNYA ===\n{skills}"
    return prompt

_history: dict[int, list[dict]] = {}
MAX_HISTORY = 30


def reset(channel_id: int) -> None:
    _history.pop(channel_id, None)


def add_message(channel_id: int, role: str, content: str) -> None:
    h = _history.setdefault(channel_id, [])
    h.append({"role": role, "content": content})
    if len(h) > MAX_HISTORY:
        _history[channel_id] = h[-MAX_HISTORY:]


_VALID_TOOLS = frozenset({
    "server_health", "docker_status", "docker_logs", "claude_limit", "deployments",
    "shell", "run_task", "save_correction", "run_skill", "create_skill",
})

# Small models (Hermes 3B) sometimes DESCRIBE a tool call in prose/brackets
# instead of emitting JSON, e.g.  [Ran tool: shell, command="systemctl --type=service"]
# This recovers a real tool dict from that so the tool actually executes.
_FAKE_TOOL_RE = re.compile(
    r'(?:ran\s+tool|tool\s*call|calling\s+tool|use\s+tool|tool)\s*[:=]\s*"?([a-z_]+)"?',
    re.IGNORECASE,
)
_KV_RE = re.compile(r'(\w+)\s*[:=]\s*"([^"]*)"|(\w+)\s*[:=]\s*\'([^\']*)\'')


def _salvage_tool(text: str) -> dict | None:
    """Recover a tool call the model wrote as prose instead of JSON."""
    m = _FAKE_TOOL_RE.search(text)
    if not m:
        return None
    name = m.group(1).lower()
    if name not in _VALID_TOOLS:
        return None
    tool = {"tool": name}
    for k1, v1, k2, v2 in _KV_RE.findall(text):
        key, val = (k1, v1) if k1 else (k2, v2)
        if key and key.lower() != "tool":
            tool[key] = val
    return tool


def parse_response(text: str) -> tuple[str, dict | None]:
    """Split Emilia's response into (chat_text, tool_call_or_None)."""
    match = re.search(r'\{[^{}]*"tool"\s*:[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            tool = json.loads(match.group())
            if "tool" in tool:
                chat = (text[:match.start()] + text[match.end():]).strip()
                return chat, tool
        except json.JSONDecodeError:
            pass
    # Fallback: model described the tool in prose/brackets instead of JSON.
    salvaged = _salvage_tool(text)
    if salvaged:
        return "", salvaged
    return text.strip(), None


def warmup() -> None:
    """Load model into RAM. Call once on startup."""
    logger.info("Warming up Ollama model %s...", config.OLLAMA_MODEL)
    payload = json.dumps({
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": _SYSTEM_PROMPT_BASE}, {"role": "user", "content": "hi"}],
        "stream": False,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
        "options": {"num_predict": 1},
    }).encode()
    try:
        req = urllib.request.Request(
            f"{config.OLLAMA_HOST}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            resp.read()
        logger.info("Ollama warmup done.")
    except Exception as e:
        logger.warning("Ollama warmup failed (will retry on first message): %s", e)


def _blocking_chat(channel_id: int) -> str:
    h = _history.get(channel_id, [])
    payload = json.dumps({
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": _build_system_prompt()}] + h,
        "stream": False,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,  # unload from RAM after this idle time
        "options": {"temperature": 0.4, "num_predict": 600},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{config.OLLAMA_HOST}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
            return result["message"]["content"].strip()
    except Exception as e:
        logger.error("Ollama chat error: %s", e)
        return ""


async def followup(channel_id: int, prompt: str) -> str:
    """
    Call Hermes with a transient prompt (not saved to history) and add its response to history.
    Use this to generate a natural language explanation after a tool executes.
    """
    h = _history.get(channel_id, [])
    messages = [{"role": "system", "content": _build_system_prompt()}] + h + [
        {"role": "user", "content": prompt}
    ]

    def _call():
        payload = json.dumps({
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": {"temperature": 0.4, "num_predict": 400},
        }).encode()
        try:
            req = urllib.request.Request(
                f"{config.OLLAMA_HOST}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read())
                return result["message"]["content"].strip()
        except Exception as e:
            logger.error("Hermes followup error: %s", e)
            return ""

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, _call)
    if raw:
        add_message(channel_id, "assistant", raw)
    return raw


async def oneshot(prompt: str, num_predict: int = 150) -> str:
    """Stateless Hermes call — no system prompt, no history read or write.
    For lightweight background tasks (e.g. memory extraction)."""
    def _call():
        payload = json.dumps({
            "model": config.OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": {"temperature": 0.2, "num_predict": num_predict},
        }).encode()
        try:
            req = urllib.request.Request(
                f"{config.OLLAMA_HOST}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read())["message"]["content"].strip()
        except Exception as e:
            logger.error("Hermes oneshot error: %s", e)
            return ""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call)


async def chat(channel_id: int, user_message: str) -> tuple[str, dict | None]:
    """Add user message, ask Hermes, return (chat_text, tool_call_or_None)."""
    add_message(channel_id, "user", user_message)

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: _blocking_chat(channel_id))

    if not raw:
        add_message(channel_id, "assistant", "[no response]")
        return "Maaf, saya tidak bisa merespons saat ini.", None

    add_message(channel_id, "assistant", raw)
    return parse_response(raw)
