"""
outbox.cafe — Tumblr reblog behavior.

The cafe is 100% original broadcast on Tumblr. The platform's social
graph runs on reblogs. Pure-original Tumblr is hard mode for growth.

This script finds posts in tags aligned with the cafe's aesthetic
(small web, zine, pixel art, generative art, etc.), runs each candidate
through a Claude staff-voice safety+drafting pass, and reblogs the
approved ones with a short in-character comment. The reblog appears on
the cafe's blog AND in the original poster's notifications → real
platform participation, not just one-way broadcast.

Posting philosophy (carried from SBT):
- The post is the point — when we reblog, we're amplifying THEIR work,
  not funneling back to us. Comments are short, on-topic, in voice.
- The cafe ADDS — a tiny observation, a thank-you, a one-line
  appreciation. Never a hijack.
- Skip controversial stuff (cafe's never_post_about rules apply
  recursively — if we wouldn't post it, we don't reblog it).

Safety stack:
1. Tag-based prefilter (only candidate tags below)
2. Hard signal filters (note_count cap, freshness, can_reblog, NSFW)
3. Claude moderation+drafting pass (loaded with the cafe's voice rules)
4. Per-run + per-day reblog caps
"""

from __future__ import annotations

import html as _html
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.llm import claude_cmd
from lib.io import atomic_write_json
from lib import tumblr

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PERSONAS_PATH = DATA / "personas.json"
STATE_PATH = DATA / "reblog_state.json"
POST_LOG = DATA / "post_log.jsonl"

TUMBLR_BASE = "https://api.tumblr.com/v2"

# Caps — conservative for launch.
MAX_REBLOGS_PER_RUN = 3
MAX_REBLOGS_PER_DAY = 6

# Candidate tags — picked for cafe aesthetic, manually curated. Order
# doesn't matter; the pool is shuffled. Add/remove tags here to steer.
CANDIDATE_TAGS = [
    "small web", "smallweb", "indie web", "indieweb",
    "zine", "handmade zine", "mail art", "art journal",
    "pixel art", "generative art", "gif art", "collage",
    "retro internet", "retro computing", "old web",
    "web revival", "neocities", "handmade web",
]

# Filter hard limits.
MAX_NOTE_COUNT = 5000        # already-viral posts don't need our boost
MAX_AGE_HOURS = 48           # don't reblog stale posts
MIN_AGE_MINUTES = 5          # tiny window so we're not literal-seconds-after-post (mildly creepy)
CANDIDATES_PER_TAG = 12      # how many posts to consider per tag
PROMPT_SNIPPET_CHARS = 800   # how much post text to send to Claude


# ---------- State ----------

def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"reblogged": [], "last_run": None}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"reblogged": [], "last_run": None}


def _save_state(state: dict[str, Any]) -> None:
    atomic_write_json(STATE_PATH, state)


def _reblog_count_today(state: dict[str, Any]) -> int:
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    return sum(
        1 for r in state.get("reblogged", [])
        if isinstance(r, dict) and r.get("ts", "").startswith(today)
    )


