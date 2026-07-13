import io

import discord
from discord import app_commands
from discord.ext import commands

from services import monitor


def _status_color(status: str) -> str:
    if status == "running":
        return "🟢"
    elif status == "exited":
        return "🔴"
    elif status in ("paused", "restarting"):
        return "🟡"
    return "⚪"


class DockerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="docker-status", description="List all Docker containers and their status")
    async def docker_status(self, interaction: discord.Interaction):
        await interaction.response.defer()
        containers = await monitor.get_docker_containers()

        if not containers:
            await interaction.followup.send("No containers found (or Docker is not running).")
            return

        embed = discord.Embed(title="🐳 Docker Containers", color=discord.Color.blue())
        rows = []
        for c in containers:
            icon = _status_color(c["status"])
            rows.append(f"{icon} **{c['name']}** — `{c['status']}`\n`{c['image']}`")

        # Split into chunks if too many containers
        chunk = ""
        field_count = 0
        for row in rows:
            if len(chunk) + len(row) > 1000:
                embed.add_field(name="​", value=chunk, inline=False)
                chunk = row + "\n\n"
                field_count += 1
            else:
                chunk += row + "\n\n"
        if chunk:
            embed.add_field(name="​", value=chunk.strip(), inline=False)

        embed.set_footer(text=f"{len(containers)} container(s) total")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="docker-logs", description="Show recent logs from a container")
    @app_commands.describe(container="Container name", lines="Number of lines (default 50)")
    async def docker_logs(
        self,
        interaction: discord.Interaction,
        container: str,
        lines: int = 50,
    ):
        await interaction.response.defer()
        try:
            logs = await monitor.get_container_logs(container, tail=min(lines, 200))
        except Exception as e:
            await interaction.followup.send(f"❌ Error: `{e}`")
            return

        if not logs.strip():
            await interaction.followup.send(f"No logs found for `{container}`.")
            return

        if len(logs) > 1900:
            await interaction.followup.send(
                f"📄 Logs for `{container}` (last {lines} lines):",
                file=discord.File(io.BytesIO(logs.encode()), filename=f"{container}.log"),
            )
        else:
            await interaction.followup.send(f"📄 Logs for `{container}`:\n```\n{logs}\n```")


async def setup(bot: commands.Bot):
    await bot.add_cog(DockerCommands(bot))
