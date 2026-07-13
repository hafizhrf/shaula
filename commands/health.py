import discord
from discord import app_commands
from discord.ext import commands

from services import monitor


class HealthCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="server-health", description="Show VPS CPU, RAM, and disk stats")
    async def server_health(self, interaction: discord.Interaction):
        await interaction.response.defer()
        stats = await monitor.get_system_health()

        embed = discord.Embed(title="🖥️ Server Health", color=discord.Color.blue())
        embed.add_field(
            name="CPU",
            value=f"`{stats['cpu_percent']}%`\nLoad: `{stats['load_1']} / {stats['load_5']} / {stats['load_15']}`",
            inline=True,
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
        embed.add_field(
            name="Uptime",
            value=f"`{stats['uptime_hours']}h`",
            inline=True,
        )
        embed.add_field(
            name="Processes",
            value=f"`{stats['process_count']}`",
            inline=True,
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HealthCommands(bot))
