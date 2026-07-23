"""
/import command -- upload an .apkg file and register it as a studyable deck.

Named import_cmd.py (not import.py) because `import` is a reserved Python
keyword and can't be used as a module name.
"""
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from anki.importer import import_apkg, UnsupportedApkgError


class ImportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="import", description="Import an Anki .apkg deck")
    @app_commands.describe(
        file="The .apkg file to import",
        deck_name="Name to give this deck (e.g. 'kaishi'). Defaults to the filename.",
    )
    async def import_deck(self, interaction: discord.Interaction, file: discord.Attachment, deck_name: str = None):
        if not file.filename.lower().endswith(".apkg"):
            await interaction.response.send_message("That doesn't look like an `.apkg` file.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        name = deck_name or Path(file.filename).stem

        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / file.filename
            await file.save(local_path)
            try:
                summary = import_apkg(str(local_path), name)
            except ValueError as e:
                await interaction.followup.send(
                    f"{e}\n\nIf you're trying to re-import an updated version of this deck: run "
                    f"`/deck export deck:{name}` to back up your progress first, then "
                    f"`/deck delete deck:{name}` to free up the name. Deleting is permanent and "
                    f"there's no automatic restore from the backup file -- that's on you to keep safe."
                )
                return
            except UnsupportedApkgError as e:
                await interaction.followup.send(f"Couldn't import this deck: {e}")
                return

        await interaction.followup.send(
            f"Imported deck **{name}**: {summary['notes']} notes, "
            f"{summary['cards']} card templates, {summary['media']} media files.\n"
            f"Try `/study {name}` to start reviewing."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ImportCog(bot))
