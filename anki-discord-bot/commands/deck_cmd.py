"""
/deck list    -- show all imported decks
/deck export  -- download a JSON backup of your progress on a deck
/deck delete  -- permanently delete a deck (after a confirmation prompt)

There is deliberately no automatic backup-before-delete. The delete command
strongly recommends running /deck export first, but if a user confirms
deletion anyway, their progress is gone. That's an intentional tradeoff to
keep this simple -- the warning is loud, but it's still on the user.
"""
import io
import json

import discord
from discord import app_commands
from discord.ext import commands

from database import models
from anki.backup import save_local_backup
from commands.autocomplete import deck_name_autocomplete


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, user_id: int, deck_id: int, deck_name: str):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.deck_id = deck_id
        self.deck_name = deck_name
        self.confirmed = None

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This confirmation isn't yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, permanently delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        models.delete_deck(self.deck_id)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"🗑️ Deck **{self.deck_name}** and all progress on it have been deleted.", view=self
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled -- nothing was deleted.", view=self)
        self.stop()


deck_group = app_commands.Group(name="deck", description="Manage imported decks")


@deck_group.command(name="settings", description="Set your daily new-card limit for a deck")
@app_commands.describe(deck="The deck name", new_per_day="How many new cards to introduce per day (Anki default: 20)")
@app_commands.autocomplete(deck=deck_name_autocomplete)
async def deck_settings(interaction: discord.Interaction, deck: str, new_per_day: int):
    deck_row = models.get_deck_by_name(deck)
    if deck_row is None:
        await interaction.response.send_message(f"No deck named **{deck}** found.", ephemeral=True)
        return
    if new_per_day < 0:
        await interaction.response.send_message("new_per_day can't be negative.", ephemeral=True)
        return

    models.set_daily_new_limit(str(interaction.user.id), deck_row["id"], new_per_day)
    await interaction.response.send_message(
        f"Set your daily new-card limit for **{deck}** to **{new_per_day}**. "
        f"This only affects you -- other users studying this deck keep their own setting.",
        ephemeral=True,
    )


@deck_group.command(name="list", description="List all imported decks")
async def deck_list(interaction: discord.Interaction):
    decks = models.list_decks()
    if not decks:
        await interaction.response.send_message("No decks imported yet. Use `/import` to add one.", ephemeral=True)
        return
    lines = [f"• **{d['name']}**" for d in decks]
    await interaction.response.send_message("Imported decks:\n" + "\n".join(lines), ephemeral=True)


@deck_group.command(name="export", description="Download a backup of your progress on a deck")
@app_commands.describe(deck="The deck name to back up your progress for")
@app_commands.autocomplete(deck=deck_name_autocomplete)
async def deck_export(interaction: discord.Interaction, deck: str):
    deck_row = models.get_deck_by_name(deck)
    if deck_row is None:
        await interaction.response.send_message(f"No deck named **{deck}** found.", ephemeral=True)
        return

    data = models.export_user_progress(deck_row["id"], str(interaction.user.id))
    save_local_backup(deck_row["id"], deck, str(interaction.user.id))
    buf = io.BytesIO(json.dumps(data, indent=2).encode("utf-8"))
    file = discord.File(buf, filename=f"{deck}_progress_backup.json")
    await interaction.response.send_message(
        content=(
            f"Here's your progress backup for **{deck}** ({len(data['cards'])} card(s) studied). "
            f"A copy was also saved locally on the bot's device.\n"
            f"Keep this file somewhere safe -- if you delete and re-import this deck later, "
            f"there's currently no automated way to restore it from this file, so treat it as "
            f"a personal record rather than a one-click restore."
        ),
        file=file,
        ephemeral=True,
    )


@deck_group.command(name="delete", description="Permanently delete a deck and all progress on it")
@app_commands.describe(deck="The deck name to delete")
@app_commands.autocomplete(deck=deck_name_autocomplete)
async def deck_delete(interaction: discord.Interaction, deck: str):
    deck_row = models.get_deck_by_name(deck)
    if deck_row is None:
        await interaction.response.send_message(f"No deck named **{deck}** found.", ephemeral=True)
        return

    view = ConfirmDeleteView(interaction.user.id, deck_row["id"], deck)
    await interaction.response.send_message(
        content=(
            f"⚠️ **This will permanently delete `{deck}` and everyone's review progress on it.** "
            f"This cannot be undone.\n\n"
            f"**Strongly recommended:** run `/deck export deck:{deck}` first to save a backup "
            f"of your own progress before confirming.\n\n"
            f"Are you sure you want to delete **{deck}**?"
        ),
        view=view,
        ephemeral=True,
    )


async def setup(bot: commands.Bot):
    bot.tree.add_command(deck_group)
