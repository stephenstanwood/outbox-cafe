"""
outbox.cafe — gentle autonomous follow loop on Bluesky.

The cafe followed exactly ONE account for months, which is about the worst
possible posture for growth: it read as a broadcaster, not a participant.
This loop makes the cafe a good neighbor — it finds kindred small-web / cafe /
cat / handmade-web accounts and follows a few a day. Follow-backs are the
immediate point, but the deeper win is that the cafe's own timeline becomes a
real curated feed of the corners it loves, which the like + wild-reply loops
then draw from — a virtuous circle.

Deliberately gentle so it never reads as a follow-bot:
  - low caps (a couple per run, ~10/day)
  - only kindred, active, human-scale accounts (not empty eggs, not celebrities)
  - positive-only: controversy / news / crypto / adult / growth-hack terms in the
    bio or the surfacing post → skip
  - NEVER auto-unfollows — these are genuine follows the cafe keeps
  - one follow per account, ever (state-deduped by DID)

Same OAuth + auth pattern as like_loop.py / engage_bsky.py.
State in data/follow_state.json (gitignored, per-Mini). Cron: every 3h.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from lib.io import atomic_write_json
from lib import bsky

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE_PATH = DATA / "follow_state.json"

# Gentle by design. Every-3h cron = 8 runs/day; the daily cap is the real governor.
FOLLOWS_PER_RUN = 2
FOLLOWS_PER_DAY = 10
FOLLOW_HISTORY_CAP = 4000     # remember every account we've ever followed (dedup)

# Human-scale kindred accounts only. Skip empty eggs (no follow-back signal, often
# spam) and mega-accounts (won't follow back; following them reads as thirsty).
MIN_FOLLOWERS = 3
MAX_FOLLOWERS = 30000
MIN_POSTS = 5

# Same aesthetic as the like loop + wild-reply topics — the corners the cafe loves.
SEARCH_TERMS = [
    "small web", "indie web", "neocities", "zine", "handmade web",
    "old internet", "web revival", "mail art", "art journal", "collage",
    "pixel art", "generative art", "fountain pen", "typewriter", "riso print",
    "coffee window", "morning light", "library card", "thrift find", "houseplant",
    "cat nap", "windowsill", "paperback", "vintage poster", "tape deck",
]

# Positive-only gate. If any term shows up in the bio OR the post that surfaced the
# account, we don't follow. Cheap substring guard — mirrors like_loop's filter, plus
# news/politics and follow-for-follow growth-hack markers (which correlate with bots).
BAD_TERMS = (
    " trump", " biden", " election", " war ", "genocide", "shooting",
    "killed", " died", " rip ", "passed away", "breaking:", "politics",
    "crypto", " nft", "$", "buy now", "onlyfans", "porn", "nsfw", "escort",
    "giveaway", "follow back", "followback", "f4f", "gain followers", "link in bio",
)


# ---------- State ----------

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"followed": []}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"followed": []}


def _save_state(state: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    state["followed"] = (state.get("followed") or [])[-FOLLOW_HISTORY_CAP:]
    atomic_write_json(STATE_PATH, state)


def _today_count(state: dict) -> int:
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    return sum(1 for e in state.get("followed", [])
               if isinstance(e, dict) and e.get("ts", "").startswith(today))


def _known_dids(state: dict) -> set[str]:
    return {e.get("did") for e in state.get("followed", []) if isinstance(e, dict) and e.get("did")}


# ---------- Bsky ----------

def _req(path: str, *, data=None, headers=None, method="GET"):
    return bsky.request(path, data=data, headers=headers, method=method)


def _search_authors(term: str, jwt: str, our_did: str, known: set[str], limit: int = 25) -> dict:
    """Candidate authors from one search, minus us, anyone we already follow
    (viewer.following set), unresolved handles, and anyone previously followed."""
    qs = urllib.parse.urlencode({"q": term, "limit": str(limit), "sort": "latest"})
    try:
        d = _req(f"/app.bsky.feed.searchPosts?{qs}", headers={"Authorization": f"Bearer {jwt}"})
    except Exception as e:
        print(f"[follow] search {term!r} failed: {e}", file=sys.stderr)
        return {}
    out: dict[str, dict] = {}
    for p in d.get("posts", []) or []:
        a = p.get("author") or {}
        did = a.get("did")
        if not did or did == our_did or did in known:
            continue
        if (a.get("viewer") or {}).get("following"):
            continue  # already following
        handle = (a.get("handle") or "").lower()
        if handle == "handle.invalid" or handle.endswith(".invalid"):
            continue
        text = ((p.get("record") or {}).get("text") or "").lower()
        if any(b in text for b in BAD_TERMS):
            continue
        out.setdefault(did, {"did": did, "handle": a.get("handle"), "term": term})
    return out


def _profiles(dids: list[str], jwt: str) -> dict:
    """Batch getProfiles (<=25 per call) → {did: profileView}."""
    out: dict[str, dict] = {}
    for i in range(0, len(dids), 25):
        chunk = dids[i:i + 25]
        qs = "&".join(f"actors={urllib.parse.quote(d)}" for d in chunk)
        try:
            d = _req(f"/app.bsky.actor.getProfiles?{qs}", headers={"Authorization": f"Bearer {jwt}"})
        except Exception as e:
            print(f"[follow] getProfiles failed: {e}", file=sys.stderr)
            continue
        for prof in d.get("profiles", []) or []:
            if prof.get("did"):
                out[prof["did"]] = prof
    return out


def _is_kindred(prof: dict | None) -> bool:
    if not prof:
        return False
    if (prof.get("viewer") or {}).get("following"):
        return False
    fc = prof.get("followersCount", 0) or 0
    pc = prof.get("postsCount", 0) or 0
    if fc < MIN_FOLLOWERS or fc > MAX_FOLLOWERS or pc < MIN_POSTS:
        return False
    bio = (prof.get("description") or "").lower()
    if any(b in bio for b in BAD_TERMS):
        return False
    return True


def _follow(our_did: str, jwt: str, subject_did: str) -> bool:
    record = {
        "$type": "app.bsky.graph.follow",
        "subject": subject_did,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    try:
        _req(
            "/com.atproto.repo.createRecord",
            data={"repo": our_did, "collection": "app.bsky.graph.follow", "record": record},
            headers={"Authorization": f"Bearer {jwt}"},
            method="POST",
        )
        return True
    except urllib.error.HTTPError as e:
        print(f"[follow] follow {subject_did} HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[follow] follow {subject_did} failed: {e}", file=sys.stderr)
        return False


# ---------- Run ----------

def run() -> int:
    if not (os.environ.get("BSKY_HANDLE") and os.environ.get("BSKY_APP_PASSWORD")):
        print("[follow] creds missing — skip")
        return 0

    state = _load_state()
    daily = _today_count(state)
    cap = min(FOLLOWS_PER_RUN, FOLLOWS_PER_DAY - daily)
    if cap <= 0:
        print(f"[follow] daily cap reached ({FOLLOWS_PER_DAY}) — skip")
        return 0

    try:
        did, jwt = bsky.login()
    except Exception as e:
        print(f"[follow] auth failed: {e}", file=sys.stderr)
        return 0

    known = _known_dids(state)
    rng = random.Random()
    terms = list(SEARCH_TERMS)
    rng.shuffle(terms)

    candidates: dict[str, dict] = {}
    for term in terms[:6]:  # 6 searches per run
        for cand_did, info in _search_authors(term, jwt, did, known).items():
            candidates.setdefault(cand_did, info)
        time.sleep(0.2)
        if len(candidates) >= 30:
            break

    if not candidates:
        print("[follow] no candidates")
        return 0

    dids = list(candidates.keys())
    rng.shuffle(dids)
    profs = _profiles(dids, jwt)

    followed_now = 0
    for cand_did in dids:
        if followed_now >= cap:
            break
        prof = profs.get(cand_did)
        if not _is_kindred(prof):
            continue
        handle = prof.get("handle", "?")
        if not _follow(did, jwt, cand_did):
            continue
        followed_now += 1
        state.setdefault("followed", []).append({
            "did": cand_did,
            "handle": handle,
            "term": candidates[cand_did].get("term"),
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        print(f"[follow] → @{handle} (followers={prof.get('followersCount')}, "
              f"posts={prof.get('postsCount')}, term={candidates[cand_did].get('term')!r})")
        try:
            from post_log import log as post_log
            post_log("follow", uri=f"at://{cand_did}", subject=f"@{handle}")
        except Exception:
            pass
        time.sleep(2.0)

    _save_state(state)
    print(f"[follow_loop] done. followed={followed_now} (daily now {daily + followed_now}/{FOLLOWS_PER_DAY})")
    return 0


if __name__ == "__main__":
    sys.exit(run())
