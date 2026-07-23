"""
SQLite storage layer.

Deliberately raw sqlite3 (no ORM) to keep the MVP simple, per the agreed scope.
All FSRS card state is stored as a JSON blob (card.to_dict()) so we don't need
to hand-maintain individual stability/difficulty columns.
"""
import sqlite3
import json
import time
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    source_apkg TEXT,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    model_name TEXT,
    fields TEXT NOT NULL,      -- JSON list of field strings, in model field order
    tags TEXT
);

-- One row per (user, card). The same imported card can be studied independently
-- by multiple Discord users, each with their own FSRS state.
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    template_ord INTEGER NOT NULL,   -- which card template on the note (0 = front/back 1, etc)
    user_id TEXT NOT NULL,           -- Discord user id, stored as string
    fsrs_state TEXT NOT NULL,        -- JSON: Card().to_dict()
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at REAL,
    UNIQUE(note_id, template_ord, user_id)
);

-- Anki note type definitions (field order + card templates), one per
-- (deck, model_name), so we know how to render a note's front/back.
CREATE TABLE IF NOT EXISTS model_defs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    definition TEXT NOT NULL,  -- JSON: {"fields": [...names...], "templates": [{"name","qfmt","afmt"}, ...]}
    UNIQUE(deck_id, model_name)
);

CREATE TABLE IF NOT EXISTS user_deck_settings (
    user_id TEXT NOT NULL,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    daily_new_limit INTEGER NOT NULL DEFAULT 20,
    daily_review_limit INTEGER NOT NULL DEFAULT 200,
    PRIMARY KEY (user_id, deck_id)
);

-- How many *brand new* cards this user has already been introduced to today,
-- per deck. Incremented exactly once per card, the first time it's ever rated.
-- This is what makes the daily new-card cap survive across multiple /study
-- calls on the same day, and reset itself on the next.
CREATE TABLE IF NOT EXISTS daily_new_intro (
    user_id TEXT NOT NULL,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    date TEXT NOT NULL,   -- ISO date, e.g. "2026-07-22" (UTC)
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, deck_id, date)
);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,   -- original filename as referenced in fields, e.g. "apple.jpg"
    filepath TEXT NOT NULL,   -- absolute path on disk where we stored it
    UNIQUE(deck_id, filename)
);

CREATE TABLE IF NOT EXISTS review_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    rating TEXT NOT NULL,
    reviewed_at REAL NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------- Decks ----------

def create_deck(name: str, source_apkg: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO decks (name, source_apkg, added_at) VALUES (?, ?, ?)",
            (name, source_apkg, time.time()),
        )
        return cur.lastrowid


