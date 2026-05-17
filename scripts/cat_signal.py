"""Cat-signal: post a Discord alert when the unattended cafe needs Stephen's
attention. Routes through ~/.claude/scripts/post-to-tasks.sh (#tasks channel).

Trigger conditions are decided by the caller (generate.py, engage_bsky.py);
this module just formats and sends. Best-effort — failures here never bubble.

Dedup: writes the last-signal timestamp per `key` to data/cat_signal_state.json
(gitignored). Repeat signals for the same key within 6 hours are suppressed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "cat_signal_state.json"
HELPER = Path(os.path.expanduser("~/.claude/scripts/post-to-tasks.sh"))
DEDUP_WINDOW_SECONDS = 6 * 3600


def _load() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def signal(key: str, message: str, priority: str = "normal") -> bool:
    """Send a cat-signal Discord alert. Dedupes per-`key` within 6 hours.

    `key` is a short identifier for what's wrong ('bsky-auth', 'fal-quota',
    'git-push', etc.). `priority` is one of low/normal/high — only changes
    the prefix label on the message.

    Returns True if the alert was sent; False if deduped or helper missing.
    """
    if not HELPER.exists():
        print(f"[cat-signal] helper missing at {HELPER} — skipping", file=sys.stderr)
        return False

    state = _load()
    now = time.time()
    last = state.get(key)
    if last and (now - last) < DEDUP_WINDOW_SECONDS:
        ago_min = int((now - last) / 60)
        print(f"[cat-signal] '{key}' was last sent {ago_min}m ago — suppressing", file=sys.stderr)
        return False

    prefix = {
        "high": "[ALERT]",
        "normal": "[note]",
        "low": "[fyi]",
    }.get(priority, "[note]")

    body = f"**{prefix} outbox.cafe** — `{key}`\n{message}"
    try:
        subprocess.run(
            [str(HELPER)],
            input=body,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as e:
        print(f"[cat-signal] post-to-tasks failed: {e}", file=sys.stderr)
        return False

    state[key] = now
    _save(state)
    print(f"[cat-signal] sent '{key}' (priority={priority})", file=sys.stderr)
    return True


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--key", required=True)
    p.add_argument("--priority", choices=["high", "normal", "low"], default="normal")
    p.add_argument("message")
    args = p.parse_args()
    sent = signal(args.key, args.message, args.priority)
    sys.exit(0 if sent else 1)
