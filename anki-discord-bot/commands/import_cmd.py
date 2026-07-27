"""
/import command -- upload an .apkg file and register it as a studyable deck.

Named import_cmd.py (not import.py) because `import` is a reserved Python
keyword and can't be used as a module name.
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from anki.importer import import_apkg, UnsupportedApkgError
from commands.autocomplete import apkg_path_autocomplete


def _make_progress_cb(interaction: discord.Interaction, loop: asyncio.AbstractEventLoop):
    """
    Builds a progress_cb suitable for import_apkg's progress_cb param.
    import_apkg runs in a worker thread (see asyncio.to_thread below), so this
    callback hops back onto the bot's event loop via run_coroutine_threadsafe
    to edit the deferred "thinking..." response. Throttled to ~1 edit/4s so we
    don't hit Discord's rate limit on rapid decks (small decks finish so fast
    the throttle means most of them show no progress messages at all, which is
    fine -- the final result message still arrives via followup.send).
    """
    state = {"last_edit": 0.0}
    labels = {"media": "Copying media files", "notes": "Importing notes"}

    def progress_cb(stage: str, done: int, total: int):
        now = time.monotonic()
        is_final = done >= total
        if not is_final and (now - state["last_edit"] < 4):
            return
        state["last_edit"] = now
        text = f"⏳ {labels.get(stage, stage)}: {done}/{total}..."
        fut = asyncio.run_coroutine_threadsafe(
            interaction.edit_original_response(content=text), loop
        )
        try:
            fut.result(timeout=5)
        except Exception:
            # A missed progress update (rate limit, transient network blip) is
            # not worth failing the whole import over -- just skip this tick.
            pass

    return progress_cb


class ImportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="import", description="Import an Anki .apkg deck (Discord's ~10MB upload limit applies)")
    @app_commands.describe(
        file="The .apkg file to import -- must be under Discord's attachment size limit",
        deck_name="Name to give this deck (e.g. 'kaishi'). Defaults to the filename.",
    )
    async def import_deck(self, interaction: discord.Interaction, file: discord.Attachment, deck_name: str = None):
        if not file.filename.lower().endswith(".apkg"):
            await interaction.response.send_message("That doesn't look like an `.apkg` file.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        name = deck_name or Path(file.filename).stem
        loop = asyncio.get_running_loop()

        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / file.filename
            try:
                await file.save(local_path)
                summary = await asyncio.to_thread(
                    import_apkg, str(local_path), name, _make_progress_cb(interaction, loop)
                )
            except ValueError as e:
                await self._safe_followup(
                    interaction,
                    f"{e}\n\nIf you're trying to re-import an updated version of this deck: run "
                    f"`/deck export deck:{name}` to back up your progress first, then "
                    f"`/deck delete deck:{name}` to free up the name. Deleting is permanent and "
                    f"there's no automatic restore from the backup file -- that's on you to keep safe."
                )
                return
            except UnsupportedApkgError as e:
                await self._safe_followup(interaction, f"Couldn't import this deck: {e}")
                return
            except Exception as e:
                await self._safe_followup(
                    interaction, f"Import failed unexpectedly: `{type(e).__name__}: {e}`"
                )
                raise  # still surface it in the bot's own logs

        await self._safe_followup(
            interaction,
            f"Imported deck **{name}**: {summary['notes']} notes, "
            f"{summary['cards']} card templates, {summary['media']} media files.\n"
            f"Try `/study {name}` to start reviewing."
        )

    @staticmethod
    async def _safe_followup(interaction: discord.Interaction, content: str):
        """
        followup.send uses the interaction's webhook token, which expires 15
        minutes after the original command. On a very large import that ran
        past that window, this call will raise discord.NotFound/HTTPException
        -- there's no way to notify the user at that point (Discord gives no
        other channel back to them), so we just swallow it rather than crash
        the whole command dispatch.
        """
        try:
            await interaction.followup.send(content)
        except discord.HTTPException:
            pass

    @app_commands.command(
        name="import_path",
        description="Import an .apkg already on the bot's device (bypasses Discord's upload size limit)",
    )
    @app_commands.describe(
        path="Full path to the .apkg on the bot's filesystem, e.g. /storage/emulated/0/Download/kaishi.apkg",
        deck_name="Name to give this deck (e.g. 'kaishi'). Defaults to the filename.",
    )
    @app_commands.autocomplete(path=apkg_path_autocomplete)
    async def import_deck_from_path(self, interaction: discord.Interaction, path: str, deck_name: str = None):
        # Reads an arbitrary path on the bot's own device -- restrict to the bot owner only.
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot's owner can import from a local path (it reads the bot's own device storage).",
                ephemeral=True,
            )
            return

        if not path.lower().endswith(".apkg"):
            await interaction.response.send_message("That doesn't look like an `.apkg` path.", ephemeral=True)
            return
        if not os.path.isfile(path):
            await interaction.response.send_message(f"No file found at `{path}` on the bot's device.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        name = deck_name or Path(path).stem
        loop = asyncio.get_running_loop()

        try:
            summary = await asyncio.to_thread(
                import_apkg, path, name, _make_progress_cb(interaction, loop)
            )
        except ValueError as e:
            await self._safe_followup(
                interaction,
                f"{e}\n\nIf you're trying to re-import an updated version of this deck: run "
                f"`/deck export deck:{name}` to back up your progress first, then "
                f"`/deck delete deck:{name}` to free up the name."
            )
            return
        except UnsupportedApkgError as e:
            await self._safe_followup(interaction, f"Couldn't import this deck: {e}")
            return
        except Exception as e:
            await self._safe_followup(
                interaction, f"Import failed unexpectedly: `{type(e).__name__}: {e}`"
            )
            raise

        await self._safe_followup(
            interaction,
            f"Imported deck **{name}**: {summary['notes']} notes, "
            f"{summary['cards']} card templates, {summary['media']} media files.\n"
            f"Try `/study {name}` to start reviewing."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ImportCog(bot))
