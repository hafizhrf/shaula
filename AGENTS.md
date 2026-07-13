# Discord DevOps Bot — Development Guide

Instructions for AI coding assistants and developers working on this codebase. Everything
here is derived from the actual source; when in doubt, the filesystem is the source of
truth. For the *runtime operating rules* an agent must follow while executing tasks on
this VPS, see `CLAUDE.md` (this file is the architecture/dev reference).

## What This Is

A Discord bot for operating a single Ubuntu 24.04 VPS, built around **two Discord
identities running in one Python process**:

- **Emilia** (`DevOpsBot`) — the front-end persona. Built-in implementation is powered by
  a small local model (Hermes 3B via Ollama) for chat, intent routing, and lightweight
  tools. Addresses the user as **"Apis"**. **As of the current deployment this in-repo
  Emilia is disabled (`EMILIA_ENABLED=false`)** — Emilia now runs as a separate service,
  the **`hermes-agent` at `/home/ubuntu/workspace/hermes-agent`**, which owns the Emilia
  Discord identity and delegates real work to Shaula here (see "Two deployment modes").
- **Shaula** (`ShaulaBot`) — the executor. Runs real work by spawning the **Claude Code
  CLI** (`/usr/bin/claude`) as a subprocess. Owns all session threads. Addresses the user
  as **"Shisou"**. This is the only client actually connecting in production.

Both clients live in the same process so they share **in-memory session state**
(`services/claude_session.py`) — the busy-guard, routing, and idle-sweep must all see the
same dict, which is only possible in-process. `services/bots.py` is the shared registry
that lets either client resolve a channel under the right identity without import cycles.

### Two deployment modes (set by env)

| Env | Meaning |
|-----|---------|
| `EMILIA_ENABLED` (default `true`) | Whether Emilia's Discord client connects in *this* process. |
| `SHAULA_ENABLED` (= `SHAULA_DISCORD_TOKEN` is set) | Whether Shaula's client runs. |

- **In-process Emilia + Shaula** (`EMILIA_ENABLED=true`, Shaula token set): `DevOpsBot`
  owns the slash-command tree (`COGS`); Shaula stays bare and only executes/owns threads.
- **Shaula-only** (`EMILIA_ENABLED=false`) — **the current production configuration.**
  Emilia's "brain" lives in a *separate* process: the **`hermes-agent`** service at
  `/home/ubuntu/workspace/hermes-agent`, which owns `DISCORD_TOKEN` (the Emilia identity)
  and delegates tasks here over the localhost intake (see "Delegation Intake"). In this
  mode the **in-repo Ollama/Hermes stack is dormant** — `DevOpsBot` never connects, so
  `conversation.py`, `intent_router.py`, `_handle_tool_call`, and the Emilia-side
  KB/memory never run in this process. To keep `/task`, `/runs`, etc. working on a live
  client, the slash commands move onto Shaula's app (`SHAULA_COGS`). Startup log confirms:
  "Emilia client DISABLED … Running Shaula-only in this process".

## Design Properties (the lens for any change)

These are observed invariants of the code, not aspirations — preserve them:

- **Session continuity is the on-disk transcript, not a live process.** No `claude`
  process is held open. Each prompt spawns a short-lived `claude` that exits; turn 1 uses
  `--session-id <uuid>` (creates the transcript), turn 2+ uses `--resume <uuid>` (rehydrates
  it). This survives a bot restart — the transcript on disk is the state. See
  `services/claude_session.py` and `_try_revive_session()` in `bot.py`.
- **Peripherals are fail-safe; the task path is sacred.** `run_store` (SQLite),
  `dify_kb` (RAG), `claude_limits`, and `memory_capture` all swallow their own errors and
  log — a broken DB/KB/network must never stop a task from running. Don't add a peripheral
  that can raise into `_execute_and_stream`.
- **`/task` is full-auto; only `/deploy` gates on approval.** `run_task_flow` runs every
  task immediately (Claude itself runs with `--permission-mode auto`); dangerous tasks get
  a visible ⚠️ heads-up and the Stop button is the safety valve. The plan/approval
  machinery (`run_planning`, `ApprovalView`) is wired only into `/deploy` and the
  Hermes-side dangerous-task confirmation.
