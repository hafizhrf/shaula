import asyncio
import io
import json
import logging
import os
import re
import signal

import discord
from discord.ext import commands

import config
import services.intent_router as _intent_router
from services import conversation, monitor, task_store, skill_manager, stdin_relay, bots, shell_tool

# Pending Claude Code task waiting for user confirmation
# {channel_id: (description, creator_id, guild_id)}
_pending_tasks: dict[int, tuple[str, int, int]] = {}

_YES = {'ya', 'ok', 'oke', 'yes', 'lanjut', 'boleh', 'y', 'go', 'yep', 'run', 'gas', 'gass'}
_NO  = {'gak', 'tidak', 'cancel', 'batalkan', 'no', 'n', 'stop', 'batal', 'nope', 'gausah'}

# Intent detection for stop/delete. We strip filler/politeness words, then check
# whether what's LEFT is only stop-verbs (or delete-verbs). So "stop aja", "udah cukup
# deh", "hapus threadnya" all match, while a real prompt like "stop nginx service" or
# "hapus file lama" keeps a non-verb word and passes through to Claude.
_FILLER = {
    'aja', 'saja', 'dong', 'donk', 'ya', 'yah', 'deh', 'dulu', 'sekarang', 'kak',
    'emilia', 'mbak', 'tolong', 'please', 'ok', 'oke', 'okeh', 'nih', 'sih', 'lah',
    'kok', 'nya', 'gpp', 'udh', 'sii', 'plis', 'thx', 'makasih',
}
_SCOPE = {'session', 'sesi', 'sessionnya', 'sesinya', 'claude', 'ini', 'itu', 'thread', 'threadnya'}
_STOP_CORE = {
    'stop', 'selesai', 'exit', 'tutup', 'keluar', 'udahan', 'udah', 'cukup', 'done',
    'end', 'akhiri', 'berhenti', 'quit', 'close', 'bye', 'dadah', 'kelar',
}
_DELETE_CORE = {'hapus', 'delete', 'remove', 'hilangkan', 'buang'}


def _intent_core(text: str) -> list[str]:
    """Word tokens with filler and scope words removed — the actual verbs that remain."""
    toks = re.findall(r'[a-z]+', text.lower())
    return [t for t in toks if t not in _FILLER and t not in _SCOPE]


def _is_session_stop(text: str) -> bool:
    core = _intent_core(text)
    return bool(core) and all(t in _STOP_CORE for t in core)


def _is_thread_delete(text: str) -> bool:
    core = _intent_core(text)
    return bool(core) and all(t in _DELETE_CORE for t in core)


def _addressed_to_emilia(message: discord.Message, bot_user) -> bool:
    """True if the message is talking TO Emilia (so it shouldn't be forwarded to Claude)."""
    if "emilia" in message.content.lower():
        return True
    if bot_user is not None and bot_user in message.mentions:
        return True
    return False


def _kill_session_task(sess) -> bool:
    """If a prompt is currently running in this session, terminate its Claude process."""
    from services import claude_session
    return claude_session.kill_session_task(sess)


async def _announce_status(client, text: str) -> None:
    """Post a status line (restart down/up) to the announce channel: STATUS_CHANNEL_ID if set,
    else the guild's system channel, else the first text channel Emilia can post in."""
    if config.STATUS_CHANNEL_ID:
        # Explicitly pinned → use ONLY this channel. Never fall back to general/system,
        # so a restart notice can't leak into the wrong channel.
        ch = client.get_channel(config.STATUS_CHANNEL_ID)
        if ch is None:
            logger.warning("STATUS_CHANNEL_ID %s not resolvable — skipping announce: %s",
                           config.STATUS_CHANNEL_ID, text)
            return
        await ch.send(text)
        return
    # No pinned channel → best-effort fallback (system channel, else first sendable).
    guild = client.get_guild(config.DISCORD_GUILD_ID)
    ch = None
    if guild and guild.me:
        sysc = guild.system_channel
        if sysc and sysc.permissions_for(guild.me).send_messages:
            ch = sysc
        else:
            ch = next((c for c in guild.text_channels
                       if c.permissions_for(guild.me).send_messages), None)
    if ch is not None:
        await ch.send(text)
    else:
        logger.warning("No status channel available to announce: %s", text)


_SESSION_ID_RE = re.compile(r'^\[([0-9a-f]{8})\]')  # thread-title prefix "[xxxxxxxx]"

# Text-like attachments Emilia will read + forward to Shaula as task context.
_TEXT_ATTACH_EXTS = {
    ".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".env", ".py", ".js", ".ts", ".sh", ".sql", ".html", ".css", ".csv", ".xml",
}
_MAX_ATTACH_BYTES = 200_000  # per-attachment cap, keeps task token usage sane


async def _collect_text_attachments(message):
    """Download text-like attachments on a message. Returns (combined_text, names)."""
    parts, names = [], []
    for att in message.attachments:
        name = att.filename or "file"
        ext = os.path.splitext(name)[1].lower()
        if ext not in _TEXT_ATTACH_EXTS:
            continue
        if att.size and att.size > _MAX_ATTACH_BYTES:
            parts.append(f"=== Lampiran {name} (dilewati: {att.size // 1024}KB > "
                         f"{_MAX_ATTACH_BYTES // 1024}KB) ===")
            names.append(f"{name} (skipped)")
            continue
        try:
            data = await att.read()
            parts.append(f"=== Lampiran: {name} ===\n{data.decode('utf-8', errors='replace')}")
            names.append(name)
        except Exception as e:
            logger.error("Attachment read failed (%s): %s", name, e)
    return ("\n\n".join(parts), names)


