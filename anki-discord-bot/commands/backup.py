"""
Local, on-device progress backups.

This writes the exact same JSON that /deck export sends to Discord, but to a
file on the bot's own storage -- so a user's study progress is also backed up
even if they never remember to run /deck export, and even for decks too large
to attach to a Discord message.

Sizing: each call OVERWRITES that (deck, user)'s single backup file rather
than appending to a growing log. So the file size is bounded by "however many
cards this user has ever studied in this deck", not by "how many times this
ran". A card row in the JSON is roughly ~150-250 bytes, so even a very active
user on a 15,000-card deck tops out around 2-4 MB for that one file -- trivial
for phone storage. Total disk use across all decks/users is (number of
deck+user pairs) x (a few MB, worst case), which is easy to bound further by
periodically pruning backups/ for decks that no longer exist, if it ever
matters.
"""
import json
import re
from pathlib import Path

from database import models

BACKUP_DIR = Path(__file__).parent.parent / "backups"


def _safe_filename_part(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def save_local_backup(deck_id: int, deck_name: str, user_id: str) -> Path:
    """
    Exports this user's progress on this deck and overwrites their backup file
    on disk. Returns the path written to. Safe to call often (e.g. at the end
    of every study session, and again whenever /deck export is run) -- it's
    just a full overwrite of one small file, not an append.
    """
    data = models.export_user_progress(deck_id, user_id)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename_part(deck_name)}_{_safe_filename_part(user_id)}.json"
    path = BACKUP_DIR / filename
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
