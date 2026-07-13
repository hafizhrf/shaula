import discord
from discord import app_commands
from discord.ext import commands

from services import skill_manager


class CorrectionsCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="corrections", description="Lihat dan kelola rules yang Emilia sudah pelajari")
    @app_commands.describe(remove="Nomor rule yang mau dihapus (opsional)")
    async def corrections_cmd(self, interaction: discord.Interaction, remove: int = 0):
        if remove > 0:
            removed = skill_manager.remove_correction(remove)
            if removed:
                await interaction.response.send_message(
                    f"✅ Rule #{remove} dihapus:\n> ~~{removed}~~", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"⚠️ Rule #{remove} tidak ditemukan.", ephemeral=True
                )
            return

        rules = skill_manager.list_corrections()
        skills = skill_manager.list_skills()

        embed = discord.Embed(title="🧠 Emilia's Memory", color=discord.Color.purple())

        if rules:
            lines = "\n".join(f"`{i+1}.` {r}" for i, r in enumerate(rules))
            embed.add_field(name=f"Rules ({len(rules)})", value=lines[:1000], inline=False)
        else:
            embed.add_field(name="Rules", value="*(belum ada — ajari Emilia sesuatu!)*", inline=False)

        if skills:
            lines = "\n".join(f"• **{n}** — {v['description']}" for n, v in skills.items())
            embed.add_field(name=f"Skills ({len(skills)})", value=lines[:1000], inline=False)
        else:
            embed.add_field(name="Skills", value="*(belum ada skill)*", inline=False)

        embed.set_footer(text="Gunakan /corrections remove:<nomor> untuk hapus rule")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(CorrectionsCommands(bot))
