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
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lib import bsky, ritual_cache

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
HISTORY_PATH = ROOT / "data" / "history.jsonl"
SIGNAL_STATE = ROOT / "data" / "cat_signal_state.json"
ABORTED_RUNS = ROOT / "data" / "aborted_runs.jsonl"
# The Mini's gen cron slots, PT. Used to notice a SHORTFALL — three gens on a
# four-gen day used to read as a perfectly ordinary line in this digest.
GEN_SLOT_HOURS = (4, 8, 12, 16)
HELPER = Path(os.path.expanduser("~/.claude/scripts/post-to-tasks.sh"))
PT = ZoneInfo("America/Los_Angeles")


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

    def req(path, *, headers=None):
        return bsky.request(path, headers=headers, timeout=15)

    try:
        did, jwt = bsky.login(handle, pw, timeout=15)
    except Exception as e:
        return {"error": f"auth failed: {e}"}
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


def _top_drops_week(n: int = 3) -> list[tuple[str, str, float]]:
    """Top archive drops of the last 7 days by bsky engagement.

    Joins post_log drop entries (subject 'our:<file>') with the engagement
    counts reflect.py already knows how to assemble (cleanup snapshots +
    live API for posts still up). Returns [(file, title, score)] descending.
    Feeds Stephen's flag-a-winner loop — see the liked-gens memory.
    """
    from collections import defaultdict
    from reflect import _fetch_live, _load_log_window, _load_snapshots, engagement_score

    drops = [
        e for e in _load_log_window(7)
        if e.get("type") in ("drop", "throwback") and (e.get("subject") or "").startswith("our:")
    ]
    if not drops:
        return []
    uris = list({e["uri"] for e in drops})
    counts = _load_snapshots()
    missing = [u for u in uris if u not in counts]
    if missing:
        counts.update(_fetch_live(missing))

    by_file: dict[str, float] = defaultdict(float)
    for e in drops:
        c = counts.get(e["uri"])
        if c is None:
            continue
        by_file[e["subject"][4:]] += engagement_score(c)

    ranked = sorted(by_file.items(), key=lambda kv: kv[1], reverse=True)[:n]
    out: list[tuple[str, str, float]] = []
    for fname, score in ranked:
        title = fname
        try:
            html = (ARCHIVE_DIR / fname).read_text(errors="ignore")
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if m:
                title = re.sub(r"\s+", " ", m.group(1).strip())
        except Exception:
            pass
        out.append((fname, title, score))
    return out


def _expected_gens_last_24h() -> int:
    """How many scheduled gen slots fall inside the last-24h window."""
    now = datetime.now(tz=PT)
    cutoff = now - timedelta(hours=24)
    n = 0
    day = cutoff.date()
    while day <= now.date():
        for h in GEN_SLOT_HOURS:
            slot = datetime.combine(day, dtime(hour=h)).replace(tzinfo=PT)
            if cutoff <= slot <= now:
                n += 1
        day += timedelta(days=1)
    return n


