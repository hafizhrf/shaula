"""
Inline buttons for the Claude / Antigravity session lifecycle.

- StopSessionView  → attached to the "🟢 Session active" notice. One button
  stops the live session (same as typing `stop session`). The button is retired the
  moment the conversation continues — see commands.task._clear_active_button, which
  edits the notice to drop this view at the start of every new turn.
- HapusThreadView   → attached to the "🛑 Session closed" notice (only inside a
  session thread). One button deletes the thread (same as typing `hapus thread`).

Views use timeout=None and explicit custom_ids (`session:stop`, `session:delete_thread`)
so they can be registered as persistent views across bot restarts.
"""
import logging

import discord

logger = logging.getLogger(__name__)


class HapusThreadView(discord.ui.View):
    """Single 🗑️ button to delete the session thread, mirroring the `hapus thread` command."""

    def __init__(self, persona: str | None = None):
        super().__init__(timeout=None)
        self._persona = persona

    @property
    def persona(self) -> str:
        if self._persona:
            return self._persona
        import config
        return "Shaula" if getattr(config, "SHAULA_ENABLED", True) else "Emilia"

    @discord.ui.button(
        label="Delete thread",
        style=discord.ButtonStyle.secondary,
        emoji="🗑️",
        custom_id="session:delete_thread",
    )
    async def delete_thread(self, interaction: discord.Interaction, button: discord.ui.Button):
        from services import claude_session

        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            try:
                await interaction.response.edit_message(view=None)
            except discord.HTTPException:
                pass
            return

        import config
        engine_label = "Antigravity" if getattr(config, "CLI_ENGINE", "") == "agy" else "Claude"
        if claude_session.is_active(channel.id):
            await interaction.response.send_message(
                f"⚠️ {engine_label} session is still active! Stop it first so {self.persona} can delete the thread, Shisou~",
                ephemeral=True,
            )
            return

        # Acknowledge and drop the button first; the channel is about to disappear anyway.
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass
        try:
            await channel.send(f"🗑️ Alright Shisou, {self.persona} is deleting this thread now~ Bye bye! ✨ (✧ω✧)")
            await channel.delete()
            project_dir = os.path.join(getattr(config, "PROJECTS_BASE_DIR", "/opt/agent/projects"), f"session-{channel.id}")
            if os.path.isdir(project_dir):
                import shutil
                shutil.rmtree(project_dir, ignore_errors=True)
        except discord.Forbidden:
            await channel.send(
                f"⚠️ {self.persona} doesn't have `Manage Threads` permission to delete this thread. "
                "Please grant it first, Shisou~"
            )
        except discord.HTTPException:
            pass


class StopSessionView(discord.ui.View):
    """Single 🛑 button to stop the live session, mirroring the `stop session` command."""

    def __init__(self, channel_id: int | None = None, persona: str | None = None):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self._persona = persona
        self.message: discord.Message | None = None

    @property
    def persona(self) -> str:
        if self._persona:
            return self._persona
        import config
        return "Shaula" if getattr(config, "SHAULA_ENABLED", True) else "Emilia"

    @discord.ui.button(
        label="Stop session",
        style=discord.ButtonStyle.danger,
        emoji="🛑",
        custom_id="session:stop",
    )
    async def stop_session(self, interaction: discord.Interaction, button: discord.ui.Button):
        import config
        from services import claude_session
        from commands.task import archive_thread, _clear_active_button

        channel_id = self.channel_id or interaction.channel_id
        channel = interaction.channel

        # Acknowledge immediately and remove the button so Discord doesn't timeout (< 3s)
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            pass

        sess = claude_session.get(channel_id) if channel_id else None
        killed = claude_session.kill_session_task(sess) if sess else False
        stopped_sess = claude_session.stop(channel_id) if channel_id else None

        # Clean up any leftover question options or other buttons in channel
        if channel:
            await _clear_active_button(sess or stopped_sess, channel)

        turns = stopped_sess.turns if stopped_sess else (sess.turns if sess else 0)
        note = " The running task was stopped too, Shisou~" if killed else ""

        is_thread = isinstance(channel, discord.Thread)
        view = HapusThreadView(self.persona) if is_thread else None
        engine_label = "Antigravity" if getattr(config, "CLI_ENGINE", "") == "agy" else "Claude"

        if channel:
            await channel.send(
                f"🛑 {engine_label} session closed, Shisou~ ({turns} turn(s)).{note}",
                view=view,
            )
            await archive_thread(channel)
