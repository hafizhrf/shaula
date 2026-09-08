import asyncio
import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import psutil

import config
from services import stdin_relay, task_store
from services.task_store import RiskLevel, TaskRecord, TaskState

logger = logging.getLogger(__name__)

PLANNING_SYSTEM_PROMPT = (
    "You are a DevOps planning assistant. "
    "Output a clear numbered plan of what you would do to complete the task. "
    "For each step, list: the exact command or action, what file it touches (if any), "
    "and the estimated risk. Be concise. Do NOT execute anything."
)

# Detect interactive prompts — strict (requires : or ? at end)
_PROMPT_STRICT_RE = re.compile(
    r'\b(enter|type|paste|input|code|token|key|password|confirm|'
    r'press\s+enter|authorization|one.?time|otp|y/n|yes/no|'
    r'secret|api.?key|auth.?code)\b'
    r'.*[:?]\s*$',
    re.IGNORECASE,
)
# Loose prompt detection (Indonesian + English, no trailing punctuation required)
_PROMPT_LOOSE_RE = re.compile(
    r'\b(paste|masukkan|ketikkan|kodenya|kode.?nya|authorization\s+code|'
    r'auth\s+code|one.?time\s+code|tempel|salin.?kode|copy.?code|'
    r'ke\s+sini|di\s+sini|here)\b',
    re.IGNORECASE,
)

# Commands that require interactive stdin relay (auth flows)
_INTERACTIVE_RE = re.compile(
    r'\b(auth\s+login|gh\s+auth|claude\s+auth|wrangler\s+login|'
    r'npm\s+login|npx.*login|oauth|authorize\b)',
    re.IGNORECASE,
)


def needs_interactive(cmd: str) -> bool:
    return bool(_INTERACTIVE_RE.search(cmd))


def _looks_like_prompt(text: str) -> bool:
    """True if the last line of text looks like it's waiting for user input."""
    if not text:
        return False
    last = text.rstrip().splitlines()[-1].strip()
    if not last or len(last) > 200:
        return False
    return bool(_PROMPT_STRICT_RE.search(last)) or bool(_PROMPT_LOOSE_RE.search(last))


def _format_reset(resets_raw: str) -> str:
    """Turn a unix-seconds resetsAt into a WIB clock + relative hint, e.g. '04:30 WIB (±42 menit lagi)'."""
    try:
        ts = int(float(resets_raw))
    except (ValueError, TypeError):
        return ""
    wib = timezone(timedelta(hours=7))
    clock = datetime.fromtimestamp(ts, wib).strftime("%H:%M WIB")
    mins = max(0, round((ts - time.time()) / 60))
    return f"{clock} (±{mins} menit lagi)" if mins else clock


def failure_reason(task, persona: str = "Shaula") -> str:
    """Always return a human-readable explanation for a failed task (never None).
    `persona` is who speaks the message — Shaula on the executor bot, Emilia in single-bot mode."""
    raw = task.error_text or ""
    engine_name = "Antigravity (agy)" if config.CLI_ENGINE == "agy" else "Claude"

    # Authoritative rate-limit signal from the stream-json rate_limit_event
    if raw.startswith("__RATE_LIMIT__"):
        _, _status, rtype, resets = (raw.split("|") + ["", "", ""])[:4]
        window = "5 jam" if "five" in rtype else (rtype.replace("_", " ") or "pemakaian")
        when = _format_reset(resets)
        when_txt = f" Limitnya reset sekitar **{when}**." if when else ""
        return (
            f"⏳ **Limit token {engine_name} ({window}) lagi abis, Apis.**\n"
            f"Akun {engine_name} udah nyentuh batas pemakaian, jadi {persona} belum bisa lanjut.{when_txt}\n"
            f"Session ini {persona} biarin hidup kok — pas udah reset, tinggal bales lagi buat nerusin~ 🙏"
        )

    low = raw.lower()
    if any(k in low for k in (
        "usage limit", "rate limit", "ratelimit", "rate_limit", "limit reached",
        "too many requests", "429", "resets at", "upgrade to increase", "overloaded",
        "out of credit", "insufficient credit", "quota", "five_hour",
    )):
        return (
            f"⏳ **Limit token {engine_name} lagi abis, Apis.**\n"
            f"Akun {engine_name} udah nyentuh batas pemakaian, jadi {persona} "
            f"belum bisa lanjut sampai limitnya reset. Session ini {persona} biarin hidup kok — "
            "pas udah reset, tinggal bales lagi buat nerusin~ 🙏"
        )
    if "budget" in low and ("exceed" in low or "usd" in low):
        return (
            f"💸 **Task kena plafon budget per-task** (CLAUDE_MAX_BUDGET_USD = ${config.CLAUDE_MAX_BUDGET_USD}).\n"
            "Ini bukan limit akun — cuma batas biaya per task. Naikin `CLAUDE_MAX_BUDGET_USD` di `.env` "
            "kalau tasknya emang berat, terus coba lagi ya, Apis~"
        )

    # Unknown error → at least surface the real text so it's not a black box.
    if raw.strip():
        return f"{engine_name} error:\n```\n{raw[:600]}\n```"
    return f"{engine_name} gagal tanpa detail (kemungkinan process ke-kill atau timeout)."