def _aborted_runs_last_24h() -> list[dict]:
    """Runs that died before generate.py could log anything (see run-on-mini.sh)."""
    if not ABORTED_RUNS.exists():
        return []
    cutoff = datetime.now(tz=PT) - timedelta(hours=24)
    out: list[dict] = []
    for line in ABORTED_RUNS.read_text(errors="ignore").splitlines():
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
        e["_dt"] = dt
        out.append(e)
    return out


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
        if isinstance(e.get("fallback_attempts"), int) and e["fallback_attempts"] >= 1:
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

    # Canon scout — refresh who's actually turning up, retire at most one name
    # that never did, then maybe welcome somebody yesterday invented.
    new_canon = retired_canon = None
    try:
        from canon_scout import run as run_scout, refresh_and_retire
        try:
            retired_canon = refresh_and_retire()
        except Exception as e:
            print(f"[digest] canon retirement errored (non-fatal): {e}", file=sys.stderr)
        new_canon = run_scout()
    except Exception as e:
        print(f"[digest] canon scout errored (non-fatal): {e}", file=sys.stderr)

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

    try:
        expected = _expected_gens_last_24h()
    except Exception:
        expected = 0
    try:
        aborted = _aborted_runs_last_24h()
    except Exception:
        aborted = []

    if count == 0:
        parts.append("⚠️ **no gens in last 24h** — cron may be wedged")
    else:
        short = f" — ⚠️ **{expected} expected**" if expected and count < expected else ""
        parts.append(f"**{count} gens** in last 24h{short}. recent titles:")
        for t in titles:
            parts.append(f"  · {t[:90]}")

    if aborted:
        parts.append("")
        parts.append(f"⚠️ **{len(aborted)} run(s) aborted before generating:**")
        for e in aborted[-4:]:
            when = e["_dt"].strftime("%a %H:%M")
            parts.append(f"  · {when} — {e.get('stage', '?')}: {str(e.get('detail', ''))[:120]}")

    parts.append("")
    if "error" in bsky:
        parts.append(f"⚠️ bsky: {bsky['error']}")
    else:
        parts.append(
            f"**bsky** · followers: {bsky.get('followers','?')} · follows: {bsky.get('follows','?')} · posts: {bsky.get('posts','?')}"
        )
        # The raw pair was printed for 30 nights while the follow loop quietly
        # ran away with it (143 → 431 follows against 47 → 82 followers) and
        # nobody read the trend. Interpreting it here — ratio plus the budget
        # that ratio buys — is what makes the posture legible at a glance.
        try:
            from follow_loop import _daily_budget
            followers, follows = int(bsky["followers"]), int(bsky["follows"])
            budget, ratio = _daily_budget(followers, follows)
            flag = " ⚠️" if ratio >= 5.0 else ""
            parts.append(f"posture: {ratio:.1f}:1 follows/followers{flag} · follow budget {budget}/day")
        except Exception:
            pass
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
            bits.append(f"{health['retried']} gen(s) fell back to single-shot")
        if bits:
            parts.append("")
            parts.append(f"⚠️ **gen health:** {', '.join(bits)} (of {health['logged']} logged)")

    # Weekend rituals draw their text from the drawer that scripts/ritual_prep.py
    # fills Mon–Fri. If it's still empty on the approach to the weekend, they'll
    # have to generate live on the day — which is exactly how all three died five
    # weekends running before anyone noticed. Quiet Mon–Wed (prep has runway),
    # loud Thu onward, when a missing item is a real risk to Saturday.
    try:
        if datetime.now(tz=PT).weekday() >= 3:
            gaps = ritual_cache.missing()
            if gaps:
                parts.append("")
                parts.append(
                    f"⚠️ **ritual drawer:** {len(gaps)} of {len(ritual_cache.ITEMS)} unfilled "
                    f"({', '.join(gaps)}) — weekend rituals will fall back to live "
                    f"generation. `scripts/ritual_prep.py` on the Mini."
                )
    except Exception as e:
        print(f"[digest] ritual drawer check failed (non-fatal): {e}", file=sys.stderr)

    if reflection:
        parts.append("")
        parts.append(f"**reflection:** {reflection}")

    if new_canon or retired_canon:
        parts.append("")
        bits = []
        if new_canon:
            bits.append(f"the universe welcomed _{new_canon}_")
        if retired_canon:
            bits.append(f"_{retired_canon}_ never turned up again and left the roster")
        parts.append(f"**canon:** {' · '.join(bits)} (see data/canon.json)")

    # Sunday extra: the week's top drops by social engagement, so flagging a
    # winner (→ spotlight / liked-gens log) doesn't require a log dive.
    if datetime.now(tz=PT).weekday() == 6:
        try:
            top = _top_drops_week()
        except Exception as e:
            print(f"[digest] top-drops failed (non-fatal): {e}", file=sys.stderr)
            top = []
        if top:
            parts.append("")
            parts.append("**this week's top drops** (bsky engagement):")
            for fname, title, score in top:
                parts.append(f"  · {title[:70]} — {score:.0f} pts · https://outbox.cafe/archive/{fname}")

    text = "\n".join(parts)
    _post_discord(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
