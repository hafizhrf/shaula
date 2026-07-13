"""
Fast keyword-based intent router — 0ms, no LLM.
Detects tool intents from message text before Hermes is ever called.
"""
import re

import config

# Knowledge-base recall markers → query the Dify RAG store (when enabled).
# "how to" questions are handled separately via _HOW_TO_RE below.
_KB_KW = {
    'sop', 'runbook', 'prosedur', 'dokumentasi', 'docs ', 'catatan kamu',
    'catatan emilia', 'di catatan', 'menurut catatan', 'inget gak', 'ingat gak',
    'masih inget', 'pernah kita', 'kita pernah', 'panduan', 'cara deploy',
}

# Explicit "remember this" store command (distinct from "inget gak" recall in _KB_KW)
_REMEMBER_RE = re.compile(
    r'\b(inget ya|ingat ya|inget ini|ingat ini|catat ini|catat ya|catat dong|'
    r'emilia inget|tolong inget|remember this|remember that)\b',
    re.IGNORECASE,
)

_HEALTH_KW = {
    'server health', 'server sehat', 'kondisi server', 'gimana server',
    'cpu', 'ram', 'memory', 'memori', 'disk usage', 'disk space',
    'uptime', 'load average', 'server load', 'server lagi',
    'resource', 'pemakaian', 'status server', 'health check',
    'cek server', 'gimana vps', 'kondisi vps',
}
_DOCKER_STATUS_KW = {
    'docker status', 'container status', 'docker ps', 'container apa',
    'container yang jalan', 'service yang jalan', 'docker list',
    'lihat container', 'cek container', 'cek docker', 'docker ada apa',
}
_DOCKER_GENERIC_KW = {'docker', 'container', 'kontainer'}
# Claude usage/limit queries → claude_limit tool
_LIMIT_KW = {
    'limit claude', 'claude limit', 'sisa limit', 'limit emilia', 'limit token',
    'limit nya', 'limitnya', 'limit 5 jam', 'limit lima jam', 'limit mingguan',
    'limit harian', 'berapa limit', 'cek limit', 'kuota claude', 'quota claude',
    'sisa token', 'limit masih', 'limit udah', 'usage claude',
}
_LOGS_KW = {'docker log', 'container log', 'lihat log', 'cek log', 'tail log', 'show log'}

# "What's deployed on the VPS?" listing → deployments tool. Phrases that mean LISTING
# (not "deploy X" which is a task). Checked before the run_task keywords.
_DEPLOY_KW = {
    'subdomain', 'deployment', 'yang dideploy', 'yang di-deploy', 'yang di deploy',
    'udah dideploy', 'sudah dideploy', 'deploy apa aja', 'deploy apa saja',
    'app apa aja', 'aplikasi apa aja', 'apa aja yang jalan', 'apa aja di vps',
    'apa aja di workspace', 'list app', 'list site', 'daftar deploy', 'list deploy',
    'cek deploy', 'ada apa aja di vps',
}

# Auth / login / account status checks
_AUTH_KW = {
    'github', 'git user', 'git config', 'logged in', 'login as', 'siapa yang login',
    'wrangler whoami', 'npm whoami', 'cloudflare login', 'gcloud',
    'ssh key', 'ssh config', 'authorized keys', 'ssh -t', 'ssh github', 'ssh git',
}
_AUTH_COMMANDS = {
    'github':          'gh auth status 2>&1 || git config --global --list | grep user',
    'git user':        'git config --global user.name && git config --global user.email',
    'git config':      'git config --global --list',
    'wrangler whoami': 'wrangler whoami',
    'npm whoami':      'npm whoami',
    'ssh key':         'ls -la ~/.ssh/',
    'authorized keys': 'cat ~/.ssh/authorized_keys 2>/dev/null | cut -c1-80',
    'ssh -t':          'ssh -T git@github.com 2>&1; echo "exit:$?"',
    'ssh github':      'ssh -T git@github.com 2>&1; echo "exit:$?"',
    'ssh git':         'ssh -T git@github.com 2>&1; echo "exit:$?"',
}
_FOLDER_KW = {
    'isi folder', 'isi dari', 'ada apa di', 'ada file apa', 'lihat file',
    'lihat folder', 'cek folder', 'ls ', 'ada folder apa', 'apa aja di',
    'lihat isi', 'workspace', 'direktori', 'directory',
}

