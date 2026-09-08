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


def _make_plan_stream_callback(status_msg: discord.Message, label: str):
    """Throttle streamed plan previews to one Discord message."""
    last_edit = 0.0

    async def on_chunk(plan_so_far: str) -> None:
        nonlocal last_edit
        now = asyncio.get_running_loop().time()
        if now - last_edit < config.STREAM_EDIT_INTERVAL_SECONDS:
            return
        last_edit = now
        preview = plan_so_far[-1600:].replace("```", "ˋˋˋ")
        await status_msg.edit(
            content=f"📝 **{label}...**\n```md\n{preview}\n```"
        )

    return on_chunk


def _make_plan_activity_callback(status_msg: discord.Message, label: str):
    """Publish tool activity separately so it never overwrites streamed agent text."""
    last_edit = 0.0
    activity_msg: discord.Message | None = None

    async def on_activity(activity: str) -> None:
        nonlocal last_edit, activity_msg
        now = asyncio.get_running_loop().time()
        if now - last_edit < config.STREAM_EDIT_INTERVAL_SECONDS:
            return
        last_edit = now
        content = f"🔎 **{label}...**\n{activity}"
        try:
            if activity_msg is None:
                activity_msg = await status_msg.channel.send(content)
            else:
                await activity_msg.edit(content=content)
        except discord.HTTPException:
            pass

    return on_activity


async def _clear_active_button(sess=None, channel=None) -> None:
    """Retire the previous 'Session active' notice's Stop button and disable question buttons.

    Called at the start of every new turn so the button vanishes and unanswered question
    buttons are disabled the moment the conversation continues (whether the user types
    their answer manually or clicks a button). Also scans channel history to ensure buttons
    from previous turns or before a bot restart are cleared.
    """
    if sess is not None:
        msg = getattr(sess, "active_msg", None)
        if msg is not None:
            sess.active_msg = None
            try:
                await msg.edit(view=None)
            except (discord.HTTPException, discord.Forbidden):
                pass

        q_msg = getattr(sess, "active_question_msg", None)
        q_view = getattr(sess, "active_question_view", None)
        sess.active_question_msg = None
        sess.active_question_view = None
        if q_view is not None and not q_view.is_finished():
            q_view.stop()
            if q_msg is not None:
                try:
                    await q_msg.edit(
                        content=f"{q_msg.content}\n\n*(answered manually by Shisou)*",
                        view=None,
                    )
                except (discord.HTTPException, discord.Forbidden):
                    pass

    # History scan: if channel is provided, also clean up any orphaned or pre-restart buttons
    # in the most recent messages.
    if channel is not None and hasattr(channel, "history"):
        try:
            async for old_msg in channel.history(limit=8):
                if not old_msg.author.bot:
                    continue
                if not old_msg.components:
                    continue

                has_stop = False
                has_active_question = False
                has_execute_plan = False
                for row in old_msg.components:
                    for comp in getattr(row, "children", []):
                        cid = getattr(comp, "custom_id", "") or ""
                        lbl = getattr(comp, "label", "") or ""
                        if "session:stop" in cid or "Stop session" in lbl:
                            has_stop = True
                        elif "opt_" in cid or "choice" in cid.lower() or "plan_opt" in cid:
                            has_active_question = True
                        elif "execute" in cid.lower() or "cancel" in cid.lower() or "plan_" in cid:
                            has_execute_plan = True

                if has_stop or has_execute_plan:
                    try:
                        await old_msg.edit(view=None)
                    except (discord.HTTPException, discord.Forbidden):
                        pass

                if has_active_question:
                    try:
                        content = old_msg.content or ""
                        if "*(answered" not in content and "*(⏰" not in content and "👉" not in content:
                            content = f"{content}\n\n*(answered manually by Shisou)*"
                        await old_msg.edit(content=content, view=None)
                    except (discord.HTTPException, discord.Forbidden):
                        pass
        except Exception as e:
            logger.debug("Failed history scan in _clear_active_button: %s", e)



async def archive_thread(channel) -> None:
    """Archive a session thread (kept for history; not deleted)."""
    if isinstance(channel, discord.Thread) and not channel.archived:
        try:
            await channel.edit(archived=True)
        except (discord.Forbidden, discord.HTTPException):
            pass