- **Two LLMs, two jobs.** Hermes (local, free, 3B) is the router/persona for the Emilia
  front-end; Claude Code is the executor. Never route execution through Hermes or chat
  through Claude.
- **Never restart the service from inside a task.** The bot runs *as* the
  `discord-devops-bot` systemd service; a task that restarts it kills its own run. Code
  changes take effect only after an *external* restart. (Repeated in `CLAUDE.md` because
  the executing agent must obey it.)

## Runtime & Deployment

- **Service:** `deploy/discord-devops-bot.service` (systemd, `User=ubuntu`,
  `Group=docker`, `Restart=on-failure`). Runs `venv/bin/python bot.py` with
  `WorkingDirectory=/home/ubuntu/workspace/discord-devops-bot` and
  `EnvironmentFile=…/.env`.
- **Restart / logs:**
  ```bash
  sudo systemctl restart discord-devops-bot
  systemctl is-active discord-devops-bot
  journalctl -u discord-devops-bot -n 50 --no-pager   # logs (journald, SyslogIdentifier=discord-devops-bot)
  ```
- **Deps:** `requirements.txt` (`discord.py`, `psutil`, `docker`, `python-dotenv`). Note
  `aiohttp` is used (for the delegation intake) and ships with discord.py.
- **Config:** `config.py` reads `.env` via `python-dotenv`. `.env.example` is **stale** —
  it predates several vars (Shaula token, Dify, kantor account, compaction threshold,
  etc.). Treat `config.py` as the canonical list of settings.

## Project Structure

File counts shift; the filesystem is canonical. Load-bearing entry points:

```
discord-devops-bot/
├── bot.py                       # Entry point. DevOpsBot (Emilia) + ShaulaBot, on_message routing,
│                                #   tool dispatch (_handle_tool_call), session revive, idle sweeper,
│                                #   graceful restart notice, main() runs both clients concurrently.
├── config.py                    # All env-backed settings (CANONICAL — .env.example is stale).
├── commands/                    # discord.py Cogs (slash commands)
│   ├── task.py                  # /task /task-kantor /task-file + _execute_and_stream (core run pipeline)
│   ├── deploy.py                # /deploy (the only approval-gated flow)
│   ├── runs.py                  # /runs /run  (query the run-history DB)
│   ├── stop_task.py             # /stop-task
│   ├── docker_cmds.py           # /docker-status /docker-logs
│   ├── health.py                # /server-health
│   ├── readfile.py              # /readfile
│   ├── corrections.py           # /corrections (Emilia-side; manage learned rules)
│   ├── reset_session.py         # /reset-session (Emilia-side; clear chat history)
│   └── ask.py                   # /ask — present but NOT in COGS (legacy/unused, see Gotchas)
├── services/
│   ├── claude_runner.py         # Builds & runs the `claude` subprocess; stream-json parsing;
│   │                            #   run_planning / run_execution / run_compaction / run_shell_interactive;
│   │                            #   SHAULA_PERSONA; Hermes RAM management.
│   ├── claude_session.py        # In-memory Session registry (per channel), idle sweep, busy-guard.
│   ├── conversation.py          # Emilia's Hermes chat: system prompt, per-channel history, tool parsing.
│   ├── intent_router.py         # Fast 0ms keyword→tool routing (runs before Hermes).
│   ├── task_store.py            # In-memory live task registry (busy-guard / kill-all). Wiped on restart.
│   ├── run_store.py             # Durable SQLite run-history (data/runs.db). Fail-safe.
│   ├── claude_limits.py         # Cache + format Claude rate-limit windows from rate_limit_event.
│   ├── shell_tool.py            # Shell policy: is_safe (untrusted) / is_fatal (all) + run().
│   ├── deployments.py           # nginx vhost + workspace-app listing (deployments tool).
│   ├── monitor.py               # psutil system health + docker SDK container status/logs.
│   ├── dify_kb.py               # Dify RAG client (retrieve/ingest). Gated by DIFY_KB_ENABLED.
│   ├── memory_capture.py        # Background "learn a durable fact" → Dify. Gated.
│   ├── skill_manager.py         # skills/corrections.json + skills/index.json read/write/run.
│   ├── stdin_relay.py           # Discord message → subprocess stdin (interactive auth flows).
│   ├── bots.py                  # Shared client registry (emilia/shaula) + addressed_to_emilia().
│   └── ollama_router.py         # Older intent classifier — only imported by the unused ask.py.
├── views/
│   ├── session_view.py          # StopSessionView, HapusThreadView (session lifecycle buttons).
│   └── approval_view.py         # ApprovalView/DangerousApprovalView + embed builders (used by /deploy).
├── skills/
│   ├── index.json               # Registry of Emilia-created skill scripts ({} by default).
│   └── corrections.json         # Learned lessons, injected into every Hermes system prompt.
├── data/runs.db                 # SQLite run-history (created at runtime).
├── deploy/discord-devops-bot.service
├── CLAUDE.md                    # Runtime operating rules for the executing agent.
└── AGENTS.md                    # This file.
```

