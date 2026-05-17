"""Spec roller for outbox.cafe hourly generations.

Picks across seven dimensions with anti-bias mechanics so the generator
doesn't converge to Claude's defaults or repeat itself within a short window.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

# Length tier weights (favor medium, occasional tiny/large for variety)
LENGTH_WEIGHTS = {"tiny": 30, "medium": 50, "large": 20}


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
    """Roll a complete spec for one hourly generation.

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


SPEC_LLM_PROMPT = """You are rolling a spec for outbox.cafe — a constantly-evolving, weird/retro corner of the internet. Each hour a new self-contained HTML page goes up. Your job: invent a fresh spec across these dimensions. Push HARD for variety. NEVER converge to your own defaults (dry deadpan, lowercase fragments, archival/museum/cabinet metaphors, melancholy minimalism, night-shift dispatcher voice).

The dimension pools are bottomless — don't think of yourself as picking from a list. INVENT. Reach into untouched corners of internet history, hobby subcultures, regional weirdness, fictional ephemera, art movements, mechanical curiosities, kitchen objects, weather phenomena, defunct industries.

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


def roll_spec_via_llm(
    seed: int | None = None,
    model: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Ask Claude to invent a fresh spec across all dimensions. Fall back to static roller on any failure."""
    import re
    import subprocess

    rng = random.Random(seed) if seed is not None else random.Random()
    dims = load_dimensions()
    history = load_recent_history(40)

    def recent_str(field: str) -> str:
        items = _recent_picks(field, history, n=25)
        return "; ".join(items) if items else "(none yet)"

    def example_str(field: str) -> str:
        return "; ".join(_examples_from_dimensions(dims, field, n=5, rng=rng))

    prompt = SPEC_LLM_PROMPT.format(
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

    cmd = ["claude", "--print", "--tools", "", "--permission-mode", "plan"]
    if model:
        cmd += ["--model", model]

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