# Phrase-based task keywords (safe for substring match — specific enough)
_TASK_PHRASES = {
    'buatin', 'buatkan', 'bikin', 'buat project', 'buat app', 'buat script',
    'buat file', 'buat folder', 'pasang', 'konfigurasi', 'configure',
    'stop service', 'start service', 'jalankan task', 'jalankan deploy',
    'deploy ke', 'deploy project', 'deploy app', 'langsung deploy',
    'edit file', 'update file', 'hapus file', 'pindahkan', 'copy file',
    'clone', 'pull repo', 'create project', 'make project',
    'init project', 'scaffold', 'create a', 'make a',
    'write a script', 'write a file', 'fix the', 'update config',
}

# Word-boundary task keywords (exact match)
# "gimana caranya deploy" is filtered earlier by _HOW_TO_RE, so "deploy" is safe here
_TASK_WORDS_RE = re.compile(
    r'\b(install|restart|remove|uninstall|upgrade|migrate|deploy)\b',
    re.IGNORECASE,
)

# "how to" question markers → skip keyword routing → Hermes explains
_HOW_TO_RE = re.compile(
    r'\b(gimana\s+cara|bagaimana\s+cara|how\s+to|how\s+do\s+i|cara\s+nya|caranya)\b',
    re.IGNORECASE,
)

# Conversational markers — message is a follow-up, not a fresh command → skip keyword routing → Hermes
_CONV_MARKERS = {
    'gitu', 'soalnya', 'tadi', 'sebelumnya', 'lanjut', 'terus gimana',
    'kan ', 'makanya', 'itu tadi', 'yang tadi', 'udah', 'sudah',
}

# Version check: maps software name → shell command
_VERSION_MAP = {
    'node': 'node --version',
    'nodejs': 'node --version',
    'npm': 'npm --version',
    'python': 'python3 --version',
    'python3': 'python3 --version',
    'pip': 'pip3 --version',
    'docker': 'docker --version',
    'git': 'git --version',
    'nginx': 'nginx -v 2>&1',
    'wrangler': 'wrangler --version',
    'claude': 'claude --version',
    'ollama': 'ollama --version',
    'go': 'go version',
    'rust': 'rustc --version',
    'java': 'java --version 2>&1',
    'php': 'php --version',
    'ruby': 'ruby --version',
}
_VERSION_KW = re.compile(r'\b(versi|version|versionnya|--version|-v)\b', re.IGNORECASE)

_PATH_RE = re.compile(r'(/[\w/.\-]+|\bworkspace\b|\bopt\b|\bhome\b)', re.IGNORECASE)
_CONTAINER_RE = re.compile(r'log[s]?\s+(?:dari\s+|of\s+|for\s+)?([a-zA-Z][\w_\-\.]+)', re.IGNORECASE)


def _find_path(msg: str) -> str:
    m = _PATH_RE.search(msg)
    if not m:
        return '/home/ubuntu/workspace'
    p = m.group().lower()
    if p in ('workspace', '/workspace'):
        return '/home/ubuntu/workspace'
    if p in ('home', '/home'):
        return '/home/ubuntu'
    if p in ('opt', '/opt'):
        return '/opt'
    return m.group()


def _detect_version(msg: str, original: str) -> dict | None:
    """Detect 'X versi berapa?' / 'what version is X?' → shell command."""
    if not _VERSION_KW.search(msg):
        return None
    for name, cmd in _VERSION_MAP.items():
        if re.search(r'\b' + re.escape(name) + r'\b', msg, re.IGNORECASE):
            return _shell(cmd)
    return _shell("node -v; python3 --version; docker --version; git --version")