# Image attachments: Hermes can't see them, but Claude (Shaula) can via its Read tool.
# We save them under workspace (already --add-dir'd for tasks) and pass the path to Claude.
_IMAGE_ATTACH_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB
_ATTACH_SAVE_DIR = "/home/ubuntu/workspace/.bot-attachments"


async def _save_image_attachments(message):
    """Download image attachments to disk (under workspace). Returns (paths, names)."""
    paths, names = [], []
    for att in message.attachments:
        name = att.filename or "image"
        ext = os.path.splitext(name)[1].lower()
        if ext not in _IMAGE_ATTACH_EXTS:
            continue
        if att.size and att.size > _MAX_IMAGE_BYTES:
            names.append(f"{name} (skipped: {att.size // (1024*1024)}MB)")
            continue
        dest_dir = os.path.join(_ATTACH_SAVE_DIR, str(message.id))
        try:
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, name)
            await att.save(dest)
            paths.append(dest)
            names.append(name)
        except Exception as e:
            logger.error("Image save failed (%s): %s", name, e)
    return paths, names


async def _augment_prompt_with_attachments(message, base_text):
    """Build a Claude prompt from a message + its attachments: text files inlined, image
    files saved to disk with their paths referenced (Claude reads them via the Read tool).
    Returns (prompt, names). If no usable attachment, returns (base_text, [])."""
    if not message.attachments:
        return base_text, []
    text_part, text_names = await _collect_text_attachments(message)
    image_paths, image_names = await _save_image_attachments(message)
    names = text_names + image_names
    if not names:
        return base_text, []
    base = (base_text or "").strip() or "Tolong lihat & proses lampiran ini ya."
    parts = [base]
    if text_part:
        parts.append(text_part)
    if image_paths:
        parts.append("[Gambar terlampir — baca/analisa pakai Read tool:]\n"
                     + "\n".join(f"- {p}" for p in image_paths))
    return ("\n\n".join(parts), names)


async def _try_revive_session(thread):
    """Rebuild the in-memory Session for a session thread whose live state was lost (e.g. after
    a bot restart — sessions live in memory, but the Claude transcript on disk survives). Reads
    the session id from the thread title `[xxxxxxxx]`, recovers the full id + account from
    run_store, and resumes the on-disk transcript. Returns the revived Session, or None."""
    from services import claude_session, run_store
    m = _SESSION_ID_RE.match(thread.name or "")
    if not m:
        return None
    rows = await run_store.get(m.group(1))
    if not rows:
        return None
    full_sid = rows[-1].get("session_id")
    if not full_sid:
        return None
    account = rows[-1].get("account") or "default"
    sess = claude_session.get_or_start(thread.id)
    sess.session_id = full_sid     # resume the SAME transcript (project_dir keyed by thread id)
    sess.started = True            # turn 2+ → --resume, not a fresh session
    sess.is_thread = True
    sess.turns = len(rows)
    sess.config_dir = config.CLAUDE_KANTOR_CONFIG_DIR if account == "kantor" else None
    logger.info("Revived Claude session %s in thread %s (account=%s)", full_sid[:8], thread.id, account)
    return sess


logger = logging.getLogger(__name__)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

COGS = [
    "commands.health",
    "commands.docker_cmds",
    "commands.task",
    "commands.deploy",
    "commands.reset_session",
    "commands.stop_task",
    "commands.corrections",
    "commands.readfile",
    "commands.runs",
]

# Cogs registered on SHAULA's app when Emilia's brain lives in the external
# hermes gateway (EMILIA_ENABLED=false). The execution/util commands move to
# Shaula so /task, /runs, etc. work again on a live client. The Emilia/Hermes-
# specific cogs (corrections, reset_session) are dropped — hermes owns Emilia's
# conversation now (it has its own /reset, /commands).
SHAULA_COGS = [
    "commands.health",
    "commands.docker_cmds",
    "commands.task",
    "commands.deploy",
    "commands.stop_task",
    "commands.readfile",
    "commands.runs",
]

# OAuth/auth code pattern: long alphanumeric strings (sometimes with # separator)
_OAUTH_CODE_RE = re.compile(r'^[A-Za-z0-9_\-]{15,}[#_\-][A-Za-z0-9_\-]{15,}$')


def _looks_like_auth_code(text: str) -> bool:
    return bool(_OAUTH_CODE_RE.match(text.strip()))


# Shell-command policy + execution now lives in services.shell_tool
# (is_safe = strict gate for untrusted/Hermes cmds, is_fatal = catastrophic-only
# gate applied even to trusted cmds, run = execute). See shell_tool.py.


_ERROR_PATTERNS = (
    "not found", "no such file", "command not found",
    "fatal:", "error:", "permission denied", "cannot", "failed",
)

def _looks_like_error(output: str, returncode: int) -> bool:
    if returncode != 0:
        return True
    lower = output.lower()
    return any(p in lower for p in _ERROR_PATTERNS)


def _status_color(status: str) -> str:
    return {"running": "🟢", "exited": "🔴", "paused": "🟡"}.get(status, "⚪")


