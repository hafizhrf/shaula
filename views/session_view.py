"""
Inline buttons for the Claude session lifecycle.

- StopSessionView  → attached to the "🟢 Session Claude aktif" notice. One button
  stops the live session (same as typing `stop session`). The button is retired the
  moment the conversation continues — see commands.task._clear_active_button, which
  edits the notice to drop this view at the start of every new turn.
- HapusThreadView   → attached to the "🛑 Session ditutup" notice (only inside a
  session thread). One button deletes the thread (same as typing `hapus thread`).

Views use timeout=None: their lifecycle is driven by the session, not a timer. They
are not registered as persistent across restarts — after a bot restart the in-memory
session is gone anyway, so a stale Stop click degrades gracefully (it just clears the
button), and that's acceptable for these throwaway notices.
"""
import logging

import discord

logger = logging.getLogger(__name__)


class HapusThreadView(discord.ui.View):
    """Single 🗑️ button to delete the session thread, mirroring the `hapus thread` command."""

    def __init__(self, persona: str = "Shaula"):
        super().__init__(timeout=None)
        self.persona = persona

    @discord.ui.button(label="Delete thread", style=discord.ButtonStyle.secondary, emoji="🗑️")
    async def delete_thread(self, interaction: discord.Interaction, button: discord.ui.Button):
        from services import claude_session

        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.edit_message(view=None)
            return
        import config
        engine_label = "Antigravity" if getattr(config, "CLI_ENGINE", "") == "agy" else "Claude"
        if claude_session.is_active(channel.id):
            await interaction.response.send_message(
                f"⚠️ {engine_label} session is still active! Stop it first so {self.persona} can delete the thread, Shisou~",
                ephemeral=True,
            )
            return

        # Drop the button first; the channel is about to disappear anyway.
        await interaction.response.edit_message(view=None)
        try:
            await channel.send(f"🗑️ Alright Shisou, {self.persona} is deleting this thread now~ Bye bye! ✨ (✧ω✧)")
            await channel.delete()
        except discord.Forbidden:
            await channel.send(
                f"⚠️ {self.persona} doesn't have `Manage Threads` permission to delete this thread. "
                "Please grant it first, Shisou~"
            )
        except discord.HTTPException:
            pass


class StopSessionView(discord.ui.View):
    """Single 🛑 button to stop the live session, mirroring the `stop session` command."""

    def __init__(self, channel_id: int, persona: str = "Shaula"):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.persona = persona
        self.message: discord.Message | None = None

    @discord.ui.button(label="Stop session", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop_session(self, interaction: discord.Interaction, button: discord.ui.Button):
        import config
        from services import claude_session
        from commands.task import archive_thread

        if not claude_session.is_active(self.channel_id):
            # Already closed (idle sweep, or a `stop session` message beat the button).
            await interaction.response.edit_message(view=None)
            return

        killed = claude_session.kill_session_task(claude_session.get(self.channel_id))
        sess = claude_session.stop(self.channel_id)
        turns = sess.turns if sess else 0
        note = f" The running task was stopped too, Shisou~" if killed else ""

        # Retire this Stop button from the active-session notice.
        await interaction.response.edit_message(view=None)

        is_thread = isinstance(interaction.channel, discord.Thread)
        view = HapusThreadView(self.persona) if is_thread else None
        engine_label = "Antigravity" if getattr(config, "CLI_ENGINE", "") == "agy" else "Claude"
        await interaction.channel.send(
            f"🛑 {engine_label} session closed, Shisou~ ({turns} turn(s)).{note}",
            view=view,
        )
        await archive_thread(interaction.channel)
