"""
/ask command — routes to local Hermes via Ollama for simple chat.
Falls back to a helpful message if Ollama is unavailable.
"""
import discord
from discord import app_commands
from discord.ext import commands

from services.ollama_router import local_chat


class AskCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ask", description="Ask Hermes (local AI) a question")
    @app_commands.describe(question="Your question")
    async def ask_cmd(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        answer = await local_chat(question)
        if answer:
            # Split long responses
            chunks = [answer[i:i+1900] for i in range(0, len(answer), 1900)]
            await interaction.followup.send(f"🤖 **Hermes:** {chunks[0]}")
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk)
        else:
            await interaction.followup.send(
                "⚠️ Hermes (Ollama) is not available right now. Use `/run` or `/task` to send tasks to Shaula, Shisou~"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AskCommands(bot))
