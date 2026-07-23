"""
Helpers for pulling image/audio references out of Anki field HTML and
resolving them to files on disk so they can be attached to a Discord message.

Anki field content looks like:
    <img src="apple.jpg">
    some text [sound:hello.mp3]
"""
import re
from pathlib import Path

from database import models

IMG_RE = re.compile(r'<img[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
SOUND_RE = re.compile(r'\[sound:([^\]]+)\]')
TAG_RE = re.compile(r'<[^>]+>')


def extract_media_filenames(field_html: str) -> list[str]:
    """Returns all image/audio filenames referenced in a single field's HTML."""
    return IMG_RE.findall(field_html) + SOUND_RE.findall(field_html)


def strip_media_and_tags(field_html: str) -> str:
    """Strip HTML tags and [sound:...] markers, leaving plain text for the embed."""
    text = SOUND_RE.sub("", field_html)
    text = TAG_RE.sub("", text)
    return text.strip()


def resolve_media_paths(deck_id: int, field_html: str) -> list[Path]:
    """Given a field's HTML, return actual filesystem paths for any media it references."""
    paths = []
    for filename in extract_media_filenames(field_html):
        stored_path = models.get_media_path(deck_id, filename)
        if stored_path and Path(stored_path).exists():
            paths.append(Path(stored_path))
    return paths