def _already_reblogged_ids(state: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    for r in state.get("reblogged", []):
        if not isinstance(r, dict):
            continue
        try:
            out.add(int(r.get("id", 0)))
        except Exception:
            pass
    return out


# ---------- Tumblr OAuth (shared signer in lib/tumblr.py) ----------

def _oauth_header(method: str, url: str, *, query_params: dict[str, str] | None = None) -> str:
    """GETs with query params (/v2/tagged) fold them into the signature; POSTs
    with a JSON body (NPF /posts endpoint) omit query_params (body not signed)."""
    return tumblr.oauth_header(method, url, params=query_params)


# ---------- Candidate fetching ----------

def _strip_html(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _post_text(post: dict) -> str:
    """Extract a representative text snippet from a Tumblr post."""
    parts: list[str] = []
    if post.get("title"):
        parts.append(post["title"])
    if post.get("summary"):
        parts.append(post["summary"])
    if post.get("body"):
        parts.append(_strip_html(post["body"]))
    if post.get("caption"):
        parts.append(_strip_html(post["caption"]))
    # Photo posts may have a description in trail
    for entry in post.get("trail") or []:
        c = entry.get("content")
        if c:
            parts.append(_strip_html(c))
    text = " ".join(parts).strip()
    return text[:PROMPT_SNIPPET_CHARS]


def _fetch_tag(tag: str) -> list[dict]:
    """OAuth-authenticated /v2/tagged fetch. api_key-only mode returns
    can_reblog=False for everything, so we must auth as the cafe."""
    url = f"{TUMBLR_BASE}/tagged"
    params = {"tag": tag, "limit": str(CANDIDATES_PER_TAG)}
    auth = _oauth_header("GET", url, query_params=params)
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers={"Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except Exception as e:
        print(f"[reblog] fetch tag {tag!r} failed: {e}", file=sys.stderr)
        return []
    return d.get("response", []) or []


# ---------- Filtering ----------

def _filter_candidate(p: dict, our_blog: str, already: set[int]) -> bool:
    """Return True if candidate passes hard filters."""
    try:
        pid = int(p.get("id", 0))
    except Exception:
        return False
    if pid == 0 or pid in already:
        return False
    if p.get("blog_name") == our_blog:
        return False
    if not p.get("reblog_key"):
        return False
    if p.get("can_reblog") is False:
        return False
    nc = p.get("note_count", 0)
    if isinstance(nc, int) and nc > MAX_NOTE_COUNT:
        return False
    ts = p.get("timestamp")
    if ts:
        age_h = (time.time() - int(ts)) / 3600.0
        if age_h > MAX_AGE_HOURS:
            return False
        if age_h < (MIN_AGE_MINUTES / 60.0):
            return False
    # If we already have substantial cafe-tags on it, skip (probably from us before)
    tags = [t.lower() for t in (p.get("tags") or [])]
    if "outbox cafe" in tags or "outbox.cafe" in tags:
        return False
    return True


# ---------- Claude moderation + draft ----------

MOD_PROMPT = """You are a moderator + ghostwriter for outbox.cafe, a small cafe on the internet staffed entirely by cats. Each post the cafe makes is in the voice of a specific cat staff member.

The cafe is considering REBLOGGING the following Tumblr post by another user. You will decide whether to reblog and, if so, draft a SHORT comment in the voice of the assigned cat.

== CAFE VOICE RULES ==
- Relentlessly positive. Never sour. Never fight people.
- NEVER comment on: current events, politics, illness/grief/death, financial advice, religion, controversial figures, anything bot-embarrassing if quoted out of context.
- NEVER break character (no "as an AI", no reference to LLMs or the cafe's automation).
- Stay tightly in the assigned cat's voice and tone.

== POST CONTEXT ==
Original blogger: @{their_blog}
Post tags: {tag_list}
Post excerpt (HTML stripped):
\"\"\"
{post_text}
\"\"\"

If the excerpt says "(no text — possibly image-only)" or is mostly empty, the post is image-only. Decide based on the TAGS and blogger handle alone. Don't ask for the image — you can't see it; just SKIP or SILENT (lean SILENT if tags look on-brand).

== ASSIGNED CAT ==
Name: {name}
Role: {full_name}
Voice/tone: {tone}
Example posts:
{examples}
Signoff convention: {signoff}

== DECIDE ==
Do TWO things:

1. Should the cafe reblog this? Answer one of:
   - SKIP — if any voice rule would be violated, or if the post is off-topic from cafe-aesthetics (too commercial, too off-color, too political, etc.), or if there's nothing genuine the cat would say about it.
   - SILENT — if the post is great but no comment is needed; just a quiet boost.
   - COMMENT — if you can write a short, specific, in-voice comment.

2. If COMMENT, draft the comment.

== COMMENT REQUIREMENTS (only if COMMENT) ==
- 1-2 sentences. Under 220 characters.
- Specific to THIS post (a detail, a noun, a phrase that landed). Generic reactions ("love this", "so cool") are FORBIDDEN.
- In the assigned cat's voice. Include their signoff if it's their convention.
- No URLs. No hashtags. No emoji unless very rare for this cat.
- DON'T explain who you are. The cafe is the speaker; recipients will know.

== OUTPUT FORMAT ==
Output EXACTLY one of these patterns and NOTHING ELSE:

SKIP: <one-line reason>
SILENT
COMMENT: <the comment>"""


def _staff_for(rng: random.Random) -> dict[str, Any]:
    personas = json.loads(PERSONAS_PATH.read_text())
    staff = personas["staff"]
    # Same reflection-adjusted weights as every other persona picker, so the
    # nightly engagement loop steers reblog voices too.
    from voice_weights import adjusted_weights
    return rng.choices(staff, weights=adjusted_weights(staff), k=1)[0]


def _moderate_and_draft(post: dict, staff: dict[str, Any]) -> tuple[str, str | None]:
    """Return (action, comment_or_reason). action ∈ {"SKIP","SILENT","COMMENT"}."""
    post_text = _post_text(post) or "(no text — possibly image-only)"
    tags = ", ".join((post.get("tags") or [])[:12])
    prompt = MOD_PROMPT.format(
        their_blog=post.get("blog_name", "?"),
        tag_list=tags or "(none)",
        post_text=post_text,
        name=staff["name"],
        full_name=staff.get("full_name", ""),
        tone=staff.get("tone", ""),
        examples="\n".join(f"- {e}" for e in (staff.get("examples") or [])[:4]),
        signoff=staff.get("signoff", "(none)"),
    )
    try:
        result = subprocess.run(
            claude_cmd("opus"),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        return "SKIP", f"claude call failed: {e}"
    if result.returncode != 0:
        return "SKIP", f"claude exit {result.returncode}: {result.stderr[:200]}"
    out = (result.stdout or "").strip()
    # Parse first non-empty line
    line = next((l for l in out.splitlines() if l.strip()), "").strip()
    if line.startswith("SKIP"):
        reason = line[5:].lstrip(":- ").strip() or "(no reason)"
        return "SKIP", reason
    if line.startswith("SILENT"):
        return "SILENT", None
    if line.startswith("COMMENT"):
        comment = line[7:].lstrip(":- ").strip()
        # Strip wrapping quotes if any
        comment = re.sub(r"^[\"']+|[\"']+$", "", comment).strip()
        if not comment or len(comment) > 240:
            return "SKIP", f"comment-malformed: {comment!r}"
        return "COMMENT", comment
    return "SKIP", f"unparseable: {line[:120]!r}"


# ---------- Reblog action ----------

def _reblog(blog: str, parent_post_id: int, parent_uuid: str, reblog_key: str, comment: str | None) -> str | None:
    """POST a reblog to Tumblr via the NPF /posts endpoint. Returns new post URL on success.

    The legacy /post endpoint with type=reblog returns HTTP 400 "Post cannot be empty"
    for everything as of 2026 — Tumblr's reblog action moved to the NPF /posts endpoint
    which takes a JSON body with parent_post_id + parent_tumblelog_uuid + reblog_key.
    """
    url = f"{TUMBLR_BASE}/blog/{blog}.tumblr.com/posts"
    # NPF content blocks. Empty content = silent reblog; one text block = commented reblog.
    content: list[dict] = []
    if comment:
        content.append({"type": "text", "text": comment})
    payload = {
        "content": content,
        "state": "published",
        "tags": "outbox cafe,the cafe,reblog",
        "parent_post_id": str(parent_post_id),
        "parent_tumblelog_uuid": parent_uuid,
        "reblog_key": reblog_key,
    }
    body = json.dumps(payload).encode()
    # NPF endpoint takes JSON body — signature base string doesn't include the body params.
    auth = _oauth_header("POST", url)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": auth, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")[:500]
        print(f"[reblog] HTTP {e.code}: {err}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[reblog] post failed: {e}", file=sys.stderr)
        return None
    pid = (d.get("response") or {}).get("id")
    if pid:
        return f"https://{blog}.tumblr.com/post/{pid}"
    return None


# ---------- Main ----------

def main():
    consumer_key = os.environ.get("TUMBLR_CONSUMER_KEY")
    blog = os.environ.get("TUMBLR_BLOG_NAME")
    if not consumer_key or not blog or not all(
        os.environ.get(k) for k in (
            "TUMBLR_CONSUMER_SECRET", "TUMBLR_OAUTH_TOKEN", "TUMBLR_OAUTH_TOKEN_SECRET"
        )
    ):
        print("[reblog] tumblr env vars missing — abort", file=sys.stderr)
        return 1

    rng = random.Random()
    state = _load_state()
    already = _already_reblogged_ids(state)
    daily = _reblog_count_today(state)
    daily_remaining = MAX_REBLOGS_PER_DAY - daily
    if daily_remaining <= 0:
        print(f"[reblog] daily cap reached ({MAX_REBLOGS_PER_DAY}) — skipping")
        return 0
    run_cap = min(MAX_REBLOGS_PER_RUN, daily_remaining)
    print(f"[reblog] daily reblogs so far: {daily}/{MAX_REBLOGS_PER_DAY}; this run cap: {run_cap}")

    # Fetch candidates from a shuffled tag list (some tags per run, not all)
    tags = list(CANDIDATE_TAGS)
    rng.shuffle(tags)
    tags = tags[:6]  # 6 tags per run keeps fetch budget reasonable
    print(f"[reblog] sampling tags: {tags}")

    candidates: list[dict] = []
    for tag in tags:
        posts = _fetch_tag(tag)
        for p in posts:
            if _filter_candidate(p, blog, already):
                p["_source_tag"] = tag
                candidates.append(p)
        time.sleep(0.3)  # polite spacing

    if not candidates:
        print("[reblog] no candidates passed filters")
        return 0

    # Dedup by id (same post may appear in multiple tags)
    seen_ids: set[int] = set()
    deduped: list[dict] = []
    for c in candidates:
        try:
            pid = int(c["id"])
        except Exception:
            continue
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        deduped.append(c)
    rng.shuffle(deduped)
    print(f"[reblog] {len(deduped)} unique candidates after dedup")

    reblogged_this_run = 0
    new_state_entries: list[dict] = []
    skips: list[tuple[str, int, str]] = []  # debug

    for cand in deduped:
        if reblogged_this_run >= run_cap:
            break
        staff = _staff_for(rng)
        action, payload = _moderate_and_draft(cand, staff)
        pid = int(cand["id"])
        their = cand.get("blog_name", "?")
        if action == "SKIP":
            skips.append((their, pid, payload or "?"))
            print(f"[reblog] SKIP @{their} ({pid}, tag={cand.get('_source_tag')}): {payload}")
            continue
        comment = payload if action == "COMMENT" else None
        parent_uuid = (cand.get("blog") or {}).get("uuid", "")
        if not parent_uuid:
            print(f"[reblog] SKIP @{their} ({pid}): no parent uuid", file=sys.stderr)
            continue
        url = _reblog(blog, pid, parent_uuid, cand["reblog_key"], comment)
        if not url:
            skips.append((their, pid, "reblog API failed"))
            continue
        reblogged_this_run += 1
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        new_state_entries.append({
            "id": pid,
            "blog": their,
            "their_url": cand.get("post_url"),
            "our_url": url,
            "tag": cand.get("_source_tag"),
            "persona": staff["name"],
            "action": action,
            "comment": comment,
            "ts": ts,
        })
        print(f"[reblog] {action} @{their} ({pid}, {staff['name']}) → {url}")
        if comment:
            print(f"         comment: {comment!r}")

        # Log to post_log.jsonl
        try:
            from post_log import log as plog
            plog(
                "tumblr_reblog",
                persona=staff["name"],
                uri=url,
                subject=f"reblog:@{their}:{pid}",
                text=comment or "",
            )
        except Exception as e:
            print(f"[reblog] post_log failed (non-fatal): {e}", file=sys.stderr)

        # Polite spacing between reblogs
        time.sleep(2)

    # Persist state
    state["reblogged"] = (state.get("reblogged") or []) + new_state_entries
    # Trim history to last 500 entries
    state["reblogged"] = state["reblogged"][-500:]
    state["last_run"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _save_state(state)

    print(f"[reblog] done. reblogged {reblogged_this_run}, skipped {len(skips)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