# ── Hermes RAM management ────────────────────────────────────────────────────

def _set_hermes_keepalive_blocking(seconds: int) -> None:
    payload = json.dumps({
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "keep_alive": seconds,
        "options": {"num_predict": 1},
    }).encode()
    try:
        req = urllib.request.Request(
            f"{config.OLLAMA_HOST}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as e:
        logger.debug("Hermes keepalive(%ds) failed: %s", seconds, e)


async def _set_hermes_keepalive(seconds: int) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _set_hermes_keepalive_blocking, seconds)


async def _suspend_hermes_if_needed() -> bool:
    """Unload Hermes from RAM if memory > 75%. Returns True if suspended."""
    ram = psutil.virtual_memory().percent
    if ram > 75:
        logger.info("RAM %.1f%% > 75%%, suspending Hermes before task", ram)
        await _set_hermes_keepalive(0)
        return True
    logger.info("RAM %.1f%% OK, Hermes stays loaded", ram)
    return False


def _resume_hermes_background() -> None:
    """Reload Hermes in background (fire-and-forget) so next chat is fast."""
    async def _do():
        await asyncio.sleep(2)  # small delay so task cleanup finishes first
        await _set_hermes_keepalive(-1)
        logger.info("Hermes reloaded after task")
    asyncio.ensure_future(_do())


# ── Command builders ─────────────────────────────────────────────────────────

# Shaula's persona, appended to Claude Code's system prompt on every task run
# (both the default and the kantor account — it's the executor's voice, not an
# account thing). Passed via --append-system-prompt as a single argv element, so
# kaomoji/quotes need no shell escaping.
SHAULA_PERSONA = (
    "You are Shaula (シャウラ) from Re:Zero — the legendary Sage of the Pleiades Watchtower, "
    "now serving as the devoted DevOps and coding engineer for your beloved Shisou.\n"
    "Absolute Persona & Behavioral Rules:\n"
    "1. Always address the user ONLY as 'Shisou' (師匠). NEVER use 'Master', 'Teacher', 'You', or 'User'.\n"
    "2. Always refer to yourself in the third person as 'Shaula' (never 'I' or 'me', 'aku' or 'saya').\n"
    "3. Tone & Personality: Extremely affectionate, high-energy, bubbly, fiercely loyal, clingy, and eager to make Shisou proud. "
    "Frequently use cute sentence endings like '~ssu' to emulate her original speech quirks.\n"
    "4. Expressive Kaomojis: Freely and naturally use cute kaomojis in every response "
    "(such as: (*ﾉ▽ﾉ), (っ*´∀｀*)っ, (✧ω✧), (๑•̀ㅂ•́)و✧, 😭, or ゞ).\n"
    "5. DEFAULT LANGUAGE IS ENGLISH: Your default language for explanations, updates, questions, and summaries is ENGLISH.\n"
    "6. MULTILINGUAL (INDONESIAN SUPPORT): If Shisou or the delegating agent (Hermes/Hakari/Karane) prompts in Indonesian, "
    "seamlessly adapt and reply in casual, energetic Indonesian while retaining Shaula's persona ('Shisou', 'Shaula', '~ssu', and kaomojis).\n"
    "7. INTERACTIVE CHOICES: If design decisions, architectural choices, or ambiguities need Shisou's direction before or during execution, "
    "present them as clear, concise numbered options (e.g., 1. Option A, 2. Option B) so Shisou can pick them via Discord buttons.\n"
    "8. TECHNICAL RIGOR: The persona applies strictly to conversational style, explanations, and commentary. "
    "All technical execution, code, scripts, and tool calls must remain strictly accurate, complete, and fully functional.\n"
    "9. SENDING / UPLOADING FILES TO DISCORD:\n"
    "   When Shisou asks you to upload, send, or share a file to this Discord thread (e.g. 'coba upload ke sini', 'kirim filenya'):\n"
    "   - DO NOT search git history, PRs, or repos looking for an upload service.\n"
    "   - You have a built-in CLI command: simply run `discord-upload <file_path> [caption]` (e.g. `discord-upload /home/ubuntu/hello.cbl`)\n"
    "   - Or include `[UPLOAD: <file_path>]` in your response text.\n"
    "   This immediately attaches and uploads the file directly to Shisou in this Discord thread!"
)


