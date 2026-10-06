"""Week-to-week memory for the two Sunday voices, so they stop repeating themselves.

WHY THIS EXISTS
---------------
Doris's column and Mr. Quiet's slip were written from fixed prompts with no
knowledge of the calendar or of anything either cat had said before. Both fell
into attractors:

- 7 of Doris's first 13 columns reviewed **Plum-Cardamom** (the prompt listed it
  as an example pick), 5 were subtitled "a Word About Restraint", and Roy's
  hobby was jam in most of them.
- The 2026-10-04 column opened "It is late September" — the model was guessing
  the season with no date at all.
- Mr. Quiet wrote "slow mornings are not lost time; they are ..." on two
  consecutive Sundays, after "the quiet you keep is also keeping you" and
  "the small things you keep are keeping you, too".

Per the house rule (fix convergence with code, not prompt AVOID lists), the
choices are made here, in code: a month-keyed seasonal muffin pool minus
anything reviewed recently, a Roy hobby not used in the last few columns, a
concrete counter object for the slip, the real Sunday date, and a validator
that rejects a slip too close to one already posted.

Everything here reads Mini-local history (``archive/columns/*.txt`` and
``data/post_log.jsonl``) and degrades to "no history" if either is missing, so
a fresh checkout still produces a valid prompt.
"""
from __future__ import annotations

import json
import random
import re
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
COLUMNS_DIR = ROOT / "archive" / "columns"
POST_LOG = ROOT / "data" / "post_log.jsonl"

# How many past Sundays each voice remembers.
COLUMN_MEMORY = 6
SLIP_MEMORY = 12

# Muffins a small cafe would plausibly bake, by month. Each flavor's first word
# is its "key" — that's what gets matched against recent column titles, so
# "Plum-Cardamom" and "Plum-Ginger" both count as a plum week.
MUFFINS_BY_MONTH: dict[int, tuple[str, ...]] = {
    1: ("Blood Orange-Poppyseed", "Bran-Date", "Grapefruit-Honey", "Cranberry-Orange", "Oatmeal-Raisin", "Savory Cheddar-Chive"),
    2: ("Meyer Lemon", "Chocolate-Cherry", "Bran-Date", "Kumquat-Almond", "Sweet Potato", "Savory Gruyère-Thyme"),
    3: ("Lemon-Poppyseed", "Carrot-Ginger", "Morning-Glory", "Marmalade", "Coconut-Lime", "Savory Spinach-Feta"),
    4: ("Rhubarb-Ginger", "Lemon-Blueberry", "Carrot-Walnut", "Strawberry-Oat", "Honey-Lavender", "Savory Asparagus-Parmesan"),
    5: ("Strawberry-Rhubarb", "Lemon-Lavender", "Apricot-Almond", "Cherry-Vanilla", "Coconut-Lime", "Savory Pea-Mint"),
    6: ("Blueberry", "Cherry-Almond", "Apricot-Ginger", "Raspberry-Lemon", "Zucchini-Walnut", "Savory Corn-Jalapeño"),
    7: ("Peach-Cornmeal", "Blackberry-Lime", "Blueberry-Lemon", "Nectarine-Ginger", "Raspberry-White Chocolate", "Savory Tomato-Basil"),
    8: ("Peach-Brown Butter", "Blackberry-Lemon", "Zucchini-Chocolate", "Fig-Honey", "Plum-Ginger", "Savory Corn-Cheddar"),
    9: ("Apple-Cinnamon", "Fig-Walnut", "Plum-Cardamom", "Pear-Ginger", "Concord Grape", "Savory Zucchini-Parmesan"),
    10: ("Pumpkin-Spice", "Apple-Cider", "Pear-Cardamom", "Cranberry-Pecan", "Maple-Oat", "Savory Sweet Potato-Sage"),
    11: ("Pumpkin-Pecan", "Cranberry-Orange", "Sweet Potato-Maple", "Apple-Walnut", "Persimmon-Spice", "Savory Cheddar-Sage"),
    12: ("Gingerbread", "Cranberry-White Chocolate", "Eggnog-Nutmeg", "Clementine-Almond", "Chocolate-Peppermint", "Savory Rosemary-Parmesan"),
}