def _shell(cmd: str) -> dict:
    """Pre-vetted shell command from the router — marked trusted to skip safety check."""
    return {"tool": "shell", "command": cmd, "trusted": True}


# --- Explicit "run this command" detection -------------------------------------
# Apis tells Emilia to run a specific command, e.g.
#   "emilia, coba run `git --version` dong"  /  "jalanin `ps aux | grep nginx`"
# This is deterministic (bypasses the flaky 3B router) and runs in raw mode
# (output posted as-is, no Hermes explanation round-trip).
_RUN_VERB_RE = re.compile(
    r'\b(run|jalan(?:in|kan)?|eksekusi|execute|exec|cmd|command|ketik)\b',
    re.IGNORECASE,
)
_BACKTICK_RE = re.compile(r'`([^`]+)`')
# Common binaries safe to take from "run <cmd>" without backticks (first token only).
_SHELL_BINARIES = {
    'git', 'ls', 'cat', 'df', 'free', 'ps', 'top', 'systemctl', 'service',
    'docker', 'npm', 'node', 'python3', 'python', 'pip', 'pip3', 'uptime',
    'whoami', 'curl', 'wget', 'ss', 'netstat', 'ip', 'du', 'nginx', 'pwd',
    'journalctl', 'echo', 'env', 'which', 'grep', 'find', 'head', 'tail',
    'wc', 'date', 'hostname', 'uname', 'id', 'lsblk', 'mount', 'ping',
    'systemd-analyze', 'dpkg', 'apt', 'pm2', 'wrangler', 'gh', 'ollama',
}
# Strip a trailing politeness/filler tail so "git status dong ya" → "git status".
_RUN_TAIL_RE = re.compile(r'\s+(dong|ya|yaa|deh|please|plz|gan|bro|sekarang|napa|nih)\b.*$', re.IGNORECASE)


def _detect_explicit_run(message: str) -> dict | None:
    """Detect an explicit 'run <command>' instruction → trusted raw shell call."""
    msg = message.lower()
    if not _RUN_VERB_RE.search(msg):
        return None

    # Path 1 (strongest): a backtick code span + a run verb → run the span verbatim.
    span = _BACKTICK_RE.search(message)
    if span:
        cmd = span.group(1).strip()
        if cmd:
            tool = _shell(cmd)
            tool["raw"] = True
            return tool

    # Path 2: "run <cmd>" with no backticks — only if <cmd> starts with a known binary.
    m = re.search(r'\b(?:run|jalan(?:in|kan)?|eksekusi|execute|exec|ketik)\b[:\s]+(.+)', message, re.IGNORECASE)
    if m:
        rest = _RUN_TAIL_RE.sub('', m.group(1).strip()).strip(' .!?')
        first = rest.split()[0].lower() if rest else ''
        if first in _SHELL_BINARIES:
            tool = _shell(rest)
            tool["raw"] = True
            return tool

    return None


def _detect_claude_auth(msg: str) -> dict | None:
    """Detect 'login claude-kantor' / 'auth claude-X' → direct interactive auth flow."""
    m = re.search(r'\bclaude-([\w]+)\b', msg, re.IGNORECASE)
    if not m:
        return None
    if not any(w in msg for w in ('login', 'auth', 'loginin', 'masuk', 'authenticate')):
        return None
    profile = m.group(1).lower()
    profile_dir = f'/home/ubuntu/.claude-{profile}'
    # BROWSER=echo → prints URL instead of hanging on xdg-open; DISPLAY= → no GUI attempt
    return _shell(f'HOME={profile_dir} BROWSER=echo DISPLAY= /usr/bin/claude auth login 2>&1')


def _is_conversational(msg: str) -> bool:
    """True if message is a follow-up/context reference, not a fresh command → skip keyword routing."""
    # "soalnya" = "because" — always giving context, not a command
    if 'soalnya' in msg:
        return True
    # past tense: "udah setup X" / "sudah install X" — refers to something already done
    if re.search(r'\b(udah|sudah)\s+(setup|install|config|cek)', msg):
        return True
    # "tadi" or "sebelumnya" together with "gitu" → clearly referencing a previous action
    if ('tadi' in msg or 'sebelumnya' in msg) and 'gitu' in msg:
        return True
    return False