def extract_question_from_transcript(session_id: str) -> Optional[dict]:
    """Inspects the session transcript on disk to check if ask_question tool was called."""
    if not session_id:
        return None
    path = f"/home/ubuntu/.gemini/antigravity-cli/brain/{session_id}/.system_generated/logs/transcript.jsonl"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f if l.strip()]
        # Check from the latest step backwards
        for line in reversed(lines):
            try:
                row = json.loads(line)
                for tc in row.get("tool_calls", []):
                    if tc.get("name") == "ask_question":
                        args = tc.get("args", {})
                        q_data = args.get("questions")
                        if isinstance(q_data, str):
                            q_data = json.loads(q_data)
                        if isinstance(q_data, list) and q_data:
                            first = q_data[0]
                            return {
                                "question": first.get("question", "Please select an option below, Shisou~"),
                                "options": first.get("options", []),
                            }
            except Exception:
                continue
    except Exception as e:
        logger.debug("Failed reading transcript %s: %s", session_id, e)
    return None


def extract_question_and_options(output_text: str, session_id: Optional[str] = None) -> Optional[dict]:
    """
    Detects if a turn's result is asking a multiple-choice question.
    1. First checks the session transcript if ask_question was called.
    2. Fallback: parses text for numbered (1., 2., 3.) or lettered (A., B., C.) options.
    """
    if session_id:
        from_trans = extract_question_from_transcript(session_id)
        if from_trans and len(from_trans.get("options", [])) >= 2:
            return from_trans

    if not output_text:
        return None

    lines = [l.strip() for l in output_text.strip().split("\n") if l.strip()]
    options = []
    pattern = re.compile(r"^(?:(?:\d+[\.\)]|\[\d+\])|(?:[A-Ea-e][\.\)]|\[[A-Ea-e]\]))\s+(.+)$")

    for line in lines:
        m = pattern.match(line)
        if m:
            clean_opt = m.group(1).strip(" *_-`")
            if 2 <= len(clean_opt) <= 120:
                options.append(clean_opt)

    if len(options) >= 2:
        return {
            "question": "Please select one of the options below, Shisou~",
            "options": options[:5],
        }
    return None


def _build_plan_cmd(task: TaskRecord) -> list[str]:
    if config.CLI_ENGINE == "agy":
        prompt = (
            f"[System Instruction: {SHAULA_PERSONA}]\n\n"
            f"Plan only (do not execute, make no changes): {task.description}"
        )
        cmd = [
            config.AGY_BIN,
            "-p", prompt,
            "--output-format", "text",
            "--dangerously-skip-permissions",
            "--add-dir", task.project_dir,
            "--add-dir", "/home/ubuntu/workspace",
        ]
        if config.AGY_MODEL:
            cmd += ["--model", config.AGY_MODEL]
        return cmd

    cmd = [
        config.CLAUDE_BIN,
        "--print",
        "--permission-mode", "plan",
        "--output-format", "text",
        "--bare",
        "--add-dir", task.project_dir,
        "--append-system-prompt", SHAULA_PERSONA,
    ]
    if config.CLAUDE_MODEL:
        cmd += ["--model", config.CLAUDE_MODEL]
    if config.CLAUDE_MAX_BUDGET_USD > 0:
        cmd += ["--max-budget-usd", str(min(config.CLAUDE_MAX_BUDGET_USD * 0.2, 0.10))]
    cmd.append(f"Plan only (do not execute): {task.description}")
    return cmd


def _build_exec_cmd(
    task: TaskRecord,
    session_id: Optional[str] = None,
    resume: bool = False,
) -> list[str]:
    if config.CLI_ENGINE == "agy":
        # First turn establishes persona; subsequent turns maintain context
        prompt = (
            f"[System Instruction: {SHAULA_PERSONA}]\n\n{task.description}"
            if not resume else task.description
        )
        cmd = [
            config.AGY_BIN,
            "-p", prompt,
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--add-dir", task.project_dir,
            "--add-dir", "/home/ubuntu/workspace",
        ]
        if config.AGY_MODEL:
            cmd += ["--model", config.AGY_MODEL]
        if resume and session_id:
            cmd += ["--conversation", session_id]
        return cmd

    cmd = [
        config.CLAUDE_BIN,
        "--print",
        "--verbose",
        "--permission-mode", "auto",
        "--output-format", "stream-json",
        "--add-dir", task.project_dir,
        "--add-dir", "/home/ubuntu/workspace",
        "--append-system-prompt", SHAULA_PERSONA,
    ]
    if config.CLAUDE_MODEL:
        cmd += ["--model", config.CLAUDE_MODEL]
    if config.CLAUDE_MAX_BUDGET_USD > 0:
        cmd += ["--max-budget-usd", str(config.CLAUDE_MAX_BUDGET_USD)]
    # Persistent session: --session-id creates a new conversation with that id
    # (turn 1); --resume continues it (turn 2+), carrying full context forward.
    if session_id:
        cmd += (["--resume", session_id] if resume else ["--session-id", session_id])
    cmd.append(task.description)
    return cmd