def _thread_name(description: str) -> str:
    name = " ".join(description.replace("__deploy__", "").split())[:90].strip()
    return f"🧵 {name}" if name else "🧵 Shaula session"


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
    header = "📝 **Initial prompt:**"
    try:
        if len(desc) <= 1800:
            await channel.send(f"{header}\n>>> {desc}")
        else:
            await channel.send(
                f"{header} *(too long — Shaula attached it as a file, Shisou~)*",
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
        await channel.send(f"🧵 Shaula created a thread for this session, Shisou~! (✧ω✧) → {thread.mention}")
        return thread
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.warning("Could not create session thread (%s) — running in channel", e)
        await channel.send(
            "⚠️ Shaula couldn't create a thread (missing 'Create Public Threads' permission). "
            "Running in this channel instead, Shisou~"
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
                "⏳ Shaula is still working on the previous prompt in this session, Shisou~! "
                "Please wait until it finishes, then send it again~"
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
        await _clear_active_button(sess, channel)      # new turn → retire previous Stop and question buttons

        # Auto-compaction: a long-lived thread keeps resuming a bigger context every turn.
        # When the last turn's context crossed the threshold, ring a heads-up and run
        # `/compact` BEFORE the user's prompt so this turn starts lean. Only turn 2+
        # (resume) has prior context to compact; threshold 0 disables the feature.
        threshold = config.CLAUDE_COMPACT_THRESHOLD_TOKENS
        if resume and threshold > 0 and sess.last_context_tokens >= threshold:
            await channel.send(
                f"🗜️ *Context in this thread is getting large (~{sess.last_context_tokens // 1000}k "
                f"tokens), Shaula will compact it first to keep things snappy, Shisou~! (✧ω✧)*"
            )
            ok = await claude_runner.run_compaction(
                session_id, sess.project_dir, config_dir=account_config_dir
            )
            if ok:
                sess.last_context_tokens = 0  # reset estimate; the next real turn refills it
                await channel.send("✅ *Context compacted~ continuing with your prompt now, Shisou!*")
            else:
                await channel.send("⚠️ *Compaction failed, Shaula will proceed as is, Shisou~*")

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
        status_msg = await channel.send("⚙️ Shaula is starting on this task now, Shisou~! (๑•̀ㅂ•́)و✧")
    last_text = ""
    response_msg: discord.Message | None = None
    activity_msg: discord.Message | None = None

    async def _show_response(text: str) -> None:
        """Keep agent narration in its own message; task status stays status-only."""
        nonlocal last_text, response_msg
        display = text[-1800:] if len(text) > 1800 else text
        display = display.replace("```", "ˋˋˋ")
        if display == last_text:
            return
        last_text = display
        content = f"💬 **{_persona()}:**\n```\n{display}\n```"
        try:
            if response_msg is None:
                response_msg = await channel.send(content)
            else:
                await response_msg.edit(content=content)
        except discord.HTTPException:
            pass

    async def on_chunk(text: str):
        await _show_response(text)

    async def on_activity(activity: str):
        """Keep repetitive tool updates in a dedicated, replaceable activity message."""
        nonlocal activity_msg
        content = f"⚡ **{_persona()} activity:** {activity}"
        try:
            if activity_msg is None:
                activity_msg = await channel.send(content)
            else:
                await activity_msg.edit(content=content)
        except discord.HTTPException:
            pass

    async def on_input_needed(proc, prompt: str):
        """Subprocess is waiting for stdin input."""
        from views.plan_view import InputPromptButtonsView
        options = []
        low = prompt.lower()
        if any(k in low for k in ("(y/n)", "[y/n]", "(yes/no)", "[yes/no]", "y/n?", "y/n")):
            options = ["Yes", "No"]
        else:
            q_info = claude_runner.extract_question_and_options(prompt)
            if q_info:
                options = q_info.get("options", [])

        view = (
            InputPromptButtonsView(channel.id, task.creator_id, options)
            if options
            else None
        )
        tip = " or click an option button below" if options else ""
        msg = await channel.send(
            f"⏸️ **{_persona()} needs input from Shisou!**\n"
            f"> `{prompt[:200]}`\n"
            f"Type your response here{tip}~ (or `cancel` to abort, 5 min timeout)",
            view=view,
        )
        if view:
            view.message = msg
        try:
            user_input = await stdin_relay.wait_for_input(channel.id, timeout=300.0)
            if view and not view.is_finished():
                view.stop()
                for item in view.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = True
                try:
                    await msg.edit(view=view)
                except discord.HTTPException:
                    pass
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.write((user_input + "\n").encode())
                await proc.stdin.drain()
                await channel.send(f"✅ Input sent, Shisou~")
        except asyncio.TimeoutError:
            if view and not view.is_finished():
                view.stop()
                for item in view.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = True
                try:
                    await msg.edit(view=view)
                except discord.HTTPException:
                    pass
            await channel.send("⏰ Timed out, continuing process without input, Shisou~")
        except asyncio.CancelledError:
            if view and not view.is_finished():
                view.stop()
                for item in view.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = True
                try:
                    await msg.edit(view=view)
                except discord.HTTPException:
                    pass
            await channel.send("❌ Input cancelled, Shisou~")
            from services.process_cleanup import terminate_process_group
            await terminate_process_group(proc, label="interactive task input")

    try:
        def on_session_id_resolved(sid: str):
            if sess:
                sess.session_id = sid

        success = await claude_runner.run_execution(
            task, on_chunk=on_chunk, on_input_needed=on_input_needed, on_activity=on_activity,
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
        await run_store.finish_run(
            task.task_id,
            "CANCELLED",
            task.cost_usd,
            task.error_text,
            prompt_tokens=getattr(task, "prompt_tokens", 0),
            completion_tokens=getattr(task, "completion_tokens", 0),
            total_tokens=getattr(task, "total_tokens", 0),
            session_id=task.session_id or (sess.session_id if sess else None),
        )
        try:
            await status_msg.edit(content="🛑 Task dihentikan (session ditutup).", embed=None)
        except discord.HTTPException:
            pass
        task.output_lines.clear()
        return

    full_output = "".join(task.output_lines)
    err_for_db = ""

    if success:
        if len(full_output) > MAX_STREAM_DISPLAY:
            await _show_response(full_output)
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
            if full_output:
                await _show_response(full_output)
            await status_msg.edit(
                content=None,
                embed=make_done_embed(task, full_output),
            )
        # Add task result to conversation history so Emilia remembers what was done
        summary = f"[Task complete: {task.description[:150]}. Brief output: {full_output[:400]}]"
        conversation.add_message(channel.id, "user", summary)

        # Auto-upload files requested via [UPLOAD: <path>] tag
        import re
        upload_matches = re.findall(r'\[UPLOAD:\s*([^\s\]]+)(?:\s+([^\]]+))?\]', full_output)
        for fpath, caption in upload_matches:
            fpath = fpath.strip()
            if not os.path.isabs(fpath):
                fpath = os.path.join(WORKSPACE_DIR, fpath)
            if os.path.isfile(fpath):
                try:
                    cap = caption.strip() if caption else f"📄 **Uploaded for Shisou:** `{os.path.basename(fpath)}`"
                    await channel.send(cap, file=discord.File(fpath))
                except Exception as e:
                    logger.warning("Auto-upload tag failed for %s: %s", fpath, e)
    else:
        reason = claude_runner.failure_reason(task, persona=_persona())
        err_for_db = reason
        await status_msg.edit(content=None, embed=make_failed_embed(task, reason))
        conversation.add_message(channel.id, "user", f"[Task failed — {reason[:150]}]")

    await run_store.finish_run(
        task.task_id,
        "DONE" if success else "FAILED",
        task.cost_usd,
        err_for_db,
        prompt_tokens=getattr(task, "prompt_tokens", 0),
        completion_tokens=getattr(task, "completion_tokens", 0),
        total_tokens=getattr(task, "total_tokens", 0),
        session_id=task.session_id or (sess.session_id if sess else None),
    )

    if session_alive:
        persona = _persona()
        # If the turn ended with a question offering options, present them as interactive buttons
        from views.plan_view import SessionQuestionChoiceView
        q_info = claude_runner.extract_question_and_options(
            full_output, session_id=sess.session_id if sess else None
        )
        if q_info and q_info.get("options"):
            q_options = q_info["options"]
            q_text = q_info.get("question", "Please select an option below, Shisou~")
            choice_view = SessionQuestionChoiceView(
                channel=channel,
                creator_id=task.creator_id,
                options=q_options,
                persona=persona,
            )
            choice_msg = await channel.send(
                f"❓ **{persona} has a question for Shisou:**\n> {q_text}\n"
                f"*Click an option button below, Shisou~ 👇*",
                view=choice_view,
            )
            choice_view.message = choice_msg
            if sess:
                sess.active_question_msg = choice_msg
                sess.active_question_view = choice_view

        view = StopSessionView(sess.channel_id, persona=persona)
        engine_label = "Antigravity (agy)" if config.CLI_ENGINE == "agy" else "Claude"
        notice = await channel.send(
            f"🟢 *Session {engine_label} active (turn {sess.turns}, id `{sess.session_id[:8]}`) — "
            f"reply in this thread to continue the conversation. Type `stop session` or click the button below to close it~*",
            view=view,
        )
        view.message = notice
        sess.active_msg = notice  # so the next turn can retire this button
    task.output_lines.clear()


class TaskCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_listener(self._on_task_approved, "on_task_approved")

    async def _on_task_approved(self, task, channel):
        claude_runner.ensure_project_dir(task)
        await _execute_and_stream(task, channel)

    @app_commands.command(
        name="run",
        description="Send and execute a task with Shaula (AI DevOps executor)",
    )
    @app_commands.describe(
        description="What should Shaula work on?",
        kantor="Run with office account (fallback engine Claude only)",
    )
    async def run_cmd(
        self, interaction: discord.Interaction, description: str, kantor: bool = False
    ):
        await interaction.response.defer()
        if kantor and config.CLI_ENGINE == "claude":
            if not config.CLAUDE_KANTOR_CONFIG_DIR or not os.path.isdir(config.CLAUDE_KANTOR_CONFIG_DIR):
                await interaction.followup.send(
                    "⚠️ Office account is not configured, Shisou~ "
                    f"Config dir `{config.CLAUDE_KANTOR_CONFIG_DIR}` not found."
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
                f"🟣 Running with **office account** (auto mode), Shisou~\n`{description[:120]}`"
            )
            await _execute_and_stream(
                record, interaction.channel, config_dir=config.CLAUDE_KANTOR_CONFIG_DIR
            )
        else:
            engine_str = f" [{config.CLI_ENGINE}]" if config.CLI_ENGINE else ""
            await interaction.followup.send(
                f"📨 Task received{engine_str} — Shaula will work on it in the thread, Shisou~! (✧ω✧)\n`{description[:120]}`"
            )
            await run_task_flow(
                description=description,
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel=interaction.channel,
            )

    @app_commands.command(name="task", description="Alias for /run")
    @app_commands.describe(description="What should Shaula work on?")
    async def task_cmd(self, interaction: discord.Interaction, description: str):
        await self.run_cmd(interaction, description, kantor=False)

    @app_commands.command(
        name="task-kantor",
        description="Alias for /run kantor:True (fallback office account Claude)",
    )
    @app_commands.describe(description="What should Shaula work on?")
    async def task_kantor_cmd(self, interaction: discord.Interaction, description: str):
        await self.run_cmd(interaction, description, kantor=True)

    @app_commands.command(
        name="plan",
        description="Plan a task with Shaula featuring interactive clarifying questions",
    )
    @app_commands.describe(
        description="What do you want to plan?",
        kantor="Run with office account (fallback engine Claude only)",
    )
    async def plan_cmd(
        self, interaction: discord.Interaction, description: str, kantor: bool = False
    ):
        await interaction.response.defer()
        engine_str = f" [{config.CLI_ENGINE}]" if config.CLI_ENGINE else ""
        await interaction.followup.send(
            f"📋 Planning task{engine_str} — Shaula is opening a thread for discussion & clarification, Shisou~! (✧ω✧)\n`{description[:120]}`"
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
        description="Read plan from a file (.md etc.) and execute it as a Shaula task",
    )
    @app_commands.describe(
        path="Plan file path — absolute or relative to /home/ubuntu/workspace",
        kantor="Run with office account (fallback engine Claude only)",
    )
    async def run_file_cmd(
        self, interaction: discord.Interaction, path: str, kantor: bool = False
    ):
        await self.task_file_cmd(interaction, path, kantor=kantor)

    @app_commands.command(
        name="task-file",
        description="Alias for /run-file",
    )
    @app_commands.describe(
        path="Plan file path — absolute or relative to /home/ubuntu/workspace",
        kantor="Run with office account (fallback engine Claude only)",
    )
    async def task_file_cmd(
        self, interaction: discord.Interaction, path: str, kantor: bool = False
    ):
        await interaction.response.defer()

        full = os.path.normpath(
            path if os.path.isabs(path) else os.path.join(WORKSPACE_DIR, path)
        )
        if not os.path.isfile(full):
            await interaction.followup.send(f"❌ File not found, Shisou~: `{full}`")
            return
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to read file, Shisou~: `{e}`")
            return
        if not content:
            await interaction.followup.send(
                f"⚠️ File `{os.path.basename(full)}` is empty — nothing to run, Shisou~"
            )
            return
        MAX_PLAN = 100_000  # ~100 KB; guards against a stray huge file blowing up tokens
        if len(content) > MAX_PLAN:
            await interaction.followup.send(
                f"⚠️ Plan is too long ({len(content) // 1000} KB > {MAX_PLAN // 1000} KB), "
                "please split it first, Shisou~"
            )
            return

        fname = os.path.basename(full)
        size_kb = len(content.encode("utf-8")) / 1024

        if kantor:
            if not config.CLAUDE_KANTOR_CONFIG_DIR or not os.path.isdir(
                config.CLAUDE_KANTOR_CONFIG_DIR
            ):
                await interaction.followup.send(
                    "⚠️ Office account is not configured, Shisou~ "
                    f"Config dir `{config.CLAUDE_KANTOR_CONFIG_DIR}` not found."
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
                f"🟣 Reading plan from **{fname}** ({size_kb:.1f} KB) — running with **office account**, Shisou~"
            )
            await _execute_and_stream(
                record, interaction.channel, config_dir=config.CLAUDE_KANTOR_CONFIG_DIR
            )
        else:
            await interaction.followup.send(
                f"📄 Reading plan from **{fname}** ({size_kb:.1f} KB) — Shaula will work on it in the thread, Shisou~! (✧ω✧)"
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
        note = " *(risky operation — Shaula will still run in auto; press 🛑 Stop if you need to abort, Shisou~)*"
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
        f"📋 **Shaula is analyzing Shisou's requirements and preparing options/questions first~** ✨\n"
        f"Please wait a moment, Shisou~ (✧ω✧)"
    )

    questions = await claude_runner.generate_plan_questions(
        task,
        config_dir=config_dir,
        on_activity=_make_plan_activity_callback(init_msg, "Analyzing requirements"),
    )

    qna = []
    if questions:
        await init_msg.edit(
            content=(
                f"✨ Shaula has analyzed the codebase! There are **{len(questions)} points** "
                f"that need clarification to make the plan just right. Please select an option below, Shisou~ 👇"
            )
        )
        for idx, q_item in enumerate(questions):
            q_text = q_item.get("question", "").strip()
            opts = q_item.get("options", [])
            if not q_text or not opts:
                continue

            num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
            formatted_opts = []
            for o_idx, opt in enumerate(opts):
                emo = num_emojis[o_idx] if o_idx < len(num_emojis) else "🔹"
                formatted_opts.append(f"{emo} {opt}")

            options_display = "\n".join(formatted_opts)
            msg_content = (
                f"**Question {idx + 1} of {len(questions)}:**\n"
                f"> **{q_text}**\n\n"
                f"{options_display}\n\n"
                f"*These are Shaula's actual options. Click one, click ✏️ Custom answer, or type your own answer directly in this thread, Shisou~*"
            )

            fut = asyncio.get_running_loop().create_future()
            view = PlanQuestionView(
                creator_id=creator_id,
                options=opts,
                future=fut,
                channel_id=thread.id,
                persona=persona,
                timeout=300.0,
            )
            q_msg = await thread.send(content=msg_content, view=view)
            view.message = q_msg

            try:
                selected_answer = await fut
            except Exception:
                selected_answer = None

            if selected_answer is None:
                await thread.send(
                    "⏸️ **Planning paused:** Shaula needs this decision from Shisou before continuing. "
                    "Please start `/plan` again when you're ready, Shisou~"
                )
                return

            qna.append({"question": q_text, "answer": selected_answer})
    else:
        await init_msg.edit(
            content=(
                "💡 Shisou's requirements are crystal clear! "
                "Shaula will craft the full Implementation Plan right away~ 🚀 (✧ω✧)"
            )
        )

    plan_status_msg = await thread.send("📝 **Drafting comprehensive Implementation Plan...**")
    plan_text = await claude_runner.generate_final_plan(
        task,
        qna,
        config_dir=config_dir,
        on_chunk=_make_plan_stream_callback(plan_status_msg, "Drafting Implementation Plan"),
        on_activity=_make_plan_activity_callback(plan_status_msg, "Drafting Implementation Plan"),
    )

    if not plan_text:
        await plan_status_msg.edit(
            content="⚠️ Sorry Shisou, Shaula couldn't generate the plan. "
            "You can try running directly via `/run` or try again, Shisou~"
        )
        return

    # The planning pass should have asked every material question first.  If the
    # agent nevertheless embeds explicit Question/Choice sections in its draft,
    # turn them into real Discord buttons and regenerate before publishing.
    late_questions = claude_runner.extract_deferred_plan_questions(plan_text)
    if late_questions:
        await plan_status_msg.edit(
            content="❓ **Shaula found decisions in the draft that need Shisou's input first.** "
            "Please choose below; Shaula will finalize the plan afterward~"
        )
        for idx, q_item in enumerate(late_questions):
            q_text = q_item["question"]
            opts = q_item["options"]
            fut = asyncio.get_running_loop().create_future()
            view = PlanQuestionView(
                creator_id=creator_id, options=opts, future=fut, channel_id=thread.id,
                persona=persona, timeout=300.0,
            )
            options_display = "\n".join(
                f"{['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣'][option_idx]} {option}"
                for option_idx, option in enumerate(opts)
            )
            q_msg = await thread.send(
                f"**Question {idx + 1} of {len(late_questions)}:**\n> **{q_text}**\n\n"
                f"{options_display}\n\n"
                "*These are Shaula's actual options. Click one, click ✏️ Custom answer, or type your own answer directly in this thread, Shisou~*",
                view=view,
            )
            view.message = q_msg
            selected_answer = await fut
            if selected_answer is None:
                await thread.send("⏸️ **Planning paused:** Shaula needs this decision before finalizing the plan, Shisou~")
                return
            qna.append({"question": q_text, "answer": selected_answer})

        plan_text = await claude_runner.generate_revised_plan(
            task=task, previous_plan=plan_text,
            revision_instruction="Use Shisou's selected answers above. Return the complete final plan now; do not include any unanswered questions or choices.",
            qna=qna, config_dir=config_dir,
            on_chunk=_make_plan_stream_callback(plan_status_msg, "Finalizing Implementation Plan"),
            on_activity=_make_plan_activity_callback(plan_status_msg, "Finalizing Implementation Plan"),
        )
        if not plan_text:
            await plan_status_msg.edit(content="⚠️ Sorry Shisou, Shaula couldn't finalize the plan after the choices.")
            return

    task.plan_text = plan_text
    try:
        await plan_status_msg.edit(content="✅ **Implementation Plan drafted below.**")
    except discord.HTTPException:
        pass

    header = "📋 **Implementation Plan is Ready, Shisou!** ٩(◕‿◕｡)۶\n\n"
    if len(plan_text) + len(header) <= 1900:
        await thread.send(f"{header}{plan_text}")
    else:
        preview = plan_text[:1200]
        await thread.send(
            f"{header}>>> {preview}...\n\n*(Full plan is quite long, Shaula attached the `.md` file below, Shisou~)*",
            file=discord.File(
                io.BytesIO(plan_text.encode("utf-8")),
                filename=f"plan_{task.task_id[:8]}.md",
            ),
        )

    current_plan = plan_text

    async def _on_confirm_execute(exec_channel):
        exec_task = task_store.create_task(
            description=(
                f"Execute the following plan approved by Shisou:\n\n{current_plan}"
            ),
            creator_id=creator_id,
            guild_id=guild_id,
            channel_id=exec_channel.id,
        )
        claude_runner.ensure_project_dir(exec_task)
        await _execute_and_stream(exec_task, exec_channel, config_dir=config_dir)

    async def _on_plan_revision(rev_channel, revision_text: str, author: discord.User | discord.Member):
        nonlocal current_plan
        status_msg = await rev_channel.send(
            f"🔄 **Shaula is updating the Implementation Plan based on Shisou's feedback:**\n"
            f"> *\"{revision_text}\"*\n\n"
            f"*Please wait a moment while Shaula refines the battle plan, Shisou~ (๑•̀ㅂ•́)و✧*"
        )
        qna.append({"question": "Revision / Modification requested by Shisou", "answer": revision_text})
        new_plan = await claude_runner.generate_revised_plan(
            task=task,
            previous_plan=current_plan,
            revision_instruction=revision_text,
            qna=qna,
            config_dir=config_dir,
            on_chunk=_make_plan_stream_callback(status_msg, "Updating revised plan"),
            on_activity=_make_plan_activity_callback(status_msg, "Updating revised plan"),
        )
        try:
            await status_msg.edit(content="✅ **Revised Implementation Plan drafted below.**")
        except discord.HTTPException:
            pass

        if not new_plan:
            await rev_channel.send(
                "⚠️ Sorry Shisou, Shaula couldn't update the plan. "
                "You can still execute the previous plan or type another feedback, Shisou~"
            )
            fallback_view = PlanExecuteView(
                creator_id=creator_id,
                on_execute=_on_confirm_execute,
                channel_id=rev_channel.id,
                on_chat_input=_on_plan_revision,
                persona=persona,
                timeout=900.0,
            )
            f_msg = await rev_channel.send(
                "👇 **Should Shaula execute the plan, or would Shisou like to adjust anything else?** (✧ω✧)",
                view=fallback_view,
            )
            fallback_view.message = f_msg
            return

        current_plan = new_plan
        task.plan_text = new_plan

        header = "📋 **Revised Implementation Plan is Ready, Shisou!** ٩(◕‿◕｡)۶\n\n"
        if len(new_plan) + len(header) <= 1900:
            await rev_channel.send(f"{header}{new_plan}")
        else:
            preview = new_plan[:1200]
            await rev_channel.send(
                f"{header}>>> {preview}...\n\n*(Full plan is quite long, Shaula attached the `.md` file below, Shisou~)*",
                file=discord.File(
                    io.BytesIO(new_plan.encode("utf-8")),
                    filename=f"plan_{task.task_id[:8]}_revised.md",
                ),
            )

        rev_view = PlanExecuteView(
            creator_id=creator_id,
            on_execute=_on_confirm_execute,
            channel_id=rev_channel.id,
            on_chat_input=_on_plan_revision,
            persona=persona,
            timeout=900.0,
        )
        r_msg = await rev_channel.send(
            "👇 **What do you think, Shisou? Should Shaula execute this revised plan now?** (✧ω✧)\n"
            "*Click Execute Plan, click Cancel, or type further feedback directly in this thread!*",
            view=rev_view,
        )
        rev_view.message = r_msg

    exec_view = PlanExecuteView(
        creator_id=creator_id,
        on_execute=_on_confirm_execute,
        channel_id=thread.id,
        on_chat_input=_on_plan_revision,
        persona=persona,
        timeout=900.0,
    )
    exec_msg = await thread.send(
        "👇 **What do you think, Shisou? Should Shaula execute this plan now?** (✧ω✧)\n"
        "*Click Execute Plan, click Cancel, or type your adjustments directly in this thread, Shisou~*",
        view=exec_view,
    )
    exec_view.message = exec_msg


async def setup(bot: commands.Bot):
    await bot.add_cog(TaskCommands(bot))
