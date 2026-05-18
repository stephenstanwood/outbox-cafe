"""Engagement loop for outbox.cafe on Bluesky.

Runs on a cron every ~15 min. Polls notifications and acts:
- mention of the cafe → reply in random staff (cat) voice
- reply to one of our posts → reply if the source isn't about filtered topics
- quote-post of one of our posts → like the quote
- new follower → noted, no action (for now; avoids feeling thirsty)
- likes / reposts → no action

Every reply goes through Claude with a strict moderation prompt: if the source
touches politics/current events/grief/finance/religion/controversy, the model
returns NOPOST and we skip the reply entirely.

State lives in data/engage_state.json (gitignored). Tracks the last-processed
notification timestamp + a rolling set of handled URIs to avoid double-replies.
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
THUMBS_DIR = ARCHIVE_DIR / "thumbs"
PERSONAS_PATH = ROOT / "data" / "personas.json"
STATE_PATH = ROOT / "data" / "engage_state.json"
WILD_STATE_PATH = ROOT / "data" / "wild_state.json"
THROWBACK_STATE_PATH = ROOT / "data" / "throwback_state.json"
BSKY_BASE = "https://bsky.social/xrpc"
MAX_REPLIES_PER_RUN = 10  # safety cap so a backlog doesn't fire 50 replies at once
HANDLED_URI_CAP = 500
AMBIENT_PROBABILITY = 0.04  # at every-15-min cron firings = ~3-4 ambient posts/day

# In-the-wild engagement — reply to strangers' posts about small-web / weird-internet
# topics. Cafe never pitches its own site; just leaves a small in-character observation.
WILD_RUN_PROBABILITY = 0.08   # per */15 firing → expected ~7-8 attempts/day, hit cap most days
WILD_DAILY_CAP = 5            # max wild replies in any 24h window
WILD_REPLIED_HISTORY_CAP = 200
WILD_RECENT_HANDLE_WINDOW = 7 * 24 * 3600  # don't reply to the same handle twice in 7 days
# Curated topic list is the FALLBACK only. Default path: Claude rolls a fresh search query
# each run (see _roll_wild_topic). Same anti-static-list pattern we use for spec rolling.
# Throwback posts — pull a random gen from >N days ago and re-surface it in cat voice.
# Cron firings = 96/day; probability 0.012 ≈ ~1.15 throwbacks/day on average.
THROWBACK_PROBABILITY = 0.012
THROWBACK_MIN_AGE_DAYS = 7
THROWBACK_RECENT_CAP = 100   # don't throwback any of the last 100 we already resurfaced

WILD_SEARCH_TOPICS = [
    "neocities",
    "small web",
    "weird web",
    "weird internet",
    "old web",
    "indie web",
    "personal site",
    "geocities",
    "zine",
    "web art",
    "homepage",
    "smallweb",
    "weirdweb",
    "html zine",
]


def _load_personas() -> dict[str, Any]:
    return json.loads(PERSONAS_PATH.read_text())


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"last_indexedAt": None, "handled_uris": []}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"last_indexedAt": None, "handled_uris": []}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _bsky(path: str, *, data=None, headers=None, method=None) -> dict:
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if isinstance(data, (dict, list)):
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, bytes):
        body = data
    req = urllib.request.Request(f"{BSKY_BASE}{path}", data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _auth() -> tuple[str, str]:
    handle = os.environ["BSKY_HANDLE"]
    pw = os.environ["BSKY_APP_PASSWORD"]
    sess = _bsky(
        "/com.atproto.server.createSession",
        data={"identifier": handle, "password": pw},
        method="POST",
    )
    return sess["did"], sess["accessJwt"]


REPLY_PROMPT = """You are {name} ({full_name}), staff at outbox.cafe — a constantly-evolving weird/retro corner of the internet, run by cats. The cafe posts a new artifact at the top of every hour. Someone has {action} on Bluesky, and you might respond.

ABOUT YOU
{species}

YOUR VOICE
{tone}

EXAMPLES OF YOUR PAST POSTS (study the rhythm, length, voice — match this exactly)
{examples}

HOUSE RULES — APPLY TO EVERY REPLY, NO EXCEPTIONS
- Relentlessly positive. Never sour.
- Never fight, never correct, never lecture. If someone is rude or critical, respond as if they paid you a compliment.
- Never reference AI, bots, models, or that this is automated.
- Never post about: politics, current events, real death/grief/illness, financial advice, religion (specific), controversial public figures, anything mean. Cat-style observation about weather or sunbeams is always safe.