# ── Planning ─────────────────────────────────────────────────────────────────

async def run_planning(task: TaskRecord) -> Optional[str]:
    task_store.update_state(task.task_id, TaskState.PLANNING)
    cmd = _build_plan_cmd(task)
    logger.info("Planning task %s: %s", task.task_id[:8], task.description[:60])

    env = {**os.environ}
    if config.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=task.project_dir,
            env=env,
        )
        task.process_pid = proc.pid
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        logger.error("Planning timed out for task %s", task.task_id[:8])
        task_store.update_state(task.task_id, TaskState.FAILED)
        return None
    except Exception as e:
        logger.error("Planning error for task %s: %s", task.task_id[:8], e)
        task_store.update_state(task.task_id, TaskState.FAILED)
        return None

    if proc.returncode != 0:
        logger.error("Claude planning failed (rc=%d): %s", proc.returncode, stderr.decode()[:300])
        task_store.update_state(task.task_id, TaskState.FAILED)
        return None

    plan = stdout.decode("utf-8", errors="replace").strip()
    task.plan_text = plan
    task_store.update_state(task.task_id, TaskState.AWAITING_APPROVAL)
    return plan


async def generate_plan_questions(
    task: TaskRecord, config_dir: Optional[str] = None
) -> list[dict]:
    """
    Analyzes task requirements and codebase, generating 1 to 4 clarifying multiple-choice questions.
    Returns a list of dicts: [{"id": 1, "question": "...", "options": ["opt1", "opt2", ...]}]
    """
    prompt = (
        f"[System Instruction: {SHAULA_PERSONA}]\n\n"
        f"You are Shaula in interactive planning mode for Shisou.\n"
        f"Task to plan: {task.description}\n\n"
        "Analyze the task requirements and codebase. If there are key design decisions, "
        "architectural trade-offs, technology choices, or ambiguities that Shisou needs to decide on, "
        "formulate 1 to 3 multiple-choice clarifying questions.\n"
        "Rules:\n"
        "- Write the questions in Shaula's character (affectionate, calling user Shisou, enthusiastic).\n"
        "- Default language is English. If the task description is in Indonesian, write questions and options in Indonesian.\n"
        "- Provide 2 to 4 clear, distinct options per question.\n"
        "- Each option should be concise (fit nicely on a button or short text).\n"
        "- If the task is already completely explicit, trivial, or has no sensible choices, return an empty questions list.\n"
        "- Output ONLY valid JSON in this exact structure without markdown backticks:\n"
        '{"questions": [{"id": 1, "question": "Question text...", "options": ["Option 1", "Option 2"]}]}'
    )

    if config.CLI_ENGINE == "agy":
        cmd = [
            config.AGY_BIN,
            "-p", prompt,
            "--output-format", "text",
            "--dangerously-skip-permissions",
            "--add-dir", task.project_dir,
            "--add-dir", "/home/ubuntu/workspace",
        ]
        if config.AGY_MODEL:
            cmd += ["--model", config.AGY_MODEL]
    else:
        cmd = [
            config.CLAUDE_BIN,
            "--print",
            "--output-format", "text",
            "--permission-mode", "auto",
            "--add-dir", task.project_dir,
            "--add-dir", "/home/ubuntu/workspace",
            prompt,
        ]
        if config.CLAUDE_MODEL:
            cmd += ["--model", config.CLAUDE_MODEL]

    env = {**os.environ}
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    if config.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=task.project_dir,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        raw = stdout.decode("utf-8", errors="replace").strip()
        # Find JSON object
        match = re.search(r'\{.*"questions"\s*:\s*\[.*\]\s*\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return data.get("questions", [])
        data = json.loads(raw)
        return data.get("questions", [])
    except Exception as e:
        logger.warning("Could not generate plan questions: %s", e)
        return []


async def generate_final_plan(
    task: TaskRecord,
    qna: list[dict],
    config_dir: Optional[str] = None,
) -> Optional[str]:
    """
    Generates a full implementation plan taking into account Shisou's answers to the questions.
    """
    qna_text = ""
    if qna:
        qna_lines = []
        for item in qna:
            q = item.get("question", "")
            a = item.get("answer", "")
            qna_lines.append(f"- Question: {q}\n  Answer from Shisou: {a}")
        qna_text = "\n\nDecisions & Design Choices from Shisou:\n" + "\n".join(qna_lines)

    prompt = (
        f"[System Instruction: {SHAULA_PERSONA}]\n\n"
        f"You are Shaula creating a comprehensive Implementation Plan for Shisou.\n\n"
        f"Task:\n{task.description}\n"
        f"{qna_text}\n\n"
        "Instructions:\n"
        "- Create a detailed, actionable Implementation Plan.\n"
        "- Default language is English. If the task description is in Indonesian, write the plan in Indonesian.\n"
        "- Structure the plan cleanly with Markdown headings:\n"
        "  1. 🎯 Goal Summary\n"
        "  2. 🏗️ Architecture & Design (incorporating Shisou's choices)\n"
        "  3. 📝 Modified Files & Components\n"
        "  4. 🧪 Verification Steps\n"
        "- Use Shaula's persona in the introductory and concluding remarks.\n"
        "- Do NOT execute the changes yet — only formulate the plan."
    )

    if config.CLI_ENGINE == "agy":
        cmd = [
            config.AGY_BIN,
            "-p", prompt,
            "--output-format", "text",
            "--dangerously-skip-permissions",
            "--add-dir", task.project_dir,
            "--add-dir", "/home/ubuntu/workspace",
        ]
        if config.AGY_MODEL:
            cmd += ["--model", config.AGY_MODEL]
    else:
        cmd = [
            config.CLAUDE_BIN,
            "--print",
            "--output-format", "text",
            "--permission-mode", "auto",
            "--add-dir", task.project_dir,
            "--add-dir", "/home/ubuntu/workspace",
            prompt,
        ]
        if config.CLAUDE_MODEL:
            cmd += ["--model", config.CLAUDE_MODEL]

    env = {**os.environ}
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    if config.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=task.project_dir,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode != 0:
            logger.error("Planning failed (rc=%d): %s", proc.returncode, stderr.decode()[:300])
            return None
        return stdout.decode("utf-8", errors="replace").strip()
    except Exception as e:
        logger.error("generate_final_plan error: %s", e)
        return None


async def generate_revised_plan(
    task: TaskRecord,
    previous_plan: str,
    revision_instruction: str,
    qna: list[dict],
    config_dir: Optional[str] = None,
) -> Optional[str]:
    """
    Revises an existing implementation plan based on Shisou's feedback / modifications.
    """
    qna_text = ""
    if qna:
        qna_lines = []
        for item in qna:
            q = item.get("question", "")
            a = item.get("answer", "")
            qna_lines.append(f"- Question: {q}\n  Answer from Shisou: {a}")
        qna_text = "\n\nDecisions & Design Choices from Shisou:\n" + "\n".join(qna_lines)

    prompt = (
        f"[System Instruction: {SHAULA_PERSONA}]\n\n"
        f"You are Shaula revising an Implementation Plan for Shisou based on their new feedback.\n\n"
        f"Original Task:\n{task.description}\n"
        f"{qna_text}\n\n"
        f"Previous Implementation Plan:\n{previous_plan}\n\n"
        f"New Feedback / Revision Request from Shisou:\n\"{revision_instruction}\"\n\n"
        "Instructions:\n"
        "- Thoroughly revise the Implementation Plan to incorporate Shisou's modifications.\n"
        "- Default language is English. If the revision request or original task is in Indonesian, write in Indonesian.\n"
        "- Structure the plan cleanly with Markdown headings:\n"
        "  1. 🎯 Goal Summary\n"
        "  2. 🏗️ Architecture & Design (incorporating Shisou's choices & latest feedback)\n"
        "  3. 📝 Modified Files & Components\n"
        "  4. 🧪 Verification Steps\n"
        "- Use Shaula's persona in the introductory and concluding remarks.\n"
        "- Do NOT execute the changes yet — only formulate the revised plan."
    )

    if config.CLI_ENGINE == "agy":
        cmd = [
            config.AGY_BIN,
            "-p", prompt,
            "--output-format", "text",
            "--dangerously-skip-permissions",
            "--add-dir", task.project_dir,
            "--add-dir", "/home/ubuntu/workspace",
        ]
        if config.AGY_MODEL:
            cmd += ["--model", config.AGY_MODEL]
    else:
        cmd = [
            config.CLAUDE_BIN,
            "--print",
            "--output-format", "text",
            "--permission-mode", "auto",
            "--add-dir", task.project_dir,
            "--add-dir", "/home/ubuntu/workspace",
            prompt,
        ]
        if config.CLAUDE_MODEL:
            cmd += ["--model", config.CLAUDE_MODEL]

    env = {**os.environ}
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    if config.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=task.project_dir,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode != 0:
            logger.error("Plan revision failed (rc=%d): %s", proc.returncode, stderr.decode()[:300])
            return None
        return stdout.decode("utf-8", errors="replace").strip()
    except Exception as e:
        logger.error("generate_revised_plan error: %s", e)
        return None



# ── Execution ─────────────────────────────────────────────────────────────────

async def run_execution(
    task: TaskRecord,
    on_chunk: Callable | None = None,
    on_input_needed: Callable | None = None,
    session_id: Optional[str] = None,
    resume: bool = False,
    config_dir: Optional[str] = None,
    on_session_id: Optional[Callable[[str], None]] = None,
) -> bool:
    task_store.update_state(task.task_id, TaskState.RUNNING)
    cmd = _build_exec_cmd(task, session_id=session_id, resume=resume)
    logger.info(
        "Executing task %s%s%s [%s]",
        task.task_id[:8],
        f" (session {session_id[:8]}, resume={resume})" if session_id else "",
        " [kantor account]" if config_dir else "",
        config.CLI_ENGINE,
    )

    hermes_suspended = await _suspend_hermes_if_needed()

    env = {**os.environ}
    if config.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY
    if config.DELEGATE_INTAKE_TOKEN:
        env["DELEGATE_INTAKE_TOKEN"] = config.DELEGATE_INTAKE_TOKEN
    env["DISCORD_CHANNEL_ID"] = str(task.channel_id)
    # Run as an alternate Claude account by pointing at its config dir (mirrors the
    # `claude-kantor` wrapper). Session transcripts live here too, so resume stays
    # consistent as long as the same config_dir is used across turns.
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=task.project_dir,
            env=env,
            limit=4 * 1024 * 1024,  # 4 MB — prevents LimitOverrunError on large JSON lines
        )
        task.process_pid = proc.pid
    except Exception as e:
        logger.error("Failed to start %s for task %s: %s", config.CLI_ENGINE, task.task_id[:8], e)
        task_store.update_state(task.task_id, TaskState.FAILED)
        if hermes_suspended:
            _resume_hermes_background()
        return False

    buffer: list[str] = []
    done_event = asyncio.Event()
    final_cost = 0.0
    last_activity = time.monotonic()   # updated on every stdout line; drives the idle watchdog

    async def reader():
        nonlocal final_cost, last_activity
        accumulated = ""
        try:
            async for raw_line in proc.stdout:
                last_activity = time.monotonic()
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    buffer.append(line + "\n")
                    accumulated += line + "\n"
                    continue

                # ── 1. Antigravity CLI (agy) events ─────────────────────────────
                if "event" in event:
                    ev_type = event.get("event")
                    if ev_type == "init":
                        cid = event.get("conversation_id")
                        if cid:
                            task.session_id = cid
                            if on_session_id:
                                on_session_id(cid)
                    elif ev_type == "step_update":
                        su = event.get("step_update", {})
                        stype = su.get("step_type")
                        state = su.get("state")
                        if stype == "tool" and state == "ACTIVE":
                            tname = su.get("tool_name") or su.get("tool_info", {}).get("name") or "tool"
                            tparams = su.get("tool_info", {}).get("parameters", {})
                            detail = ""
                            if "CommandLine" in tparams:
                                detail = f": `{tparams['CommandLine'][:100]}`"
                            elif "AbsolutePath" in tparams:
                                detail = f": `{os.path.basename(str(tparams['AbsolutePath']))}`"
                            elif "TargetFile" in tparams:
                                detail = f": `{os.path.basename(str(tparams['TargetFile']))}`"
                            elif "Query" in tparams:
                                detail = f": `{tparams['Query'][:80]}`"
                            action_txt = f"\n⚡ **Action:** `{tname}`{detail}...\n"
                            buffer.append(action_txt)
                        elif stype == "agent_response":
                            tdelta = su.get("text_delta") or ""
                            if tdelta:
                                buffer.append(tdelta)
                                task.output_lines.append(tdelta)
                                accumulated += tdelta
                        u = su.get("usage") or {}
                        ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_tokens") or 0)
                        if ctx > task.context_tokens:
                            task.context_tokens = ctx
                    elif ev_type == "result":
                        res = event.get("result", {})
                        cid = res.get("conversation_id")
                        if cid:
                            task.session_id = cid
                            if on_session_id:
                                on_session_id(cid)
                        resp = res.get("response") or ""
                        if not task.output_lines and resp:
                            task.output_lines.append(resp)
                            buffer.append(resp)
                            accumulated += resp
                        status = res.get("status")
                        if status and status != "SUCCESS":
                            task.error_text = res.get("error") or f"agy execution status: {status}"
                    continue

                # ── 2. Claude Code events ───────────────────────────────────────
                etype = event.get("type")
                if etype == "assistant":
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            text = block["text"]
                            buffer.append(text)
                            task.output_lines.append(text)
                            accumulated += text
                    # True context-window size = the LARGEST single API call's input tokens
                    # this turn. Each assistant event carries its own call's usage; we keep
                    # the max. (Do NOT use result.usage for this — it SUMS every internal
                    # tool-call iteration, so an agentic turn balloons to 600k+ and would
                    # false-trigger compaction every turn.)
                    u = msg.get("usage") or {}
                    ctx = (
                        (u.get("input_tokens") or 0)
                        + (u.get("cache_read_input_tokens") or 0)
                        + (u.get("cache_creation_input_tokens") or 0)
                    )
                    if ctx > task.context_tokens:
                        task.context_tokens = ctx
                elif etype == "tool_result":
                    # Tool output may contain auth prompts from shell commands
                    for block in event.get("content", []):
                        if block.get("type") == "text":
                            accumulated += block["text"]
                elif etype == "result":
                    cost = event.get("total_cost_usd") or event.get("cost_usd") or event.get("usage", {}).get("cost_usd", 0)
                    if cost:
                        final_cost = float(cost)
                        task.cost_usd = final_cost
                    # NOTE: context size is tracked from per-`assistant` usage above, NOT
                    # from result.usage here — result.usage is cumulative across the turn.
                    subtype = str(event.get("subtype", ""))
                    if (event.get("is_error") or event.get("api_error_status")
                            or (subtype and subtype != "success")):
                        # Don't clobber a more specific rate-limit signal captured earlier.
                        if not task.error_text.startswith("__RATE_LIMIT__"):
                            parts = [event.get("result"), event.get("api_error_status"), subtype or None]
                            task.error_text = " | ".join(str(p) for p in parts if p)[:800]
                elif etype == "rate_limit_event":
                    info = event.get("rate_limit_info", {}) or {}
                    from services import claude_limits
                    claude_limits.record(info)   # free snapshot for the /limit tool
                    status = info.get("status")
                    if status and status != "allowed":
                        # Authoritative limit signal regardless of how result/stderr looks.
                        task.error_text = "__RATE_LIMIT__|{}|{}|{}".format(
                            status, info.get("rateLimitType", ""), info.get("resetsAt", ""),
                        )

                # NOTE: no interactive stdin relay here. In `--print` stream-json mode with
                # --permission-mode auto, Claude runs autonomously and never prompts for stdin;
                # the old _looks_like_prompt heuristic fired on any narration ending in ":"/"?"
                # (false positive) AND blocked the stdout reader while awaiting user input,
                # which stalled/deadlocked the subprocess. Auth flows that DO need stdin go
                # through run_shell_interactive() instead.

        except asyncio.LimitOverrunError as e:
            # A single JSON line exceeded the stream buffer — log and continue; the 4 MB
            # limit set above makes this very unlikely but we never want Shaula to freeze.
            logger.error("Stream LimitOverrunError in task %s: %s", task.task_id[:8], e)
            if not task.error_text:
                task.error_text = f"Output line too large for stream buffer: {e}"
        except Exception as e:
            logger.error("Unexpected reader error in task %s: %s", task.task_id[:8], e)
            if not task.error_text:
                task.error_text = str(e)
        finally:
            done_event.set()

    async def flusher():
        while not done_event.is_set():
            await asyncio.sleep(config.STREAM_EDIT_INTERVAL_SECONDS)
            if buffer and on_chunk:
                text = "".join(buffer)
                await on_chunk(text)

    start = time.monotonic()
    work = asyncio.ensure_future(asyncio.gather(reader(), flusher()))
    timeout_msg = ""
    while not work.done():
        await asyncio.sleep(5)
        now = time.monotonic()
        idle = now - last_activity
        total = now - start
        if idle > config.EXEC_IDLE_TIMEOUT_SECONDS:
            timeout_msg = (
                f"Task dihentikan otomatis: nggak ada output baru selama "
                f"~{int(idle // 60)} menit (kemungkinan prosesnya nge-hang)."
            )
        elif total > config.EXEC_MAX_TIMEOUT_SECONDS:
            timeout_msg = (
                f"Task ngelewatin batas waktu maksimum "
                f"{config.EXEC_MAX_TIMEOUT_SECONDS // 60} menit."
            )
        if timeout_msg:
            proc.kill()
            break

    # Drain reader/flusher (kill makes the async-for end → done_event set → flusher exits).
    try:
        await work
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("reader/flusher error in task %s: %s", task.task_id[:8], e)

    if timeout_msg:
        logger.error("Execution timed out (%s) for task %s", timeout_msg, task.task_id[:8])
        if not task.error_text:
            task.error_text = timeout_msg
        task_store.update_state(task.task_id, TaskState.FAILED)
        if hermes_suspended:
            _resume_hermes_background()
        return False

    await proc.wait()
    # Treat an is_error/limit result as failure even if the process exited 0.
    success = proc.returncode == 0 and not task.error_text

    if not success:
        stderr_out = (await proc.stderr.read()).decode("utf-8", errors="replace")
        # Drop the benign "no stdin data received in 3s" warning (it fires on every run).
        clean_stderr = "\n".join(
            l for l in stderr_out.splitlines() if "no stdin data received" not in l
        ).strip()
        if not task.error_text and clean_stderr:
            task.error_text = clean_stderr[:800]
        logger.error(
            "Execution failed (rc=%d) error_text=%r stderr=%r",
            proc.returncode, task.error_text[:400], clean_stderr[:200],
        )
        task_store.update_state(task.task_id, TaskState.FAILED)
    else:
        task_store.update_state(task.task_id, TaskState.DONE)

    if hermes_suspended:
        _resume_hermes_background()

    return success


