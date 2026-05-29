"""
outbox.cafe — autonomous liking loop on bsky + tumblr.

Likes are free social capital — each like is a notification on the
recipient's account → discovery surface. The cafe's wild-replies are
slow and content-heavy; likes are quick, cheap, and high-volume.

Conservative cadence: runs every 3h (8x/day). Per run, 1-2 bsky likes
and 1-2 tumblr likes from candidate searches matching the cafe's
aesthetic. State tracked in data/like_state.json so we never re-like.

Same OAuth + auth pattern as reblog_tumblr.py and engage_bsky.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from lib.io import atomic_write_json
from lib import bsky, tumblr

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE_PATH = DATA / "like_state.json"

BSKY_BASE = "https://bsky.social/xrpc"
TUMBLR_BASE = "https://api.tumblr.com/v2"

# Conservative — polite, not bot-y.
BSKY_LIKES_PER_RUN = 2
TUMBLR_LIKES_PER_RUN = 2
BSKY_LIKES_PER_DAY = 24
TUMBLR_LIKES_PER_DAY = 18

# Bsky search queries — mirrors the cafe's wild topics, plus a few seeds.
BSKY_SEARCH_TERMS = [
    "small web", "indie web", "pixel art", "generative art",
    "neocities", "zine", "handmade web", "old internet",
    "fountain pen", "mail art", "morning light",
    "the radio", "coffee window", "thrift store find",
    "library stamp", "library card", "vintage poster",
    "tape deck", "casio keyboard", "old browser",
]

# Tumblr tag pool — same as reblog tags.
TUMBLR_TAGS = [
    "small web", "smallweb", "indie web", "indieweb",
    "zine", "handmade zine", "mail art", "art journal",
    "pixel art", "generative art", "gif art", "collage",
    "retro internet", "retro computing", "old web",
    "web revival", "neocities", "handmade web",
]


# ---------- State ----------

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"bsky": [], "tumblr": []}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"bsky": [], "tumblr": []}


def _save_state(state: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    # Trim history to last 1000 per platform
    for k in ("bsky", "tumblr"):
        state[k] = (state.get(k) or [])[-1000:]
    atomic_write_json(STATE_PATH, state)


def _today_count(state: dict, platform: str) -> int:
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    return sum(1 for e in state.get(platform, []) if isinstance(e, dict) and e.get("ts", "").startswith(today))


# ---------- Bsky ----------

def _bsky_req(path: str, *, data=None, headers=None, method="GET"):
    return bsky.request(path, data=data, headers=headers, method=method)


def _bsky_auth() -> tuple[str, str]:
    return bsky.login()


def _bsky_search(query: str, jwt: str, limit: int = 25) -> list[dict]:
    """searchPosts requires auth (returns 401 without). Pass the session JWT."""
    qs = urllib.parse.urlencode({"q": query, "limit": str(limit), "sort": "latest"})
    try:
        d = _bsky_req(f"/app.bsky.feed.searchPosts?{qs}", headers={"Authorization": f"Bearer {jwt}"})
    except Exception as e:
        print(f"[like/bsky] search {query!r} failed: {e}", file=sys.stderr)
        return []
    return d.get("posts", []) or []


def _bsky_like_candidates(state: dict, our_did: str, jwt: str) -> list[dict]:
    """Return a shuffled list of bsky posts that match cafe interests and aren't already liked."""
    liked = {e.get("uri") for e in state.get("bsky", []) if isinstance(e, dict)}
    rng = random.Random()
    rng.shuffle(BSKY_SEARCH_TERMS)
    candidates: list[dict] = []
    for term in BSKY_SEARCH_TERMS[:5]:  # 5 search terms per run
        for p in _bsky_search(term, jwt, limit=20):
            uri = p.get("uri")
            if not uri or uri in liked:
                continue
            author = (p.get("author") or {}).get("did")
            if author == our_did:
                continue
            # Skip if already engaged (reposted/liked) heavily
            vc = p.get("viewer") or {}
            if vc.get("like"):
                continue
            # Avoid posts that look like they'd contain stuff we shouldn't engage with
            text = ((p.get("record") or {}).get("text") or "").lower()
            if any(bad in text for bad in (
                " trump", " biden", " election", " war ", "genocide",
                "killed", "died", "rip ", "passed away",
                "crypto ", "nft ", "$", "buy now",
                "onlyfans", "porn",
            )):
                continue
            candidates.append({"uri": uri, "cid": p.get("cid"), "author": (p.get("author") or {}).get("handle", "?"),
                              "text": text[:80]})
        time.sleep(0.2)
    rng.shuffle(candidates)
    return candidates


def _bsky_like(did: str, jwt: str, uri: str, cid: str) -> bool:
    record = {
        "$type": "app.bsky.feed.like",
        "subject": {"uri": uri, "cid": cid},
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        _bsky_req(
            "/com.atproto.repo.createRecord",
            data={"repo": did, "collection": "app.bsky.feed.like", "record": record},
            headers={"Authorization": f"Bearer {jwt}"},
            method="POST",
        )
        return True
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"[like/bsky] like {uri} HTTP {e.code}: {err}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[like/bsky] like {uri} failed: {e}", file=sys.stderr)
        return False


