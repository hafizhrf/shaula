import asyncio
import io
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import config
from services import claude_runner, task_store
from services.task_store import RiskLevel, TaskState
from views.approval_view import (
    ApprovalView,
    DangerousApprovalView,
    make_done_embed,
    make_failed_embed,
    make_plan_embed,
    make_running_embed,
)
from views.plan_view import PlanExecuteView, PlanQuestionView
from views.session_view import StopSessionView

logger = logging.getLogger(__name__)

MAX_STREAM_DISPLAY = 1900
WORKSPACE_DIR = "/home/ubuntu/workspace"  # base for relative paths in /task-file


def _persona() -> str:
    """Who is speaking: Shaula when the executor bot is on, else Emilia (single-bot mode)."""
    return "Shaula" if config.SHAULA_ENABLED else "Emilia"


async def _clear_active_button(sess) -> None:
    """Retire the previous 'Session aktif' notice's Stop button.

    Called at the start of every new turn so the button vanishes the moment the
    conversation continues (whether the user chats or the bot replies).
    """
    msg = getattr(sess, "active_msg", None)
    if msg is None:
        return
    sess.active_msg = None
    try:
        await msg.edit(view=None)
    except discord.HTTPException:
        pass


async def archive_thread(channel) -> None:
    """Archive a session thread (kept for history; not deleted)."""
    if isinstance(channel, discord.Thread) and not channel.archived:
        try:
            await channel.edit(archived=True)
        except (discord.Forbidden, discord.HTTPException):
            pass


def _thread_name(description: str) -> str:
    name = " ".join(description.replace("__deploy__", "").split())[:90].strip()
    return f"🧵 {name}" if name else "🧵 Claude session"


async def _prefix_thread_id(thread, session_id: str, description: str) -> None:
    """Rename a freshly created session thread to start with the session id, so the thread
    maps to journald logs (`session xxxxxxxx`) and the on-disk Claude transcript at a glance.
    Fires once, only when a session thread is first created."""
    sid = session_id[:8]
    desc = " ".join(description.replace("__deploy__", "").split())
    name = f"[{sid}] 🧵 {desc}"[:100].strip()  # Discord thread-name cap = 100
    try:
        await thread.edit(name=name)
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.warning("Could not prefix thread with session id %s: %s", sid, e)


async def _post_initial_prompt(channel, description: str) -> None:
    """Post the FULL initial prompt as a message so Shisou can read it even when the thread
    title truncates it. Inline if short, else as a .txt attachment (plans can be huge)."""
    desc = (description or "").strip()
    if not desc:
        return
    header = "📝 **Prompt awal:**"
    try:
        if len(desc) <= 1800:
            await channel.send(f"{header}\n>>> {desc}")
        else:
            await channel.send(
                f"{header} *(panjang — Shaula lampirin sebagai file ya~)*",
                file=discord.File(io.BytesIO(desc.encode("utf-8")), filename="prompt.txt"),
            )
    except discord.HTTPException:
        pass


async def _ensure_session_thread(channel, task):
    """Return a thread to host a new session. Falls back to the channel if threads aren't allowed."""
    if isinstance(channel, discord.Thread):
        return channel  # already inside a thread
    try:
        thread = await channel.create_thread(
            name=_thread_name(task.description),
            type=discord.ChannelType.public_thread,
            auto_archive_duration=1440,  # 24h
        )
        await channel.send(f"🧵 Shaula buatin thread buat session ini ya, Shisou~ → {thread.mention}")
        return thread
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.warning("Could not create session thread (%s) — running in channel", e)
        await channel.send(
            "⚠️ Shaula nggak bisa bikin thread (kurang izin `Create Public Threads`). "
            "Session jalan di channel ini dulu ya~"
        )
        return channel