## Message Flow (Emilia front-end)

`DevOpsBot.on_message` (`bot.py`) processes a non-slash, non-bot message in priority
order:

1. **Auth-code catch** — a stray OAuth/auth code arriving with no waiting process → hint.
2. **stdin relay** — if a subprocess is waiting (`stdin_relay.is_waiting`), the message is
   its stdin (`cancel` aborts).
3. **Pending dangerous task** — a prior dangerous `run_task` is awaiting `ya`/`gak`.
4. **Session lifecycle** —
   - With Shaula on: Emilia stays out of threads entirely; in the main channel she defers
     to Shaula when a live session exists and the message isn't addressed to her.
   - Single-bot mode: Emilia handles `stop session` / `hapus thread` / continue-session
     herself.
5. **Attachment → task** — text/image attachments in the main channel are bundled into a
   prompt and handed to Shaula (`_augment_prompt_with_attachments`).
6. **Routing** — `intent_router.detect_tool()` (instant keyword match, no LLM) → if hit,
   run the tool; else `conversation.chat()` asks Hermes, which may emit a tool-call JSON.

### Tools (the Hermes/intent tool catalog)

Dispatched by `_handle_tool_call` in `bot.py`. Each returns a short summary string that's
appended to conversation history:

`server_health`, `docker_status`, `docker_logs`, `shell`, `run_task`, `save_correction`,
`create_skill`, `run_skill`, `claude_limit`, `deployments`, `knowledge_base`.

The tool contract Hermes is taught lives in `conversation.py::_SYSTEM_PROMPT_BASE`
(`_VALID_TOOLS` is the validation set). `intent_router.py` pre-empts Hermes for clear
cases (health, docker, limits, deployments, version checks, auth status, explicit
`run \`cmd\``, "remember this", folder listing, and task keywords).

## Two LLM Systems

> **Production note:** with `EMILIA_ENABLED=false`, the **Hermes/Ollama column below is
> dormant in this process** — the local-model front-end is now the separate `hermes-agent`
> service. The code stays in-repo (and is used if you flip `EMILIA_ENABLED=true`), but in
> the current deployment only the Claude Code path runs here.

| | Hermes (Emilia) — *dormant in prod* | Claude Code (Shaula) |
|---|---|---|
| Where | Ollama at `OLLAMA_HOST` (default `localhost:11434`), model `OLLAMA_MODEL` (`hermes3:3b`) | `/usr/bin/claude` (`CLAUDE_BIN`) subprocess |
| Role | chat, persona, intent/tool routing | real execution (files, shell, deploys) |
| Calls | `conversation.py` (`chat`/`followup`/`oneshot`), `ollama_router.py` | `claude_runner.py` |
| Cost | local/free | metered; rate-limited per account |

**Hermes RAM management** (`claude_runner.py`): before a Claude task, if system RAM > 75%
Hermes is unloaded (`keep_alive=0`) and reloaded in the background after. `OLLAMA_KEEP_ALIVE`
controls idle unload (default `10m`).

## Sessions & Threads (`claude_session.py`)

