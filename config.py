import os
from dotenv import load_dotenv

load_dotenv()

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise ValueError(f"Missing required env var: {key}")
    return val

def _optional(key: str, default: str) -> str:
    return os.getenv(key, default)

DISCORD_TOKEN = _require("DISCORD_TOKEN")
DISCORD_GUILD_ID = int(_require("DISCORD_GUILD_ID"))

# Shaula = separate Discord bot (own app/token) that runs Claude Code execution.
# Same process as Emilia (shared session state); blank token = Shaula disabled.
SHAULA_DISCORD_TOKEN = _optional("SHAULA_DISCORD_TOKEN", "").strip()
SHAULA_ENABLED = bool(SHAULA_DISCORD_TOKEN)

DEVOPS_ROLE_ID = int(_optional("DEVOPS_ROLE_ID", "0"))
ADMIN_ROLE_ID = int(_optional("ADMIN_ROLE_ID", "0"))

# When false, Emilia's Discord client does NOT connect in this process. Used after
# migrating Emilia's brain+body to the standalone hermes-agent gateway, which then
# owns DISCORD_TOKEN. Shaula keeps running here unchanged. Default true (legacy behavior).
EMILIA_ENABLED = _optional("EMILIA_ENABLED", "true").strip().lower() not in {"false", "0", "no"}

# Channel for restart/up status notices. 0 = auto (guild system channel, else first
# text channel Emilia can post in). Set to a channel ID to pin it.
STATUS_CHANNEL_ID = int(_optional("STATUS_CHANNEL_ID", "0") or "0")

# CLI Executor Engine: 'claude' or 'agy' (Antigravity CLI).
# Controlled by CLI_ENGINE in .env; defaults to claude if unset.
CLI_ENGINE = _optional("CLI_ENGINE", "claude").strip().lower()

# Empty = use `claude login` OAuth (Pro subscription). Set to sk-ant-... for API key auth.
ANTHROPIC_API_KEY = _optional("ANTHROPIC_API_KEY", "")
CLAUDE_BIN = _optional("CLAUDE_BIN", "/usr/bin/claude")
# Model for Claude Code tasks. Empty = use the account default (no --model flag).
# Set to e.g. "sonnet" to cut token usage on routine DevOps tasks.
CLAUDE_MODEL = _optional("CLAUDE_MODEL", "").strip()
# Config dir for the "kantor" (work-profile) Claude account, used by /task-kantor or /run (kantor=True).
# Mirrors the `claude-kantor` wrapper: CLAUDE_CONFIG_DIR=$HOME/.claude-kantor.
# Empty = default account (the bot's normal ~/.claude). Used as a fallback when the
# default account hits its 5-hour limit.
CLAUDE_KANTOR_CONFIG_DIR = _optional(
    "CLAUDE_KANTOR_CONFIG_DIR", os.path.expanduser("~/.claude-kantor")
)

# Antigravity CLI (agy) configuration
AGY_BIN = _optional("AGY_BIN", "/home/ubuntu/.local/bin/agy").strip()
# Model for agy tasks. Empty = use agy CLI default (e.g., Gemini 3.8 Flash High).
# Override with e.g. "gemini-3.8-flash-high", "gemini-3.1-pro-high", etc.
AGY_MODEL = _optional("AGY_MODEL", "").strip()

# Dify knowledge base (RAG) — local Dify instance gives Emilia semantic recall over
# SOPs/runbooks/lessons. Blank dataset id/key = feature off (graceful no-op).
DIFY_BASE_URL = _optional("DIFY_BASE_URL", "http://127.0.0.1:5001")
DIFY_DATASET_ID = _optional("DIFY_DATASET_ID", "").strip()
DIFY_DATASET_API_KEY = _optional("DIFY_DATASET_API_KEY", "").strip()
DIFY_KB_ENABLED = bool(DIFY_DATASET_ID and DIFY_DATASET_API_KEY)

def _float_or_zero(key: str, default: str) -> float:
    """Parse a float env var; blank/invalid → 0.0 (used as 'no limit')."""
    try:
        return float(_optional(key, default).strip() or 0)
    except ValueError:
        return 0.0

# 0 (or blank) = NO per-task budget cap (don't pass --max-budget-usd at all).
CLAUDE_MAX_BUDGET_USD = _float_or_zero("CLAUDE_MAX_BUDGET_USD", "0")

