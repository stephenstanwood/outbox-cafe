"""Reflection pass: look at what the cafe posted recently, see what got
engagement, and write `data/voice_weights.json` so the next 24h of posting
biases (within bounds) toward voices and topics that landed.

Runs once a night. Best-effort: bsky API failures are non-fatal, the file is
just rewritten with whatever sample we got. The persona pickers default to
1.0 multipliers when the file is missing or partial.

Voice guardrails:
- Per-persona multiplier clamped to [0.5, 1.5] so no cat goes silent and no
  cat dominates.
- Personas need >= MIN_POSTS_PER_PERSONA in the window to be adjusted at all.
- Sample-size floor: if total qualifying posts < MIN_SAMPLE, just write 1.0s.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
POST_LOG = ROOT / "data" / "post_log.jsonl"
ENGAGEMENT_SNAPSHOT = ROOT / "data" / "post_engagement.jsonl"
WEIGHTS_PATH = ROOT / "data" / "voice_weights.json"
PERSONAS_PATH = ROOT / "data" / "personas.json"

BSKY_BASE = "https://bsky.social/xrpc"
PUBLIC_BSKY = "https://public.api.bsky.app/xrpc"

LOOKBACK_DAYS = 14
MIN_POSTS_PER_PERSONA = 3
MIN_SAMPLE = 12

PERSONA_FLOOR = 0.5
PERSONA_CAP = 1.5


# ---- engagement score -----------------------------------------------------

def engagement_score(counts: dict[str, int]) -> float:
    """Replies and quotes (real conversation) weigh more than likes (passive)."""
    return (
        counts.get("like_count", 0)
        + 2.0 * counts.get("reply_count", 0)
        + 1.5 * counts.get("repost_count", 0)
        + 2.0 * counts.get("quote_count", 0)
    )


# ---- bsky fetch ----------------------------------------------------------

def _fetch_live(uris: list[str], chunk: int = 10, timeout: int = 30) -> dict[str, dict]:
    """Public getPosts for posts still live on bsky. Bluesky 400s a whole batch
    if any URI in it has been deleted, so we chunk small and fall back to per-URI
    on failure rather than losing the whole batch."""
    out: dict[str, dict] = {}

    def _one(uri: str) -> None:
        try:
            url = f"{PUBLIC_BSKY}/app.bsky.feed.getPosts?uris={urllib.request.quote(uri, safe='')}"
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.load(r)
        except Exception:
            return  # deleted, network blip — skip silently
        for p in data.get("posts", []):
            out[p["uri"]] = {
                "like_count": p.get("likeCount", 0),
                "reply_count": p.get("replyCount", 0),
                "repost_count": p.get("repostCount", 0),
                "quote_count": p.get("quoteCount", 0),
            }

    for i in range(0, len(uris), chunk):
        batch = uris[i : i + chunk]
        params = "&".join(f"uris={urllib.request.quote(u, safe='')}" for u in batch)
        url = f"{PUBLIC_BSKY}/app.bsky.feed.getPosts?{params}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.load(r)
            for p in data.get("posts", []):
                out[p["uri"]] = {
                    "like_count": p.get("likeCount", 0),
                    "reply_count": p.get("replyCount", 0),
                    "repost_count": p.get("repostCount", 0),
                    "quote_count": p.get("quoteCount", 0),
                }
        except Exception:
            # One of the URIs is likely deleted; retry individually so the rest survive.
            for u in batch:
                _one(u)
    return out


def _load_snapshots() -> dict[str, dict]:
    """Engagement counts captured by cleanup_bsky just before deletion. Authoritative
    for old posts. If the same URI appears multiple times (shouldn't, but defensive),
    last-write-wins."""
    out: dict[str, dict] = {}
    if not ENGAGEMENT_SNAPSHOT.exists():
        return out
    for line in ENGAGEMENT_SNAPSHOT.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        uri = e.get("uri")
        if not uri:
            continue
        out[uri] = {
            "like_count": e.get("like_count", 0),
            "reply_count": e.get("reply_count", 0),
            "repost_count": e.get("repost_count", 0),
            "quote_count": e.get("quote_count", 0),
        }
    return out


# ---- post log loading ----------------------------------------------------

def _load_log_window(days: int) -> list[dict]:
    if not POST_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for line in POST_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        ts_raw = entry.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        if not entry.get("uri"):
            continue
        out.append(entry)
    return out


# ---- weight computation --------------------------------------------------

def _persona_multipliers(entries: list[dict], counts: dict[str, dict]) -> dict[str, dict]:
    """Per-persona multiplier = avg(score) / avg(score across all qualifying posts), clamped."""
    by_persona: dict[str, list[float]] = defaultdict(list)
    for e in entries:
        score = engagement_score(counts.get(e["uri"], {}))
        persona = e.get("persona")
        if not persona:
            continue
        by_persona[persona].append(score)

    all_scores = [s for scores in by_persona.values() for s in scores]
    if len(all_scores) < MIN_SAMPLE:
        # Not enough data yet — return 1.0 for everyone we've seen.
        return {p: {"multiplier": 1.0, "avg_score": round(sum(v) / max(len(v), 1), 2), "posts": len(v)}
                for p, v in by_persona.items()}

    global_avg = sum(all_scores) / len(all_scores)
    if global_avg <= 0:
        return {p: {"multiplier": 1.0, "avg_score": 0.0, "posts": len(v)}
                for p, v in by_persona.items()}

    out: dict[str, dict] = {}
    for persona, scores in by_persona.items():
        avg = sum(scores) / len(scores)
        if len(scores) < MIN_POSTS_PER_PERSONA:
            mult = 1.0  # don't adjust off too-small samples
        else:
            mult = max(PERSONA_FLOOR, min(PERSONA_CAP, avg / global_avg))
        out[persona] = {"multiplier": round(mult, 3), "avg_score": round(avg, 2), "posts": len(scores)}
    return out


def _type_breakdown(entries: list[dict], counts: dict[str, dict]) -> dict[str, dict]:
    by_type: dict[str, list[float]] = defaultdict(list)
    for e in entries:
        score = engagement_score(counts.get(e["uri"], {}))
        by_type[e.get("type", "unknown")].append(score)
    out = {}
    for t, scores in by_type.items():
        avg = sum(scores) / len(scores) if scores else 0
        out[t] = {"avg_score": round(avg, 2), "posts": len(scores)}
    return out


def _wild_topics_warm(entries: list[dict], counts: dict[str, dict], limit: int = 8) -> list[dict]:
    """Wild replies where the target actually engaged (reply_count >= 1) or got real likes."""
    warm = []
    for e in entries:
        if e.get("type") != "wild":
            continue
        topic = e.get("topic")
        if not topic:
            continue
        c = counts.get(e["uri"], {})
        score = engagement_score(c)
        # 'real engagement' on a wild reply = the target replied back, or a few likes
        if c.get("reply_count", 0) >= 1 or score >= 2:
            warm.append({"topic": topic, "score": round(score, 1), "reply_count": c.get("reply_count", 0)})
    warm.sort(key=lambda x: x["score"], reverse=True)
    # de-dupe by topic, keeping highest-scoring instance
    seen: set[str] = set()
    deduped: list[dict] = []
    for w in warm:
        if w["topic"] in seen:
            continue
        seen.add(w["topic"])
        deduped.append(w)
    return deduped[:limit]


def _summary(persona_mults: dict[str, dict], type_break: dict[str, dict], warm_topics: list[dict], sample: int) -> str:
    if sample < MIN_SAMPLE:
        return f"sample too small ({sample} posts) — running with neutral 1.0× weights"
    leaders = sorted(persona_mults.items(), key=lambda x: x[1]["multiplier"], reverse=True)
    top = [f"{name} ({d['multiplier']}×, n={d['posts']})" for name, d in leaders[:3] if d["multiplier"] > 1.0]
    bottom = [f"{name} ({d['multiplier']}×)" for name, d in leaders[-2:] if d["multiplier"] < 1.0]
    type_line = " · ".join(f"{t}:{d['avg_score']}" for t, d in sorted(type_break.items(), key=lambda x: x[1]["avg_score"], reverse=True))
    topic_line = ", ".join(t["topic"] for t in warm_topics[:5]) if warm_topics else "—"
    parts = []
    if top:
        parts.append("up: " + ", ".join(top))
    if bottom:
        parts.append("down: " + ", ".join(bottom))
    parts.append("avg by type: " + type_line)
    parts.append("warm topics: " + topic_line)
    return " | ".join(parts)


# ---- main ----------------------------------------------------------------

def run() -> int:
    entries = _load_log_window(LOOKBACK_DAYS)
    if not entries:
        print("[reflect] no post log entries in window — nothing to reflect on")
        return 0

    uris = list({e["uri"] for e in entries})
    print(f"[reflect] reflecting on {len(uris)} posts over {LOOKBACK_DAYS}d")

    # Snapshots are authoritative for old posts (cleanup_bsky freezes counts
    # before deletion). Live API fills in posts still on bsky (last ~36h).
    snapshots = _load_snapshots()
    needs_live = [u for u in uris if u not in snapshots]
    live = _fetch_live(needs_live) if needs_live else {}
    counts = {**snapshots, **live}
    print(f"[reflect] counts: {len(snapshots)} snapshot + {len(live)} live = {len(counts)}/{len(uris)}")

    # Only keep entries we got counts for — missing posts may have been deleted by cleanup_bsky
    qualifying = [e for e in entries if e["uri"] in counts]

    persona_mults = _persona_multipliers(qualifying, counts)
    type_break = _type_breakdown(qualifying, counts)
    warm_topics = _wild_topics_warm(qualifying, counts)
    summary = _summary(persona_mults, type_break, warm_topics, len(qualifying))

    output = {
        "updated_ts": datetime.now(timezone.utc).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "sample_size": len(qualifying),
        "persona": persona_mults,
        "type": type_break,
        "wild_topics_warm": warm_topics,
        "summary": summary,
    }

    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(output, indent=2))
    print(f"[reflect] wrote {WEIGHTS_PATH.name}")
    print(f"[reflect] {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
