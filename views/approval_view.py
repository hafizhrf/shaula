import io
import logging

import discord

import config
from services import task_store
from services.task_store import RiskLevel, TaskRecord, TaskState

logger = logging.getLogger(__name__)


def _can_interact(interaction: discord.Interaction, task: TaskRecord) -> bool:
    if interaction.user.id == task.creator_id:
        return True
    user_role_ids = {r.id for r in interaction.user.roles}
    return bool(user_role_ids & config.ALLOWED_APPROVER_ROLE_IDS)


def _risk_color(risk: RiskLevel) -> discord.Color:
    return {
        RiskLevel.SAFE: discord.Color.green(),
        RiskLevel.MEDIUM: discord.Color.orange(),
        RiskLevel.DANGEROUS: discord.Color.red(),
    }[risk]


def make_plan_embed(task: TaskRecord) -> discord.Embed:
    plan = task.plan_text or "_No plan generated._"
    if len(plan) > 3000:
        plan = plan[:2997] + "..."

    embed = discord.Embed(
        title=f"📋 Task Plan — {task.risk_level.name}",
        color=_risk_color(task.risk_level),
    )
    embed.add_field(name="Task", value=task.description[:1024], inline=False)
    embed.add_field(name="Plan", value=plan, inline=False)
    embed.set_footer(text=f"Task {task.task_id[:8]} | Expires in 15 min | Creator <@{task.creator_id}>")
    return embed


def make_running_embed(task: TaskRecord) -> discord.Embed:
    # Truncate — the full prompt (which may carry a big attached doc) is posted separately;
    # an oversized embed would 400 ("Embed size exceeds maximum size of 6000").
    desc = task.description if len(task.description) <= 500 else task.description[:497] + "..."
    return discord.Embed(
        title="⚙️ Running...",
        description=f"**{desc}**\n\n_Waking up Shaula... (✧ω✧)_",
        color=discord.Color.blue(),
    ).set_footer(text=f"Task {task.task_id[:8]}")


def make_done_embed(task: TaskRecord, output: str) -> discord.Embed:
    embed = discord.Embed(
        title="✅ Task Complete",
        color=discord.Color.green(),
    )
    embed.add_field(name="Task", value=task.description[:512], inline=False)
    if task.cost_usd:
        embed.set_footer(text=f"Task {task.task_id[:8]} | Cost: ${task.cost_usd:.4f}")
    else:
        embed.set_footer(text=f"Task {task.task_id[:8]}")
    return embed


def make_failed_embed(task: TaskRecord, reason: str = "") -> discord.Embed:
    embed = discord.Embed(
        title="❌ Task Failed",
        description=reason or "Shaula nemu error saat eksekusi.",
        color=discord.Color.red(),
    )
    embed.set_footer(text=f"Task {task.task_id[:8]}")
    return embed


class ApprovalView(discord.ui.View):
    def __init__(self, task: TaskRecord):
        super().__init__(timeout=float(config.APPROVAL_TIMEOUT_SECONDS))
        self.task = task
        self.message: discord.Message | None = None

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_interact(interaction, self.task):
            await interaction.response.send_message(
                "You are not authorized to approve this task.", ephemeral=True
            )
            return

        self.stop()
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=f"✅ Approved by {interaction.user.mention} — starting execution...",
            embed=None,
            view=self,
        )
        interaction.client.dispatch("task_approved", self.task, interaction.channel)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_interact(interaction, self.task):
            await interaction.response.send_message(
                "You are not authorized to reject this task.", ephemeral=True
            )
            return

        task_store.update_state(self.task.task_id, TaskState.CANCELLED)
        self.stop()
        await interaction.response.edit_message(
            content=f"❌ Rejected by {interaction.user.mention}.",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Show Diff", style=discord.ButtonStyle.secondary, emoji="📄")
    async def show_diff(self, interaction: discord.Interaction, button: discord.ui.Button):
        plan = self.task.plan_text or "No plan available."
        if len(plan) > 1900:
            await interaction.response.send_message(
                file=discord.File(
                    io.BytesIO(plan.encode()),
                    filename=f"plan_{self.task.task_id[:8]}.txt",
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"```\n{plan}\n```", ephemeral=True
            )

    async def on_timeout(self):
        task_store.update_state(self.task.task_id, TaskState.CANCELLED)
        if self.message:
            try:
                await self.message.edit(
                    content="⏰ Approval timed out. Task cancelled.",
                    embed=None,
                    view=None,
                )
            except Exception:
                pass


class DangerousApprovalView(ApprovalView):
    """First confirmation step for DANGEROUS tasks — leads to a second confirm."""

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_interact(interaction, self.task):
            await interaction.response.send_message(
                "You are not authorized to approve this task.", ephemeral=True
            )
            return

        confirm_view = FinalDangerView(self.task)
        embed = discord.Embed(
            title="🚨 Final Approval Required",
            description=(
                f"**This is a DANGEROUS task.**\n\n"
                f"{self.task.description}\n\n"
                "This may delete or overwrite data. Are you absolutely sure?"
            ),
            color=discord.Color.dark_red(),
        )
        self.stop()
        msg = await interaction.response.edit_message(embed=embed, view=confirm_view)
        confirm_view.message = self.message


class FinalDangerView(discord.ui.View):
    def __init__(self, task: TaskRecord):
        super().__init__(timeout=float(config.APPROVAL_TIMEOUT_SECONDS))
        self.task = task
        self.message: discord.Message | None = None

    @discord.ui.button(label="Final Execute", style=discord.ButtonStyle.danger, emoji="🚨")
    async def final_approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_interact(interaction, self.task):
            await interaction.response.send_message(
                "You are not authorized.", ephemeral=True
            )
            return

        self.stop()
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=f"🚨 Final approval by {interaction.user.mention} — executing dangerous task...",
            embed=None,
            view=self,
        )
        interaction.client.dispatch("task_approved", self.task, interaction.channel)

    @discord.ui.button(label="Abort", style=discord.ButtonStyle.secondary, emoji="❌")
    async def abort(self, interaction: discord.Interaction, button: discord.ui.Button):
        task_store.update_state(self.task.task_id, TaskState.CANCELLED)
        self.stop()
        await interaction.response.edit_message(
            content=f"❌ Aborted by {interaction.user.mention}.",
            embed=None,
            view=None,
        )

    async def on_timeout(self):
        task_store.update_state(self.task.task_id, TaskState.CANCELLED)
        if self.message:
            try:
                await self.message.edit(
                    content="⏰ Final approval timed out. Task aborted.",
                    embed=None,
                    view=None,
                )
            except Exception:
                pass