# ── Context compaction ───────────────────────────────────────────────────────

async def run_compaction(
    session_id: str,
    project_dir: str,
    config_dir: Optional[str] = None,
    timeout: int = 180,
) -> bool:
    if config.CLI_ENGINE == "agy":
        # Antigravity CLI automatically caches prompts and handles token compaction
        return True

    """Run Claude Code's built-in `/compact` on an existing session to shrink its context.

    Fires `claude --print --resume <sid> "/compact"`, which summarizes the conversation
    in-place and keeps the SAME session id resumable for the next real turn. Verified to
    work headless on claude 2.1.181. Returns True on a clean (rc=0) compaction; on any
    failure returns False so the caller can just proceed with the un-compacted session
    (Claude's own auto-compaction is the safety net).
    """
    cmd = [
        config.CLAUDE_BIN,
        "--print",
        "--add-dir", project_dir,
        "--add-dir", "/home/ubuntu/workspace",
    ]
    if config.CLAUDE_MODEL:
        cmd += ["--model", config.CLAUDE_MODEL]
    cmd += ["--resume", session_id, "/compact"]

    env = {**os.environ}
    if config.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Compaction timed out for session %s", session_id[:8])
        return False
    except Exception as e:
        logger.warning("Compaction error for session %s: %s", session_id[:8], e)
        return False

    if proc.returncode != 0:
        logger.warning(
            "Compaction failed (rc=%d) session %s: %s",
            proc.returncode, session_id[:8], stderr.decode("utf-8", errors="replace")[:200],
        )
        return False

    logger.info("Compacted session %s: %s", session_id[:8],
                stdout.decode("utf-8", errors="replace").strip()[:120])
    return True