def run_bsky_likes(state: dict) -> int:
    if not (os.environ.get("BSKY_HANDLE") and os.environ.get("BSKY_APP_PASSWORD")):
        print("[like/bsky] creds missing — skip")
        return 0
    daily = _today_count(state, "bsky")
    remaining = BSKY_LIKES_PER_DAY - daily
    cap = min(BSKY_LIKES_PER_RUN, remaining)
    if cap <= 0:
        print(f"[like/bsky] daily cap reached ({BSKY_LIKES_PER_DAY}) — skip")
        return 0

    try:
        did, jwt = _bsky_auth()
    except Exception as e:
        print(f"[like/bsky] auth failed: {e}", file=sys.stderr)
        return 0

    candidates = _bsky_like_candidates(state, did, jwt)
    if not candidates:
        print("[like/bsky] no candidates")
        return 0

    liked_now = 0
    for c in candidates:
        if liked_now >= cap:
            break
        if not _bsky_like(did, jwt, c["uri"], c["cid"]):
            continue
        liked_now += 1
        state.setdefault("bsky", []).append({
            "uri": c["uri"],
            "author": c["author"],
            "text": c["text"],
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        print(f"[like/bsky] ♥ @{c['author']} | {c['text']!r}")
        time.sleep(1.5)

    return liked_now


# ---------- Tumblr ----------

def _q(s) -> str:
    return urllib.parse.quote(str(s), safe="-._~")


def _tumblr_oauth_header(method: str, url: str, *, query_params: dict | None = None) -> str:
    return tumblr.oauth_header(method, url, params=query_params)


def _tumblr_search_tag(tag: str, limit: int = 15) -> list[dict]:
    url = f"{TUMBLR_BASE}/tagged"
    params = {"tag": tag, "limit": str(limit)}
    auth = _tumblr_oauth_header("GET", url, query_params=params)
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                  headers={"Authorization": auth})
    try:
        return (json.load(urllib.request.urlopen(req, timeout=30)).get("response") or [])
    except Exception as e:
        print(f"[like/tumblr] tag {tag!r} failed: {e}", file=sys.stderr)
        return []


def _tumblr_like_candidates(state: dict, our_blog: str) -> list[dict]:
    liked_ids = {int(e.get("id", 0)) for e in state.get("tumblr", []) if isinstance(e, dict)}
    rng = random.Random()
    tags = list(TUMBLR_TAGS); rng.shuffle(tags)
    candidates: list[dict] = []
    seen: set[int] = set()
    for tag in tags[:4]:
        for p in _tumblr_search_tag(tag, limit=12):
            try:
                pid = int(p.get("id", 0))
            except Exception:
                continue
            if not pid or pid in seen or pid in liked_ids:
                continue
            if p.get("blog_name") == our_blog:
                continue
            if not p.get("reblog_key"):
                continue
            if p.get("liked"):  # already liked
                continue
            nc = p.get("note_count", 0) or 0
            if nc > 8000:  # skip mega-viral
                continue
            seen.add(pid)
            candidates.append({
                "id": pid,
                "reblog_key": p["reblog_key"],
                "blog_name": p.get("blog_name", "?"),
                "tag": tag,
            })
        time.sleep(0.25)
    rng.shuffle(candidates)
    return candidates


def _tumblr_like(post_id: int, reblog_key: str) -> bool:
    url = f"{TUMBLR_BASE}/user/like"
    fields = {"id": str(post_id), "reblog_key": reblog_key}
    auth = _tumblr_oauth_header("POST", url, query_params=fields)
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body,
                                  headers={"Authorization": auth, "Content-Type": "application/x-www-form-urlencoded"},
                                  method="POST")
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"[like/tumblr] like {post_id} HTTP {e.code}: {err}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[like/tumblr] like {post_id} failed: {e}", file=sys.stderr)
        return False


def run_tumblr_likes(state: dict) -> int:
    needed = ("TUMBLR_CONSUMER_KEY", "TUMBLR_CONSUMER_SECRET", "TUMBLR_OAUTH_TOKEN", "TUMBLR_OAUTH_TOKEN_SECRET")
    if not all(os.environ.get(k) for k in needed):
        print("[like/tumblr] tumblr creds missing — skip")
        return 0
    our_blog = os.environ.get("TUMBLR_BLOG_NAME", "outbox-cafe")
    daily = _today_count(state, "tumblr")
    cap = min(TUMBLR_LIKES_PER_RUN, TUMBLR_LIKES_PER_DAY - daily)
    if cap <= 0:
        print(f"[like/tumblr] daily cap reached ({TUMBLR_LIKES_PER_DAY}) — skip")
        return 0

    candidates = _tumblr_like_candidates(state, our_blog)
    if not candidates:
        print("[like/tumblr] no candidates")
        return 0

    liked_now = 0
    for c in candidates:
        if liked_now >= cap:
            break
        if not _tumblr_like(c["id"], c["reblog_key"]):
            continue
        liked_now += 1
        state.setdefault("tumblr", []).append({
            "id": c["id"],
            "blog": c["blog_name"],
            "tag": c["tag"],
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        print(f"[like/tumblr] ♥ @{c['blog_name']} (tag={c['tag']}, id={c['id']})")
        time.sleep(1.2)

    return liked_now


def main():
    state = _load_state()
    b = run_bsky_likes(state)
    t = run_tumblr_likes(state)
    _save_state(state)
    print(f"[like_loop] done. bsky={b} tumblr={t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
