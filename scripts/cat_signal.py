"""Cat-signal: DM Stephen a Discord alert when the unattended cafe needs his
attention. Posts directly to the bot's DM channel — NEVER to #tasks. Stephen
explicitly does not want routine activity in #tasks; the only thing that
reaches him from this project is a breakage DM via this module.

Trigger conditions are decided by the caller (generate.py, engage_bsky.py);
this module just formats and sends. Best-effort — failures here never bubble.

Dedup: writes the last-signal timestamp per `key` to data/cat_signal_state.json
(gitignored). Repeat signals for the same key within 6 hours are suppressed.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "cat_signal_state.json"
BOT_TOKEN_FILE = Path(os.path.expanduser("~/.claude/channels/discord/.env"))
DM_CHANNEL = "1486102002474811524"   # bot ↔ Stephen DM channel
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


def _read_bot_token() -> str | None:
    try:
        with BOT_TOKEN_FILE.open() as f:
            for line in f:
                if line.startswith("DISCORD_BOT_TOKEN="):
                    return line.strip().split("=", 1)[1]
    except Exception:
        return None
    return None


def _dm(token: str, body: str) -> bool:
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{DM_CHANNEL}/messages",
        data=json.dumps({"content": body}).encode(),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        print(f"[cat-signal] discord DM HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[cat-signal] discord DM failed: {e}", file=sys.stderr)
    return False


def signal(key: str, message: str, priority: str = "normal") -> bool:
    """DM Stephen with a cat-signal alert. Dedupes per-`key` within 6 hours.

    `key` is a short identifier for what's wrong ('bsky-auth', 'fal-quota',
    'git-push', etc.). `priority` is one of low/normal/high — only changes
    the prefix label on the message.

    Returns True if the alert was sent; False if deduped or token missing.
    """
    state = _load()
    now = time.time()
    last = state.get(key)
    if last and (now - last) < DEDUP_WINDOW_SECONDS:
        ago_min = int((now - last) / 60)
        print(f"[cat-signal] '{key}' was last sent {ago_min}m ago — suppressing", file=sys.stderr)
        return False

    token = _read_bot_token()
    if not token:
        print(f"[cat-signal] no bot token at {BOT_TOKEN_FILE} — skipping", file=sys.stderr)
        return False

    prefix = {
        "high": "🔴",
        "normal": "🟡",
        "low": "🔵",
    }.get(priority, "🟡")

    body = f"{prefix} **outbox.cafe** · `{key}`\n{message}"
    if not _dm(token, body):
        return False

    state[key] = now
    _save(state)
    print(f"[cat-signal] DM sent '{key}' (priority={priority})", file=sys.stderr)
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
