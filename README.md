# Shaula — Discord DevOps Bot

A Discord bot for operating a single Ubuntu 24.04 VPS. It exposes two Discord identities
running inside **one Python process**:

- **Shaula** (`ShaulaBot`) — the **executor**. Runs real DevOps work by spawning the
  [Claude Code](https://claude.com/claude-code) CLI (`/usr/bin/claude`) as a subprocess.
  Owns all session threads and addresses the user as *"Shisou"*. This is the client that
  actually connects in production.
- **Emilia** (`DevOpsBot`) — an optional **front-end persona** powered by a small local
  model (Hermes 3B via Ollama) for chat and intent routing. In the current production
  setup this in-repo Emilia is **disabled** (see [`EMILIA_ENABLED`](#emilia_enabled--the-two-deployment-modes)).

Real work is done full-auto by Claude Code; only `/deploy` is gated behind an approval
button. For the full architecture, message flow, and internals, see
[`AGENTS.md`](AGENTS.md). Runtime operating rules for the executing agent live in
[`CLAUDE.md`](CLAUDE.md).

---

## Requirements

- **Ubuntu 24.04** (or similar Linux) — the bot is designed to operate its host VPS.
- **Python 3.12+**
- **[Claude Code CLI](https://claude.com/claude-code)** at `/usr/bin/claude` (or set
  `CLAUDE_BIN`). Authenticated either via `claude login` (Pro/Max subscription) or an
  `ANTHROPIC_API_KEY`.
- **Docker** (optional) — for the `/docker-status` and `/docker-logs` commands.
- **Ollama** (optional) — only needed if you enable the in-repo Emilia front-end
  (`EMILIA_ENABLED=true`).
- One or two **Discord bot applications** (one for Shaula, optionally one for Emilia),
  created at the [Discord Developer Portal](https://discord.com/developers/applications).

Python dependencies (`requirements.txt`): `discord.py`, `psutil`, `docker`,
`python-dotenv`.

---

## Setup

```bash
# 1. Clone
git clone git@github.com:hafizhrf/shaula.git
cd shaula

# 2. Create a virtualenv and install deps
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# edit .env and fill in your tokens/IDs (see "Configuration" below)

# 4. Run
./venv/bin/python bot.py
```

On boot the log confirms the mode, e.g. *"Emilia client DISABLED … Running Shaula-only in
this process"*.

### Discord application setup

1. Create an application at the Discord Developer Portal → **Bot** → add a bot, copy the
   **token** into `SHAULA_DISCORD_TOKEN` (and `DISCORD_TOKEN` for Emilia if you use it).
2. Under **Bot → Privileged Gateway Intents**, enable **Message Content Intent**.
3. Invite the bot to your server with the `bot` and `applications.commands` scopes.
4. Put your server's ID in `DISCORD_GUILD_ID` (right-click the server → *Copy Server ID*;
   Developer Mode must be on). Slash commands are synced to this guild on startup.

---

## Configuration

Configuration is read from `.env` via `python-dotenv`. `config.py` is the **canonical**
list of every setting; `.env.example` mirrors it with placeholder values. **Copy
`.env.example` to `.env` — never commit `.env`** (it is git-ignored).

**Required**

| Var | Meaning |
|-----|---------|
| `DISCORD_TOKEN` | Emilia's bot token. Required by config even in Shaula-only mode (it's owned by the external gateway there). |
| `DISCORD_GUILD_ID` | The Discord server (guild) ID where slash commands are registered. |

**Most-used optional settings** (full list and defaults in `config.py`):

| Var | Default | Meaning |
|-----|---------|---------|
| `SHAULA_DISCORD_TOKEN` | *(blank)* | Shaula's bot token. Blank = Shaula disabled. |
| `EMILIA_ENABLED` | `true` | Whether the in-process Emilia client connects. See below. |
| `ANTHROPIC_API_KEY` | *(blank)* | Blank = use `claude login` OAuth. Set `sk-ant-…` for API-key auth. |
| `CLAUDE_BIN` | `/usr/bin/claude` | Path to the Claude Code CLI. |
| `CLAUDE_MODEL` | *(blank)* | Model for tasks. Blank = account default. e.g. `sonnet`. |
| `CLAUDE_MAX_BUDGET_USD` | `0` | Per-task cost cap. `0` = no cap. |
| `PROJECTS_BASE_DIR` | `/opt/agent/projects` | Base cwd for session working directories. |
| `DEVOPS_ROLE_ID` / `ADMIN_ROLE_ID` | `0` | Roles allowed to approve `/deploy`. `0` = unset. |
| `STATUS_CHANNEL_ID` | `0` | Channel for restart/up notices. `0` = auto. |

Other groups: execution watchdog (`EXEC_IDLE_TIMEOUT_SECONDS`,
`EXEC_MAX_TIMEOUT_SECONDS`, `CLAUDE_SESSION_IDLE_SECONDS`), Ollama (`OLLAMA_HOST`,
`OLLAMA_MODEL`, `OLLAMA_KEEP_ALIVE`), Dify RAG (`DIFY_*`), Cloudflare DNS (`CF_*`,
`VPS_PUBLIC_IP`), and the delegation intake (`DELEGATE_INTAKE_PORT`,
`DELEGATE_INTAKE_TOKEN`). See `.env.example` for the annotated template.

### `EMILIA_ENABLED` & the two deployment modes

The bot ships two Discord identities in one process. `EMILIA_ENABLED` picks the mode:

- **`EMILIA_ENABLED=true` (in-process Emilia + Shaula, the legacy default)**
  Emilia's Discord client connects here and owns the slash-command tree. She runs a local
  **Hermes 3B (Ollama)** model for chat, persona, and instant keyword→tool routing, and
  hands real work to Shaula. Requires Ollama running. Use this for an all-in-one
  single-process deployment.

- **`EMILIA_ENABLED=false` (Shaula-only, the current production config)**
  The in-process Emilia client does **not** connect, and the in-repo Ollama/Hermes stack
  stays dormant. Emilia's "brain" instead runs as a **separate service** (the
  `hermes-agent` gateway) that owns the Emilia Discord identity and delegates tasks to
  Shaula over a localhost HTTP intake. To keep `/task`, `/runs`, etc. working, the slash
  commands move onto Shaula's own app. This avoids a Discord token collision (two
  processes can't both connect the same Emilia token) and lets the heavy Claude executor
  and the lightweight persona scale/restart independently.

Practically: set `EMILIA_ENABLED=false` when Emilia is run elsewhere (or you don't want
the local-model persona); set it `true` to run the built-in Emilia + Shaula together in
this one process.

---

## Slash commands

| Command | Notes |
|---------|-------|
| `/task <description>` | Full-auto Claude task in a session thread. |
| `/task-kantor <description>` | Same, using the fallback Claude account (rate-limit escape). |
| `/task-file <path> [kantor]` | Read a plan file (≤100 KB) and run it as a task. |
| `/deploy <target>` | **Approval-gated**: plan → approve → execute. |
| `/runs [limit]` · `/run <id>` | Query the durable run-history DB. |
| `/stop-task [task_id]` | Stop a running task and close its session. |
| `/server-health` | CPU / RAM / disk / uptime. |
| `/docker-status` · `/docker-logs <name>` | Docker status/logs via the Docker SDK. |
| `/readfile <path>` | Show a file in Discord. |

`/corrections` and `/reset-session` are Emilia-side and only registered when
`EMILIA_ENABLED=true`.

---

## Running as a service (systemd)

A unit file is provided at [`deploy/discord-devops-bot.service`](deploy/discord-devops-bot.service).
It runs `venv/bin/python bot.py` as the `ubuntu` user with the `.env` file as its
`EnvironmentFile`.

```bash
sudo cp deploy/discord-devops-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now discord-devops-bot

# manage
systemctl is-active discord-devops-bot
journalctl -u discord-devops-bot -n 50 --no-pager
sudo systemctl restart discord-devops-bot
```

> ⚠️ The bot runs **as** the `discord-devops-bot` service and can operate its own host.
> Never restart/stop the service from *inside* a task — it kills its own run. See
> `CLAUDE.md`.

---

## Security notes

- **`.env` holds live secrets** (bot tokens, API keys, Cloudflare token, intake secret)
  and is git-ignored. Only `.env.example` (placeholders) is committed. If a token ever
  lands in git history, rotate it.
- Shaula executes real shell/file/deploy actions with the host user's privileges
  (passwordless sudo on the target VPS). Run it only on a box you intend it to operate,
  and restrict who can invoke `/task`/`/deploy` via the Discord role IDs.

---

## Documentation

- [`AGENTS.md`](AGENTS.md) — architecture, message flow, sessions, task pipeline (dev reference).
- [`CLAUDE.md`](CLAUDE.md) — runtime operating rules for the executing agent.
