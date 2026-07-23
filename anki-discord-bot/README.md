# Anki-style Discord study bot (MVP)

Import `.apkg` decks, then study them in Discord with Again/Hard/Good/Easy
buttons. Scheduling is real FSRS (via the `fsrs` PyPI package), not a
reimplementation from scratch.

## Setup

```bash
pip install -r requirements.txt
export DISCORD_BOT_TOKEN="your-bot-token-here"
python main.py
```

Your bot needs the `applications.commands` and `bot` scopes when you generate
its invite link, with at least the "Send Messages", "Embed Links", and
"Attach Files" permissions.

## Usage

```
/import file:<kaishi.apkg> deck_name:kaishi
/study deck:kaishi
/deck settings deck:kaishi new_per_day:30
/deck list
/deck export deck:kaishi
/deck delete deck:kaishi
```

Each due card is posted with a **Reveal** button. After revealing, rate it
with **Again / Hard / Good / Easy** — that updates your personal FSRS
schedule for that card and the bot moves to the next due one.

## How the daily queue actually works (important)

This is not "show me the next due card." `/study` builds a real Anki-style
queue each time it's run:

- **Reviews first**: any card you've studied before whose FSRS due date has
  passed gets shown before anything new.
- **New cards, capped per day**: cards you've never studied are capped at a
  daily limit (default 20, same as Anki's default) via `/deck settings`.
  This cap is tracked in a `daily_new_intro` table keyed by
  `(user, deck, date)` — so running `/study` five times in one day doesn't
  give you five batches of 20 new cards, and the cap resets automatically
  the next calendar date (UTC) without any code needing to know "it's a new
  day" — it's purely a date comparison, same as Anki itself.
- **No import happens in `/study`** — `/study` only ever reads what's
  already in the database. Importing is `/import`'s job alone.

One simplification worth knowing: the daily boundary is UTC, not the user's
local timezone. If your day rolls over at a different time than UTC
midnight, your new-card reset will happen a few hours off from your
personal "midnight." Fine for a single-user personal bot; if this matters,
add a per-user timezone field to `user_deck_settings` and offset the date
comparison in `build_today_queue`.

## Project structure

```
main.py                  bot entrypoint, loads cogs
commands/
    import_cmd.py         /import slash command
    study.py               /study slash command + session/button logic
anki/
    importer.py            unzips .apkg, reads collection.anki2 sqlite, extracts notes/cards/media
    scheduler.py            wraps the fsrs library (state stored as JSON per user per card)
    template.py             renders {{Field}} / conditional Anki card templates
    media.py                pulls <img>/[sound:] references out of field HTML
database/
    models.py               sqlite3 schema + all queries (decks, notes, cards, media, model_defs)
media_store/                extracted media files land here, one subfolder per deck
```

## Scheduling model: per-user, not global

Each Discord user studying a deck gets their **own** FSRS state per card
(`cards` table is keyed by `note_id, template_ord, user_id`). That means two
people can import and study the same deck independently without stepping on
each other's progress — useful if you want this in a shared server later.

## Known limitations (by design, to keep MVP scope sane)

- **`.apkg` format**: only the classic SQLite-based collection
  (`collection.anki2`/`.anki21`) is supported. Anki's newer
  zstd-compressed `collection.anki21b` format isn't handled yet — if an
  import fails with `UnsupportedApkgError`, that's why. Most publicly shared
  decks (Kaishi 1.5k included) use the classic format.
- **Templates**: `template.py` handles `{{Field}}`, `{{#Field}}...{{/Field}}`,
  `{{^Field}}...{{/Field}}`, and `{{FrontSide}}`. Cloze deletions
  (`{{cloze:Field}}`) are *not* rendered as cloze — the field is inserted as
  plain text without blanking anything out. Fine for Basic-style decks, not
  for cloze decks.
- **Export `.apkg`**: not implemented yet (next step once the MVP above is
  solid).
- **Sessions live in memory**: if the bot restarts mid-session, run `/study`
  again — nothing studied is lost since each rating is written to SQLite
  immediately, only the "what's left in today's queue" ordering resets.
- **One Discord process**: `sessions` dict is per-process; this won't work
  correctly if you ever run multiple bot instances behind a load balancer
  without moving session state into the DB/Redis.

## Next steps once this MVP is solid

- `.apkg` export
- Handle `collection.anki21b` (needs `zstandard` + Anki's schema protobufs)
- Proper cloze rendering
- `/stats`, streaks, leaderboards
