import asyncio
import os
import sys

import discord
from discord.ext import commands

from database.models import init_db

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

INTENTS = discord.Intents.default()
INTENTS.message_content = False  # not needed; we only use slash commands + buttons

bot = commands.Bot(command_prefix="!", intents=INTENTS)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Slash command sync failed: {e}")


async def main():
    if not TOKEN:
        print("Set the DISCORD_BOT_TOKEN environment variable before running the bot.", file=sys.stderr)
        sys.exit(1)

    init_db()

    async with bot:
        await bot.load_extension("commands.import_cmd")
        await bot.load_extension("commands.study")
        await bot.load_extension("commands.deck_cmd")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