def detect_tool(message: str) -> dict | None:
    """
    Returns tool call dict if message clearly maps to a tool.
    Returns None for pure chat messages → Hermes handles them.
    """
    msg = message.lower()

    # "How to" questions → ground on the knowledge base if available, else Hermes explains
    if _HOW_TO_RE.search(msg):
        return {"tool": "knowledge_base", "query": message} if config.DIFY_KB_ENABLED else None

    # Conversational follow-ups always go to Hermes (they need conversation context)
    if _is_conversational(msg):
        return None

    # Explicit "run `cmd`" / "jalanin `cmd`" → deterministic raw shell (beats task routing)
    run_cmd = _detect_explicit_run(message)
    if run_cmd:
        return run_cmd

    # Explicit "remember this" → store the fact (save_correction also feeds the KB)
    rm = _REMEMBER_RE.search(message)
    if rm:
        fact = _REMEMBER_RE.sub('', message).strip(' :,.-').strip()
        if len(fact) >= 4:
            return {"tool": "save_correction", "rule": fact}

    # Explicit SOP / runbook / recall questions → knowledge base (only when enabled)
    if config.DIFY_KB_ENABLED and any(kw in msg for kw in _KB_KW):
        return {"tool": "knowledge_base", "query": message}

    # Claude profile auth (login claude-kantor) → direct interactive shell, bypass Claude Code
    auth = _detect_claude_auth(msg)
    if auth:
        return auth

    # Claude usage/limit queries — before task keywords (so "limit" doesn't trigger run_task)
    if any(kw in msg for kw in _LIMIT_KW):
        return {"tool": "claude_limit"}

    # "What's deployed?" listing — before task keywords so "deploy apa aja" lists instead
    # of being treated as a "deploy X" task.
    if any(kw in msg for kw in _DEPLOY_KW):
        return {"tool": "deployments"}

    # Version queries — before task keywords
    if _VERSION_KW.search(msg):
        result = _detect_version(msg, message)
        if result:
            return result

    # Auth / login / account status
    for kw, cmd in _AUTH_COMMANDS.items():
        if kw in msg:
            return _shell(cmd)
    if any(kw in msg for kw in _AUTH_KW):
        return _shell("gh auth status 2>&1; echo '---'; git config --global user.name; git config --global user.email")

    # Server health
    if any(kw in msg for kw in _HEALTH_KW):
        return {"tool": "server_health"}

    # Docker logs
    if any(kw in msg for kw in _LOGS_KW):
        m = _CONTAINER_RE.search(message)
        container = m.group(1) if m else ""
        if container and container.lower() not in {'dari', 'for', 'of', 'the', 'ini', 'docker'}:
            return {"tool": "docker_logs", "container": container}
        return {"tool": "docker_status"}

    # Docker status (explicit)
    if any(kw in msg for kw in _DOCKER_STATUS_KW):
        return {"tool": "docker_status"}

    # Docker (generic)
    if any(kw in msg for kw in _DOCKER_GENERIC_KW):
        if any(w in msg for w in ('status', 'list', 'ls', 'jalan', 'running', 'ada', 'cek', 'lihat', 'apa')):
            return {"tool": "docker_status"}

    # Task phrases (specific enough for substring match)
    if any(kw in msg for kw in _TASK_PHRASES):
        return {"tool": "run_task", "description": message}

    # Task words (need word boundary — avoids "keinstall" matching "install")
    if _TASK_WORDS_RE.search(msg):
        return {"tool": "run_task", "description": message}

    # Folder / file listing
    if any(kw in msg for kw in _FOLDER_KW):
        return _shell(f"ls -la {_find_path(msg)}")

    return None  # pure chat → Hermes
