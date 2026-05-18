"""Read-side of the reflection loop.

`scripts/reflect.py` writes data/voice_weights.json once a night based on
real bsky engagement. This module is the read-side that the persona
pickers and topic roller import to apply the learned multipliers.

Defaults to a no-op when the file is missing or malformed — the cafe
runs unchanged on day 1 before any reflection pass has happened.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WEIGHTS_PATH = Path(__file__).resolve().parent.parent / "data" / "voice_weights.json"

# Voice guardrails — these are intentional bounds, not heuristics to tune freely.
# Cap stops any single persona from dominating; floor stops any from going silent.
PERSONA_MULTIPLIER_FLOOR = 0.5
PERSONA_MULTIPLIER_CAP = 1.5


def _load() -> dict[str, Any]:
    try:
        return json.loads(WEIGHTS_PATH.read_text())
    except Exception:
        return {}


def adjusted_weights(staff_pool: list[dict[str, Any]]) -> list[float]:
    """Return the base weights multiplied by learned per-persona multipliers.

    `staff_pool` is the raw list from personas.json; ordering preserved.
    Personas without entries (or with the file missing) get multiplier 1.0.
    """
    data = _load()
    persona = data.get("persona", {})
    out: list[float] = []
    for s in staff_pool:
        base = float(s.get("weight", 1))
        entry = persona.get(s.get("name", ""), {})
        mult = float(entry.get("multiplier", 1.0))
        # Re-clamp defensively in case the file was hand-edited
        mult = max(PERSONA_MULTIPLIER_FLOOR, min(PERSONA_MULTIPLIER_CAP, mult))
        out.append(base * mult)
    return out


def warm_wild_topics(limit: int = 8) -> list[str]:
    """Topics from recent wild replies that earned engagement (target reply, likes)."""
    data = _load()
    warm = data.get("wild_topics_warm", [])
    return [t.get("topic", "") for t in warm if t.get("topic")][:limit]


def summary_line() -> str:
    """One-line summary suitable for the nightly digest. Empty string if no data."""
    data = _load()
    return data.get("summary", "")
