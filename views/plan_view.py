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

# Active question registry for /plan mode: channel_id -> PlanQuestionView
_active_plan_questions: dict[int, "PlanQuestionView"] = {}


def get_active_plan_question(channel_id: int) -> Optional["PlanQuestionView"]:
    return _active_plan_questions.get(channel_id)


def register_plan_question(channel_id: int, view: "PlanQuestionView") -> None:
    if channel_id:
        _active_plan_questions[channel_id] = view


def unregister_plan_question(channel_id: int) -> None:
    if channel_id:
        _active_plan_questions.pop(channel_id, None)


class CustomAnswerModal(discord.ui.Modal, title="Custom Answer"):
    answer = discord.ui.TextInput(
        label="Your custom choice / instruction",
        style=discord.TextStyle.paragraph,
        placeholder="Type what you want here (e.g. ai assist + ocr too if can)...",
        required=True,
        max_length=1000,
    )

    def __init__(self, parent_view: "PlanQuestionView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        custom_text = self.answer.value.strip()
        if not custom_text:
            await interaction.response.send_message("Answer cannot be empty, Shisou~", ephemeral=True)
            return

        await interaction.response.defer()
        await self.parent_view.handle_custom_input(custom_text, interaction.user)


class PlanQuestionView(discord.ui.View):
    """Presents a question's options as buttons. Resolves a Future when an option is selected or text is typed."""

    def __init__(
        self,
        creator_id: int,
        options: list[str],
        future: asyncio.Future,
        channel_id: int = 0,
        persona: str = "Shaula",
        timeout: float = 300.0,
    ):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id
        self.creator_id = creator_id
        self.options = options
        self.future = future
        self.persona = persona
        self.message: Optional[discord.Message] = None

        # Build option buttons dynamically (first 3 in row 0, 4th in row 1 if present)
        for idx, opt in enumerate(options[:4]):
            emoji = NUM_EMOJIS[idx] if idx < len(NUM_EMOJIS) else "🔹"
            clean_label = opt.strip().replace("\n", " ")
            if len(clean_label) > 60:
                clean_label = clean_label[:57] + "..."
            button_label = f"{idx + 1}. {clean_label}"

            btn = discord.ui.Button(
                label=button_label[:80],
                style=discord.ButtonStyle.primary,
                emoji=emoji,
                custom_id=f"plan_opt_{idx}",
                row=0 if idx < 3 else 1,
            )
            btn.callback = self._make_callback(opt, idx)
            self.add_item(btn)

        # Custom answer button (opens Modal)
        custom_btn = discord.ui.Button(
            label="Custom answer",
            style=discord.ButtonStyle.secondary,
            emoji="✏️",
            custom_id="plan_opt_custom",
            row=1,
        )
        custom_btn.callback = self._make_custom_callback()
        self.add_item(custom_btn)

        # Skip / Default button
        skip_btn = discord.ui.Button(
            label="As Shaula wishes",
            style=discord.ButtonStyle.secondary,
            emoji="⏩",
            custom_id="plan_opt_skip",
            row=1,
        )
        skip_btn.callback = self._make_callback("As Shaula wishes (pick best option)", -1)
        self.add_item(skip_btn)

        if channel_id:
            register_plan_question(channel_id, self)

    def _can_interact_user(self, user: discord.User | discord.Member) -> bool:
        if user.id == self.creator_id:
            return True
        if hasattr(user, "roles"):
            user_role_ids = {r.id for r in user.roles}
            return bool(user_role_ids & config.ALLOWED_APPROVER_ROLE_IDS)
        return False

    def _can_interact(self, interaction: discord.Interaction) -> bool:
        return self._can_interact_user(interaction.user)

    def _make_custom_callback(self):
        async def callback(interaction: discord.Interaction):
            if not self._can_interact(interaction):
                await interaction.response.send_message(
                    f"Only Shisou <@{self.creator_id}> can choose this option, Shisou~",
                    ephemeral=True,
                )
                return
            modal = CustomAnswerModal(self)
            await interaction.response.send_modal(modal)

        return callback

    def _make_callback(self, choice_text: str, choice_idx: int):
        async def callback(interaction: discord.Interaction):
            if not self._can_interact(interaction):
                await interaction.response.send_message(
                    f"Only Shisou <@{self.creator_id}> can choose this option, Shisou~",
                    ephemeral=True,
                )
                return

            self.stop()
            unregister_plan_question(self.channel_id)

            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
                    if (choice_idx >= 0 and item.custom_id == f"plan_opt_{choice_idx}") or (
                        choice_idx < 0 and item.custom_id == "plan_opt_skip"
                    ):
                        item.style = discord.ButtonStyle.success

            selected_desc = f"**{choice_text}**" if choice_idx >= 0 else "*As Shaula wishes*"
            await interaction.response.edit_message(
                content=f"{interaction.message.content}\n\n👉 **Selected by Shisou:** {selected_desc}",
                view=self,
            )

            if not self.future.done():
                self.future.set_result(choice_text)

        return callback

    async def handle_custom_input(self, text: str, user: discord.User | discord.Member) -> bool:
        if not self._can_interact_user(user):
            return False

        self.stop()
        unregister_plan_question(self.channel_id)

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
                if item.custom_id == "plan_opt_custom":
                    item.style = discord.ButtonStyle.success

        clean_text = text.strip()
        display_text = clean_text if len(clean_text) <= 200 else clean_text[:197] + "..."
        if self.message:
            try:
                await self.message.edit(
                    content=f"{self.message.content}\n\n👉 **Custom answer by Shisou:** **{display_text}**",
                    view=self,
                )
            except discord.HTTPException:
                pass

        if not self.future.done():
            self.future.set_result(clean_text)
        return True

    async def on_timeout(self):
        unregister_plan_question(self.channel_id)
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content=f"{self.message.content}\n\n*(⏰ Time's up — Shaula will pick the recommended option, Shisou~)*",
                    view=self,
                )
            except discord.HTTPException:
                pass
        if not self.future.done():
            # Default to first option or fallback text
            default_choice = self.options[0] if self.options else "As Shaula wishes"
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

    @discord.ui.button(label="Execute Plan", style=discord.ButtonStyle.success, emoji="🚀")
    async def execute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_interact(interaction):
            await interaction.response.send_message(
                f"Only Shisou <@{self.creator_id}> can execute this plan, Shisou~",
                ephemeral=True,
            )
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(view=self)
        await interaction.channel.send(
            f"🚀 **Plan approved by Shisou {interaction.user.mention}!**\n"
            f"Shaula is starting execution right now~ Ganbarimasu! (๑•̀ㅂ•́)و✧"
        )
        asyncio.create_task(self.on_execute(interaction.channel))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_interact(interaction):
            await interaction.response.send_message(
                f"Only Shisou <@{self.creator_id}> can cancel this plan, Shisou~",
                ephemeral=True,
            )
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(view=self)
        await interaction.channel.send(f"🛑 Plan cancelled by {interaction.user.mention}, Shisou~")

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SessionQuestionChoiceView(discord.ui.View):
    """Buttons presented when a /run turn ends with clarifying questions or options."""

    def __init__(
        self,
        channel: discord.TextChannel | discord.Thread,
        creator_id: int,
        options: list[str],
        persona: str = "Shaula",
        timeout: float = 600.0,
    ):
        super().__init__(timeout=timeout)
        self.channel = channel
        self.creator_id = creator_id
        self.options = options
        self.persona = persona
        self.message: Optional[discord.Message] = None

        num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        for idx, opt in enumerate(options[:4]):
            emoji = num_emojis[idx] if idx < len(num_emojis) else "🔹"
            clean_label = opt.strip().replace("\n", " ")
            if len(clean_label) > 65:
                clean_label = clean_label[:62] + "..."
            button_label = f"{idx + 1}. {clean_label}"

            btn = discord.ui.Button(
                label=button_label[:80],
                style=discord.ButtonStyle.primary,
                emoji=emoji,
                custom_id=f"run_opt_{idx}",
            )
            btn.callback = self._make_callback(opt, idx)
            self.add_item(btn)

    def _can_interact(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.creator_id:
            return True
        user_role_ids = {r.id for r in interaction.user.roles}
        return bool(user_role_ids & config.ALLOWED_APPROVER_ROLE_IDS)

    def _make_callback(self, choice_text: str, choice_idx: int):
        async def callback(interaction: discord.Interaction):
            if not self._can_interact(interaction):
                await interaction.response.send_message(
                    f"Only Shisou <@{self.creator_id}> can choose this option, Shisou~",
                    ephemeral=True,
                )
                return

            self.stop()
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
                    if item.custom_id == f"run_opt_{choice_idx}":
                        item.style = discord.ButtonStyle.success

            await interaction.response.edit_message(
                content=f"{interaction.message.content}\n\n👉 **Selected by Shisou:** **{choice_text}**",
                view=self,
            )

            # Continue the live session with the chosen option as the user's prompt
            from commands.task import _execute_and_stream
            from services import task_store, conversation

            prompt = f"Shisou's choice: {choice_text}"
            conversation.add_message(self.channel.id, "user", prompt)
            exec_task = task_store.create_task(
                description=prompt,
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=self.channel.id,
            )
            asyncio.create_task(_execute_and_stream(exec_task, self.channel))

        return callback

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class InputPromptButtonsView(discord.ui.View):
    """Quick-reply buttons for stdin prompts (e.g. Yes/No, Proceed/Cancel)."""

    def __init__(
        self,
        channel_id: int,
        creator_id: int,
        options: list[str],
        timeout: float = 300.0,
    ):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id
        self.creator_id = creator_id
        self.message: Optional[discord.Message] = None

        for idx, opt in enumerate(options[:5]):
            style = (
                discord.ButtonStyle.success
                if opt.lower() in ("yes", "y", "ya", "lanjut", "proceed")
                else (
                    discord.ButtonStyle.danger
                    if opt.lower() in ("no", "n", "tidak", "batal", "cancel")
                    else discord.ButtonStyle.primary
                )
            )
            btn = discord.ui.Button(
                label=opt[:80],
                style=style,
                custom_id=f"input_opt_{idx}",
            )
            btn.callback = self._make_callback(opt)
            self.add_item(btn)

    def _make_callback(self, choice_text: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.creator_id:
                await interaction.response.send_message(
                    "Only the task creator can choose this response, Shisou~",
                    ephemeral=True,
                )
                return

            self.stop()
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            await interaction.response.edit_message(
                content=f"{interaction.message.content}\n\n👉 **Input sent:** `{choice_text}`",
                view=self,
            )

            from services import stdin_relay
            stdin_relay.provide_input(self.channel_id, choice_text)

        return callback

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

