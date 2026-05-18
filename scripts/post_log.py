"""Append-only log of every post / reply / like the cafe makes on social.

JSONL at data/post_log.jsonl (gitignored — per-Mini state). Each line:
    {"ts": "...", "type": "drop|reply|ambient|like", "persona": "M.",
     "uri": "at://...", "subject": "@handle or our:gen-path", "text": "..."}

Used later to: bias persona weights toward voices that get engagement, audit
the bot for any weird patterns, debug "wait, did the cafe post that?".
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "post_log.jsonl"


def log(
    type_: str,
    persona: str | None = None,
    uri: str | None = None,
    subject: str | None = None,
    text: str | None = None,
    **extra,
) -> None:
    """Best-effort append. Never raises — logging failure must not break a post."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "type": type_,
        }
        if persona is not None:
            entry["persona"] = persona
        if uri is not None:
            entry["uri"] = uri
        if subject is not None:
            entry["subject"] = subject
        if text is not None:
            entry["text"] = text[:500]
        if extra:
            entry.update(extra)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[post_log] write failed (non-fatal): {e}", file=sys.stderr)