async def _execute_and_stream(task, channel: discord.TextChannel, use_session: bool = True,
                              config_dir: str | None = None):
    from services import conversation, stdin_relay, claude_session, bots, run_store
    channel = bots.shaula_channel(channel)  # execute + stream AS Shaula (the Claude executor)

    # Attach this execution to a persistent Claude session so context carries across
    # prompts. A brand-new session gets its own thread; later turns --resume in it.
    sess = None
    session_id = None
    resume = False
    account_config_dir = config_dir  # which Claude account to run as (None = default)
    if use_session:
        sess = claude_session.get(channel.id)
        # Conflict guard: never run two prompts on the same session at once.
        if sess and sess.busy:
            await channel.send(
                "⏳ Shaula masih ngerjain prompt sebelumnya di session ini, Shisou~ "
                "Tunggu yang ini kelar dulu, terus kirim lagi ya."
            )
            return
        if sess is None:
            work_channel = await _ensure_session_thread(channel, task)
            sess = claude_session.get_or_start(work_channel.id)
            sess.is_thread = isinstance(work_channel, discord.Thread)
            sess.origin_channel_id = channel.id if work_channel.id != channel.id else None
            sess.config_dir = config_dir  # bind the account to this NEW session
            channel = work_channel  # all session I/O happens in the thread
            if isinstance(work_channel, discord.Thread):
                await _prefix_thread_id(work_channel, sess.session_id, task.description)
                await _post_initial_prompt(work_channel, task.description)
        sess.busy = True
        sess.current_task = task              # so a stop/stop-task can kill this run
        task.project_dir = sess.project_dir   # stable cwd for the whole session
        session_id = sess.session_id
        resume = sess.started
        account_config_dir = sess.config_dir  # existing session's account wins (resume safety)
        await _clear_active_button(sess)      # new turn → retire the previous Stop button

        # Auto-compaction: a long-lived thread keeps resuming a bigger context every turn.
        # When the last turn's context crossed the threshold, ring a heads-up and run
        # `/compact` BEFORE the user's prompt so this turn starts lean. Only turn 2+
        # (resume) has prior context to compact; threshold 0 disables the feature.
        threshold = config.CLAUDE_COMPACT_THRESHOLD_TOKENS
        if resume and threshold > 0 and sess.last_context_tokens >= threshold:
            await channel.send(
                f"🗜️ *Context di thread ini udah gede banget (~{sess.last_context_tokens // 1000}k "
                f"token), Shaula ringkas dulu biar enteng ya, Shisou~ (✧ω✧)*"
            )
            ok = await claude_runner.run_compaction(
                session_id, sess.project_dir, config_dir=account_config_dir
            )
            if ok:
                sess.last_context_tokens = 0  # reset estimate; the next real turn refills it
                await channel.send("✅ *Udah dikompres~ lanjut kerjain prompt-nya sekarang!*")
            else:
                await channel.send("⚠️ *Compress gagal, Shaula lanjut apa adanya aja ya~*")

    # Record this run in the durable history DB (debug aid; fail-safe, never blocks the task).
    await run_store.start_run(
        task,
        session_id=session_id,
        channel_id=channel.id,  # the actual session channel (thread), not where /task was typed
        origin_channel_id=(sess.origin_channel_id if sess else None),
        account=("kantor" if account_config_dir else "default"),
        turn=((sess.turns + 1) if sess else 1),
    )

    # A display-only send must never abort the task (and orphan its run row).
    try:
        status_msg = await channel.send(embed=make_running_embed(task))
    except discord.HTTPException as e:
        logger.warning("Running-embed send failed (%s) — falling back to plain text", e)
        status_msg = await channel.send("⚙️ Shaula mulai ngerjain task ini ya, Shisou~")
    last_text = ""

    async def on_chunk(text: str):
        nonlocal last_text
        display = text[-MAX_STREAM_DISPLAY:] if len(text) > MAX_STREAM_DISPLAY else text
        if display == last_text:
            return
        last_text = display
        try:
            await status_msg.edit(content=f"```\n{display}\n```", embed=None)
        except discord.HTTPException:
            pass

    async def on_input_needed(proc, prompt: str):
        """Claude Code subprocess is waiting for stdin input."""
        await channel.send(
            f"⏸️ **{_persona()} butuh input dari Shisou!**\n"
            f"> `{prompt[:200]}`\n"
            f"Ketik responnya di sini~ (atau `cancel` untuk batalkan, timeout 5 menit)"
        )
        try:
            user_input = await stdin_relay.wait_for_input(channel.id, timeout=300.0)
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.write((user_input + "\n").encode())
                await proc.stdin.drain()
                await channel.send(f"✅ Input dikirim~")
        except asyncio.TimeoutError:
            await channel.send("⏰ Timeout, process dilanjutkan tanpa input.")
        except asyncio.CancelledError:
            await channel.send("❌ Input dibatalkan.")
            proc.kill()

    try:
        def on_session_id_resolved(sid: str):
            if sess:
                sess.session_id = sid

        success = await claude_runner.run_execution(
            task, on_chunk=on_chunk, on_input_needed=on_input_needed,
            session_id=session_id, resume=resume, config_dir=account_config_dir,
            on_session_id=on_session_id_resolved,
        )
    finally:
        if sess:
            sess.started = True      # session now exists on disk → future turns resume
            sess.turns += 1
            sess.busy = False
            sess.touch()
            sess.current_task = None
            # Remember this turn's context size so the NEXT turn can decide to compact.
            if task.context_tokens:
                sess.last_context_tokens = task.context_tokens

    # If the session was closed (stop / stop-task) WHILE this prompt was running, the
    # stop handler already messaged the user — don't post a misleading result or footer.
    session_alive = sess is not None and claude_session.get(sess.channel_id) is sess
    if sess is not None and not session_alive:
        await run_store.finish_run(task.task_id, "CANCELLED", task.cost_usd, task.error_text)
        try:
            await status_msg.edit(content="🛑 Task dihentikan (session ditutup).", embed=None)
        except discord.HTTPException:
            pass
        return

    full_output = "".join(task.output_lines)
    err_for_db = ""

    if success:
        if len(full_output) > MAX_STREAM_DISPLAY:
            await status_msg.edit(
                content=None,
                embed=make_done_embed(task, full_output),
            )
            await channel.send(
                file=discord.File(
                    io.BytesIO(full_output.encode()),
                    filename=f"output_{task.task_id[:8]}.txt",
                )
            )
        else:
            await status_msg.edit(
                content=f"```\n{full_output}\n```" if full_output else None,
                embed=make_done_embed(task, full_output),
            )
        # Add task result to conversation history so Emilia remembers what was done
        summary = f"[Task selesai: {task.description[:150]}. Output singkat: {full_output[:400]}]"
        conversation.add_message(channel.id, "user", summary)
    else:
        reason = claude_runner.failure_reason(task, persona=_persona())
        err_for_db = reason
        await status_msg.edit(content=None, embed=make_failed_embed(task, reason))
        conversation.add_message(channel.id, "user", f"[Task gagal — {reason[:150]}]")

    await run_store.finish_run(
        task.task_id, "DONE" if success else "FAILED", task.cost_usd, err_for_db
    )

    if session_alive:
        persona = _persona()
        view = StopSessionView(sess.channel_id, persona=persona)
        engine_label = "Antigravity (agy)" if config.CLI_ENGINE == "agy" else "Claude"
        notice = await channel.send(
            f"🟢 *Session {engine_label} aktif (turn {sess.turns}, id `{sess.session_id[:8]}`) — bales "
            f"aja buat lanjutin obrolan ke session yang sama. Ketik `selesai` / `stop session` "
            f"atau tekan tombol di bawah kalau mau {persona} tutup~*",
            view=view,
        )
        view.message = notice
        sess.active_msg = notice  # so the next turn can retire this button


class TaskCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_listener(self._on_task_approved, "on_task_approved")

    async def _on_task_approved(self, task, channel):
        claude_runner.ensure_project_dir(task)
        await _execute_and_stream(task, channel)

    @app_commands.command(
        name="run",
        description="Kirim dan jalankan tugas dengan Shaula (AI DevOps executor)",
    )
    @app_commands.describe(
        description="Apa yang harus dikerjakan Shaula?",
        kantor="Jalanin pakai akun kantor (khusus fallback engine Claude)",
    )
    async def run_cmd(
        self, interaction: discord.Interaction, description: str, kantor: bool = False
    ):
        await interaction.response.defer()
        if kantor and config.CLI_ENGINE == "claude":
            if not config.CLAUDE_KANTOR_CONFIG_DIR or not os.path.isdir(config.CLAUDE_KANTOR_CONFIG_DIR):
                await interaction.followup.send(
                    "⚠️ Akun kantor belum ke-setup, Shisou~ "
                    f"Config dir `{config.CLAUDE_KANTOR_CONFIG_DIR}` nggak ketemu."
                )
                return

            record = task_store.create_task(
                description=description,
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
            )
            claude_runner.ensure_project_dir(record)

            await interaction.followup.send(
                f"🟣 Jalanin pakai **akun kantor** (auto mode), Shisou~\n`{description[:120]}`"
            )
            await _execute_and_stream(
                record, interaction.channel, config_dir=config.CLAUDE_KANTOR_CONFIG_DIR
            )
        else:
            engine_str = f" [{config.CLI_ENGINE}]" if config.CLI_ENGINE else ""
            await interaction.followup.send(
                f"📨 Task diterima{engine_str} — Shaula kerjain di thread ya, Shisou~\n`{description[:120]}`"
            )
            await run_task_flow(
                description=description,
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel=interaction.channel,
            )

    @app_commands.command(name="task", description="Alias untuk /run")
    @app_commands.describe(description="Apa yang harus dikerjakan Shaula?")
    async def task_cmd(self, interaction: discord.Interaction, description: str):
        await self.run_cmd(interaction, description, kantor=False)

    @app_commands.command(
        name="task-kantor",
        description="Alias untuk /run kantor:True (fallback akun kantor Claude)",
    )
    @app_commands.describe(description="Apa yang harus dikerjakan Shaula?")
    async def task_kantor_cmd(self, interaction: discord.Interaction, description: str):
        await self.run_cmd(interaction, description, kantor=True)

    @app_commands.command(
        name="plan",
        description="Rencanakan tugas bersama Shaula dengan pertanyaan klarifikasi interaktif",
    )
    @app_commands.describe(
        description="Apa yang ingin direncanakan?",
        kantor="Jalanin pakai akun kantor (khusus fallback engine Claude)",
    )
    async def plan_cmd(
        self, interaction: discord.Interaction, description: str, kantor: bool = False
    ):
        await interaction.response.defer()
        engine_str = f" [{config.CLI_ENGINE}]" if config.CLI_ENGINE else ""
        await interaction.followup.send(
            f"📋 Merencanakan task{engine_str} — Shaula buka thread untuk diskusi & klarifikasi ya, Shisou~\n`{description[:120]}`"
        )
        account_config_dir = (
            config.CLAUDE_KANTOR_CONFIG_DIR
            if (kantor and config.CLI_ENGINE == "claude")
            else None
        )
        await run_plan_flow(
            description=description,
            creator_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel=interaction.channel,
            config_dir=account_config_dir,
        )

    @app_commands.command(
        name="run-file",
        description="Baca plan dari file (.md dll) lalu jalanin sebagai task Shaula",
    )
    @app_commands.describe(
        path="Path file plan — absolut atau relatif ke /home/ubuntu/workspace",
        kantor="Jalanin pakai akun kantor (khusus engine Claude)",
    )
    async def run_file_cmd(
        self, interaction: discord.Interaction, path: str, kantor: bool = False
    ):
        await self.task_file_cmd(interaction, path, kantor=kantor)

    @app_commands.command(
        name="task-file",
        description="Baca plan dari file (.md dll) lalu jalanin sebagai task Shaula",
    )
    @app_commands.describe(
        path="Path file plan — absolut atau relatif ke /home/ubuntu/workspace",
        kantor="Jalanin pakai akun kantor (default: akun utama)",
    )
    async def task_file_cmd(
        self, interaction: discord.Interaction, path: str, kantor: bool = False
    ):
        await interaction.response.defer()

        full = os.path.normpath(
            path if os.path.isabs(path) else os.path.join(WORKSPACE_DIR, path)
        )
        if not os.path.isfile(full):
            await interaction.followup.send(f"❌ File nggak ketemu, Shisou~: `{full}`")
            return
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal baca file: `{e}`")
            return
        if not content:
            await interaction.followup.send(
                f"⚠️ File `{os.path.basename(full)}` kosong — nggak ada yang dijalanin."
            )
            return
        MAX_PLAN = 100_000  # ~100 KB; guards against a stray huge file blowing up tokens
        if len(content) > MAX_PLAN:
            await interaction.followup.send(
                f"⚠️ Plan-nya kepanjangan ({len(content) // 1000} KB > {MAX_PLAN // 1000} KB), "
                "pecah dulu ya Shisou~"
            )
            return

        fname = os.path.basename(full)
        size_kb = len(content.encode("utf-8")) / 1024

        if kantor:
            if not config.CLAUDE_KANTOR_CONFIG_DIR or not os.path.isdir(
                config.CLAUDE_KANTOR_CONFIG_DIR
            ):
                await interaction.followup.send(
                    "⚠️ Akun kantor belum ke-setup, Shisou~ "
                    f"Config dir `{config.CLAUDE_KANTOR_CONFIG_DIR}` nggak ketemu."
                )
                return
            record = task_store.create_task(
                description=content,
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
            )
            claude_runner.ensure_project_dir(record)
            await interaction.followup.send(
                f"🟣 Baca plan dari **{fname}** ({size_kb:.1f} KB) — jalanin pakai **akun kantor** ya, Shisou~"
            )
            await _execute_and_stream(
                record, interaction.channel, config_dir=config.CLAUDE_KANTOR_CONFIG_DIR
            )
        else:
            await interaction.followup.send(
                f"📄 Baca plan dari **{fname}** ({size_kb:.1f} KB) — Shaula kerjain di thread ya, Shisou~"
            )
            await run_task_flow(
                description=content,
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel=interaction.channel,
            )