# Roy's hobbies. The column prompt used to say "pick something, don't repeat",
# and the model picked jam nearly every week.
ROY_HOBBIES = (
    "woodworking (he built the bay-window bench)", "model trains", "birdwatching from the porch",
    "fixing old radios", "crossword puzzles in pen", "growing tomatoes he never let ripen fully",
    "whittling small spoons", "fishing, badly and happily", "collecting postcards of lighthouses",
    "repairing clocks", "learning the harmonica, slowly", "keeping bees for one summer",
    "building kites", "putting up jam", "playing cribbage", "photographing weather",
)
# Words that reveal which hobby a past column used.
_HOBBY_KEYS = {
    "woodworking": ("woodwork", "bench", "lathe", "sawdust"), "model trains": ("train",),
    "birdwatching": ("bird",), "radios": ("radio",), "crossword": ("crossword",),
    "tomatoes": ("tomato",), "whittling": ("whittl",), "fishing": ("fishing", "fished"),
    "postcards": ("postcard",), "clocks": ("clock",), "harmonica": ("harmonica",),
    "bees": ("bees", "beekeep", "hive"), "kites": ("kite",), "jam": ("jam", "preserve"),
    "cribbage": ("cribbage",), "weather": ("weather",),
}

# Small concrete things on or near the counter. The slip is built around one,
# which is what made "the spoon waits patiently for the cup to be ready" land.
SLIP_OBJECTS = (
    "spoon", "saucer", "sugar bowl", "kettle", "doorbell", "coat hook", "umbrella stand",
    "napkin", "window latch", "chalkboard", "radiator", "teapot lid", "stool", "doormat",
    "lamp", "apron", "tip jar", "creamer", "string of the blinds", "pastry case",
    "light switch", "menu card", "corkboard pin", "mug handle", "tea towel", "broom",
)

# Words too common in these voices to count as overlap between two slips.
_STOP = set("""a an and are as at be but by for from has have i in is it its it's of on or
so that the their they this to too was what when which who will with you your yours
not no just also still own one""".split())


def ritual_sunday(now: datetime | None = None) -> date:
    """The Sunday this ISO week's rituals post on.

    Prep runs Monday-ish and the rituals drain the drawer the following
    weekend, all inside one ISO week (Mon→Sun) — see lib/ritual_cache.
    """
    now = now or datetime.now().astimezone()
    y, w, _ = now.date().isocalendar()
    return date.fromisocalendar(y, w, 7)


def season_phrase(d: date) -> str:
    """"early October", "mid-October", "late October"."""
    part = "early" if d.day <= 10 else ("mid-" if d.day <= 20 else "late")
    month = d.strftime("%B")
    return f"mid-{month}" if part == "mid-" else f"{part} {month}"


# ---------- Doris ----------

def recent_columns(n: int = COLUMN_MEMORY, before: date | None = None) -> list[str]:
    """Text of the last `n` archived columns (newest first), optionally before a date."""
    if not COLUMNS_DIR.exists():
        return []
    out = []
    for f in sorted(COLUMNS_DIR.glob("*.txt"), reverse=True):
        if before and f.stem >= before.isoformat():
            continue
        try:
            out.append(f.read_text())
        except OSError:
            continue
        if len(out) >= n:
            break
    return out


def _title(text: str) -> str:
    return (text.strip().splitlines() or [""])[0].strip()


def _flavor_key(flavor: str) -> str:
    return re.split(r"[-\s]", flavor.replace("Savory ", ""))[0].lower()