- One `Session` per Discord channel/thread; `project_dir =
  $PROJECTS_BASE_DIR/session-{channel_id}` is the stable cwd across turns.
- Fields: `session_id` (uuid → names the on-disk transcript & thread-title prefix),
  `started`, `busy` (concurrency guard), `current_task`, `config_dir` (account),
  `turns`, `is_thread`, `origin_channel_id`, `active_msg`, `last_context_tokens`.
- **Idle close:** `CLAUDE_SESSION_IDLE_SECONDS` (default 1800). A 60s sweeper
  (`_session_idle_sweeper`) closes idle sessions, posts a notice, and archives the thread.
  When Shaula is on, **Shaula** owns the sweep so messages speak as Shaula and the two
  clients don't race the shared dict.
- **Revive after restart:** a thread titled `[xxxxxxxx]` whose in-memory session was lost
  is rebuilt from the run-history DB (`_try_revive_session`) and resumed from the
  transcript.
- **Lifecycle UI:** `views/session_view.py` — `StopSessionView` (mirrors `stop session`)
  and `HapusThreadView` (mirrors `hapus thread`). Buttons are retired at the start of every
  new turn via `_clear_active_button`.

## Task Execution Pipeline

`commands/task.py::_execute_and_stream` is the heart:

1. Resolve/create the session (in a thread); guard against concurrent runs (`sess.busy`).
2. **Auto-compaction check** (turn 2+): if `sess.last_context_tokens >=
   CLAUDE_COMPACT_THRESHOLD_TOKENS`, post a heads-up, run `claude_runner.run_compaction`
   (`/compact` over `--resume`), then continue. Threshold `0` disables it.
3. Record the run in `run_store` (durable, fail-safe).
4. `claude_runner.run_execution` spawns `claude --print --verbose --permission-mode auto
   --output-format stream-json --add-dir <project> --add-dir /home/ubuntu/workspace
   --append-system-prompt <SHAULA_PERSONA> [--model] [--max-budget-usd]
   (--session-id | --resume) <prompt>`.
5. **stream-json reader** parses event types: `assistant` (text → streamed to Discord and
   buffered; per-call `usage` tracked → `task.context_tokens` = max single-call input
   size), `tool_result`, `result` (captures `total_cost_usd`), `rate_limit_event` (cached
   in `claude_limits`; a non-`allowed` status sets a `__RATE_LIMIT__` error signal). Output
   is flushed to a single edited Discord message every `STREAM_EDIT_INTERVAL_SECONDS` (1.5s).
6. **Watchdog:** kill on `EXEC_IDLE_TIMEOUT_SECONDS` (600s no new output) or
   `EXEC_MAX_TIMEOUT_SECONDS` (5400s absolute).
7. On finish: post done/failed embed (output > 1900 chars → attach as file), record a
   short summary to conversation history, mark the run DONE/FAILED, and (if still alive)
   post the "🟢 Session aktif" notice with the Stop button.

`failure_reason()` translates raw errors into a friendly message — special-casing rate
limits (with WIB reset time) and the per-task budget cap.

### Context auto-compaction (`run_compaction`)

`claude_runner.run_compaction(session_id, project_dir, config_dir)` runs `claude --print
--resume <sid> "/compact"`. Verified to work headless on claude 2.1.181 and to keep the
session resumable. It's best-effort: any non-zero exit returns `False` and the caller
proceeds un-compacted (Claude's own auto-compaction is the backstop). Context size is the
**max single-call input** across the turn's `assistant` events (`input_tokens +
cache_read_input_tokens + cache_creation_input_tokens`), stored on `task.context_tokens`,
then carried to `sess.last_context_tokens` for the *next* turn to decide on. Do **not** use
`result.usage` for this — it sums every internal tool-call iteration, so an agentic turn
balloons past the real window size and would false-trigger compaction every turn.

## Persistence: two stores, different jobs

- `task_store.py` — **in-memory** live registry (`TaskRecord`). Backs busy-guards and
  `kill_all_running()`. Wiped on restart.
- `run_store.py` — **durable SQLite** (`data/runs.db`, WAL). One row per turn keyed by
  `task_id`, carrying `session_id`, `account`, `turn`, `cost_usd`, state, timestamps. On
  boot it reconciles any leftover `RUNNING` rows → `INTERRUPTED`. All access is
  fail-safe and off-thread (`asyncio.to_thread`). Queried by `/runs` and `/run`.

## Shell Safety (`shell_tool.py`)

Two gates for the Emilia `shell` tool:
- `is_safe(cmd)` — **untrusted** commands (Hermes-emitted): blocks shell metacharacters
  (`|>&;$\`!`) and dangerous verbs.