async def run_task_flow(
    description: str,
    creator_id: int,
    guild_id: int,
    channel: discord.TextChannel,
) -> None:
    """
    Shared task flow used by both the slash command and the natural-language handler.

    Full-auto: every task auto-executes in a continuable Claude session (Claude itself
    runs with --permission-mode auto). There is no plan-mode/approval gate — plan mode
    is unused on this VPS and only ever failed ("Claude could not generate a plan"),
    which left tasks dead and un-continuable. DANGEROUS tasks still run, but get a
    visible ⚠️ heads-up; the Stop-session button is the safety valve.
    """
    from services import bots
    channel = bots.shaula_channel(channel)  # task flow runs + posts AS Shaula
    record = task_store.create_task(
        description=description,
        creator_id=creator_id,
        guild_id=guild_id,
        channel_id=channel.id,
    )
    claude_runner.ensure_project_dir(record)

    risk_icon = {
        RiskLevel.SAFE: "🟢",
        RiskLevel.MEDIUM: "🟡",
        RiskLevel.DANGEROUS: "⚠️",
    }.get(record.risk_level, "🟢")
    note = ""
    if record.risk_level == RiskLevel.DANGEROUS:
        note = " *(operasi berisiko — Shaula tetap jalanin auto; tekan 🛑 Stop kalau perlu hentikan)*"
    await channel.send(f"{risk_icon} Running: `{description[:120]}`{note}")
    await _execute_and_stream(record, channel)


