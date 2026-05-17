"""Social posting for outbox.cafe.

Wires up to Bluesky / Tumblr / Are.na (added in waves). Stub for now —
credentials arrive via environment variables loaded from ~/.outbox-cafe/.env
on the Mini. Each platform module is self-contained so we can launch one
at a time without blocking the others.
"""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_PATH = ROOT / "data" / "personas.json"


def _load_personas() -> dict[str, Any]:
    import json
    return json.loads(PERSONAS_PATH.read_text())


def pick_persona(rng: random.Random | None = None) -> dict[str, Any]:
    """Weighted-pick a staff member for the next post."""
    rng = rng or random.Random()
    data = _load_personas()
    staff = data["staff"]
    weights = [s["weight"] for s in staff]
    return rng.choices(staff, weights=weights, k=1)[0]


# --- Platform stubs ---------------------------------------------------------

def post_to_bluesky(text: str) -> dict[str, Any]:
    """Post a status to Bluesky via app-password auth (atproto)."""
    handle = os.environ.get("BSKY_HANDLE")
    app_pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not app_pw:
        return {"ok": False, "skip": "BSKY_HANDLE / BSKY_APP_PASSWORD not set"}
    # TODO: implement via the atproto HTTP API once creds land.
    return {"ok": False, "skip": "bluesky integration not wired yet"}


def post_to_tumblr(text: str) -> dict[str, Any]:
    if not os.environ.get("TUMBLR_CONSUMER_KEY"):
        return {"ok": False, "skip": "TUMBLR_* not set"}
    return {"ok": False, "skip": "tumblr integration not wired yet"}


def post_to_arena(text: str, image_url: str | None = None) -> dict[str, Any]:
    if not os.environ.get("ARENA_TOKEN"):
        return {"ok": False, "skip": "ARENA_TOKEN not set"}
    return {"ok": False, "skip": "are.na integration not wired yet"}


PLATFORMS = {
    "bluesky": post_to_bluesky,
    "tumblr": post_to_tumblr,
    "arena": post_to_arena,
}


def post_everywhere(text: str) -> dict[str, dict[str, Any]]:
    return {name: fn(text) for name, fn in PLATFORMS.items()}


if __name__ == "__main__":
    import sys
    text = sys.stdin.read().strip() if not sys.stdin.isatty() else "test post from outbox.cafe"
    print(post_everywhere(text))