def get_deck_by_name(name: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM decks WHERE name = ?", (name,)).fetchone()


def list_decks():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM decks ORDER BY name").fetchall()


# ---------- Model defs (note type: field order + templates) ----------

def upsert_model_def(deck_id: int, model_name: str, definition_json: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO model_defs (deck_id, model_name, definition) VALUES (?, ?, ?)
               ON CONFLICT(deck_id, model_name) DO UPDATE SET definition=excluded.definition""",
            (deck_id, model_name, definition_json),
        )


def get_model_def(deck_id: int, model_name: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM model_defs WHERE deck_id=? AND model_name=?",
            (deck_id, model_name),
        ).fetchone()


def delete_deck(deck_id: int):
    """Cascade-deletes the deck and everything under it (notes, cards, media, model_defs, review_log)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM decks WHERE id=?", (deck_id,))


def export_user_progress(deck_id: int, user_id: str) -> dict:
    """
    Dumps everything needed to fully restore this user's study progress on this
    deck: per-card FSRS state plus the review history. Note content itself
    (fields/media) isn't included -- that comes back from re-importing the
    original .apkg, this backup is just the *progress*.
    """
    with get_conn() as conn:
        deck = conn.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
        cards = conn.execute(
            """SELECT c.note_id, c.template_ord, c.fsrs_state, c.reps, c.lapses, c.last_reviewed_at
               FROM cards c WHERE c.deck_id=? AND c.user_id=?""",
            (deck_id, user_id),
        ).fetchall()
        logs = conn.execute(
            """SELECT rl.card_id, rl.rating, rl.reviewed_at FROM review_log rl
               JOIN cards c ON c.id = rl.card_id
               WHERE c.deck_id=? AND rl.user_id=?""",
            (deck_id, user_id),
        ).fetchall()

        return {
            "deck_name": deck["name"] if deck else None,
            "user_id": user_id,
            "cards": [dict(c) for c in cards],
            "review_log": [dict(l) for l in logs],
        }


# ---------- Notes ----------

def insert_note(deck_id: int, model_name: str, fields: list[str], tags: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (deck_id, model_name, fields, tags) VALUES (?, ?, ?, ?)",
            (deck_id, model_name, json.dumps(fields), tags),
        )
        return cur.lastrowid


# ---------- Cards ----------

def insert_card_template(note_id: int, deck_id: int, template_ord: int):
    """
    Registers that a (note, template) pair exists. Per-user FSRS state is
    created lazily the first time a user studies it (see get_or_create_user_card).
    We store this as a placeholder row with user_id = '' so /study can enumerate
    "all card templates in a deck" without needing a separate table.
    """
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO cards
               (note_id, deck_id, template_ord, user_id, fsrs_state, reps, lapses)
               VALUES (?, ?, ?, '', '{}', 0, 0)""",
            (note_id, deck_id, template_ord),
        )


def get_or_create_user_card(note_id: int, deck_id: int, template_ord: int, user_id: str, default_state_json: str):
    """Returns (row, was_newly_created). `was_newly_created` is True only the
    very first time this (note, template, user) triple is studied -- used to
    know whether to count it against today's new-card quota."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM cards WHERE note_id=? AND template_ord=? AND user_id=?""",
            (note_id, template_ord, user_id),
        ).fetchone()
        if row:
            return row, False
        conn.execute(
            """INSERT INTO cards (note_id, deck_id, template_ord, user_id, fsrs_state, reps, lapses)
               VALUES (?, ?, ?, ?, ?, 0, 0)""",
            (note_id, deck_id, template_ord, user_id, default_state_json),
        )
        new_row = conn.execute(
            """SELECT * FROM cards WHERE note_id=? AND template_ord=? AND user_id=?""",
            (note_id, template_ord, user_id),
        ).fetchone()
        return new_row, True


# ---------- Per-user deck settings & daily new-card quota ----------

def get_deck_settings(user_id: str, deck_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_deck_settings WHERE user_id=? AND deck_id=?",
            (user_id, deck_id),
        ).fetchone()
        if row:
            return row
        conn.execute(
            "INSERT INTO user_deck_settings (user_id, deck_id) VALUES (?, ?)",
            (user_id, deck_id),
        )
        return conn.execute(
            "SELECT * FROM user_deck_settings WHERE user_id=? AND deck_id=?",
            (user_id, deck_id),
        ).fetchone()


def set_daily_new_limit(user_id: str, deck_id: int, limit: int):
    get_deck_settings(user_id, deck_id)  # ensure row exists
    with get_conn() as conn:
        conn.execute(
            "UPDATE user_deck_settings SET daily_new_limit=? WHERE user_id=? AND deck_id=?",
            (limit, user_id, deck_id),
        )


def get_new_intro_count_today(user_id: str, deck_id: int, date_str: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM daily_new_intro WHERE user_id=? AND deck_id=? AND date=?",
            (user_id, deck_id, date_str),
        ).fetchone()
        return row["count"] if row else 0


def increment_new_intro_count(user_id: str, deck_id: int, date_str: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO daily_new_intro (user_id, deck_id, date, count) VALUES (?, ?, ?, 1)
               ON CONFLICT(user_id, deck_id, date) DO UPDATE SET count = count + 1""",
            (user_id, deck_id, date_str),
        )


def build_today_queue(deck_id: int, user_id: str, now_dt) -> list[tuple[int, int, bool]]:
    """
    Builds today's study queue the way Anki does:
      - "review" cards: already studied before, and due <= now -- always included
        (up to daily_review_limit), and always shown before new cards.
      - "new" cards: never studied by this user -- capped by daily_new_limit,
        accounting for any new cards already introduced earlier *today*.

    Returns a list of (note_id, template_ord, is_new) tuples, review cards first.
    `now_dt` must be a timezone-aware datetime (UTC) -- pass it in explicitly
    rather than reading the clock here so this function stays easy to test.
    """
    now_iso = now_dt.isoformat()
    today_str = now_dt.date().isoformat()
    settings = get_deck_settings(user_id, deck_id)

    with get_conn() as conn:
        templates = conn.execute(
            "SELECT DISTINCT note_id, template_ord FROM notes n "
            "JOIN cards c ON c.note_id = n.id "
            "WHERE n.deck_id = ?",
            (deck_id,),
        ).fetchall()

        review_candidates = []
        new_candidates = []
        for t in templates:
            user_row = conn.execute(
                "SELECT * FROM cards WHERE note_id=? AND template_ord=? AND user_id=?",
                (t["note_id"], t["template_ord"], user_id),
            ).fetchone()
            if user_row is None:
                new_candidates.append((t["note_id"], t["template_ord"]))
            else:
                state = json.loads(user_row["fsrs_state"])
                due_str = state.get("due")
                if due_str is not None and due_str <= now_iso:
                    review_candidates.append((t["note_id"], t["template_ord"]))

    already_new_today = get_new_intro_count_today(user_id, deck_id, today_str)
    remaining_new_quota = max(settings["daily_new_limit"] - already_new_today, 0)

    review_queue = [(nid, ord_, False) for nid, ord_ in review_candidates[: settings["daily_review_limit"]]]
    new_queue = [(nid, ord_, True) for nid, ord_ in new_candidates[:remaining_new_quota]]

    return review_queue + new_queue


def update_user_card(note_id: int, template_ord: int, user_id: str, fsrs_state_json: str,
                      reps: int, lapses: int, reviewed_at: float):
    with get_conn() as conn:
        conn.execute(
            """UPDATE cards SET fsrs_state=?, reps=?, lapses=?, last_reviewed_at=?
               WHERE note_id=? AND template_ord=? AND user_id=?""",
            (fsrs_state_json, reps, lapses, reviewed_at, note_id, template_ord, user_id),
        )


def log_review(card_row_id: int, user_id: str, rating: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO review_log (card_id, user_id, rating, reviewed_at) VALUES (?, ?, ?, ?)",
            (card_row_id, user_id, rating, time.time()),
        )


def get_note(note_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()


# ---------- Media ----------

def insert_media(deck_id: int, filename: str, filepath: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO media (deck_id, filename, filepath) VALUES (?, ?, ?)",
            (deck_id, filename, filepath),
        )


def get_media_path(deck_id: int, filename: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT filepath FROM media WHERE deck_id=? AND filename=?",
            (deck_id, filename),
        ).fetchone()
        return row["filepath"] if row else None
