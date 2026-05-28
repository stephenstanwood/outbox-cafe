"""Nightly cafe digest: post a brief Discord summary of the last 24h.

Scheduled at 03:00 PT so Stephen reads it when he wakes up at 04:00.

Includes:
- gen count for the last 24h + a sample title
- recent bsky activity (post count, last post text, follower count if available)
- any cat-signal alerts fired
- whether the cron has fired healthily

Best-effort — failures here don't propagate, the cafe keeps running regardless.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
HISTORY_PATH = ROOT / "data" / "history.jsonl"
SIGNAL_STATE = ROOT / "data" / "cat_signal_state.json"
HELPER = Path(os.path.expanduser("~/.claude/scripts/post-to-tasks.sh"))
PT = ZoneInfo("America/Los_Angeles")
BSKY_BASE = "https://bsky.social/xrpc"


def _post_discord(text: str) -> None:
    if not HELPER.exists():
        print(f"[digest] helper missing — printing instead\n{text}")
        return
    try:
        subprocess.run([str(HELPER)], input=text, text=True, timeout=15, check=False)
    except Exception as e:
        print(f"[digest] post failed: {e}", file=sys.stderr)


def _gens_last_24h() -> tuple[int, list[str]]:
    """Return (count, sample_titles[:3]) of gens written in the last 24h."""
    cutoff = datetime.now(tz=PT) - timedelta(hours=24)
    recent: list[tuple[datetime, str]] = []
    for f in ARCHIVE_DIR.glob("*.html"):
        if f.name == "index.html":
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})", f.stem)
        if not m:
            continue
        try:
            dt = datetime.strptime(
                f"{m.group(1)} {m.group(2)}:{m.group(3)}",
                "%Y-%m-%d %H:%M",
            ).replace(tzinfo=PT)
        except Exception:
            continue
        if dt < cutoff:
            continue
        # Extract title cheaply
        try:
            html = f.read_text(errors="ignore")
            tm = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = re.sub(r"\s+", " ", tm.group(1).strip()) if tm else f.stem
        except Exception:
            title = f.stem
        recent.append((dt, title))
    recent.sort(key=lambda t: t[0], reverse=True)
    return len(recent), [t for _, t in recent[:3]]


def _bsky_summary() -> dict:
    """Quick bsky stats: profile counts + last self-post text."""
    handle = os.environ.get("BSKY_HANDLE")
    pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        return {"error": "BSKY_* env not set"}

    def req(path, *, data=None, headers=None, method=None):
        h = {"Accept": "application/json"}
        if headers:
            h.update(headers)
        body = None
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
        r = urllib.request.Request(f"{BSKY_BASE}{path}", data=body, headers=h, method=method)
        with urllib.request.urlopen(r, timeout=15) as resp:
            return json.load(resp)

    try:
        sess = req(
            "/com.atproto.server.createSession",
            data={"identifier": handle, "password": pw},
            method="POST",
        )
    except Exception as e:
        return {"error": f"auth failed: {e}"}
    did = sess["did"]
    jwt = sess["accessJwt"]
    auth = {"Authorization": f"Bearer {jwt}"}

    out: dict = {"did": did}
    try:
        prof = req(f"/app.bsky.actor.getProfile?actor={handle}", headers=auth)
        out["followers"] = prof.get("followersCount", 0)
        out["follows"] = prof.get("followsCount", 0)
        out["posts"] = prof.get("postsCount", 0)
    except Exception as e:
        out["profile_error"] = str(e)[:100]

    try:
        feed = req(f"/app.bsky.feed.getAuthorFeed?actor={did}&limit=5", headers=auth)
        items = feed.get("feed", [])
        last_text = ""
        for item in items:
            rec = (item.get("post") or {}).get("record") or {}
            if rec.get("text"):
                last_text = rec["text"]
                break
        out["last_post"] = last_text[:120] + ("…" if len(last_text) > 120 else "")
    except Exception as e:
        out["feed_error"] = str(e)[:100]

    return out


def _signals_last_24h() -> list[str]:
    if not SIGNAL_STATE.exists():
        return []
    try:
        state = json.loads(SIGNAL_STATE.read_text())
    except Exception:
        return []
    import time
    cutoff = time.time() - 24 * 3600
    return [k for k, v in state.items() if isinstance(v, (int, float)) and v > cutoff]


def _gen_health_last_24h() -> dict:
    """Summarize data/runs.jsonl over the last 24h: gens logged + failed posts + retries."""
    runs_path = ROOT / "data" / "runs.jsonl"
    if not runs_path.exists():
        return {}
    cutoff = datetime.now(tz=PT) - timedelta(hours=24)
    logged = bsky_fail = tumblr_fail = retried = 0
    for line in runs_path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            dt = datetime.fromisoformat(e.get("ts", ""))
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=PT)
        if dt < cutoff:
            continue
        logged += 1
        if e.get("bsky") is False:
            bsky_fail += 1
        if e.get("tumblr") is False:
            tumblr_fail += 1
        if isinstance(e.get("attempts"), int) and e["attempts"] > 1:
            retried += 1
    return {"logged": logged, "bsky_fail": bsky_fail, "tumblr_fail": tumblr_fail, "retried": retried}


def main() -> int:
    # Run the reflection pass first so its summary lands in tonight's digest and
    # tomorrow's posts already use the updated weights.
    try:
        from reflect import run as run_reflect
        run_reflect()
    except Exception as e:
        print(f"[digest] reflect pass errored (non-fatal): {e}", file=sys.stderr)

    count, titles = _gens_last_24h()
    bsky = _bsky_summary()
    signals = _signals_last_24h()
    try:
        from voice_weights import summary_line
        reflection = summary_line()
    except Exception:
        reflection = ""

    parts = ["**outbox.cafe nightly digest**"]
    parts.append(f"_{datetime.now(tz=PT).strftime('%a %b %d %Y · %H:%M PT')}_")
    parts.append("")

    if count == 0:
        parts.append("⚠️ **no gens in last 24h** — cron may be wedged")
    else:
        parts.append(f"**{count} gens** in last 24h. recent titles:")
        for t in titles:
            parts.append(f"  · {t[:90]}")

    parts.append("")
    if "error" in bsky:
        parts.append(f"⚠️ bsky: {bsky['error']}")
    else:
        parts.append(
            f"**bsky** · followers: {bsky.get('followers','?')} · follows: {bsky.get('follows','?')} · posts: {bsky.get('posts','?')}"
        )
        if bsky.get("last_post"):
            parts.append(f"last post: _{bsky['last_post']}_")

    if signals:
        parts.append("")
        parts.append(f"**signals fired in last 24h:** {', '.join('`' + s + '`' for s in signals)}")

    # Gen health from runs.jsonl — only surface a line when something's off, so a
    # healthy day stays quiet. (runs.jsonl is written per-gen by generate.py.)
    try:
        health = _gen_health_last_24h()
    except Exception:
        health = {}
    if health.get("logged"):
        bits = []
        if health["bsky_fail"]:
            bits.append(f"{health['bsky_fail']} bsky post(s) failed")
        if health["tumblr_fail"]:
            bits.append(f"{health['tumblr_fail']} tumblr post(s) failed")
        if health["retried"]:
            bits.append(f"{health['retried']} gen(s) needed a retry")
        if bits:
            parts.append("")
            parts.append(f"⚠️ **gen health:** {', '.join(bits)} (of {health['logged']} logged)")

    if reflection:
        parts.append("")
        parts.append(f"**reflection:** {reflection}")

    text = "\n".join(parts)
    _post_discord(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
