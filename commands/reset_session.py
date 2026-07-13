import discord
from discord import app_commands
from discord.ext import commands

from services import conversation


class ResetSessionCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="reset-session", description="Clear Emilia's conversation history in this channel")
    async def reset_session(self, interaction: discord.Interaction):
        conversation.reset(interaction.channel_id)
        await interaction.response.send_message("🔄 Conversation history cleared.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ResetSessionCommands(bot))
