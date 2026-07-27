"""
Shared autocomplete callbacks, used to replace "type the exact deck name" /
"type the exact file path" text fields with a tappable list wherever those
show up (`/study`, `/deck settings`, `/deck export`, `/deck delete`,
`/import_path`).

Discord autocomplete has hard constraints we're working within here:
  - must respond within ~3 seconds
  - max 25 choices returned
Both callbacks below are deliberately cheap (a DB query / a shallow glob) to
stay well inside that window.
"""
from pathlib import Path

import discord
from discord import app_commands

from database import models


async def deck_name_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    decks = models.list_decks()
    current_lower = current.lower()
    matches = [d["name"] for d in decks if current_lower in d["name"].lower()]
    return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


# Common places an .apkg ends up on an Android phone under Termux. Adjust/add
# to this list if your files live somewhere else -- e.g. if you haven't run
# `termux-setup-storage` yet, the /storage/emulated/0/... paths just won't
# exist and are silently skipped below.
APKG_SEARCH_ROOTS = [
    Path.home() / "storage" / "downloads",   # ~/storage/downloads, after termux-setup-storage
    Path("/storage/emulated/0/Download"),
    Path("/storage/emulated/0"),
    Path.home(),
]


async def apkg_path_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current_lower = current.lower()
    results: list[str] = []
    seen: set[str] = set()

    for root in APKG_SEARCH_ROOTS:
        if len(results) >= 25:
            break
        if not root.exists():
            continue
        try:
            # Shallow on purpose (top level + one level of subfolders) so this
            # can't turn into a slow recursive scan of all of /storage/emulated/0.
            candidates = list(root.glob("*.apkg")) + list(root.glob("*/*.apkg"))
        except (PermissionError, OSError):
            continue
        for p in candidates:
            resolved = str(p.resolve())
            if resolved in seen:
                continue
            if current_lower and current_lower not in p.name.lower():
                continue
            seen.add(resolved)
            results.append(resolved)
            if len(results) >= 25:
                break

    return [app_commands.Choice(name=Path(p).name, value=p) for p in results]