async def _handle_tool_call(message: discord.Message, tool: dict) -> str:
    """Execute a tool call and return a summary string for conversation history."""
    name = tool.get("tool", "")

    if name == "server_health":
        stats = await monitor.get_system_health()
        embed = discord.Embed(title="🖥️ Server Health", color=discord.Color.blue())
        embed.add_field(
            name="CPU",
            value=f"`{stats['cpu_percent']}%` | Load: `{stats['load_1']} / {stats['load_5']} / {stats['load_15']}`",
            inline=False,
        )
        embed.add_field(
            name="Memory",
            value=f"`{stats['mem_used_gb']} / {stats['mem_total_gb']} GB` ({stats['mem_percent']}%)",
            inline=True,
        )
        embed.add_field(
            name="Disk",
            value=f"`{stats['disk_used_gb']} / {stats['disk_total_gb']} GB` ({stats['disk_percent']}%)",
            inline=True,
        )
        embed.add_field(name="Uptime", value=f"`{stats['uptime_hours']}h`", inline=True)
        await message.channel.send(embed=embed)
        return (
            f"server_health result: CPU {stats['cpu_percent']}%, "
            f"RAM {stats['mem_used_gb']}/{stats['mem_total_gb']}GB ({stats['mem_percent']}%), "
            f"Disk {stats['disk_used_gb']}/{stats['disk_total_gb']}GB, "
            f"Uptime {stats['uptime_hours']}h"
        )

    elif name == "docker_status":
        containers = await monitor.get_docker_containers()
        if not containers:
            await message.channel.send("No Docker containers found.")
            return "docker_status: no containers"
        embed = discord.Embed(title="🐳 Docker Containers", color=discord.Color.blue())
        rows = "\n".join(
            f"{_status_color(c['status'])} **{c['name']}** — `{c['status']}` — `{c['image']}`"
            for c in containers
        )
        embed.description = rows[:4000]
        embed.set_footer(text=f"{len(containers)} container(s)")
        await message.channel.send(embed=embed)
        summary = ", ".join(f"{c['name']}({c['status']})" for c in containers)
        return f"docker_status: {summary}"

    elif name == "docker_logs":
        container = tool.get("container", "")
        if not container:
            await message.channel.send("⚠️ Nama container tidak disebutkan.")
            return "docker_logs: no container name"
        try:
            logs = await monitor.get_container_logs(container, tail=50)
        except Exception as e:
            await message.channel.send(f"❌ `{e}`")
            return f"docker_logs error: {e}"
        if len(logs) > 1900:
            await message.channel.send(
                f"📄 Logs `{container}`:",
                file=discord.File(io.BytesIO(logs.encode()), filename=f"{container}.log"),
            )
        else:
            await message.channel.send(f"```\n{logs or '(kosong)'}\n```")
        return f"docker_logs {container}: {len(logs)} chars"

    elif name == "shell":
        cmd = tool.get("command", "").strip()
        if not cmd:
            await message.channel.send("⚠️ Command kosong.")
            return "shell: empty command"

        # Auth flows need interactive stdin relay — run in streaming mode
        from services.claude_runner import needs_interactive, run_shell_interactive
        if needs_interactive(cmd):
            await message.channel.send(f"🔐 Menjalankan auth flow: `{cmd}`\n*(Output akan muncul langsung~)*")
            output, returncode = await run_shell_interactive(cmd, message.channel)
            if returncode == 0:
                await message.channel.send("✅ Auth flow selesai!")
            else:
                await message.channel.send(f"⚠️ Auth flow selesai dengan exit code {returncode}.")
            return f"shell_auth '{cmd}' rc={returncode}"

        # Fatal-only gate: blocks catastrophic commands even when trusted.
        if shell_tool.is_fatal(cmd):
            await message.channel.send(
                f"⛔ Command `{cmd[:120]}` itu operasi **fatal** — Emilia nggak jalanin ya, Apis."
            )
            return f"shell: blocked fatal command '{cmd[:80]}'"

        if not tool.get("trusted") and not shell_tool.is_safe(cmd):
            await message.channel.send(
                f"⚠️ Command `{cmd}` tidak aman untuk dijalankan langsung.\n"
                "Gunakan `/task` atau tulis tasknya supaya saya bisa minta approval dulu."
            )
            return f"shell: blocked unsafe command '{cmd}'"
        try:
            output, returncode = await shell_tool.run(cmd)
        except asyncio.TimeoutError:
            await message.channel.send("⏰ Command timeout.")
            return "shell: timeout"
        except Exception as e:
            await message.channel.send(f"❌ Error: `{e}`")
            return f"shell error: {e}"

        display = output if output else "(no output)"
        if len(display) > 1900:
            await message.channel.send(
                file=discord.File(io.BytesIO(display.encode()), filename="output.txt")
            )
        else:
            await message.channel.send(f"```\n{display}\n```")

        # Raw mode (explicit "run `cmd`"): post output only, skip the Hermes round-trip.
        if tool.get("raw"):
            return f"shell '{cmd}' rc={returncode}: {display[:200]}"

        from services import conversation as conv
        if _looks_like_error(output, returncode):
            # Ask Hermes to interpret the error and suggest a fix
            conv.add_message(
                message.channel.id, "user",
                f"[Shell error saat menjalankan `{cmd}` (rc={returncode}):\n{output[:400]}]"
            )
            chat_text, fix_tool = await conv.chat(
                message.channel.id,
                "Ada error tadi. Jelaskan singkat ke Apis apa yang kurang/tidak terinstall, "
                "dan tanyakan apakah mau Emilia perbaiki/install sekarang."
            )
            if chat_text:
                await message.channel.send(chat_text)
            if fix_tool:
                fix_result = await _handle_tool_call(message, fix_tool)
                conv.add_message(message.channel.id, "user", f"[Tool result: {fix_result}]")
        else:
            # Success — ask Hermes to explain results in natural language
            explain = await conv.followup(
                message.channel.id,
                "Jelaskan hasil perintah tadi ke Apis secara singkat dan ramah ya~"
            )
            if explain:
                text, _ = conv.parse_response(explain)
                if text:
                    for chunk in [text[i:i+1900] for i in range(0, len(text), 1900)]:
                        await message.channel.send(chunk)

        return f"shell '{cmd}' rc={returncode}: {display[:200]}"

    elif name == "run_task":
        from commands.task import run_task_flow
        from services.task_store import classify_risk, RiskLevel
        description = tool.get("description", "").strip()
        if not description:
            await message.channel.send("⚠️ Deskripsi task kosong.")
            return "run_task: empty description"

        risk = classify_risk(description)

        if risk == RiskLevel.DANGEROUS:
            _pending_tasks[message.channel.id] = (description, message.author.id, message.guild.id)
            await message.channel.send(
                f"⚠️ Apis, task ini termasuk **operasi berbahaya**!\n"
                f"```\n{description[:180]}\n```\n"
                f"Ketik **ya** untuk lanjut dengan approval, atau **gak** untuk batalkan."
            )
            return f"run_task dangerous pending: {description[:100]}"
        else:
            await run_task_flow(
                description=description,
                creator_id=message.author.id,
                guild_id=message.guild.id,
                channel=message.channel,
            )
            return f"run_task dispatched: {description[:100]}"

    elif name == "save_correction":
        rule = tool.get("rule", "").strip()
        if not rule:
            await message.channel.send("⚠️ Rule kosong, tidak disimpan.")
            return "save_correction: empty rule"
        added = skill_manager.add_correction(rule)
        if added:
            from services import dify_kb
            dify_kb.ingest("correction", rule)  # also store as retrievable KB memory (no-op if KB off)
            all_rules = skill_manager.list_corrections()
            await message.channel.send(
                f"✏️ Emilia catat ya, Apis~\n"
                f"> {rule}\n"
                f"*(Lesson #{len(all_rules)} tersimpan — langsung berlaku)*"
            )
        else:
            await message.channel.send("*(Rule ini sudah Emilia catat sebelumnya~)*")
        return f"save_correction: {'added' if added else 'duplicate'} — {rule[:80]}"

    elif name == "create_skill":
        skill_name = tool.get("name", "").strip()
        description = tool.get("description", "").strip()
        task_desc = tool.get("task", "").strip()
        if not skill_name or not task_desc:
            await message.channel.send("⚠️ Skill name atau task kosong.")
            return "create_skill: missing fields"
        await message.channel.send(f"✨ Emilia buat skill **{skill_name}** ya, Apis~")
        from commands.task import run_task_flow
        full_task = (
            f"{task_desc}\n\n"
            f"After creating the script, register it by appending to "
            f"/home/ubuntu/workspace/discord-devops-bot/skills/index.json: "
            f'{{"{skill_name}": {{"description": "{description}", "file": "{skill_name}.py"}}}}'
        )
        await run_task_flow(
            description=full_task,
            creator_id=message.author.id,
            guild_id=message.guild.id,
            channel=message.channel,
        )
        return f"create_skill dispatched: {skill_name}"

    elif name == "run_skill":
        skill_name = tool.get("name", "").strip()
        if not skill_name:
            skills = skill_manager.list_skills()
            if not skills:
                await message.channel.send("Emilia belum punya skill apapun, Apis~")
            else:
                lines = "\n".join(f"• **{n}** — {v['description']}" for n, v in skills.items())
                await message.channel.send(f"Skill yang Emilia punya:\n{lines}")
            return "run_skill: no name given"
        await message.channel.send(f"⚙️ Menjalankan skill `{skill_name}`...")
        output, rc = await skill_manager.run_skill(skill_name)
        display = output if output else "(no output)"
        if len(display) > 1900:
            import io
            await message.channel.send(
                file=discord.File(io.BytesIO(display.encode()), filename=f"{skill_name}_output.txt")
            )
        else:
            await message.channel.send(f"```\n{display}\n```")
        return f"run_skill {skill_name} rc={rc}: {display[:100]}"

    elif name == "claude_limit":
        from services import claude_limits
        age = claude_limits.snapshot_age()
        if age is None or age > 300:  # empty or older than 5 min → refresh from API
            await message.channel.send("🔍 Emilia cek sisa limit Claude dulu ya, Apis~ *(bentar, nanya ke API)*")
            await claude_limits.probe()
        report = claude_limits.format_report()
        if report:
            embed = discord.Embed(
                title="📊 Sisa Limit Claude",
                description=report,
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="Sumber: rate-limit API Claude. Angka persis (%) cuma ada di /usage interaktif.")
            await message.channel.send(embed=embed)
            return f"claude_limit: {report[:150]}"
        await message.channel.send(
            "Hmm, Emilia belum dapet info limit dari API, Apis. Mungkin lagi limit total atau jaringan—coba lagi sebentar ya~"
        )
        return "claude_limit: no data"

    elif name == "deployments":
        from services import deployments
        sites = await deployments.sites_with_status()
        apps = deployments.list_workspace_apps()
        embed = discord.Embed(title="🌐 Yang ke-deploy di VPS", color=discord.Color.blurple())
        if sites:
            lines = []
            for s in sites:
                dot = "🟢" if s["up"] else ("🔴" if s["up"] is False else "⚪")
                proto = "https" if s["ssl"] else "http"
                where = f"`127.0.0.1:{s['port']}`" if s["port"] else "_(no proxy)_"
                lines.append(f"{dot} [{s['fqdn']}]({proto}://{s['fqdn']}) → {where}")
            embed.add_field(name="Subdomain (nginx)", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Subdomain (nginx)", value="_(belum ada vhost)_", inline=False)
        if apps:
            embed.add_field(
                name="App di workspace", value=", ".join(f"`{a}`" for a in apps)[:1024], inline=False
            )
        embed.set_footer(text="🟢 origin up · 🔴 origin down · ⚪ no proxy port")
        await message.channel.send(embed=embed)
        summary = "; ".join(
            f"{s['fqdn']}(:{s['port'] or '?'},{'up' if s['up'] else 'down' if s['up'] is False else '?'})"
            for s in sites
        )
        return f"deployments: {summary or 'none'} | apps: {', '.join(apps) or 'none'}"

    elif name == "knowledge_base":
        from services import dify_kb, conversation as conv
        query = (tool.get("query", "") or message.content).strip()
        chunks = dify_kb.retrieve(query)
        if not chunks:
            # KB empty / disabled / down → let Hermes answer from its own knowledge
            answer = await conv.followup(message.channel.id, message.content)
            text, _ = conv.parse_response(answer)
            if text:
                for c in [text[i:i + 1900] for i in range(0, len(text), 1900)]:
                    await message.channel.send(c)
            return "knowledge_base: no chunks (Hermes fallback)"
        # Ground Hermes on the retrieved notes (passed transiently, not stored raw in history)
        context = dify_kb.format_chunks(chunks)
        answer = await conv.followup(
            message.channel.id,
            f"Berdasarkan catatan internal ini:\n{context}\n\n"
            f"Jawab pertanyaan Apis: \"{query}\" — ringkas, jelas, pakai gaya Emilia. "
            f"Kalau catatannya nggak nyambung sama pertanyaannya, bilang aja Emilia belum punya catatannya ya."
        )
        text, _ = conv.parse_response(answer)
        if text:
            for c in [text[i:i + 1900] for i in range(0, len(text), 1900)]:
                await message.channel.send(c)
        return f"knowledge_base: {len(chunks)} chunk → dijawab"

    else:
        logger.warning("Unknown tool: %s", name)
        return f"unknown tool: {name}"


async def _continue_session(message: discord.Message) -> None:
    """Send a message as the next prompt to the channel's live Claude session.
    Any attachments (text inlined, images saved to disk) are folded into the prompt so
    Shaula can consume them mid-thread too."""
    from commands.task import _execute_and_stream

    channel = message.channel
    prompt, _names = await _augment_prompt_with_attachments(message, message.content)
    conversation.add_message(channel.id, "user", message.content)
    record = task_store.create_task(
        description=prompt,
        creator_id=message.author.id,
        guild_id=message.guild.id,
        channel_id=channel.id,
    )
    # _execute_and_stream resolves the session itself (resume=True after turn 1).
    await _execute_and_stream(record, channel)


class DevOpsBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True  # needed for on_message content
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        from services import run_store
        run_store.init_db()  # durable run-history DB (data/runs.db); fail-safe
        for cog in COGS:
            await self.load_extension(cog)
            logger.info("Loaded cog: %s", cog)

        guild = discord.Object(id=config.DISCORD_GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info("Synced %d slash commands to guild %d", len(synced), config.DISCORD_GUILD_ID)

    async def on_ready(self):
        bots.emilia = self
        bots.emilia_user_id = self.user.id
        logger.info("Bot ready as %s (ID: %s)", self.user, self.user.id)
        os.makedirs(config.PROJECTS_BASE_DIR, exist_ok=True)
        # Pre-load Ollama model into RAM so first message isn't slow
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, conversation.warmup)
        # Start the idle-session sweeper once (on_ready can fire again on reconnect).
        # When Shaula is on, SHAULA owns session sweeping — so idle-close messages speak as
        # Shaula and the two clients don't race on the shared session dict. Emilia sweeps solo.
        if not config.SHAULA_ENABLED and not getattr(self, "_sweeper_started", False):
            self._sweeper_started = True
            self.loop.create_task(self._session_idle_sweeper())
        # Announce we're up — once per process start (not on every gateway reconnect).
        if not getattr(self, "_startup_announced", False):
            self._startup_announced = True
            try:
                await _announce_status(self, "✅ Emilia udah online lagi, Apis~ (bot baru di-restart)")
            except Exception as e:
                logger.error("startup announce failed: %s", e)

    async def _session_idle_sweeper(self):
        from services import claude_session
        from commands.task import archive_thread
        while not self.is_closed():
            await asyncio.sleep(60)
            for sess in claude_session.sweep_idle():
                channel = bots.shaula_channel(self.get_channel(sess.channel_id))
                if channel:
                    try:
                        from commands.task import _clear_active_button
                        from views.session_view import HapusThreadView
                        await _clear_active_button(sess)
                        await channel.send(
                            f"💤 Session Claude ditutup otomatis (idle "
                            f"{claude_session.IDLE_TIMEOUT // 60} menit), Apis~",
                            view=HapusThreadView("Emilia") if sess.is_thread else None,
                        )
                        await archive_thread(channel)
                    except Exception:
                        pass

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        await self.process_commands(message)
        if message.content.startswith("/"):
            return

        channel_id = message.channel.id

        # Priority 0: detect auth/OAuth codes that arrived after stdin_relay window closed
        if _looks_like_auth_code(message.content) and not stdin_relay.is_waiting(channel_id):
            await message.channel.send(
                "⚠️ Emilia dapat kode auth tapi lagi gak nunggu input dari process manapun.\n"
                "Kalau mau login profil Claude tertentu, bilang misalnya:\n"
                "> **`emilia, login claude-kantor`**\n"
                "Emilia bakal jalanin langsung dan nunggu kodenya~"
            )
            return

        # Priority 1: stdin relay — user is providing input to a waiting subprocess
        if stdin_relay.is_waiting(channel_id):
            user_input = message.content.strip()
            if user_input.lower() == 'cancel':
                stdin_relay.cancel(channel_id)
                await message.channel.send("❌ Input dibatalkan, Emilia hentikan process-nya ya~")
            else:
                provided = stdin_relay.provide_input(channel_id, user_input)
                if not provided:
                    # Future already resolved/cancelled — fall through to normal handling
                    pass
                else:
                    return
            return

        # Priority 2: pending dangerous task waiting for confirmation
        first_word = message.content.lower().strip().split()[0] if message.content.strip() else ''
        if channel_id in _pending_tasks:
            desc, creator_id, guild_id = _pending_tasks[channel_id]
            if first_word in _YES:
                del _pending_tasks[channel_id]
                from commands.task import run_task_flow
                async with message.channel.typing():
                    pass
                await run_task_flow(desc, creator_id, guild_id, message.channel)
                return
            elif first_word in _NO:
                del _pending_tasks[channel_id]
                await message.channel.send("Oke Apis, Emilia batalkan ya~ ✨")
                return
            else:
                # User said something else — cancel pending and handle as new message
                del _pending_tasks[channel_id]

        # Session lifecycle & Claude continuation.
        from services import claude_session
        from commands.task import archive_thread
        if config.SHAULA_ENABLED:
            # Shaula owns ALL threads — Emilia never responds inside a thread, so the two
            # bots don't bentrok when a message merely contains the word "emilia". In the
            # main channel, Emilia still hands a (channel-hosted) live session to Shaula
            # unless the message is addressed to her.
            if isinstance(message.channel, discord.Thread):
                return
            if claude_session.is_active(channel_id) and not bots.addressed_to_emilia(message):
                return
        else:
            # Single-bot mode: Emilia handles session lifecycle herself.
            # Priority 3: delete a session thread (inside a thread, after session ended)
            if isinstance(message.channel, discord.Thread) and _is_thread_delete(message.content):
                if claude_session.is_active(channel_id):
                    _s = claude_session.get(channel_id)
                    extra = "Ada task yang lagi jalan — " if (_s and _s.busy) else ""
                    await message.channel.send(
                        f"⚠️ {extra}session Claude masih aktif di thread ini. Ketik `stop session` dulu, "
                        "baru Emilia bisa hapus threadnya ya, Apis~"
                    )
                    return
                await message.channel.send("🗑️ Oke Apis, Emilia hapus thread ini ya~ dadah~ ✨")
                try:
                    await message.channel.delete()
                except discord.Forbidden:
                    await message.channel.send(
                        "⚠️ Emilia nggak punya izin `Manage Threads` buat hapus thread. "
                        "Tambahin permission-nya di role Emilia dulu ya~"
                    )
                return
            # Priority 4: live Claude session attached to this channel/thread
            if claude_session.is_active(channel_id):
                if _is_session_stop(message.content):
                    from commands.task import _clear_active_button
                    from views.session_view import HapusThreadView
                    killed = _kill_session_task(claude_session.get(channel_id))
                    sess = claude_session.stop(channel_id)
                    turns = sess.turns if sess else 0
                    if sess:
                        await _clear_active_button(sess)
                    is_thread = isinstance(message.channel, discord.Thread)
                    note = " Task yang lagi jalan Emilia hentikan juga ya~" if killed else ""
                    await message.channel.send(
                        f"🛑 Session Claude ditutup ya, Apis~ ({turns} turn).{note}",
                        view=HapusThreadView("Emilia") if is_thread else None,
                    )
                    await archive_thread(message.channel)
                    return
                if not bots.addressed_to_emilia(message):
                    async with message.channel.typing():
                        await _continue_session(message)
                    return

        # Attachment → task: a text/.md file attached in the main channel means "do this,
        # here's the doc". Download it and forward the content to Shaula as task context
        # (no need to save to disk + /task-file).
        if message.attachments:
            description, names = await _augment_prompt_with_attachments(message, message.content)
            if names:  # at least one usable text/image attachment → forward to Shaula
                from commands.task import run_task_flow
                await message.channel.send(
                    f"📎 Emilia terima lampiran ({', '.join(names)}) — diteruskan ke Shaula "
                    "buat dikerjain ya, Apis~"
                )
                await run_task_flow(description, message.author.id, message.guild.id, message.channel)
                return

        async with message.channel.typing():
            # Phase 1: instant keyword routing (no LLM involved)
            quick_tool = _intent_router.detect_tool(message.content)

            if quick_tool:
                try:
                    result_summary = await _handle_tool_call(message, quick_tool)
                    # Keep history so Hermes has context for follow-up questions.
                    # Use proper JSON format (not "[Ran tool: ...]") so Hermes learns
                    # to generate JSON tool calls, not echo history markers.
                    conversation.add_message(channel_id, "user", message.content)
                    tool_record = {k: v for k, v in quick_tool.items() if k != "trusted"}
                    conversation.add_message(channel_id, "assistant", json.dumps(tool_record))
                    conversation.add_message(channel_id, "user", f"[Tool result: {result_summary}]")
                except Exception as e:
                    logger.error("Tool call error: %s", e)
                    await message.channel.send(f"❌ Error: `{e}`")
                return

            # Phase 2: ask Hermes (for genuine chat / complex routing)
            try:
                chat_text, tool_call = await conversation.chat(channel_id, message.content)
            except Exception as e:
                logger.error("conversation.chat error: %s", e)
                await message.channel.send("❌ Terjadi error saat memproses pesan.")
                return

            if tool_call:
                # Hermes wants a tool — discard any text it generated
                # (it might be hallucinated data; the embed IS the answer)
                try:
                    result_summary = await _handle_tool_call(message, tool_call)
                    conversation.add_message(channel_id, "user", f"[Tool result: {result_summary}]")
                except Exception as e:
                    logger.error("Tool call error: %s", e)
                    await message.channel.send(f"❌ Error: `{e}`")
            else:
                # Pure chat — send Hermes response
                if chat_text:
                    for chunk in [chat_text[i:i+1900] for i in range(0, len(chat_text), 1900)]:
                        await message.channel.send(chunk)
                    # Background: learn any durable fact from this exchange (no-op if KB off).
                    from services import memory_capture
                    asyncio.create_task(memory_capture.maybe_capture(message.content, chat_text))

    async def close(self):
        logger.info("Shutting down...")
        import signal as _signal
        pids = task_store.kill_all_running()
        for pid in pids:
            try:
                os.kill(pid, _signal.SIGTERM)
            except ProcessLookupError:
                pass
        await super().close()


class ShaulaBot(commands.Bot):
    """Second Discord identity that runs Claude Code execution and owns live sessions.

    Same process as Emilia (shared claude_session state). It handles only the Claude side:
    in a thread, a non-addressed message is the next prompt for the live session; it also
    does stop/delete and the busy-guard. New tasks arrive via Emilia hand-off — run_task_flow
    / _execute_and_stream resolve THIS client's channel through services.bots.shaula_channel.
    """

    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True  # privileged — must be enabled in Shaula's app settings
        super().__init__(command_prefix="!shaula ", intents=intents)

    async def setup_hook(self):
        # Localhost intake so the external Emilia (hermes gateway, separate process)
        # can hand a task to Shaula here → existing run_task_flow (thread + streaming).
        await self._start_delegation_intake()
        # When Emilia's brain is external (EMILIA_ENABLED=false), the slash commands
        # move onto Shaula's app so /task, /runs, etc. work again on a live client.
        # (When Emilia runs in-process, DevOpsBot owns the tree — leave Shaula bare.)
        if not config.EMILIA_ENABLED:
            from services import run_store
            run_store.init_db()
            for cog in SHAULA_COGS:
                try:
                    await self.load_extension(cog)
                    logger.info("Shaula loaded cog: %s", cog)
                except Exception as e:
                    logger.error("Shaula failed to load cog %s: %s", cog, e)
            guild = discord.Object(id=config.DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Shaula synced %d slash commands to guild %d", len(synced), config.DISCORD_GUILD_ID)

    async def _start_delegation_intake(self):
        try:
            from aiohttp import web
        except Exception as e:  # pragma: no cover — aiohttp ships with discord.py
            logger.error("aiohttp unavailable — delegation intake disabled: %s", e)
            return
        app = web.Application()
        app.router.add_post("/delegate", self._handle_delegate)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", config.DELEGATE_INTAKE_PORT)
        await site.start()
        self._intake_runner = runner
        logger.info("Shaula delegation intake listening on 127.0.0.1:%d", config.DELEGATE_INTAKE_PORT)

    async def _handle_delegate(self, request):
        """POST /delegate {task, channel_id, user_id?, } → run a Shaula task (thread)."""
        from aiohttp import web
        token = request.headers.get("X-Intake-Token", "")
        if config.DELEGATE_INTAKE_TOKEN and token != config.DELEGATE_INTAKE_TOKEN:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        task = (data.get("task") or "").strip()
        channel_id = data.get("channel_id")
        user_id = data.get("user_id")
        if not task or not channel_id:
            return web.json_response({"ok": False, "error": "task and channel_id required"}, status=400)
        try:
            channel_id = int(channel_id)
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "error": "channel_id must be int"}, status=400)
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except Exception:
                return web.json_response({"ok": False, "error": f"channel {channel_id} not found"}, status=404)
        try:
            creator_id = int(user_id) if user_id else (bots.shaula_user_id or 0)
        except (TypeError, ValueError):
            creator_id = bots.shaula_user_id or 0
        guild_id = getattr(getattr(channel, "guild", None), "id", config.DISCORD_GUILD_ID)
        from commands.task import run_task_flow
        self.loop.create_task(run_task_flow(task, creator_id, guild_id, channel))
        logger.info("Delegation accepted: channel=%s user=%s task=%.60s", channel_id, creator_id, task)
        return web.json_response({"ok": True, "status": "started"})

    async def on_ready(self):
        bots.shaula = self
        bots.shaula_user_id = self.user.id
        logger.info("Shaula ready as %s (ID: %s)", self.user, self.user.id)
        if not getattr(self, "_sweeper_started", False):
            self._sweeper_started = True
            self.loop.create_task(self._session_idle_sweeper())

    async def _session_idle_sweeper(self):
        from services import claude_session
        from commands.task import archive_thread
        while not self.is_closed():
            await asyncio.sleep(60)
            for sess in claude_session.sweep_idle():
                channel = self.get_channel(sess.channel_id)
                if channel:
                    try:
                        from commands.task import _clear_active_button
                        from views.session_view import HapusThreadView
                        await _clear_active_button(sess)
                        await channel.send(
                            f"💤 Session Claude ditutup otomatis (idle "
                            f"{claude_session.IDLE_TIMEOUT // 60} menit), Shisou~",
                            view=HapusThreadView("Shaula") if sess.is_thread else None,
                        )
                        await archive_thread(channel)
                    except Exception:
                        pass

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.content.startswith("/"):
            return
        # In a thread Shaula owns everything (Emilia stays out, even if "emilia" appears
        # in the text). Outside a thread, defer to Emilia when addressed to her.
        if not isinstance(message.channel, discord.Thread) and bots.addressed_to_emilia(message):
            return  # Emilia's lane (main channel only)

        channel_id = message.channel.id
        from services import claude_session
        from commands.task import archive_thread

        # Delete a session thread (only after the session has ended)
        if isinstance(message.channel, discord.Thread) and _is_thread_delete(message.content):
            if claude_session.is_active(channel_id):
                _s = claude_session.get(channel_id)
                extra = "Ada task yang lagi jalan — " if (_s and _s.busy) else ""
                await message.channel.send(
                    f"⚠️ {extra}session Claude masih aktif. Ketik `stop session` dulu, "
                    "baru Shaula bisa hapus threadnya ya, Shisou~"
                )
                return
            await message.channel.send("🗑️ Oke Shisou, Shaula hapus thread ini ya~ dadah~ ✨")
            try:
                await message.channel.delete()
            except discord.Forbidden:
                await message.channel.send(
                    "⚠️ Shaula nggak punya izin `Manage Threads` buat hapus thread. "
                    "Tambahin permission-nya di role Shaula dulu ya~"
                )
            return

        # Live Claude session: stop it, or forward the message as the next prompt
        if claude_session.is_active(channel_id):
            if _is_session_stop(message.content):
                from commands.task import _clear_active_button
                from views.session_view import HapusThreadView
                killed = _kill_session_task(claude_session.get(channel_id))
                sess = claude_session.stop(channel_id)
                turns = sess.turns if sess else 0
                if sess:
                    await _clear_active_button(sess)
                is_thread = isinstance(message.channel, discord.Thread)
                note = " Task yang lagi jalan Shaula hentikan juga ya~" if killed else ""
                await message.channel.send(
                    f"🛑 Session Claude ditutup ya, Shisou~ ({turns} turn).{note}",
                    view=HapusThreadView("Shaula") if is_thread else None,
                )
                await archive_thread(message.channel)
                return
            async with message.channel.typing():
                await _continue_session(message)
            return

        # No active in-memory session. If this is a session thread (title `[xxxxxxxx]`), the
        # live state was likely lost to a bot restart — revive it from the on-disk transcript
        # so the thread stays continuable instead of going silent.
        if isinstance(message.channel, discord.Thread) and _SESSION_ID_RE.match(message.channel.name or ""):
            if _is_session_stop(message.content):
                await message.channel.send(
                    "ℹ️ Session ini udah ketutup, Shisou~ (kemungkinan abis bot restart). "
                    "Ketik `hapus thread` kalau mau dibersihin."
                )
                return
            revived = await _try_revive_session(message.channel)
            if revived is not None:
                await message.channel.send(
                    f"♻️ Session `{revived.session_id[:8]}` Shaula lanjutin lagi ya "
                    "(di-resume dari transcript)~"
                )
                async with message.channel.typing():
                    await _continue_session(message)
                return
        # Not a revivable session thread → Shaula stays quiet.