- `is_fatal(cmd)` — **all** commands, even `trusted`: blocks only catastrophic patterns
  (`rm` on root/home/system dirs, `dd`/`mkfs`/`wipefs`, raw block-device writes, fork
  bombs, recursive chmod/chown on `/`, power-state changes). Targeted deletes (e.g.
  `rm -rf /tmp/x`) pass — per the full-auto preference.

Router-vetted commands set `trusted: true` (skip `is_safe`); `raw: true` posts output
verbatim with no Hermes round-trip. Interactive auth flows (`gh auth`, `claude auth`, …)
detected by `needs_interactive` route to `run_shell_interactive` with `stdin_relay`.

## Knowledge Base & Memory (gated, optional)

- `dify_kb.py` — local Dify RAG (`DIFY_KB_ENABLED` = dataset id + key both set). `retrieve`
  backs the `knowledge_base` tool; `ingest` is fed by `save_correction`.
- `memory_capture.py` — after a chat, a background Hermes pass decides if a *durable* fact
  was revealed and ingests it (deduped by vector similarity). RAG memory, not fine-tuning.

## Rate Limits (`claude_limits.py`)

Source of truth is the `rate_limit_event` in Claude Code's stream-json, captured for free
during every task. Tracks `five_hour` / `seven_day` / `seven_day_opus` windows
(status + resetsAt; no exact %). `probe()` fires a tiny request when the cache is stale.
Surfaced via the `claude_limit` tool.

## Skills & Corrections (`skill_manager.py`)

- `skills/corrections.json` — lessons Apis taught Emilia; injected into every Hermes
  system prompt (`_build_system_prompt`). Added via the `save_correction` tool /
  `/corrections`.
- `skills/index.json` — registry of Emilia-created skill scripts (run via `run_skill`,
  created via `create_skill` which dispatches a Claude task to write + register the script).

## Slash Commands

Loaded as Cogs. `COGS` (Emilia/in-process) vs `SHAULA_COGS` (when `EMILIA_ENABLED=false`).

| Command | File | Notes |
|---|---|---|
| `/task <description>` | task.py | Full-auto Claude task in a session thread. |
| `/task-kantor <description>` | task.py | Same, using the "kantor" Claude account (`CLAUDE_KANTOR_CONFIG_DIR`) — rate-limit fallback. |
| `/task-file <path> [kantor]` | task.py | Read a plan file (≤100 KB) and run it as a task. |
| `/deploy <target>` | deploy.py | **Approval-gated**: plan → ApprovalView → execute. |
| `/runs [limit]` | runs.py | Recent run history. |
| `/run <id>` | runs.py | All turns of one task/session id. |
| `/stop-task [task_id]` | stop_task.py | Stop a running task + close the session. |
| `/server-health` | health.py | CPU/RAM/disk/uptime. |
| `/docker-status` · `/docker-logs <name>` | docker_cmds.py | Docker via SDK. |
| `/readfile <path>` | readfile.py | Show a file in Discord. |
| `/corrections [remove]` | corrections.py | Emilia-side: manage learned rules (not in SHAULA_COGS). |
| `/reset-session` | reset_session.py | Emilia-side: clear chat history (not in SHAULA_COGS). |

`/ask` exists (`commands/ask.py`) but is **not** in either cog list — not registered.

## Delegation Intake (HTTP)