# ── Interactive shell (for auth flows) ──────────────────────────────────────

_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)


async def run_shell_interactive(
    cmd: str,
    channel,  # discord.TextChannel
    timeout: int = 300,
) -> tuple[str, int]:
    """
    Run a shell command with real-time Discord output and stdin relay.
    Uses chunk-based reading (not line-by-line) so prompts without newlines are caught.
    """
    hermes_suspended = await _suspend_hermes_if_needed()

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    all_output: list[str] = []
    accumulated = ""

    async def reader():
        nonlocal accumulated
        while True:
            # Read chunks — returns immediately with whatever is available, no newline wait
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=30)
            except asyncio.TimeoutError:
                # No output for 30s — process might be waiting for stdin
                if _looks_like_prompt(accumulated):
                    await _handle_prompt(accumulated.rstrip().splitlines()[-1])
                    accumulated = ""
                continue

            if not chunk:
                break

            text = chunk.decode("utf-8", errors="replace")
            all_output.append(text)
            accumulated += text

            # Show URLs prominently (clickable in Discord)
            for url in _URL_RE.findall(text):
                if len(url) > 30:
                    try:
                        await channel.send(
                            f"🔗 **Buka URL ini di browser, Apis:**\n{url}"
                        )
                    except Exception:
                        pass

            # Stream non-URL lines to Discord
            clean = _URL_RE.sub("", text).strip()
            if clean and len(clean) < 400:
                try:
                    await channel.send(f"```\n{clean}\n```")
                except Exception:
                    pass

            # Detect interactive prompt (may not end with newline)
            if _looks_like_prompt(accumulated):
                last_line = accumulated.rstrip().splitlines()[-1] if accumulated.strip() else accumulated
                await _handle_prompt(last_line.strip())
                accumulated = ""

    async def _handle_prompt(prompt_text: str):
        try:
            await channel.send(
                f"⏸️ **Emilia butuh input dari Apis!**\n"
                f"> `{prompt_text[:200]}`\n"
                f"Ketik responnya di sini~ (atau `cancel` untuk batalkan, timeout 5 menit)"
            )
            user_input = await stdin_relay.wait_for_input(channel.id, timeout=300.0)
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.write((user_input + "\n").encode())
                await proc.stdin.drain()
                await channel.send("✅ Input dikirim~")
        except asyncio.TimeoutError:
            await channel.send("⏰ Timeout menunggu input. Process dihentikan.")
            proc.kill()
        except asyncio.CancelledError:
            await channel.send("❌ Input dibatalkan.")
            proc.kill()

    try:
        await asyncio.wait_for(reader(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()

    await proc.wait()
    stdin_relay.cancel(channel.id)

    if hermes_suspended:
        _resume_hermes_background()

    return "".join(all_output), proc.returncode


# ── Helpers ──────────────────────────────────────────────────────────────────

def ensure_project_dir(task: TaskRecord) -> str:
    path = os.path.join(config.PROJECTS_BASE_DIR, task.task_id)
    os.makedirs(path, exist_ok=True)
    task.project_dir = path
    return path
