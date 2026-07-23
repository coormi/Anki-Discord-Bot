"""
Thin wrapper around the `fsrs` library (open-spaced-repetition/py-fsrs).

We don't hand-roll SM-2/FSRS math ourselves -- we use the published, tested
library and just handle (de)serialization so card state can live in SQLite
as a JSON blob.
"""
import json
from datetime import datetime, timezone

from fsrs import Scheduler, Card, Rating

_scheduler = Scheduler()

RATING_MAP = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}


def new_card_state_json() -> str:
    """State for a card nobody has studied yet."""
    return json.dumps(Card().to_dict())


def review(state_json: str, rating_str: str) -> dict:
    """
    Applies a review rating to a card's stored FSRS state.

    Returns:
        {
          "state_json": <new state to persist>,
          "due": <datetime the card is next due>,
          "interval_days": <float, roughly how far out this was scheduled>,
        }
    """
    rating = RATING_MAP[rating_str.lower()]
    card = Card.from_dict(json.loads(state_json))
    new_card, log = _scheduler.review_card(card, rating)

    new_state = new_card.to_dict()
    due_dt = datetime.fromisoformat(new_state["due"])
    now = datetime.now(timezone.utc)
    interval_days = max((due_dt - now).total_seconds() / 86400, 0)

    return {
        "state_json": json.dumps(new_state),
        "due": due_dt,
        "interval_days": interval_days,
    }


def is_due(state_json: str) -> bool:
    state = json.loads(state_json)
    due_str = state.get("due")
    if due_str is None:
        return True
    due_dt = datetime.fromisoformat(due_str)
    return due_dt <= datetime.now(timezone.utc)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