`ShaulaBot._start_delegation_intake` runs an aiohttp server on
`127.0.0.1:DELEGATE_INTAKE_PORT` (default 8765). `POST /delegate {task, channel_id,
user_id?}` with header `X-Intake-Token: <DELEGATE_INTAKE_TOKEN>` hands a task to
`run_task_flow`. This is how the *external* Emilia (hermes gateway) delegates work to
Shaula here. Bound to localhost; the token is a shared secret.

## Configuration (`config.py`)

Required: `DISCORD_TOKEN`, `DISCORD_GUILD_ID`. Notable optional vars:

- **Identities/modes:** `SHAULA_DISCORD_TOKEN`, `EMILIA_ENABLED`, `STATUS_CHANNEL_ID`,
  `DEVOPS_ROLE_ID`, `ADMIN_ROLE_ID`.
- **Claude:** `ANTHROPIC_API_KEY` (blank = use `claude login` OAuth), `CLAUDE_BIN`,
  `CLAUDE_MODEL` (blank = account default), `CLAUDE_KANTOR_CONFIG_DIR`,
  `CLAUDE_MAX_BUDGET_USD` (0 = no cap), **`CLAUDE_COMPACT_THRESHOLD_TOKENS`** (default
  120000, 0 = off).
- **Execution:** `PROJECTS_BASE_DIR`, `STREAM_EDIT_INTERVAL_SECONDS`,
  `EXEC_IDLE_TIMEOUT_SECONDS`, `EXEC_MAX_TIMEOUT_SECONDS`, `CLAUDE_SESSION_IDLE_SECONDS`,
  `APPROVAL_TIMEOUT_SECONDS`, `RUN_DB_PATH`, `LOG_LEVEL`.
- **Ollama:** `OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_KEEP_ALIVE`.
- **Dify RAG:** `DIFY_BASE_URL`, `DIFY_DATASET_ID`, `DIFY_DATASET_API_KEY`.
- **Cloudflare (DNS automation, consumed by `/opt/agent/bin/cf-dns`):** `CF_API_TOKEN`,
  `CF_DOMAIN`, `CF_ZONE_ID`, `CF_PROXY`, `VPS_PUBLIC_IP`.
- **Delegation:** `DELEGATE_INTAKE_PORT`, `DELEGATE_INTAKE_TOKEN`.

## How to Add Things

- **A new Hermes/intent tool:** add a `{"tool": ...}` contract line to
  `_SYSTEM_PROMPT_BASE` and the name to `_VALID_TOOLS` in `conversation.py`; handle it in
  `_handle_tool_call` (`bot.py`); optionally pre-route it in `intent_router.detect_tool`.
- **A new slash command:** add a Cog under `commands/`, append its module path to `COGS`
  (and `SHAULA_COGS` if it must work in Shaula-only mode).
- **A new config var:** add it to `config.py` via `_optional`/`_require`/`_float_or_zero`
  with a comment. `.env.example` is stale — keeping it current is optional, but `config.py`
  must carry the doc.
- **Behavior that runs *as a task*** (real VPS work) belongs in the Claude side
  (`run_task_flow` / `_execute_and_stream`), not the Hermes tool layer.

## Gotchas

- `EMILIA_ENABLED=false` in production: Emilia's brain is the **`hermes-agent`** service
  (`/home/ubuntu/workspace/hermes-agent`), which owns the Emilia Discord identity and
  delegates to Shaula here. This process is **Shaula-only**, owns the slash commands via
  `SHAULA_COGS`, and the in-repo Ollama/Hermes stack does not run. Flip `EMILIA_ENABLED=true`
  to revive the built-in Emilia.
- `commands/ask.py` and `services/ollama_router.py` are **legacy/unused** — `ask.py` is not
  in any cog list, and `ollama_router.py` is only imported by `ask.py`. Don't build on them.
- `.env.example` lags `config.py` — read `config.py` for the real settings.
- The bot runs as the systemd service it could be asked to manage — **never** restart/stop
  `discord-devops-bot` from inside a task (it kills its own run). See `CLAUDE.md`.
- Persona is injected at runtime via `--append-system-prompt` (`SHAULA_PERSONA`), not via
  `CLAUDE.md`. The executing agent on the Claude side is **Shaula** (calls the user
  "Shisou"); Emilia is the front-end voice.