async def main():
    emilia = DevOpsBot()
    bots.emilia = emilia
    shaula = None

    async def _run(client, token, name):
        try:
            async with client:
                await client.start(token)
        except Exception as e:
            logger.error("%s client stopped: %s", name, e)

    runners = []
    if config.EMILIA_ENABLED:
        runners.append(_run(emilia, config.DISCORD_TOKEN, "Emilia"))
    else:
        logger.info(
            "Emilia client DISABLED (EMILIA_ENABLED=false) — DISCORD_TOKEN is owned by "
            "the external hermes-agent gateway. Running Shaula-only in this process."
        )
    if config.SHAULA_ENABLED:
        shaula = ShaulaBot()
        bots.shaula = shaula
        runners.append(_run(shaula, config.SHAULA_DISCORD_TOKEN, "Shaula"))
        logger.info("Shaula enabled — running Emilia + Shaula in one process.")
    else:
        logger.info("Shaula disabled (no SHAULA_DISCORD_TOKEN) — Emilia only.")

    # Graceful restart notice: on SIGTERM/SIGINT (e.g. `systemctl restart`), tell Discord
    # we're going down BEFORE closing, then exit cleanly. The matching "up" notice fires
    # from DevOpsBot.on_ready when the new process connects.
    loop = asyncio.get_running_loop()
    _shutting_down = False

    async def _graceful_shutdown():
        nonlocal _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        logger.info("Shutdown signal received — announcing then closing clients")
        if config.EMILIA_ENABLED:
            try:
                await asyncio.wait_for(
                    _announce_status(emilia, "🔌 Emilia mau restart sebentar ya, Apis~ — bentar lagi balik!"),
                    timeout=10,
                )
            except Exception as e:
                logger.error("shutdown announce failed: %s", e)
        if shaula is not None:
            await shaula.close()
        if config.EMILIA_ENABLED:
            await emilia.close()

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(_sig, lambda: loop.create_task(_graceful_shutdown()))
        except NotImplementedError:
            pass  # signal handlers unavailable (e.g. non-Unix) — skip graceful notice

    await asyncio.gather(*runners)


if __name__ == "__main__":
    asyncio.run(main())
