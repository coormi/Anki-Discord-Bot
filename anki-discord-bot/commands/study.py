"""
/study <deck> -- runs a review session in the channel it's invoked from.

Flow per card:
  1. Send front (question) as an embed + attached media, with a "Reveal" button.
  2. On Reveal: send back (answer) as an embed + attached media, with
     Again/Hard/Good/Easy buttons.
  3. On a rating press: update this user's FSRS state for that card, then
     move on to the next due card, or announce the session is complete.

Session state is kept in memory per (guild/dm channel doesn't matter, we key
by Discord user id) -- if the bot restarts mid-session the user just runs
/study again to get a fresh queue. Review progress itself is persisted in
SQLite as soon as each rating is submitted, so nothing studied is ever lost.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database import models
from anki import scheduler, template, media as media_utils
from anki.backup import save_local_backup
from commands.autocomplete import deck_name_autocomplete


@dataclass
class StudySession:
    deck_id: int
    deck_name: str
    queue: list[tuple[int, int, bool]]  # (note_id, template_ord, is_new)
    reviewed_count: int = 0


class RevealView(discord.ui.View):
    def __init__(self, cog: "StudyCog", user_id: int, note_id: int, template_ord: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.note_id = note_id
        self.template_ord = template_ord

    @discord.ui.button(label="Reveal", style=discord.ButtonStyle.primary, emoji="👁️")
    async def reveal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your card to reveal.", ephemeral=True)
            return
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await self.cog.show_answer(interaction, self.user_id, self.note_id, self.template_ord)


class RatingView(discord.ui.View):
    def __init__(self, cog: "StudyCog", user_id: int, note_id: int, template_ord: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.note_id = note_id
        self.template_ord = template_ord

    async def _rate(self, interaction: discord.Interaction, rating: str):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your card to rate.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await self.cog.submit_rating(interaction, self.user_id, self.note_id, self.template_ord, rating)

    @discord.ui.button(label="Again", style=discord.ButtonStyle.danger)
    async def again(self, interaction, button):
        await self._rate(interaction, "again")

    @discord.ui.button(label="Hard", style=discord.ButtonStyle.secondary)
    async def hard(self, interaction, button):
        await self._rate(interaction, "hard")

    @discord.ui.button(label="Good", style=discord.ButtonStyle.success)
    async def good(self, interaction, button):
        await self._rate(interaction, "good")

    @discord.ui.button(label="Easy", style=discord.ButtonStyle.success)
    async def easy(self, interaction, button):
        await self._rate(interaction, "easy")


class StudyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, StudySession] = {}

    # ---------- helpers ----------

    def _load_render_data(self, deck_id: int, note_id: int, template_ord: int):
        note_row = models.get_note(note_id)
        fields_list = json.loads(note_row["fields"])
        model_def_row = models.get_model_def(deck_id, note_row["model_name"])

        if model_def_row is None:
            # Fallback: no template info, just show raw fields.
            front = fields_list[0] if fields_list else ""
            back = "\n".join(fields_list[1:]) if len(fields_list) > 1 else ""
        else:
            definition = json.loads(model_def_row["definition"])
            field_names = definition["fields"]
            templates = definition["templates"]
            tmpl = templates[template_ord] if template_ord < len(templates) else templates[0]
            front, back = template.render_card(fields_list, field_names, tmpl["qfmt"], tmpl["afmt"])

        front_text = media_utils.strip_media_and_tags(front)
        back_text = media_utils.strip_media_and_tags(back)
        front_media = media_utils.resolve_media_paths(deck_id, front)
        back_media = media_utils.resolve_media_paths(deck_id, back)
        return front_text, back_text, front_media, back_media

    async def _send_card(self, channel, user: discord.User, deck_name: str, note_id: int,
                         template_ord: int, is_new: bool):
        front_text, _, front_media, _ = self._load_render_data(
            self.sessions[user.id].deck_id, note_id, template_ord
        )
        label = "🆕 New" if is_new else "🔁 Review"
        embed = discord.Embed(title=f"📚 {deck_name} · {label}", description=front_text or "(no text)")
        files = [discord.File(p) for p in front_media[:4]]  # Discord caps attachments; keep it sane
        if files:
            embed.set_image(url=f"attachment://{files[0].filename}")
        view = RevealView(self, user.id, note_id, template_ord)
        await channel.send(content=user.mention, embed=embed, files=files, view=view)

    async def _advance(self, channel, user: discord.User):
        session = self.sessions.get(user.id)
        if session is None:
            return
        if not session.queue:
            save_local_backup(session.deck_id, session.deck_name, str(user.id))
            await channel.send(
                f"🎉 {user.mention} That's today's queue for **{session.deck_name}** done "
                f"(reviews due + today's new-card allotment). Reviewed {session.reviewed_count} card(s). "
                f"Your progress was also auto-saved locally on the bot's device. "
                f"Run `/study {session.deck_name}` again later today if more cards become due, "
                f"or tomorrow for your next batch of new cards."
            )
            del self.sessions[user.id]
            return
        note_id, template_ord, is_new = session.queue.pop(0)
        await self._send_card(channel, user, session.deck_name, note_id, template_ord, is_new)

    # ---------- view callbacks ----------

    async def show_answer(self, interaction: discord.Interaction, user_id: int, note_id: int, template_ord: int):
        session = self.sessions.get(user_id)
        if session is None:
            return
        _, back_text, _, back_media = self._load_render_data(session.deck_id, note_id, template_ord)
        embed = discord.Embed(title="Answer", description=back_text or "(no text)")
        files = [discord.File(p) for p in back_media[:4]]
        if files:
            embed.set_image(url=f"attachment://{files[0].filename}")
        view = RatingView(self, user_id, note_id, template_ord)
        await interaction.followup.send(
            content=interaction.user.mention, embed=embed, files=files, view=view
        )

    async def submit_rating(self, interaction: discord.Interaction, user_id: int, note_id: int,
                             template_ord: int, rating: str):
        session = self.sessions.get(user_id)
        if session is None:
            return
        deck_id = session.deck_id

        user_card, was_new = models.get_or_create_user_card(
            note_id, deck_id, template_ord, str(user_id), scheduler.new_card_state_json()
        )
        if was_new:
            today_str = datetime.now(timezone.utc).date().isoformat()
            models.increment_new_intro_count(str(user_id), deck_id, today_str)

        result = scheduler.review(user_card["fsrs_state"], rating)
        reps = user_card["reps"] + 1
        lapses = user_card["lapses"] + (1 if rating == "again" else 0)
        models.update_user_card(note_id, template_ord, str(user_id), result["state_json"], reps, lapses,
                                 reviewed_at=result["due"].timestamp())
        models.log_review(user_card["id"], str(user_id), rating)

        session.reviewed_count += 1
        await self._advance(interaction.channel, interaction.user)

    # ---------- slash command ----------

    @app_commands.command(name="study", description="Start a study session for a deck")
    @app_commands.describe(deck="The deck name you imported, e.g. 'kaishi'")
    @app_commands.autocomplete(deck=deck_name_autocomplete)
    async def study(self, interaction: discord.Interaction, deck: str):
        deck_row = models.get_deck_by_name(deck)
        if deck_row is None:
            await interaction.response.send_message(
                f"No deck named **{deck}** found. Import one first with `/import`.", ephemeral=True
            )
            return

        now = datetime.now(timezone.utc)
        queue = models.build_today_queue(deck_row["id"], str(interaction.user.id), now)
        if not queue:
            await interaction.response.send_message(
                f"No cards due in **{deck}** right now -- either you're all caught up, or you've "
                f"already hit today's new-card limit. Check back later or tomorrow.", ephemeral=True
            )
            return

        review_count = sum(1 for _, _, is_new in queue if not is_new)
        new_count = sum(1 for _, _, is_new in queue if is_new)

        self.sessions[interaction.user.id] = StudySession(
            deck_id=deck_row["id"], deck_name=deck_row["name"], queue=list(queue)
        )
        await interaction.response.send_message(
            f"Starting session in **{deck}** -- {review_count} review(s), {new_count} new card(s)."
        )
        await self._advance(interaction.channel, interaction.user)


async def setup(bot: commands.Bot):
    await bot.add_cog(StudyCog(bot))
