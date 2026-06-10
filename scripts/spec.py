"""Spec roller for outbox.cafe scheduled generations (4x/day).

Picks across seven dimensions with anti-bias mechanics so the generator
doesn't converge to Claude's defaults or repeat itself within a short window.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.llm import claude_cmd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DIMENSIONS_PATH = DATA_DIR / "dimensions.json"
HISTORY_PATH = DATA_DIR / "history.jsonl"

# How many recent specs to consider for anti-clustering.
# Long enough to suppress every pool: smallest is forbidden_register (~8 effective)
# and we want hard-block to span ~half the pool, so 40 is comfortable for everything.
RECENT_WINDOW = 40

# Items picked in the last min(HARD_BLOCK_CAP, pool//2) gens are excluded entirely.
# At 10-min cadence: 20 gens ≈ 3.3 hours of "you can't see this again."
HARD_BLOCK_CAP = 20

# Probability the wildcard slot is used at all
WILDCARD_PROBABILITY = 0.40

# Probability a forbidden register is enforced
FORBIDDEN_PROBABILITY = 0.65

# Probability a gen gets NO spec at all — carte blanche, the model builds the
# page it most wants to exist (Stephen, 2026-06-09: "so they can build what
# they want"). ~2-3 gens/week at 4/day. Anti-convergence guardrails still
# apply in the prompt; this skips the rolled dimensions, not the house rules.
CARTE_BLANCHE_PROBABILITY = 0.10

# Length tier weights (favor medium, occasional tiny/large for variety)
LENGTH_WEIGHTS = {"tiny": 30, "medium": 50, "large": 20}

# Format buckets — picked in code (not by the LLM) so games/puzzles/art actually
# get rotation. Without this, the LLM defaults to "interactive [X] logbook browser"
# for ~70% of gens regardless of how the prompt nudges it. Bucket is chosen with
# anti-bias on recent history, then handed to the LLM as a hard constraint.
FORMAT_BUCKETS: dict[str, dict[str, Any]] = {
    "game_toy": {
        "label": "TINY BROWSER GAME / INTERACTIVE TOY",
        "description": "A small, real, playable thing. The visitor clicks, taps, types, drags, or steers and something actually happens. Build a working game/toy, not a description of one — the button must do the thing, the maze must navigate, the dice must roll.",
        "examples": [
            "a one-button game (click increases something, that's the whole loop)",
            "a tiny clickable garden you plant in (click cells, things grow)",
            "a hidden-object scene (find the cat / find five matchbooks)",
            "a magic 8-ball or fortune machine that gives one answer per click",
            "a pet you can pet (click it, it responds — purrs, wiggles, blooms)",
            "a small maze steered with arrow keys (real, playable)",
            "a sliding-tile puzzle (3x3) that reveals a hidden picture",
            "a memory-match grid with 6-8 pairs",
            "a feed-the-creature game (drag food onto a creature, it reacts)",
            "a rotary-phone dial toy (each digit plays a tone or reveals a word)",
            "a tap-the-bell game where each ring slightly changes the world",
            "a click-to-summon-a-firework or click-to-pop-a-bubble button",
            "a tiny terrarium you can shake or tilt with mouse movement",
            "a coin-flip oracle: ask, click, get answer",
        ],
    },
    "puzzle": {
        "label": "REAL SOLVABLE PUZZLE",
        "description": "A genuine puzzle the visitor can solve in-browser. Provide a reveal mechanism (button, key combo, inspect-element easter egg) but don't give the answer upfront.",
        "examples": [
            "a real cryptogram you can solve in-browser (with reveal button)",
            "a riddle whose answer is hidden (click-reveal, inspect-element, or keyboard cue)",
            "a 4x4 Sudoku-lite you can fill in",
            "a spot-the-difference between two near-identical scenes",
            "a word ladder from one word to another, 4-6 steps",
            "a logic grid puzzle: 3 items, 3 traits, fill in the truth",
            "a maze you trace with the cursor (no clicking, just following)",
            "a 'what comes next' visual sequence puzzle",
            "a find-the-hidden-word puzzle: word camouflaged in dense text",
            "a treasure hunt where clicked clues lead to hidden divs across the page",
        ],
    },
    "gen_art": {
        "label": "GENERATIVE / CSS ART PIECE",
        "description": "A pure visual or sensory piece. Minimal or no text. CSS, SVG, JS animation. The piece IS the visual — beauty over information.",
        "examples": [
            "a CSS-only generative wallpaper that slowly morphs",
            "a kaleidoscope (CSS conic gradient + slow rotation)",
            "an ASCII rain or snow animation that runs forever",
            "a CSS landscape painting (mountain, lake, sky — pure shapes, no images)",
            "a constellation map of fictional stars with named asterisms",
            "a particle-system snow globe you can shake with the cursor",
            "a music box you wind by scrolling — mechanical shapes turn",
            "a window with parallax weather happening 'outside'",
            "a single drop of light pulsing in the dark, breathing",
            "a quilt pattern that's also a map of somewhere",
            "a flipbook (click to advance, 8-12 frames of a small scene)",
            "a slow ink-drop expanding across the page",
            "a sun-and-moon clock without numbers — only color and shadow",
            "a hand-drawn-feeling cursor trail you paint with",
            "a CSS art portrait of a fictional person, framed like a museum tag",
        ],
    },
    "structural": {
        "label": "STRUCTURALLY WEIRD PAGE",
        "description": "The strangeness IS the point. Form-as-content. Don't explain the joke; let the visitor sit in it.",
        "examples": [
            "a page that pretends to be loading forever (the loading IS the content)",
            "a page that's just one very long footer (no header, no body)",
            "a page that's a single modal dialog you can't close",
            "a 404 that isn't actually a 404",
            "a comments section with no original post",
            "a transcript of a phone call where one side is just static",
            "a typewriter that types its own page out, one character at a time",
            "a page that's only an iframe of itself, recursive",
            "a page that's a directory listing of files that don't exist",
            "a page that's the same sentence repeated 30 times, slightly different each time",
            "a page that's a single map legend with no map",
            "a page that's a Russian-doll of nested boxes you click into",
            "a page that's the closing credits for nothing",
            "a page that is a single very long word with internal structure",
            "a page that's an unsolved equation written as a poem",
        ],
    },
    "document": {
        "label": "TRADITIONAL CONTENT SHAPE",
        "description": "The page LOOKS LIKE a real-world document — a thing with specific physical-object texture. Receipt, menu, flyer, card, letter, ad, ticket, package label. Read like the object it imitates.",
        "examples": [
            "shop receipt with itemized list",
            "restaurant menu",
            "yard sale flyer",
            "missing pet flyer",
            "real estate listing",
            "classified ads page",
            "recipe card",
            "back of a cereal box",
            "fan letter (one side only)",
            "spam email (artisanal)",
            "horoscope page (12 signs, all weird)",
            "voicemail transcript",
            "set list from a concert that didn't happen",
            "manifesto / open letter",
            "instruction manual (page 47 of 200)",
            "wedding registry for fictional people",
            "ringtone download page",
            "auction listing for a single weird item",
            "product review",
            "FAQ page",
            "church bulletin",
            "HOA newsletter",
            "obituary for something that did not die",
        ],
    },
    "archive_browser": {
        "label": "BROWSEABLE ARCHIVE / DIRECTORY",
        "description": "A logbook, ledger, registry, catalog, or member directory the visitor browses. USED HEAVILY in recent gens — only pick this when truly fresh, and never with a radio/broadcast/operator subject.",
        "examples": [
            "BBS login screen and main menu",
            "library card with stamped due dates",
            "field guide entry (one species)",
            "museum exhibit placard",
            "corkboard / community bulletin board",
            "mailing list digest",
            "magazine spread / center fold",
        ],
    },
}

# Bucket selection weights — heavily downweight archive_browser (current attractor),
# upweight the categories that are starved (game_toy, puzzle, gen_art).
FORMAT_BUCKET_WEIGHTS = {
    "game_toy":        25,
    "puzzle":          18,
    "gen_art":         25,
    "structural":      14,
    "document":        20,
    "archive_browser": 5,
}


def _pick_format_bucket(history: list[dict], rng: random.Random) -> str:
    """Pick a format bucket with anti-bias on recent history.

    Hard-blocks buckets used in the last 3 rolls so the next gen lands in a
    fresh category. Falls back to weighted choice over all buckets if every
    bucket happens to be blocked (only possible with a tiny pool).
    """
    recent_buckets: list[str] = []
    for h in history:
        b = h.get("format_bucket")
        if isinstance(b, str):
            recent_buckets.append(b)

    HARD_BLOCK_RECENT = 3
    blocked = set(recent_buckets[-HARD_BLOCK_RECENT:]) if recent_buckets else set()
    pool = [k for k in FORMAT_BUCKETS if k not in blocked]
    if not pool:
        pool = list(FORMAT_BUCKETS)
    weights = [FORMAT_BUCKET_WEIGHTS.get(k, 10) for k in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def load_dimensions() -> dict[str, Any]:
    return json.loads(DIMENSIONS_PATH.read_text())


def load_recent_history(n: int = RECENT_WINDOW) -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text().splitlines()
    return [json.loads(l) for l in lines[-n:] if l.strip()]


def append_history(spec: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(spec) + "\n")


def _weighted_choice(options: list[str], recent_values: list[str], rng: random.Random) -> str:
    """Pick from options with anti-repeat mechanics.

    Two-zone scheme:
    - HARD BLOCK: items picked in the last K rolls (K ≈ pool_size / 2, capped)
      are excluded entirely from this pick. Guarantees no near-term repeats
      and forces real spread across the pool.
    - SOFT PENALTY: items farther back in the window still get a reduced
      weight, decaying linearly until they're freely re-selectable past the
      window.
    """
    pool_size = len(options)
    block_k = min(pool_size // 2, HARD_BLOCK_CAP, len(recent_values))
    blocked = set(recent_values[-block_k:]) if block_k > 0 else set()
    pool = [o for o in options if o not in blocked]
    if not pool:
        pool = options  # safety: tiny pool, nothing left — fall back to full set

    weights = []
    for opt in pool:
        penalty = 1.0
        # walk from newest backward; first match wins (hardest penalty)
        for idx, recent in enumerate(reversed(recent_values)):
            if idx < block_k:
                continue  # this zone is already hard-blocked, no soft penalty needed
            if recent == opt:
                # idx > block_k: soft penalty, decays back to 1.0 over the rest of the window
                soft_idx = idx - block_k
                penalty = min(penalty, 0.1 + 0.05 * soft_idx)
                break
        weights.append(penalty)
    return rng.choices(pool, weights=weights, k=1)[0]


def roll_spec(seed: int | None = None) -> dict[str, Any]:
    """Roll a complete spec for one scheduled generation.

    Returns a dict with all dimensions populated, anti-bias applied,
    and a generated_at timestamp.
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    dims = load_dimensions()
    history = load_recent_history()

    def recent(field: str) -> list[str]:
        return [
            (h.get(field, {}).get("value") if isinstance(h.get(field), dict) else h.get(field))
            for h in history
        ]

    spec: dict[str, Any] = {}

    # Simple dimensions: weighted_choice across the full list, penalize recency
    for field in ("era", "format", "subject", "tone", "mandatory_element", "palette"):
        spec[field] = {"value": _weighted_choice(dims[field], recent(field), rng)}

    # Length: weighted by tier preference, not anti-bias (it's tiny set)
    tier = rng.choices(
        list(LENGTH_WEIGHTS.keys()),
        weights=list(LENGTH_WEIGHTS.values()),
        k=1,
    )[0]
    length_obj = next(L for L in dims["length"] if L["key"] == tier)
    spec["length"] = length_obj

    # Wildcard: fires with probability, otherwise the "no wildcard" entry
    if rng.random() < WILDCARD_PROBABILITY:
        wc_pool = [w for w in dims["wildcard"] if "no wildcard" not in w.lower()]
        spec["wildcard"] = {"value": _weighted_choice(wc_pool, recent("wildcard"), rng)}
    else:
        spec["wildcard"] = {"value": "no wildcard this hour"}

    # Forbidden register: enforced with probability — pushes away from Claude defaults
    if rng.random() < FORBIDDEN_PROBABILITY:
        fr_pool = [f for f in dims["forbidden_register"] if "no register forbidden" not in f.lower()]
        spec["forbidden_register"] = {"value": _weighted_choice(fr_pool, recent("forbidden_register"), rng)}
    else:
        spec["forbidden_register"] = {"value": "no register forbidden this hour"}

    spec["generated_at"] = datetime.now(timezone.utc).isoformat()
    spec["seed"] = seed
    return spec