# Context auto-compaction. When a session's true context-window size (the largest single
# API call's input tokens in the last turn — NOT cumulative turn usage) reaches this many
# tokens, Shaula runs `/compact` (with a heads-up) BEFORE the next prompt, so a long-lived
# thread doesn't keep shipping a huge context every turn. 0 = disable.
# Default ~150k ≈ 75% of the 200k window on sonnet — leaves headroom but compacts rarely.
CLAUDE_COMPACT_THRESHOLD_TOKENS = int(_optional("CLAUDE_COMPACT_THRESHOLD_TOKENS", "150000"))

# How long Ollama keeps Hermes resident in RAM after a request. "-1" = forever
# (pins ~1.8GB — bad on a small box); a duration like "10m" frees it when idle.
OLLAMA_KEEP_ALIVE = _optional("OLLAMA_KEEP_ALIVE", "10m")

PROJECTS_BASE_DIR = _optional("PROJECTS_BASE_DIR", "/opt/agent/projects")

# Persistent run-history DB (SQLite). Survives restarts; queried via /runs and /run.
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DB_PATH = _optional("RUN_DB_PATH", os.path.join(_BOT_DIR, "data", "runs.db"))
APPROVAL_TIMEOUT_SECONDS = int(_optional("APPROVAL_TIMEOUT_SECONDS", "900"))
STREAM_EDIT_INTERVAL_SECONDS = float(_optional("STREAM_EDIT_INTERVAL_SECONDS", "1.5"))

# `/plan` is streamed to Discord and may inspect a large codebase before composing a
# response. Keep a separate, generous ceiling so it does not inherit the short
# one-shot planning timeout used by lightweight commands.
PLAN_TIMEOUT_SECONDS = int(_optional("PLAN_TIMEOUT_SECONDS", "1800"))  # 30 min

# Execution watchdog. Kill a Claude task only when it goes SILENT for this long
# (genuinely hung), instead of a hard wall-clock cap — long-but-active tasks keep
# running. EXEC_MAX_TIMEOUT_SECONDS is an absolute safety ceiling.
EXEC_IDLE_TIMEOUT_SECONDS = int(_optional("EXEC_IDLE_TIMEOUT_SECONDS", "600"))   # 10 min no output
EXEC_MAX_TIMEOUT_SECONDS = int(_optional("EXEC_MAX_TIMEOUT_SECONDS", "5400"))    # 90 min absolute cap

# Agy CLI otherwise stops every print-mode task after its own 5-minute default.
# Keep its ceiling aligned with the bot watchdog; the idle watchdog remains 10 minutes.
AGY_PRINT_TIMEOUT_SECONDS = int(_optional("AGY_PRINT_TIMEOUT_SECONDS", "5400"))
LOG_LEVEL = _optional("LOG_LEVEL", "INFO")

ALLOWED_APPROVER_ROLE_IDS = {r for r in (DEVOPS_ROLE_ID, ADMIN_ROLE_ID) if r != 0}

OLLAMA_HOST = _optional("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = _optional("OLLAMA_MODEL", "hermes3:3b")

# Cloudflare DNS automation. These are consumed by /opt/agent/bin/cf-dns through
# the environment it inherits from this process; kept optional so the bot still
# boots before Cloudflare is configured.
CF_API_TOKEN = _optional("CF_API_TOKEN", "")
CF_DOMAIN = _optional("CF_DOMAIN", "")
CF_ZONE_ID = _optional("CF_ZONE_ID", "")
CF_PROXY = _optional("CF_PROXY", "true")
VPS_PUBLIC_IP = _optional("VPS_PUBLIC_IP", "")

CLOUDFLARE_CONFIGURED = bool(CF_API_TOKEN and CF_DOMAIN)

# Delegation intake: a localhost HTTP endpoint that lets the external Emilia
# (hermes-agent gateway, separate process) hand a task to Shaula here. Shaula
# then runs the existing run_task_flow (thread + streaming + run-history), just
# like the old in-process Emilia hand-off. Bound to 127.0.0.1 only; the token
# is a shared secret so nothing else on the box can trigger tasks.
DELEGATE_INTAKE_PORT = int(_optional("DELEGATE_INTAKE_PORT", "8765"))
DELEGATE_INTAKE_TOKEN = _optional("DELEGATE_INTAKE_TOKEN", "").strip()
