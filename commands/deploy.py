import logging

import discord
from discord import app_commands
from discord.ext import commands

from services import claude_runner, task_store
from services.task_store import RiskLevel, TaskState
from views.approval_view import (
    ApprovalView,
    make_failed_embed,
    make_plan_embed,
)
from commands.task import _execute_and_stream

logger = logging.getLogger(__name__)

DEPLOY_SYSTEM_PROMPT = (
    "You are a deployment assistant on a DevOps server. "
    "When given a target (VPS hostname or Cloudflare domain), determine the deployment type:\n"
    "- Cloudflare domain: use `wrangler deploy` from the matching project directory.\n"
    "- VPS target: use docker compose, git pull, and service health checks via SSH if needed.\n"
    "Always verify the deployment succeeded (curl, docker ps, wrangler whoami).\n"
    "Output a summary: target, commands run, result, URL if applicable."
)


class DeployCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_listener(self._on_task_approved, "on_task_approved")

    async def _on_task_approved(self, task, channel):
        if task.description.startswith("__deploy__"):
            claude_runner.ensure_project_dir(task)
            await _execute_and_stream(task, channel)

    @app_commands.command(name="deploy", description="Deploy an app to a VPS or Cloudflare domain")
    @app_commands.describe(target="Domain or VPS hostname to deploy to (e.g. dify.hafizhrf.me)")
    async def deploy_cmd(self, interaction: discord.Interaction, target: str):
        await interaction.response.defer()

        description = f"__deploy__ Deploy to {target}. {DEPLOY_SYSTEM_PROMPT}"
        record = task_store.create_task(
            description=description,
            creator_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
        )
        # Deployments are always MEDIUM risk (require one approval)
        record.risk_level = RiskLevel.MEDIUM
        claude_runner.ensure_project_dir(record)

        await interaction.followup.send(f"🔍 Planning deployment to `{target}`...")
        plan = await claude_runner.run_planning(record)

        if plan is None:
            await interaction.edit_original_response(
                content=None,
                embed=make_failed_embed(record, "Could not generate a deployment plan."),
            )
            return

        embed = make_plan_embed(record)
        embed.title = f"🚀 Deployment Plan — `{target}`"
        view = ApprovalView(record)
        msg = await interaction.edit_original_response(content=None, embed=embed, view=view)
        view.message = msg


async def setup(bot: commands.Bot):
    await bot.add_cog(DeployCommands(bot))