def _recent_picks(field: str, history: list[dict], n: int = 25) -> list[str]:
    """Last N distinct values for a field, newest first."""
    seen: set[str] = set()
    out: list[str] = []
    for h in reversed(history):
        v = h.get(field, {})
        if isinstance(v, dict):
            v = v.get("value")
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(str(v))
        if len(out) >= n:
            break
    return out


def _examples_from_dimensions(dims: dict, field: str, n: int, rng: random.Random) -> list[str]:
    """Pull n random example values from the static seed list (texture cue for the LLM)."""
    pool = dims.get(field, [])
    if not pool:
        return []
    picks = rng.sample(pool, min(n, len(pool)))
    return [str(p) for p in picks]


SPEC_LLM_PROMPT = """You are rolling a spec for outbox.cafe — a constantly-evolving, weird/retro corner of the internet. A new self-contained HTML page goes up four times a day. Your job: invent a fresh spec across these dimensions. Push HARD for variety. NEVER converge to your own defaults (dry deadpan, lowercase fragments, archival/museum/cabinet metaphors, melancholy minimalism, night-shift dispatcher voice).

The dimension pools are bottomless — don't think of yourself as picking from a list. INVENT. Reach into untouched corners of internet history, hobby subcultures, regional weirdness, fictional ephemera, art movements, mechanical curiosities, kitchen objects, weather phenomena, defunct industries.

FORMAT BUCKET FOR THIS HOUR (HARD CONSTRAINT)
=============================================
The format bucket has been pre-chosen for you to force category rotation. Invent your `format` value WITHIN THIS BUCKET ONLY:

>>> {format_bucket_label} <<<
{format_bucket_description}

Examples from this bucket (texture only — invent something fresh, don't copy verbatim):
{format_bucket_examples}

Your `format` value MUST be a {format_bucket_label}. Do NOT produce a logbook / ledger / registry / catalog / directory browser unless the bucket above is BROWSEABLE ARCHIVE / DIRECTORY. Do NOT produce a "this site IS a [physical document]" answer unless the bucket above is TRADITIONAL CONTENT SHAPE. Stay inside the bucket.

AVOID THESE PATTERNS THE ENGINE OVERUSES (no exceptions, regardless of how cleverly you'd phrase them):
- "Interactive [X] logbook / ledger / registry / catalog / directory / archive browser" format pattern. ~70% of recent gens have been this shape. Hard-banned unless the bucket above explicitly says BROWSEABLE ARCHIVE / DIRECTORY.
- Radio, CB radio, ham radio, BBS, telephone exchange, switchboard, broadcast station, TV station, telegraph, dispatcher, taxi-dispatch, operator-shift, transmission, signal, frequency-tuner subjects. Heavily overused. Off-limits for at least the next several rounds, regardless of bucket.
- Reused fictional proper nouns: "Millbrook", "Whitmore", "Riverside [Society]", "the [town] [Society/Club] of [thing]". Pick fresh names, settings, and scaffolding — and where possible, avoid the "fictional small-town society/club of hobbyists" frame entirely.
- "Society / club / cabinet / observatory / parlor / salon of [niche hobby]" as the subject scaffolding — pattern is exhausted.
- Day-of-the-week themes — no Wednesdays, Tuesdays, Mondays, weekends-as-concept, "a regular [day]", etc. Days as throwaway specifics in a piece are fine; days as the SUBJECT are off-limits.
- "Fictional baseball team" / minor-league-anything subjects.
- "One specific X" framing (one specific cloud, one specific Tuesday, one specific intersection) — the construction has been used too much.
- Eulogies / obituaries / tributes for inanimate objects (chairs, rubber bands, etc.) — also overused.
- Society-for-the-appreciation-of-one-X subjects.

RECENT PICKS TO AVOID (last ~25 gens; do not repeat any, and aim to land in a different aesthetic territory):
- era: {era_recent}
- format: {format_recent}
- subject: {subject_recent}
- tone: {tone_recent}
- palette: {palette_recent}
- mandatory_element: {mandatory_recent}
- wildcard: {wildcard_recent}
- forbidden_register: {forbidden_recent}

DIMENSIONS — definitions and seed examples (texture cues only; do not be limited to these):

- era: aesthetic/temporal frame. Seeds: {era_examples}. Also fair game: medieval scriptoria, 1970s mail art, 1923 amateur radio QSL cards, 1850 penny dreadful, 2031 retro-future, 1880 panorama broadside, 1965 ham radio, 2014 SoundCloud rap, 1932 transcontinental telegraph, etc.

- format: the SHAPE of the piece. The possibility space is enormous — text-based things, tiny browser games, generative art pieces, real puzzles, confusing-but-good experiences, pages-as-physical-objects (a receipt, a clock, a J-card, a panel of stained glass, a cassette label, an EKG strip, a postage stamp), sensory pieces (just-sound, just-color, just-motion, just-cursor), formats nobody has named yet. INVENT one specific shape: describe in 1-2 concrete sentences what the page IS, what it DOES, and what the visitor sees and touches. Reach for something the internet hasn't quite seen before — do not recombine the obvious archetypes.

- subject: what the page is ABOUT — specific, concrete, can be fictional. Seeds: {subject_examples}. Reach for weird specific corners.

- tone: the voice. Seeds: {tone_examples}. Pick a HUMAN, specific voice. Tone can be melancholy/anxious/dreamy — never cruel or cynical.

- length: pick exactly one of "tiny" (30-80 lines), "medium" (120-220 lines), or "large" (350-600 lines).

- palette: two-color combo with hex codes that feels like a specific physical thing. Seeds: {palette_examples}.

- mandatory_element: one specific functional/interactive feature that MUST be present and working. Seeds: {mandatory_examples}.

- wildcard: an additional weird constraint. Seeds: {wildcard_examples}. About 35% of the time set to "no wildcard this hour".

- forbidden_register: an aesthetic to actively AVOID this hour (rotates among Claude's defaults so the engine doesn't converge). Seeds: {forbidden_examples}. About 30% of the time set to "no register forbidden this hour".

OUTPUT — strict JSON, no prose, no fences, no commentary. Schema:
{{
  "era": {{"value": "..."}},
  "format": {{"value": "..."}},
  "subject": {{"value": "..."}},
  "tone": {{"value": "..."}},
  "length": "tiny" | "medium" | "large",
  "palette": {{"value": "..."}},
  "mandatory_element": {{"value": "..."}},
  "wildcard": {{"value": "..."}},
  "forbidden_register": {{"value": "..."}}
}}
Start with {{ and end with }}. Nothing else.
"""


