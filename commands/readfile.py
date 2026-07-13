"""
/readfile <path> — baca isi file dan tampilkan di Discord.
Berguna untuk mengirim plan panjang yang disimpan dalam .md atau file lainnya.
Path bisa absolut atau relatif ke /home/ubuntu/workspace.
"""
import io
import os

import discord
from discord import app_commands
from discord.ext import commands

WORKSPACE = "/home/ubuntu/workspace"
_CODE_EXTS = {"md", "py", "js", "ts", "json", "yaml", "yml", "sh", "txt", "toml", "env"}


class ReadFileCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="readfile",
        description="Baca isi file dan tampilkan di Discord (untuk plan panjang, .md, dll)",
    )
    @app_commands.describe(path="Path file — absolut atau relatif ke /home/ubuntu/workspace")
    async def readfile_cmd(self, interaction: discord.Interaction, path: str):
        await interaction.response.defer()

        full_path = (
            os.path.normpath(path)
            if os.path.isabs(path)
            else os.path.normpath(os.path.join(WORKSPACE, path))
        )

        if not os.path.isfile(full_path):
            await interaction.followup.send(f"❌ File tidak ditemukan: `{full_path}`")
            return

        try:
            with open(full_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except PermissionError:
            await interaction.followup.send(f"❌ Tidak ada izin baca: `{full_path}`")
            return
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal baca file: `{e}`")
            return

        filename = os.path.basename(full_path)
        size_kb = len(content.encode("utf-8")) / 1024
        header = f"📄 **{filename}** ({size_kb:.1f} KB) dari `{full_path}`"

        if len(content) <= 1800:
            ext = os.path.splitext(filename)[1].lstrip(".").lower()
            lang = ext if ext in _CODE_EXTS else ""
            await interaction.followup.send(f"{header}:\n```{lang}\n{content}\n```")
        else:
            # Too long for inline — send as file attachment so Discord renders it properly
            await interaction.followup.send(
                f"{header}\n*(konten terlalu panjang buat inline — Emilia kirim sebagai lampiran ya~)*",
                file=discord.File(io.BytesIO(content.encode("utf-8")), filename=filename),
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ReadFileCommands(bot))
