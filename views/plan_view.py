"""
Interactive Discord views for the /plan workflow:
- PlanQuestionView: Presents multiple-choice options as clickable Discord buttons.
- PlanExecuteView: Presents confirmation to execute or cancel the generated plan.
"""
import asyncio
import logging
from typing import Callable, Optional

import discord

import config

logger = logging.getLogger(__name__)

NUM_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


class PlanQuestionView(discord.ui.View):
    """Presents a question's options as buttons. Resolves a Future when an option is selected."""

    def __init__(
        self,
        creator_id: int,
        options: list[str],
        future: asyncio.Future,
        persona: str = "Shaula",
        timeout: float = 300.0,
    ):
        super().__init__(timeout=timeout)
        self.creator_id = creator_id
        self.options = options
        self.future = future
        self.persona = persona
        self.message: Optional[discord.Message] = None

        # Build option buttons dynamically
        for idx, opt in enumerate(options[:4]):  # Max 4 choices + 1 skip button = 5 buttons (1 row)
            emoji = NUM_EMOJIS[idx] if idx < len(NUM_EMOJIS) else "🔹"
            # Keep button label compact (Discord max label length is 80 chars)
            clean_label = opt.strip().replace("\n", " ")
            if len(clean_label) > 65:
                clean_label = clean_label[:62] + "..."
            button_label = f"{idx + 1}. {clean_label}"

            btn = discord.ui.Button(
                label=button_label[:80],
                style=discord.ButtonStyle.primary,
                emoji=emoji,
                custom_id=f"plan_opt_{idx}",
            )
            btn.callback = self._make_callback(opt, idx)
            self.add_item(btn)

        # Skip / Default button
        skip_btn = discord.ui.Button(
            label="Terserah Shaula",
            style=discord.ButtonStyle.secondary,
            emoji="⏩",
            custom_id="plan_opt_skip",
        )
        skip_btn.callback = self._make_callback("Terserah Shaula (pilih opsi terbaik menurutmu)", -1)
        self.add_item(skip_btn)

    def _can_interact(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.creator_id:
            return True
        user_role_ids = {r.id for r in interaction.user.roles}
        return bool(user_role_ids & config.ALLOWED_APPROVER_ROLE_IDS)

    def _make_callback(self, choice_text: str, choice_idx: int):
        async def callback(interaction: discord.Interaction):
            if not self._can_interact(interaction):
                await interaction.response.send_message(
                    f"Hanya Shisou <@{self.creator_id}> yang bisa memilih opsi ini ya~",
                    ephemeral=True,
                )
                return

            self.stop()
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
                    if (choice_idx >= 0 and item.custom_id == f"plan_opt_{choice_idx}") or (
                        choice_idx < 0 and item.custom_id == "plan_opt_skip"
                    ):
                        item.style = discord.ButtonStyle.success

            selected_desc = f"**{choice_text}**" if choice_idx >= 0 else "*Terserah Shaula*"
            await interaction.response.edit_message(
                content=f"{interaction.message.content}\n\n👉 **Dipilih oleh Shisou:** {selected_desc}",
                view=self,
            )

            if not self.future.done():
                self.future.set_result(choice_text)

        return callback

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content=f"{self.message.content}\n\n*(⏰ Waktu habis — Shaula pilihkan opsi rekomendasi ya~)*",
                    view=self,
                )
            except discord.HTTPException:
                pass
        if not self.future.done():
            # Default to first option or fallback text
            default_choice = self.options[0] if self.options else "Terserah Shaula"
            self.future.set_result(default_choice)


class PlanExecuteView(discord.ui.View):
    """Attached to the final plan message to confirm execution or cancel."""

    def __init__(
        self,
        creator_id: int,
        on_execute: Callable,
        persona: str = "Shaula",
        timeout: float = 900.0,  # 15 mins
    ):
        super().__init__(timeout=timeout)
        self.creator_id = creator_id
        self.on_execute = on_execute
        self.persona = persona
        self.message: Optional[discord.Message] = None

    def _can_interact(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.creator_id:
            return True
        user_role_ids = {r.id for r in interaction.user.roles}
        return bool(user_role_ids & config.ALLOWED_APPROVER_ROLE_IDS)

    @discord.ui.button(label="Jalankan Plan Ini", style=discord.ButtonStyle.success, emoji="🚀")
    async def execute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_interact(interaction):
            await interaction.response.send_message(
                f"Hanya Shisou <@{self.creator_id}> yang bisa mengeksekusi plan ini ya~",
                ephemeral=True,
            )
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(view=self)
        await interaction.channel.send(
            f"🚀 **Plan disetujui oleh Shisou {interaction.user.mention}!**\n"
            f"Shaula langsung mulai eksekusi sekarang ya~ Ganbarimasu! (๑•̀ㅂ•́)و✧"
        )
        asyncio.create_task(self.on_execute(interaction.channel))

    @discord.ui.button(label="Batalkan", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_interact(interaction):
            await interaction.response.send_message(
                f"Hanya Shisou <@{self.creator_id}> yang bisa membatalkan plan ini ya~",
                ephemeral=True,
            )
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(view=self)
        await interaction.channel.send(f"🛑 Plan dibatalkan oleh {interaction.user.mention}.")

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
