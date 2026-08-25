"""Pre-generated text for the weekend rituals, so they never need Claude on the day.

WHY THIS EXISTS
---------------
The three weekly rituals all fire on the weekend:

    Pancake Saturday sequence   Sat  7am / 1pm / 7pm
    Mr. Quiet's slip            Sun  9:06am
    Doris's muffin column       Sun  3:06pm

The Claude weekly usage window resets **Monday 12am PT**. Four gens a day, each
rolling multiple candidates, routinely exhaust that window before the weekend —
so the rituals were scheduled at precisely the moment there was never any
capacity left. They generated at post time, so a spent window meant no post at
all, and every one of them just logged `failed to generate — abort` and exited.

That is not a hypothetical. Pancake, the slip, and the column each died five
consecutive weekends (2026-07-25 through 2026-08-23) with the same cause on
every line: ``You've hit your weekly limit``. Mr. Quiet and Doris are the two
best-performing voices on the account (1.269x and 1.188x in the nightly
reflection), and both were silent for over a month while the daily drops kept
landing perfectly — cap exhaustion is a *tail-of-week* problem, and only the
rituals live in the tail.

THE FIX
-------
Separate *when the words are written* from *when they are posted*. A weekday
prep run (``scripts/ritual_prep.py``, cron Mon-Fri early morning) generates the
week's ritual text while the window is fresh and parks it here. On the weekend
each ritual takes its line out of the drawer and posts it — zero Claude calls on
the day, so a spent window cannot silence the ritual.

Nothing about the rituals' identity moves: the slip still appears Sunday
morning, Pancake still walks the keyboard Saturday at 7. Only the token spend
moves to Monday. The content is week-agnostic by construction — every ritual
prompt already forbids current events — so a line written Monday reads exactly
the same on Sunday.

Live generation is still the fallback. If the drawer is empty (prep failed all
week, or someone runs a ritual with --force), each script generates on the spot
exactly as it used to. This is a safety net over the old path, not a
replacement for it.

SHAPE
-----
``data/ritual_cache.json``, keyed by ISO week::

    {"week": "2026-W35",
     "items": {"slip": {"text": "...", "generated_at": "...", "consumed_at": null}}}

ISO weeks run Monday->Sunday, so a Monday prep run and the Saturday/Sunday
rituals that consume it always share one key — the drawer is filled and emptied
inside a single week, and a new week starts empty on its own.

Per-Mini runtime state, gitignored like every other state file here. Tracking it
would dirty the tree the gen runner rebases onto — see the loop-state-file
lesson in CLAUDE.md.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .io import atomic_write_json

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = ROOT / "data" / "ritual_cache.json"

# Every item the prep run fills, in generation order.
ITEMS = ("slip", "doris", "pancake_1", "pancake_2", "pancake_3")


def week_key(when: datetime | None = None) -> str:
    """ISO-week key, e.g. "2026-W35". Local time — the rituals are PT-scheduled."""
    when = when or datetime.now().astimezone()
    year, week, _ = when.date().isocalendar()
    return f"{year}-W{week:02d}"


def load() -> dict:
    """Whole cache, or an empty shell. Never raises on a corrupt/absent file."""
    try:
        data = json.loads(CACHE_PATH.read_text())
        if isinstance(data, dict) and isinstance(data.get("items"), dict):
            return data
    except Exception:
        pass
    return {"week": None, "items": {}}


def _save(data: dict) -> None:
    atomic_write_json(CACHE_PATH, data)


def _fresh_entry(data: dict, item: str) -> dict | None:
    """The entry for `item` if it belongs to the current week and is unconsumed."""
    if data.get("week") != week_key():
        return None
    entry = data.get("items", {}).get(item)
    if not isinstance(entry, dict):
        return None
    if entry.get("consumed_at"):
        return None
    text = entry.get("text")
    return entry if isinstance(text, str) and text.strip() else None


def peek(item: str) -> str | None:
    """Text waiting for `item` this week, without consuming it. Else None."""
    entry = _fresh_entry(load(), item)
    return entry["text"] if entry else None


def take(item: str) -> str | None:
    """Consume and return this week's text for `item`, or None if none is waiting.

    Marks the entry consumed rather than deleting it, so a rerun regenerates
    live instead of reposting identical text, and the drawer stays readable when
    working out what a given weekend actually posted.
    """
    data = load()
    entry = _fresh_entry(data, item)
    if not entry:
        return None
    entry["consumed_at"] = datetime.now().astimezone().isoformat()
    _save(data)
    return entry["text"]


def store(item: str, text: str) -> None:
    """Park `text` for `item` for the current week. Starts a new week clean."""
    data = load()
    key = week_key()
    if data.get("week") != key:
        data = {"week": key, "items": {}}
    data["items"][item] = {
        "text": text,
        "generated_at": datetime.now().astimezone().isoformat(),
        "consumed_at": None,
    }
    _save(data)


def missing(items: "tuple[str, ...]" = ITEMS) -> list[str]:
    """Items with nothing waiting this week — what a prep run still has to make."""
    data = load()
    return [item for item in items if _fresh_entry(data, item) is None]