def pick_muffin(d: date, recent: list[str], rng: random.Random | None = None) -> str:
    """A seasonal flavor whose key fruit hasn't been reviewed in recent columns."""
    rng = rng or random.Random()
    titles = " ".join(_title(t).lower() for t in recent)
    pool = list(MUFFINS_BY_MONTH[d.month])
    fresh = [f for f in pool if not re.search(rf"\b{_flavor_key(f)}", titles)]
    return rng.choice(fresh or pool)


def pick_roy_hobby(recent: list[str], rng: random.Random | None = None) -> str:
    """A hobby of Roy's not mentioned in the last few columns."""
    rng = rng or random.Random()
    body = " ".join(recent[:4]).lower()
    # Word-start match: "train" must not fire on "restraint".
    used = {k for k, words in _HOBBY_KEYS.items()
            if any(re.search(rf"\b{w}", body) for w in words)}
    fresh = [h for h in ROY_HOBBIES if not any(k in h for k in used)]
    return rng.choice(fresh or list(ROY_HOBBIES))


def recent_subtitles(recent: list[str]) -> list[str]:
    """The descriptor half of recent titles ("a Word About Restraint")."""
    subs = []
    for t in recent:
        title = _title(t)
        if "," in title:
            subs.append(title.split(",", 1)[1].strip())
    return subs


def column_brief(now: datetime | None = None, rng: random.Random | None = None) -> dict:
    d = ritual_sunday(now)
    recent = recent_columns(before=d)
    return {
        "date": d,
        "date_str": d.strftime("%A, %B %-d"),
        "season": season_phrase(d),
        "muffin": pick_muffin(d, recent, rng),
        "roy": pick_roy_hobby(recent, rng),
        "avoid_titles": [_title(t) for t in recent],
        "avoid_subtitles": sorted(set(recent_subtitles(recent))),
    }


def column_title_ok(text: str, recent_titles: list[str]) -> bool:
    """Reject a column whose title, or its descriptor, repeats a recent one.

    Case/punctuation-insensitive. The descriptor check is what stops a fifth
    "a Word About Restraint".
    """
    def norm(s: str) -> str:
        return re.sub(r"[^a-z]+", " ", s.lower()).strip()

    title = _title(text)
    if norm(title) in {norm(t) for t in recent_titles}:
        return False
    subs = recent_subtitles([title])
    return not (subs and norm(subs[0]) in {norm(s) for s in recent_subtitles(recent_titles)})


# ---------- Mr. Quiet ----------

def recent_slips(n: int = SLIP_MEMORY) -> list[str]:
    """Distinct slip lines from post_log, newest first."""
    lines: list[str] = []
    try:
        rows = POST_LOG.read_text().splitlines()
    except OSError:
        return []
    for raw in reversed(rows):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not str(e.get("type", "")).startswith("slip"):
            continue
        text = (e.get("text") or "").strip()
        if text and text not in lines:
            lines.append(text)
        if len(lines) >= n:
            break
    return lines


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z']+", s.lower())


def _content(s: str) -> set[str]:
    return {w.rstrip("s") for w in _words(s) if w not in _STOP and len(w) > 2}


def slip_too_similar(line: str, recent: list[str]) -> str | None:
    """The recent slip `line` echoes, or None.

    Echo = shares a 4-word run, or half its content words. Catches
    "slow mornings are not lost time; they are X" against its twin.
    """
    words = _words(line)
    grams = {tuple(words[i:i + 4]) for i in range(len(words) - 3)}
    mine = _content(line)
    for prev in recent:
        pw = _words(prev)
        if grams & {tuple(pw[i:i + 4]) for i in range(len(pw) - 3)}:
            return prev
        theirs = _content(prev)
        if mine and theirs and len(mine & theirs) / min(len(mine), len(theirs)) >= 0.5:
            return prev
    return None


def pick_slip_object(recent: list[str], rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    text = " ".join(recent).lower()
    fresh = [o for o in SLIP_OBJECTS if o.split()[-1] not in text]
    return rng.choice(fresh or list(SLIP_OBJECTS))
