"""
.apkg importer.

An .apkg file is a zip archive containing:
  - collection.anki2 or collection.anki21 : a SQLite database (notes, cards, decks, models)
  - media                                  : a JSON file mapping "0", "1", ... -> original filename
  - 0, 1, 2, ...                           : the actual media files, named by their media-map index

This covers the common case (legacy uncompressed collection). Some newer decks
exported by recent Anki versions use collection.anki21b with zstd + protobuf
instead of plain SQLite -- if you hit one of those, importer will raise
UnsupportedApkgError and you'll need the `zstandard` package + Anki's schema
proto definitions to extend this.
"""
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from database import models

MEDIA_STORE_ROOT = Path(__file__).parent.parent / "media_store"


class UnsupportedApkgError(Exception):
    pass


def _open_collection_db(extract_dir: Path) -> sqlite3.Connection:
    for name in ("collection.anki21", "collection.anki2"):
        candidate = extract_dir / name
        if candidate.exists():
            conn = sqlite3.connect(candidate)
            conn.row_factory = sqlite3.Row
            return conn
    if (extract_dir / "collection.anki21b").exists():
        raise UnsupportedApkgError(
            "This deck uses Anki's newer compressed collection format "
            "(collection.anki21b). This importer only supports the classic "
            "SQLite-based .apkg format for now."
        )
    raise UnsupportedApkgError("No collection.anki2/anki21 found in this .apkg")


def _extract_media_map(extract_dir: Path) -> dict[str, str]:
    media_json_path = extract_dir / "media"
    if not media_json_path.exists():
        return {}
    with open(media_json_path, "r", encoding="utf-8") as f:
        # maps index string -> original filename
        return json.load(f)


def import_apkg(filepath: str, deck_name: str) -> dict:
    """
    Imports an .apkg into our own database under `deck_name`.
    Returns a summary dict: {"notes": n, "cards": n, "media": n}
    """
    if models.get_deck_by_name(deck_name) is not None:
        raise ValueError(f"A deck named '{deck_name}' already exists. Pick another name.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(filepath, "r") as z:
            z.extractall(tmp_path)

        conn = _open_collection_db(tmp_path)
        try:
            col_row = conn.execute("SELECT models, decks FROM col LIMIT 1").fetchone()
            note_models = json.loads(col_row["models"])  # mid -> model definition

            deck_id = models.create_deck(deck_name, source_apkg=str(filepath))

            # ---- note type (model) definitions: field order + templates ----
            for mid, model in note_models.items():
                definition = {
                    "fields": [f["name"] for f in model.get("flds", [])],
                    "templates": [
                        {"name": t.get("name", ""), "qfmt": t.get("qfmt", ""), "afmt": t.get("afmt", "")}
                        for t in model.get("tmpls", [])
                    ],
                }
                models.upsert_model_def(deck_id, model["name"], json.dumps(definition))

            # ---- media ----
            media_map = _extract_media_map(tmp_path)
            deck_media_dir = MEDIA_STORE_ROOT / str(deck_id)
            deck_media_dir.mkdir(parents=True, exist_ok=True)
            media_count = 0
            for index, original_name in media_map.items():
                src = tmp_path / index
                if not src.exists():
                    continue
                dest = deck_media_dir / original_name
                shutil.copyfile(src, dest)
                models.insert_media(deck_id, original_name, str(dest))
                media_count += 1

            # ---- notes + cards ----
            note_rows = conn.execute("SELECT id, mid, flds, tags FROM notes").fetchall()
            card_rows = conn.execute("SELECT id, nid, did, ord FROM cards").fetchall()

            # group card templates by note id (a note can have multiple card templates,
            # e.g. Basic-and-reversed)
            note_id_to_card_ords: dict[int, set[int]] = {}
            for c in card_rows:
                note_id_to_card_ords.setdefault(c["nid"], set()).add(c["ord"])

            note_count = 0
            card_count = 0
            for n in note_rows:
                model = note_models.get(str(n["mid"]))
                model_name = model["name"] if model else "Unknown"
                fields = n["flds"].split("\x1f")  # Anki's field separator
                new_note_id = models.insert_note(deck_id, model_name, fields, n["tags"])
                note_count += 1

                for ord_ in note_id_to_card_ords.get(n["id"], {0}):
                    models.insert_card_template(new_note_id, deck_id, ord_)
                    card_count += 1

            return {"notes": note_count, "cards": card_count, "media": media_count, "deck_id": deck_id}
        finally:
            conn.close()
