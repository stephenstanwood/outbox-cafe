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
