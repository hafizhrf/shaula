import os
import signal

import discord
from discord import app_commands
from discord.ext import commands

from services import task_store, claude_session
from services.task_store import TaskState


class StopTaskCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="stop-task", description="Stop running tasks and close Shaula's session")
    @app_commands.describe(task_id="Task ID (first 8 chars shown in the task message). Leave empty to stop all running tasks.")
    async def stop_task(self, interaction: discord.Interaction, task_id: str = ""):
        running = task_store.list_running()
        # Also close the channel's persistent Claude session, if any.
        closed_sess = claude_session.stop(interaction.channel_id) if not task_id else None

        if closed_sess and closed_sess.is_thread:
            from commands.task import archive_thread
            await archive_thread(interaction.channel)

        if not running:
            if closed_sess:
                from commands.task import _clear_active_button
                from views.session_view import HapusThreadView
                await _clear_active_button(closed_sess)
                is_thread = isinstance(interaction.channel, discord.Thread)
                await interaction.response.send_message(
                    f"🛑 Shaula session closed, Shisou~ ({closed_sess.turns} turn(s)).",
                    view=HapusThreadView("Shaula") if is_thread else None,
                )
            else:
                await interaction.response.send_message("No active tasks or sessions found, Shisou~", ephemeral=True)
            return

        if task_id:
            targets = [t for t in running if t.task_id.startswith(task_id.lower())]
            if not targets:
                ids = ", ".join(f"`{t.task_id[:8]}`" for t in running)
                await interaction.response.send_message(
                    f"Task `{task_id}` not found, Shisou~. Currently running: {ids}", ephemeral=True
                )
                return
        else:
            targets = running

        stopped = []
        for task in targets:
            if task.process_pid:
                try:
                    os.kill(task.process_pid, signal.SIGTERM)
                    stopped.append(f"`{task.task_id[:8]}` — {task.description[:60]}")
                except ProcessLookupError:
                    pass
            task_store.update_state(task.task_id, TaskState.CANCELLED)

        if stopped:
            msg = "Shaula stopped the task(s), Shisou~! (๑•̀ㅂ•́)و✧\n" + "\n".join(f"❌ {s}" for s in stopped)
        else:
            msg = "Task already finished before it could be stopped, Shisou~"

        await interaction.response.send_message(msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(StopTaskCommands(bot))
