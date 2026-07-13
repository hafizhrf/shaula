"""
/runs and /run — browse the persistent Claude run history (services/run_store.py).

Each run/turn is one row keyed by task_id, carrying the stable session_id (which also
names the thread title and the on-disk Claude transcript), so these commands let you map
a Discord thread → its runs → logs without leaving Discord.
"""
import discord
from discord import app_commands
from discord.ext import commands

from services import run_store

_STATE_EMOJI = {
    "RUNNING": "🔵",
    "DONE": "✅",
    "FAILED": "❌",
    "CANCELLED": "🛑",
    "INTERRUPTED": "🔌",
    "PENDING": "⚪",
}


def _short(s, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _when(row: dict) -> str:
    # ISO timestamps are stored as UTC; show the time part compactly.
    ts = row.get("finished_at") or row.get("created_at") or ""
    return ts.replace("T", " ")


class RunsCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="runs", description="Lihat history run Claude/Shaula terakhir")
    @app_commands.describe(limit="Berapa run terakhir (default 10, max 25)")
    async def runs_cmd(self, interaction: discord.Interaction, limit: int = 10):
        await interaction.response.defer()
        rows = await run_store.recent(limit)
        if not rows:
            await interaction.followup.send("📭 Belum ada run yang kecatat, Shisou~")
            return

        lines = []
        for r in rows:
            emoji = _STATE_EMOJI.get(r.get("state", ""), "•")
            tid = (r.get("task_id") or "")[:8]
            sid = (r.get("session_id") or "--------")[:8]
            cost = r.get("cost_usd") or 0.0
            acct = r.get("account") or "default"
            lines.append(
                f"{emoji} `{tid}` sess `{sid}` · {_short(r.get('description'), 48)} · "
                f"${cost:.3f} · {acct} · {_when(r)}"
            )
        embed = discord.Embed(
            title=f"🗂️ Run history (terakhir {len(rows)})",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Detail: /run <id>  (task_id atau session_id, 8 char awal cukup)")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="run", description="Detail satu run / semua turn dari sebuah session")
    @app_commands.describe(id="task_id atau session_id (boleh 8 char pertama aja)")
    async def run_cmd(self, interaction: discord.Interaction, id: str):
        await interaction.response.defer()
        rows = await run_store.get(id)
        if not rows:
            await interaction.followup.send(
                f"🔍 Nggak ketemu run dengan id `{id}`, Shisou~ (coba `/runs` buat lihat daftarnya)"
            )
            return

        # If the prefix matched a session, there may be several turns.
        head = rows[0]
        sid = (head.get("session_id") or "--------")[:8]
        embed = discord.Embed(
            title=f"🧵 Session `{sid}` — {len(rows)} turn",
            color=discord.Color.blurple(),
        )
        for r in rows[:25]:
            emoji = _STATE_EMOJI.get(r.get("state", ""), "•")
            tid = (r.get("task_id") or "")[:8]
            cost = r.get("cost_usd") or 0.0
            field_lines = [
                f"**state** {emoji} {r.get('state')}  ·  **akun** {r.get('account')}  ·  **${cost:.4f}**",
                f"**desc** {_short(r.get('description'), 300)}",
                f"**mulai** {r.get('created_at') or '?'}  ·  **selesai** {r.get('finished_at') or '—'}",
            ]
            err = (r.get("error_text") or "").strip()
            if err:
                field_lines.append(f"**error** {_short(err, 400)}")
            chan = r.get("channel_id")
            if chan:
                field_lines.append(f"**channel** <#{chan}>")
            embed.add_field(
                name=f"turn {r.get('turn')} · `{tid}`",
                value="\n".join(field_lines),
                inline=False,
            )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(RunsCommands(bot))