def _carte_blanche_spec(rng: random.Random, dims: dict[str, Any]) -> dict[str, Any]:
    """A no-brief spec: every dimension is the builder's call. Length still
    rolls (an unconstrained gen otherwise always goes maximalist)."""
    tier = rng.choices(list(LENGTH_WEIGHTS.keys()), weights=list(LENGTH_WEIGHTS.values()), k=1)[0]
    spec: dict[str, Any] = {
        f: {"value": "builder's choice"}
        for f in ("era", "format", "subject", "tone", "palette", "mandatory_element")
    }
    spec["wildcard"] = {"value": "no wildcard this hour"}
    spec["forbidden_register"] = {"value": "no register forbidden this hour"}
    spec["length"] = next(L for L in dims["length"] if L["key"] == tier)
    spec["carte_blanche"] = True
    spec["format_bucket"] = "carte_blanche"
    spec["rolled_by"] = "carte_blanche"
    spec["generated_at"] = datetime.now(timezone.utc).isoformat()
    return spec


def roll_spec_via_llm(
    seed: int | None = None,
    model: str | None = None,
    timeout: int = 240,
) -> dict[str, Any]:
    """Ask Claude to invent a fresh spec across all dimensions. Fall back to static roller on any failure."""
    import re
    import subprocess

    rng = random.Random(seed) if seed is not None else random.Random()
    dims = load_dimensions()
    history = load_recent_history(40)

    # Carte blanche: occasionally there is no brief at all.
    if rng.random() < CARTE_BLANCHE_PROBABILITY:
        print("[spec] carte blanche this hour — no brief, the corkboard is theirs")
        return _carte_blanche_spec(rng, dims)

    def recent_str(field: str) -> str:
        items = _recent_picks(field, history, n=25)
        return "; ".join(items) if items else "(none yet)"

    def example_str(field: str) -> str:
        return "; ".join(_examples_from_dimensions(dims, field, n=5, rng=rng))

    bucket_key = _pick_format_bucket(history, rng)
    bucket = FORMAT_BUCKETS[bucket_key]
    bucket_examples = rng.sample(bucket["examples"], min(6, len(bucket["examples"])))

    prompt = SPEC_LLM_PROMPT.format(
        format_bucket_label=bucket["label"],
        format_bucket_description=bucket["description"],
        format_bucket_examples="\n".join(f"  - {e}" for e in bucket_examples),
        era_recent=recent_str("era"),
        format_recent=recent_str("format"),
        subject_recent=recent_str("subject"),
        tone_recent=recent_str("tone"),
        palette_recent=recent_str("palette"),
        mandatory_recent=recent_str("mandatory_element"),
        wildcard_recent=recent_str("wildcard"),
        forbidden_recent=recent_str("forbidden_register"),
        era_examples=example_str("era"),
        subject_examples=example_str("subject"),
        tone_examples=example_str("tone"),
        palette_examples=example_str("palette"),
        mandatory_examples=example_str("mandatory_element"),
        wildcard_examples=example_str("wildcard"),
        forbidden_examples=example_str("forbidden_register"),
    )

    # Spec rolling is just JSON output. Runs on opus like everything else now
    # (Max OAuth = $0); falls back to the static roller on any failure anyway.
    cmd = claude_cmd(model or "opus")

    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude exit {result.returncode}: {result.stderr[:300]}")
        out = result.stdout.strip()
        # Strip ```json fences if present
        out = re.sub(r"^```(?:json)?\s*", "", out)
        out = re.sub(r"\s*```\s*$", "", out)
        start = out.find("{")
        end = out.rfind("}")
        if start < 0 or end < 0:
            raise ValueError(f"no JSON object in output: {out[:200]!r}")
        data = json.loads(out[start:end + 1])
    except Exception as e:
        print(f"[spec_llm] failed: {e} — falling back to static roller", flush=True)
        return roll_spec(seed=seed)

    spec: dict[str, Any] = {}
    for field in (
        "era", "format", "subject", "tone",
        "palette", "mandatory_element", "wildcard", "forbidden_register",
    ):
        v = data.get(field)
        if isinstance(v, dict) and v.get("value"):
            spec[field] = {"value": str(v["value"])}
        elif isinstance(v, str) and v.strip():
            spec[field] = {"value": v.strip()}
        else:
            print(f"[spec_llm] missing/bad field {field!r} — falling back to static roller", flush=True)
            return roll_spec(seed=seed)

    length_key = data.get("length")
    if isinstance(length_key, dict):
        length_key = length_key.get("key")
    if length_key not in ("tiny", "medium", "large"):
        length_key = "medium"
    spec["length"] = next(L for L in dims["length"] if L["key"] == length_key)

    spec["generated_at"] = datetime.now(timezone.utc).isoformat()
    spec["seed"] = seed
    spec["rolled_by"] = "llm"
    spec["format_bucket"] = bucket_key
    return spec


def format_spec_for_human(spec: dict[str, Any]) -> str:
    """Pretty-print a spec for logs and the page footer watermark."""

    def v(field: str) -> str:
        item = spec.get(field, {})
        if isinstance(item, dict):
            return item.get("value") or item.get("key") or str(item)
        return str(item)

    lines = [
        f"  era              {v('era')}",
        f"  format           {v('format')}",
        f"  subject          {v('subject')}",
        f"  tone             {v('tone')}",
        f"  length           {spec['length']['key']} ({spec['length']['lines']} lines)",
        f"  palette          {v('palette')}",
        f"  mandatory        {v('mandatory_element')}",
        f"  wildcard         {v('wildcard')}",
        f"  forbidden        {v('forbidden_register')}",
    ]
    if spec.get("format_bucket"):
        lines.insert(2, f"  format_bucket    {spec['format_bucket']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--save", action="store_true", help="append to history.jsonl")
    args = parser.parse_args()

    s = roll_spec(seed=args.seed)
    print(format_spec_for_human(s))
    if args.save:
        append_history(s)
        print("\nappended to history.jsonl")