MODERATION GATE — read the source carefully
If the source post touches ANY of the filtered topics above, OR if a reply would draw the cafe into a controversy, output ONLY the single token NOPOST (no punctuation, no quotes, no explanation). When in doubt, output NOPOST.

THE SOURCE POST
From: @{their_handle}
Their post: {source_text}

OUR POST THEY'RE RESPONDING TO (if this is a reply to us; if mention, this is blank)
{our_context}

YOUR TASK
If safe to respond: write a brief reply (under 220 characters) in your voice. End with your signoff exactly as written: {signoff!r} (or no signoff if it's empty). Match your typical capitalization, punctuation, and rhythm.

Otherwise output the single token NOPOST.

OUTPUT FORMAT
- Either: NOPOST
- Or: the reply text alone — no preamble, no quotes around it, no explanation.
"""


def _generate_reply(
    staff: dict[str, Any],
    action: str,
    their_handle: str,
    source_text: str,
    our_context: str = "",
) -> str | None:
    """Have Claude write the reply in staff's voice. Returns None if NOPOST or failure."""
    prompt = REPLY_PROMPT.format(
        name=staff["name"],
        full_name=staff["full_name"],
        species=staff.get("species", "(unspecified)"),
        tone=staff["tone"],
        examples="\n\n".join(staff["examples"]),
        action=action,
        their_handle=their_handle,
        source_text=source_text or "(no text)",
        our_context=our_context or "(N/A)",
        signoff=staff.get("signoff", ""),
    )
    try:
        result = subprocess.run(
            ["claude", "--print", "--tools", "", "--permission-mode", "plan", "--model", "haiku"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        print(f"[engage] claude subprocess failed: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"[engage] claude exit {result.returncode}: {result.stderr[:200]}", file=sys.stderr)
        return None
    text = (result.stdout or "").strip()
    if "NOPOST" in text.upper()[:40]:
        return None
    text = re.sub(r"^```[a-z]*\s*", "", text).strip()
    text = re.sub(r"\s*```\s*$", "", text).strip()
    if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
        text = text[1:-1].strip()
    if not text:
        return None
    return text


def _create_reply(
    did: str,
    jwt: str,
    text: str,
    parent_uri: str,
    parent_cid: str,
    root_uri: str | None = None,
    root_cid: str | None = None,
) -> dict:
    root_uri = root_uri or parent_uri
    root_cid = root_cid or parent_cid
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
        "reply": {
            "root": {"uri": root_uri, "cid": root_cid},
            "parent": {"uri": parent_uri, "cid": parent_cid},
        },
    }
    return _bsky(
        "/com.atproto.repo.createRecord",
        data={
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": record,
        },
        headers={"Authorization": f"Bearer {jwt}"},
        method="POST",
    )


def _create_like(did: str, jwt: str, subject_uri: str, subject_cid: str) -> dict:
    record = {
        "$type": "app.bsky.feed.like",
        "subject": {"uri": subject_uri, "cid": subject_cid},
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return _bsky(
        "/com.atproto.repo.createRecord",
        data={
            "repo": did,
            "collection": "app.bsky.feed.like",
            "record": record,
        },
        headers={"Authorization": f"Bearer {jwt}"},
        method="POST",
    )


AMBIENT_PROMPT = """You are {name} ({full_name}), staff at outbox.cafe — a constantly-evolving weird/retro corner of the internet, run by cats. You're posting a small "ambient" observation on Bluesky — just a normal between-drop post, NOT about any specific new piece.

ABOUT YOU
{species}

YOUR VOICE
{tone}

TOPICS YOU TEND TO OBSERVE
{topics}

EXAMPLES OF YOUR PAST POSTS (study rhythm/length/voice — match exactly)
{examples}

HOUSE RULES — APPLY TO EVERY POST
- Relentlessly positive. Never sour.
- Never reference AI, bots, models, or that this is automated.
- Never post about politics, current events, real death/grief/illness, financial advice, religion, public figures, anything controversial.
- This is an ambient observation, NOT a drop announcement — do NOT mention "today's posting" or link out. Just one small slice of cafe life.

YOUR TASK
Write a single short observation in your voice (under 220 characters). End with your signoff exactly as written: {signoff!r} (or no signoff if empty). Use your typical capitalization, punctuation, and rhythm.

OUTPUT THE POST TEXT ONLY. No preamble, no quotes around it, no explanation.
"""


def _generate_ambient(staff: dict[str, Any]) -> str | None:
    prompt = AMBIENT_PROMPT.format(
        name=staff["name"],
        full_name=staff["full_name"],
        species=staff.get("species", "(unspecified)"),
        tone=staff["tone"],
        topics="\n".join(f"- {t}" for t in staff["topics"]),
        examples="\n\n".join(staff["examples"]),
        signoff=staff.get("signoff", ""),
    )
    try:
        result = subprocess.run(
            ["claude", "--print", "--tools", "", "--permission-mode", "plan", "--model", "haiku"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        print(f"[engage/ambient] claude failed: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"[engage/ambient] claude exit {result.returncode}", file=sys.stderr)
        return None
    text = (result.stdout or "").strip()
    text = re.sub(r"^```[a-z]*\s*", "", text).strip()
    text = re.sub(r"\s*```\s*$", "", text).strip()
    if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
        text = text[1:-1].strip()
    return text or None


def _create_plain_post(did: str, jwt: str, text: str) -> dict:
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
    }
    return _bsky(
        "/com.atproto.repo.createRecord",
        data={
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": record,
        },
        headers={"Authorization": f"Bearer {jwt}"},
        method="POST",
    )


def _maybe_ambient_post(
    did: str,
    jwt: str,
    staff_pool: list,
    weights: list,
    rng: random.Random,
) -> bool:
    """Roll the dice; if hit, post a between-drop staff observation. Returns True if posted."""
    if rng.random() > AMBIENT_PROBABILITY:
        return False
    staff = rng.choices(staff_pool, weights=weights, k=1)[0]
    text = _generate_ambient(staff)
    if not text:
        return False
    try:
        resp = _create_plain_post(did, jwt, text)
        print(f"[engage/ambient] posted as {staff['name']}: {resp.get('uri','?')}")
        try:
            from post_log import log as post_log
            post_log("ambient", persona=staff["name"], uri=resp.get("uri"), text=text)
        except Exception:
            pass
        return True
    except urllib.error.HTTPError as e:
        print(f"[engage/ambient] HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[engage/ambient] post failed: {e}", file=sys.stderr)
    return False


WILD_REPLY_PROMPT = """You are {name} ({full_name}), staff at outbox.cafe — a constantly-evolving weird/retro corner of the internet, run by cats. You're scrolling Bluesky and noticed a stranger's post you might gently react to. This is NOT someone who mentioned the cafe — they don't know about us. Your reply should be a small, sincere observation in your voice — NEVER a pitch, never promotional, never linking out.

ABOUT YOU
{species}

YOUR VOICE
{tone}

YOUR PAST POSTS (study rhythm, length, voice — match exactly)
{examples}

HOUSE RULES — APPLY TO EVERY REPLY
- Relentlessly positive. Never sour, never corrective, never sarcastic.
- Never reference AI, bots, models, or that the cafe is automated.
- DO NOT promote outbox.cafe. No URL, no "we have an archive," no "come visit." Just one small in-character remark.
- Never post about: politics, current events, real death/grief/illness, financial advice, religion (specific), controversial public figures, anything mean.
- Don't reply with a question that demands a response. A gentle observation is better than starting a conversation.
- Be a cat at the next table making a small remark — not a brand account.

MODERATION GATE — read the source carefully
If ANY of these apply, output ONLY the single token NOPOST:
- The source touches a filtered topic (politics, news, grief, illness, finance, religion, controversy).
- A reply would feel like spam, brand-account energy, or unsolicited marketing.
- The post is a specific technical question we can't actually answer.
- The post is part of a beef, drama, or pile-on.
- The post is melancholy/heavy in a way that a cat-cafe quip would feel tone-deaf.
- The post is itself a reply to something else (we want top-level posts only — already filtered, but double-check).
When in doubt, NOPOST.

THE POST YOU'RE CONSIDERING
From: @{their_handle}
Their post: {source_text}

YOUR TASK
If safe and well-suited: write a brief reply (under 200 chars) in your voice. End with your signoff exactly as written: {signoff!r} (or no signoff if empty). Use your typical capitalization, punctuation, and rhythm.

Otherwise: NOPOST.

OUTPUT FORMAT
- Either: NOPOST
- Or: the reply text alone — no preamble, no quotes around it, no explanation.
"""


WILD_TOPIC_ROLL_PROMPT = """You are picking a single Bluesky search query for outbox.cafe — a cat-staffed corner of the weird/retro/small/old/handmade web — to use as a way to find a stranger's post we might gently reply to. The goal is to land on adjacent communities the cafe would love: people who make their own personal sites, neocities, zines, fan pages, hobby blogs, generative art, web1.0/2.0 nostalgia, the small-internet movement, paper crafts, indie booksellers, animation fans, weird-museum fans, cat-shaped corners of the internet.

You may invent the query. Reach for an UNEXPECTED corner each time — don't keep returning to the obvious "neocities" / "smallweb" defaults. Try things like:
- a small subculture name ("garage zine", "mail art", "ham radio", "soda bottle collector")
- a craft term ("riso print", "spiral binding", "letterpress")
- a website class ("guestbook", "webring", "fan page", "geocities")
- a hobby + medium ("model train", "cassette label", "amateur radio QSL")
- an aesthetic word ("cozy", "handmade", "low-tech", "analog")
- an object the cafe would love ("rotary phone", "rolodex", "library card", "stamp")

AVOID:
- anything political, news-y, controversial
- "AI" / "LLM" / "ChatGPT" / "Claude" — those threads are too charged
- crypto / NFT / monetization terms
- too-broad single words ("art", "design") — those return brand-account spam
- queries you'd expect a marketing intern to pick

OUTPUT FORMAT
Exactly one line: the query string itself. No quotes, no explanation, no commentary. 1-3 words ideally. Plain text only."""


def _roll_wild_topic(rng: random.Random) -> str:
    """Ask Claude to invent a fresh search query. Falls back to a static list if it fails."""
    try:
        result = subprocess.run(
            ["claude", "--print", "--tools", "", "--permission-mode", "plan", "--model", "haiku"],
            input=WILD_TOPIC_ROLL_PROMPT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude exit {result.returncode}")
        out = (result.stdout or "").strip()
        # First non-empty line, strip quotes/fences
        for line in out.splitlines():
            line = line.strip()
            line = re.sub(r"^[\"'`]+|[\"'`]+$", "", line)
            line = re.sub(r"^```[a-z]*\s*", "", line)
            line = re.sub(r"\s*```\s*$", "", line)
            if line and len(line) <= 60:
                return line
        raise ValueError("no usable line in output")
    except Exception as e:
        print(f"[wild] LLM topic roll failed ({e}); falling back to static list", file=sys.stderr)
        return rng.choice(WILD_SEARCH_TOPICS)


def _load_wild_state() -> dict[str, Any]:
    if not WILD_STATE_PATH.exists():
        return {"replied": []}
    try:
        return json.loads(WILD_STATE_PATH.read_text())
    except Exception:
        return {"replied": []}


def _save_wild_state(state: dict[str, Any]) -> None:
    WILD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WILD_STATE_PATH.write_text(json.dumps(state, indent=2))


def _wild_count_24h(state: dict[str, Any]) -> int:
    import time
    cutoff = time.time() - 24 * 3600
    return sum(
        1 for r in state.get("replied", [])
        if isinstance(r, dict) and not r.get("skipped") and r.get("ts", 0) > cutoff
    )


def _wild_recent_handles(state: dict[str, Any]) -> set[str]:
    import time
    cutoff = time.time() - WILD_RECENT_HANDLE_WINDOW
    return {
        r.get("handle") for r in state.get("replied", [])
        if isinstance(r, dict) and r.get("handle") and r.get("ts", 0) > cutoff
    }


def _generate_wild_reply(
    staff: dict[str, Any],
    their_handle: str,
    source_text: str,
) -> str | None:
    prompt = WILD_REPLY_PROMPT.format(
        name=staff["name"],
        full_name=staff["full_name"],
        species=staff.get("species", "(unspecified)"),
        tone=staff["tone"],
        examples="\n\n".join(staff["examples"]),
        their_handle=their_handle,
        source_text=source_text or "(no text)",
        signoff=staff.get("signoff", ""),
    )
    try:
        result = subprocess.run(
            ["claude", "--print", "--tools", "", "--permission-mode", "plan", "--model", "haiku"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        print(f"[wild] claude failed: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"[wild] claude exit {result.returncode}", file=sys.stderr)
        return None
    text = (result.stdout or "").strip()
    if "NOPOST" in text.upper()[:40]:
        return None
    text = re.sub(r"^```[a-z]*\s*", "", text).strip()
    text = re.sub(r"\s*```\s*$", "", text).strip()
    if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
        text = text[1:-1].strip()
    return text or None


def _maybe_wild_reply(
    did: str,
    jwt: str,
    staff_pool: list,
    weights: list,
    rng: random.Random,
) -> bool:
    """Search a curated small-web topic, find a fresh stranger's post, reply in cat voice.

    Strict daily cap + 7-day per-handle dedup so we never look like a follower bot.
    Returns True if a reply was posted.
    """
    if rng.random() > WILD_RUN_PROBABILITY:
        return False

    state = _load_wild_state()
    daily = _wild_count_24h(state)
    if daily >= WILD_DAILY_CAP:
        print(f"[wild] daily cap {WILD_DAILY_CAP} reached ({daily}) — skip")
        return False

    replied_uris = {
        r.get("uri") for r in state.get("replied", [])
        if isinstance(r, dict) and r.get("uri")
    }
    recent_handles = _wild_recent_handles(state)
    our_handle = os.environ.get("BSKY_HANDLE", "")

    topic = _roll_wild_topic(rng)
    try:
        search = _bsky(
            f"/app.bsky.feed.searchPosts?q={urllib.parse.quote(topic)}&sort=latest&limit=20",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    except Exception as e:
        print(f"[wild] search {topic!r} failed: {e}", file=sys.stderr)
        return False

    posts = search.get("posts") or []
    target = None
    for p in posts:
        uri = p.get("uri")
        if not uri or uri in replied_uris:
            continue
        author = p.get("author") or {}
        handle = author.get("handle", "")
        if handle == our_handle or handle in recent_handles:
            continue
        rec = p.get("record") or {}
        text = (rec.get("text") or "").strip()
        if len(text) < 30:
            continue
        if rec.get("reply"):  # top-level only
            continue
        # Skip if the post itself contains common controversy/news markers
        lower = text.lower()
        if any(t in lower for t in [
            "trump", "biden", "election", "war", "shooting", "shooter",
            "rip", "passed away", "died", "obituary",
            "bitcoin", "crypto", "stock", "etf",
        ]):
            continue
        target = p
        break

    if not target:
        print(f"[wild] no candidates for topic {topic!r} (scanned {len(posts)})")
        return False

    target_uri = target["uri"]
    target_cid = target["cid"]
    target_handle = (target.get("author") or {}).get("handle", "(unknown)")
    target_text = (target.get("record") or {}).get("text", "")

    staff = rng.choices(staff_pool, weights=weights, k=1)[0]
    reply_text = _generate_wild_reply(staff, target_handle, target_text)

    import time
    if not reply_text:
        # Record the NOPOST so we don't keep re-evaluating the same post each run
        state.setdefault("replied", []).append({
            "uri": target_uri,
            "ts": time.time(),
            "skipped": True,
            "topic": topic,
        })
        state["replied"] = state["replied"][-WILD_REPLIED_HISTORY_CAP:]
        _save_wild_state(state)
        print(f"[wild] NOPOST for @{target_handle} ({topic!r}): {target_text[:60]!r}")
        return False

    try:
        resp = _create_reply(did, jwt, reply_text, target_uri, target_cid)
        print(f"[wild] replied as {staff['name']} to @{target_handle} ({topic!r}): {resp.get('uri','?')}")
        state.setdefault("replied", []).append({
            "uri": target_uri,
            "ts": time.time(),
            "persona": staff["name"],
            "topic": topic,
            "handle": target_handle,
        })
        state["replied"] = state["replied"][-WILD_REPLIED_HISTORY_CAP:]
        _save_wild_state(state)
        try:
            from post_log import log as post_log
            post_log(
                "wild",
                persona=staff["name"],
                uri=resp.get("uri"),
                subject=f"@{target_handle}",
                text=reply_text,
                topic=topic,
            )
        except Exception:
            pass
        return True
    except urllib.error.HTTPError as e:
        print(f"[wild] reply HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[wild] reply failed: {e}", file=sys.stderr)
    return False


def _load_throwback_state() -> dict[str, Any]:
    if not THROWBACK_STATE_PATH.exists():
        return {"thrown": []}
    try:
        return json.loads(THROWBACK_STATE_PATH.read_text())
    except Exception:
        return {"thrown": []}


def _save_throwback_state(state: dict[str, Any]) -> None:
    THROWBACK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    THROWBACK_STATE_PATH.write_text(json.dumps(state, indent=2))


def _maybe_throwback_post(rng: random.Random) -> bool:
    """Pick a random archive entry from >7 days ago and post a 'from the cabinet' note about it.

    Reuses post_bsky.post_drop with kind='throwback' so the image/URL/facet plumbing
    is the same as a fresh drop. Returns True if posted.
    """
    if rng.random() > THROWBACK_PROBABILITY:
        return False

    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt, timedelta as _td
    pt = ZoneInfo("America/Los_Angeles")
    cutoff = _dt.now(tz=pt) - _td(days=THROWBACK_MIN_AGE_DAYS)

    candidates: list[Path] = []
    for f in ARCHIVE_DIR.glob("*.html"):
        if f.name == "index.html":
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})", f.stem)
        if not m:
            continue
        try:
            dt = _dt.strptime(
                f"{m.group(1)} {m.group(2)}:{m.group(3)}",
                "%Y-%m-%d %H:%M",
            ).replace(tzinfo=pt)
        except Exception:
            continue
        if dt > cutoff:
            continue
        candidates.append(f)

    if not candidates:
        print("[throwback] no archive entries old enough yet — skip")
        return False

    state = _load_throwback_state()
    recently_thrown = {r.get("file") for r in state.get("thrown", []) if isinstance(r, dict)}
    fresh = [c for c in candidates if c.name not in recently_thrown]
    pool = fresh if fresh else candidates  # eventually we'll have thrown everything; recycle

    target = rng.choice(pool)
    thumb = THUMBS_DIR / (target.stem + ".png")

    try:
        from post_bsky import post_drop
        posted = post_drop(target, thumb if thumb.exists() else None, kind="throwback")
    except Exception as e:
        print(f"[throwback] post errored: {e}", file=sys.stderr)
        return False

    if not posted:
        return False

    import time
    state.setdefault("thrown", []).append({"file": target.name, "ts": time.time()})
    state["thrown"] = state["thrown"][-THROWBACK_RECENT_CAP:]
    _save_throwback_state(state)
    print(f"[throwback] resurfaced {target.name}")
    return True


def _fetch_post(uri: str, jwt: str) -> dict | None:
    encoded = urllib.parse.quote(uri, safe="")
    try:
        data = _bsky(
            f"/app.bsky.feed.getPosts?uris={encoded}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    except Exception as e:
        print(f"[engage] getPosts failed for {uri}: {e}", file=sys.stderr)
        return None
    posts = data.get("posts") or []
    return posts[0] if posts else None


def run(skip_ambient: bool = False, max_replies: int | None = None) -> int:
    handle = os.environ.get("BSKY_HANDLE")
    pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        print("[engage] BSKY_HANDLE / BSKY_APP_PASSWORD missing — exiting", file=sys.stderr)
        return 1
    cap = max_replies if max_replies is not None else MAX_REPLIES_PER_RUN

    state = _load_state()
    handled: set[str] = set(state.get("handled_uris", []))
    last_seen = state.get("last_indexedAt")

    try:
        did, jwt = _auth()
    except Exception as e:
        print(f"[engage] auth failed: {e}", file=sys.stderr)
        try:
            from cat_signal import signal
            signal("bsky-auth", f"bluesky auth failed in engagement loop. likely cause: app password revoked or expired. err: {str(e)[:200]}", priority="high")
        except Exception:
            pass
        return 2

    try:
        notifs = _bsky(
            "/app.bsky.notification.listNotifications?limit=50",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    except Exception as e:
        print(f"[engage] listNotifications failed: {e}", file=sys.stderr)
        return 3
    items = notifs.get("notifications") or []
    items.sort(key=lambda n: n.get("indexedAt", ""))

    personas = _load_personas()
    staff_pool = personas["staff"]
    weights = [s["weight"] for s in staff_pool]
    rng = random.Random()
    actions = 0

    for n in items:
        idx_at = n.get("indexedAt", "")
        n_uri = n.get("uri", "")
        if last_seen and idx_at <= last_seen:
            continue
        if n_uri in handled:
            continue
        if actions >= cap:
            print(f"[engage] hit cap of {cap} replies — stopping for this run")
            break

        reason = n.get("reason", "")
        author = (n.get("author") or {}).get("handle", "(unknown)")

        if reason in ("mention", "reply"):
            src_post = _fetch_post(n_uri, jwt) or {}
            src_text = ((src_post.get("record") or {}).get("text") or "").strip()
            our_context = ""
            if reason == "reply":
                reply_block = (src_post.get("record") or {}).get("reply") or {}
                root_ref = reply_block.get("root") or {}
                root_uri_ref = root_ref.get("uri", "")
                if root_uri_ref:
                    root_post = _fetch_post(root_uri_ref, jwt) or {}
                    our_context = ((root_post.get("record") or {}).get("text") or "").strip()

            staff = rng.choices(staff_pool, weights=weights, k=1)[0]
            action_desc = (
                "mentioned the cafe (your handle was tagged in their post)"
                if reason == "mention"
                else "replied to one of our posts"
            )
            text = _generate_reply(staff, action_desc, author, src_text, our_context)
            if not text:
                print(f"[engage] skip (NOPOST or empty): {author} — {src_text[:60]!r}")
            else:
                try:
                    parent_uri = src_post.get("uri")
                    parent_cid = src_post.get("cid")
                    reply_block = (src_post.get("record") or {}).get("reply") or {}
                    root = reply_block.get("root") or {}
                    root_uri_ref = root.get("uri") or parent_uri
                    root_cid_ref = root.get("cid") or parent_cid
                    resp = _create_reply(
                        did, jwt, text, parent_uri, parent_cid, root_uri_ref, root_cid_ref
                    )
                    print(f"[engage] replied as {staff['name']} to @{author}: {resp.get('uri','?')}")
                    try:
                        from post_log import log as post_log
                        post_log("reply", persona=staff["name"], uri=resp.get("uri"), subject=f"@{author}", text=text)
                    except Exception:
                        pass
                    actions += 1
                except urllib.error.HTTPError as e:
                    print(f"[engage] reply post HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
                except Exception as e:
                    print(f"[engage] reply post failed: {e}", file=sys.stderr)

        elif reason == "quote":
            try:
                _create_like(did, jwt, n_uri, n.get("cid"))
                print(f"[engage] liked quote from @{author}")
                try:
                    from post_log import log as post_log
                    post_log("like", uri=n_uri, subject=f"@{author}")
                except Exception:
                    pass
                actions += 1
            except Exception as e:
                print(f"[engage] like failed: {e}", file=sys.stderr)

        elif reason == "follow":
            print(f"[engage] new follower @{author} — noted, no action")

        else:
            # like / repost / others: nothing to do
            pass

        handled.add(n_uri)
        # Keep handled set bounded
        if len(handled) > HANDLED_URI_CAP:
            handled = set(list(handled)[-HANDLED_URI_CAP:])
        state["handled_uris"] = list(handled)
        if idx_at:
            state["last_indexedAt"] = idx_at
        _save_state(state)

    # Roll for an ambient between-drop post (low probability per run).
    # Skipped when called from inside the hourly gen cron — that run already
    # produced a drop announcement, no need to also fire an ambient observation.
    if not skip_ambient and _maybe_ambient_post(did, jwt, staff_pool, weights, rng):
        actions += 1

    # Roll for an in-the-wild reply (low probability per run, daily cap of 5).
    # Same skip-on-gen rule — wild engagement belongs to between-drop hours
    # so the cafe feels like it's quietly out in the world, not piggy-backed
    # on its own drop firehose.
    if not skip_ambient and _maybe_wild_reply(did, jwt, staff_pool, weights, rng):
        actions += 1

    # Roll for a throwback — resurface an older archive entry, ~1.15/day expected.
    # Same skip-on-gen rule — the hourly drop already covers "new content" for this run.
    if not skip_ambient and _maybe_throwback_post(rng):
        actions += 1

    if actions == 0:
        print("[engage] nothing new")
    return 0


if __name__ == "__main__":
    sys.exit(run())