async def run_plan_flow(
    description: str,
    creator_id: int,
    guild_id: int,
    channel: discord.TextChannel,
    config_dir: str | None = None,
) -> None:
    """
    Interactive planning flow:
    1. Opens/uses a dedicated thread for the plan.
    2. Analyzes codebase and asks Shisou clarifying multiple-choice questions via buttons.
    3. Synthesizes answers into a full implementation plan.
    4. Presents the plan and provides a 1-click execution button.
    """
    from services import bots
    channel = bots.shaula_channel(channel)
    task = task_store.create_task(
        description=description,
        creator_id=creator_id,
        guild_id=guild_id,
        channel_id=channel.id,
    )
    claude_runner.ensure_project_dir(task)

    thread = await _ensure_session_thread(channel, task)
    if isinstance(thread, discord.Thread):
        desc_clean = " ".join(description.split())[:80]
        try:
            await thread.edit(name=f"📋 Plan: {desc_clean}")
        except Exception:
            pass

    persona = _persona()
    init_msg = await thread.send(
        f"📋 **Shaula lagi pelajari kebutuhan Shisou dan nyiapin opsi/pertanyaan dulu ya~** ✨\n"
        f"Mohon tunggu sebentar..."
    )

    questions = await claude_runner.generate_plan_questions(task, config_dir=config_dir)

    qna = []
    if questions:
        await init_msg.edit(
            content=(
                f"✨ Shaula udah analisis kodenya! Ada **{len(questions)} hal** "
                f"yang perlu didiskusikan biar rancangan solusinya pas. Silakan pilih opsi di bawah ya, Shisou~ 👇"
            )
        )
        for idx, q_item in enumerate(questions):
            q_text = q_item.get("question", "").strip()
            opts = q_item.get("options", [])
            if not q_text or not opts:
                continue

            num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
            formatted_opts = []
            for o_idx, opt in enumerate(opts[:4]):
                emo = num_emojis[o_idx] if o_idx < len(num_emojis) else "🔹"
                formatted_opts.append(f"{emo} {opt}")

            options_display = "\n".join(formatted_opts)
            msg_content = (
                f"**Pertanyaan {idx + 1} dari {len(questions)}:**\n"
                f"> **{q_text}**\n\n"
                f"{options_display}"
            )

            fut = asyncio.get_running_loop().create_future()
            view = PlanQuestionView(
                creator_id=creator_id,
                options=opts,
                future=fut,
                persona=persona,
                timeout=300.0,
            )
            q_msg = await thread.send(content=msg_content, view=view)
            view.message = q_msg

            try:
                selected_answer = await fut
            except Exception:
                selected_answer = opts[0] if opts else "Terserah Shaula"

            qna.append({"question": q_text, "answer": selected_answer})
    else:
        await init_msg.edit(
            content=(
                "💡 Kebutuhan task Shisou sudah sangat jelas! "
                "Shaula langsung susun detail Implementation Plan-nya ya~ 🚀"
            )
        )

    plan_status_msg = await thread.send("📝 **Sedang menyusun Implementation Plan lengkap...**")
    plan_text = await claude_runner.generate_final_plan(task, qna, config_dir=config_dir)

    if not plan_text:
        await plan_status_msg.edit(
            content="⚠️ Maaf ya Shisou, Shaula gagal menyusun plan. "
            "Shisou bisa coba jalankan langsung via `/run` atau ulangi lagi ya~"
        )
        return

    task.plan_text = plan_text
    try:
        await plan_status_msg.delete()
    except Exception:
        pass

    header = "📋 **Implementation Plan Siap, Shisou!** ٩(◕‿◕｡)۶\n\n"
    if len(plan_text) + len(header) <= 1900:
        await thread.send(f"{header}{plan_text}")
    else:
        preview = plan_text[:1200]
        await thread.send(
            f"{header}>>> {preview}...\n\n*(Plan lengkap cukup panjang, Shaula lampirkan file `.md` di bawah ya~)*",
            file=discord.File(
                io.BytesIO(plan_text.encode("utf-8")),
                filename=f"plan_{task.task_id[:8]}.md",
            ),
        )

    async def _on_confirm_execute(exec_channel):
        exec_task = task_store.create_task(
            description=(
                f"Laksanakan rencana berikut yang telah disetujui Shisou:\n\n{plan_text}"
            ),
            creator_id=creator_id,
            guild_id=guild_id,
            channel_id=exec_channel.id,
        )
        claude_runner.ensure_project_dir(exec_task)
        await _execute_and_stream(exec_task, exec_channel, config_dir=config_dir)

    exec_view = PlanExecuteView(
        creator_id=creator_id,
        on_execute=_on_confirm_execute,
        persona=persona,
        timeout=900.0,
    )
    exec_msg = await thread.send(
        "👇 **Gimana Shisou? Mau langsung Shaula kerjain sesuai plan di atas?**",
        view=exec_view,
    )
    exec_view.message = exec_msg


async def setup(bot: commands.Bot):
    await bot.add_cog(TaskCommands(bot))
