"""Reciprocity: does the cafe's outbound attention ever come back?

The cafe spends most of its social voice on OTHER people — wild replies to
strangers' posts, follows, hearts from the like loop, acknowledgment likes for
new followers. `reflect.py` deliberately refuses to score any of that (the
engagement on a stranger's post is theirs, not ours), which left those gestures
as bare counts: `gestures out: wild:227 · follow:186`. Whether a single one of
them ever turned into a follower, a like, or a reply was unknown — not
"measured as zero", unknown. Batch 27 mis-read a bogus number in that gap and
wrote it into the roadmap as strategy.

This closes the gap with attribution, nightly:

1. **Followers snapshot.** `getFollowers` is paged into a DID→handle map and
   diffed against last night's (`data/reciprocity_state.json`). Every NEW
   follower is looked up against the outbound gestures aimed at that account
   in the previous ATTRIBUTION_DAYS. Lost followers are recorded too.
2. **Inbound notifications.** `listNotifications` since last night's
   watermark; each like / repost / reply / mention / quote is attributed the
   same way. Follows are left to the snapshot diff (the canonical source —
   it can't miss one between runs).
3. Every event — attributed or not — is appended to `data/reciprocity.jsonl`
   with the full list of prior gestures that touched the account, and the
   nightly digest prints a rolling conversion line per gesture type.

Attribution rule: a gesture claims an event only if it happened BEFORE the
event and within ATTRIBUTION_DAYS. When several gestures touched the same
account, the *proactive* one (the cafe went first: wild reply, follow, like
loop) wins over the *reactive* one (they engaged first and the cafe answered:
reply to a mention, ack-like), and the earliest of that class is credited.
"Unprompted" means nothing the cafe did touched that account in the window.

The first run only SEEDS the snapshot — there is no earlier state to diff
against — and the summary's denominators start from that moment, so the line
never compares post-seed conversions against pre-seed gestures that could not
have been observed converting.

Zero LLM calls. Best-effort throughout: a network failure prints and returns
an empty line; the digest carries on.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_LOG = ROOT / "data" / "post_log.jsonl"
LIKE_STATE = ROOT / "data" / "like_state.json"
STATE_PATH = ROOT / "data" / "reciprocity_state.json"
EVENTS_PATH = ROOT / "data" / "reciprocity.jsonl"

# A gesture older than this cannot claim credit for an event.
ATTRIBUTION_DAYS = 30
# The digest's rolling window for the conversion line.
SUMMARY_DAYS = 30
# Paging bounds — followers is ~1 page today; notifications is a day's worth.
FOLLOWER_PAGES_MAX = 50
NOTIF_PAGES_MAX = 6

# The cafe went first.
PROACTIVE = ("wild", "follow", "like_loop")
# They went first; the cafe answered. A subsequent follow from a `reply`
# target is still meaningful (they mentioned us, we answered, they followed);
# one from a `follow_ack_like` target is not (they already followed).
REACTIVE = ("reply", "follow_ack_like", "like")
GESTURE_TYPES = PROACTIVE + REACTIVE

# Inbound notification reasons that count as engagement. `follow` is
# deliberately absent — the snapshot diff owns follows.
INBOUND_REASONS = ("like", "repost", "reply", "mention", "quote")

LABELS = {
    "wild": "wild reply",
    "follow": "follow",
    "like_loop": "like",
    "reply": "answered",
    "follow_ack_like": "ack-like",
    "like": "quote-like",
}


# ---- time -----------------------------------------------------------------

def parse_ts(raw: str | None) -> datetime | None:
    """ISO timestamp → aware UTC datetime, or None. Accepts the trailing-Z form
    every cafe log writes and bsky's fractional `indexedAt`."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_handle(h: str | None) -> str:
    return (h or "").strip().lstrip("@").casefold()


# ---- gestures out ---------------------------------------------------------

def _at_uri_did(uri: str | None) -> str | None:
    if not uri or not uri.startswith("at://"):
        return None
    rest = uri[5:].split("/", 1)[0]
    return rest if rest.startswith("did:") else None


def load_gestures(since: datetime, *, post_log: Path | None = None,
                  like_state: Path | None = None) -> list[dict]:
    """Every outbound gesture since `since`, oldest first.

    Each: {"type", "ts" (aware UTC), "did" (may be None), "handle" (lower, no @)}.
    post_log rows carry the target handle in `subject`; follow/ack/quote-like
    rows also carry the target's DID in the uri. The bsky like loop logs to
    its own state file (uri + author), not post_log.
    """
    # Resolve these at call time. Tests and one-off audits deliberately swap the
    # module paths; binding POST_LOG/LIKE_STATE as defaults at import time made
    # run() silently keep reading the production files instead.
    post_log = post_log or POST_LOG
    like_state = like_state or LIKE_STATE
    out: list[dict] = []
    if post_log.exists():
        for line in post_log.read_text(errors="ignore").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get("type")
            if t not in GESTURE_TYPES:
                continue
            subj = e.get("subject") or ""
            if not subj.startswith("@"):
                continue
            ts = parse_ts(e.get("ts"))
            if ts is None or ts < since:
                continue
            did = _at_uri_did(e.get("uri"))
            # Own-repo uris (wild/reply rows log OUR reply's uri) say nothing
            # about the target — only a foreign DID is a target DID.
            if t in ("wild", "reply"):
                did = None
            out.append({"type": t, "ts": ts, "did": did, "handle": _norm_handle(subj)})
    if like_state.exists():
        try:
            rows = (json.loads(like_state.read_text()) or {}).get("bsky") or []
        except Exception:
            rows = []
        for r in rows:
            ts = parse_ts(r.get("ts"))
            if ts is None or ts < since:
                continue
            out.append({
                "type": "like_loop",
                "ts": ts,
                "did": _at_uri_did(r.get("uri")),
                "handle": _norm_handle(r.get("author")),
            })
    out.sort(key=lambda g: g["ts"])
    return out


def gestures_for(did: str | None, handle: str | None, gestures: list[dict],
                 before: datetime, window_days: int = ATTRIBUTION_DAYS) -> list[dict]:
    """Gestures aimed at this account in the `window_days` before `before`.

    Matches by DID when both sides have one (handles get renamed), else by
    handle. Strictly before the event: a gesture cannot claim an event that
    preceded it.
    """
    h = _norm_handle(handle)
    floor = before - timedelta(days=window_days)
    hits = []
    for g in gestures:
        if not (floor <= g["ts"] < before):
            continue
        if did and g["did"]:
            if g["did"] != did:
                continue
        elif not h or g["handle"] != h:
            continue
        hits.append(g)
    return hits


def attribute(matches: list[dict]) -> dict | None:
    """The gesture that gets the credit: earliest proactive, else earliest
    reactive, else None."""
    for cls in (PROACTIVE, REACTIVE):
        for g in matches:  # already oldest-first
            if g["type"] in cls:
                return g
    return None


def diff_followers(prev: dict[str, dict], cur: dict[str, str]) -> tuple[dict[str, str], dict[str, dict]]:
    """(new: did→handle, lost: did→prev-info)."""
    new = {d: h for d, h in cur.items() if d not in prev}
    lost = {d: info for d, info in prev.items() if d not in cur}
    return new, lost


# ---- state + events -------------------------------------------------------

def _load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()) or {}
    except Exception:
        return {}


def _save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(path)


def _append_event(event: dict, path: Path = EVENTS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _load_events(since: datetime, path: Path = EVENTS_PATH) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        ts = parse_ts(e.get("ts"))
        if ts is None or ts < since:
            continue
        e["_ts"] = ts
        out.append(e)
    return out


def make_event(kind: str, did: str | None, handle: str | None, at: datetime,
               gestures: list[dict]) -> dict:
    matches = gestures_for(did, handle, gestures, at)
    credited = attribute(matches)
    return {
        "ts": _iso(at),
        "event": kind,
        "did": did,
        "handle": _norm_handle(handle),
        "attributed": credited["type"] if credited else None,
        "gestures": [
            {"type": g["type"], "ts": _iso(g["ts"]),
             "lag_h": round((at - g["ts"]).total_seconds() / 3600, 1)}
            for g in matches
        ],
    }


# ---- network --------------------------------------------------------------

def fetch_followers(did: str, jwt: str) -> dict[str, str]:
    """All followers as DID→handle via paged getFollowers."""
    from lib import bsky
    auth = {"Authorization": f"Bearer {jwt}"}
    out: dict[str, str] = {}
    cursor = None
    for _ in range(FOLLOWER_PAGES_MAX):
        q = {"actor": did, "limit": 100}
        if cursor:
            q["cursor"] = cursor
        resp = bsky.request(f"/app.bsky.graph.getFollowers?{urllib.parse.urlencode(q)}",
                            headers=auth, timeout=20)
        for f in resp.get("followers") or []:
            if f.get("did"):
                out[f["did"]] = f.get("handle") or ""
        cursor = resp.get("cursor")
        if not cursor:
            break
    return out


def fetch_notifications(jwt: str, since: datetime | None) -> list[dict]:
    """Notifications newer than `since` (all of the last pages if None)."""
    from lib import bsky
    auth = {"Authorization": f"Bearer {jwt}"}
    out: list[dict] = []
    cursor = None
    for _ in range(NOTIF_PAGES_MAX):
        q = {"limit": 100}
        if cursor:
            q["cursor"] = cursor
        resp = bsky.request(f"/app.bsky.notification.listNotifications?{urllib.parse.urlencode(q)}",
                            headers=auth, timeout=20)
        items = resp.get("notifications") or []
        done = False
        for n in items:
            ts = parse_ts(n.get("indexedAt"))
            if ts is None:
                continue
            if since is not None and ts <= since:
                done = True
                break
            out.append(n)
        cursor = resp.get("cursor")
        if done or not cursor or not items:
            break
    return out


# ---- summary --------------------------------------------------------------

def summarize(events: list[dict], gestures: list[dict], floor: datetime,
              inbound_today: tuple[int, int, int] | None = None) -> str:
    """The digest lines.

    Denominator per type: distinct accounts the cafe reached with that gesture
    since `floor`. Numerators: distinct accounts among the events that carry a
    matching gesture at or after `floor` — so both sides count the same
    gestures, and nothing pre-seed is compared against post-seed outcomes.
    """
    reached: dict[str, set[str]] = defaultdict(set)
    for g in gestures:
        if g["ts"] >= floor:
            reached[g["type"]].add(g["did"] or g["handle"])

    followed: dict[str, set[str]] = defaultdict(set)
    engaged: dict[str, set[str]] = defaultdict(set)
    unprompted_follows = 0
    for e in events:
        key = e.get("did") or e.get("handle") or ""
        credited = e.get("attributed")
        # Attribution is deliberately single-touch: the earliest proactive
        # gesture wins, otherwise the earliest reactive one. Keep the complete
        # gesture list in the event for auditability, but do not let one return
        # visit inflate every gesture type that happened to touch the account.
        eligible = {
            g["type"] for g in e.get("gestures") or []
            if (parse_ts(g.get("ts")) or floor) >= floor
        }
        kind = credited if credited in eligible else None
        if e.get("event") == "follow":
            if kind is None:
                unprompted_follows += 1
            else:
                followed[kind].add(key)
        elif e.get("event") in INBOUND_REASONS:
            if kind is not None:
                engaged[kind].add(key)

    bits = []
    for t in GESTURE_TYPES:
        n = len(reached.get(t, ()))
        if not n:
            continue
        parts = []
        if t != "follow_ack_like":
            parts.append(f"{len(followed.get(t, ()))} followed")
        parts.append(f"{len(engaged.get(t, ()))} engaged")
        bits.append(f"{LABELS[t]} {n} → {', '.join(parts)}")
    if unprompted_follows:
        bits.append(f"unprompted follows {unprompted_follows}")

    lines = []
    if inbound_today is not None:
        total, accounts, reached_first = inbound_today
        if total:
            lines.append(
                f"inbound today: {total} like/reply/repost from {accounts} account(s)"
                f" — {reached_first} the cafe had reached first"
            )
    if bits:
        lines.append(f"**reciprocity** (since {floor.strftime('%b %d')}): {' · '.join(bits)}")
    return "\n".join(lines)


# ---- run ------------------------------------------------------------------

def run(*, now: datetime | None = None, state_path: Path = STATE_PATH,
        events_path: Path = EVENTS_PATH, fetch_followers_fn=None,
        fetch_notifications_fn=None, login_fn=None) -> str:
    """Take tonight's snapshot, attribute, persist, and return the digest text
    ("" when there is nothing to say or the network failed)."""
    now = now or datetime.now(timezone.utc)
    handle = os.environ.get("BSKY_HANDLE")
    pw = os.environ.get("BSKY_APP_PASSWORD")
    if login_fn is None:
        if not handle or not pw:
            print("[reciprocity] BSKY_* env not set — skipping", file=sys.stderr)
            return ""
        from lib import bsky
        login_fn = lambda: bsky.login(handle, pw, timeout=15)  # noqa: E731
    fetch_followers_fn = fetch_followers_fn or fetch_followers
    fetch_notifications_fn = fetch_notifications_fn or fetch_notifications

    try:
        did, jwt = login_fn()
        current = fetch_followers_fn(did, jwt)
    except Exception as e:
        print(f"[reciprocity] followers fetch failed (non-fatal): {e}", file=sys.stderr)
        return ""

    state = _load_state(state_path)

    if not state.get("followers"):
        # First night: nothing to diff against. Seed and start the clock.
        state = {
            "seeded_at": _iso(now),
            "snapshot_at": _iso(now),
            "notif_watermark": _iso(now),
            "followers": {d: {"handle": h, "first_seen": _iso(now)} for d, h in current.items()},
        }
        _save_state(state, state_path)
        print(f"[reciprocity] seeded snapshot with {len(current)} follower(s) — attribution starts now")
        return (f"**reciprocity:** started tracking tonight — {len(current)} followers on the books;"
                f" follow-backs and engagement now attribute to the cafe's own gestures")

    # The seed is the observation boundary: a pre-seed gesture cannot enter a
    # denominator because its conversion may already have happened unseen.
    seeded = parse_ts(state.get("seeded_at")) or now
    gesture_floor = max(seeded, now - timedelta(days=ATTRIBUTION_DAYS))
    gestures = load_gestures(gesture_floor)

    prev = state["followers"]
    new, lost = diff_followers(prev, current)
    for d, h in new.items():
        ev = make_event("follow", d, h, now, gestures)
        _append_event(ev, events_path)
        tag = ev["attributed"] or "unprompted"
        print(f"[reciprocity] new follower @{h} — {tag}"
              + (f" ({len(ev['gestures'])} prior gesture(s))" if ev["gestures"] else ""))
    for d, info in lost.items():
        ev = make_event("unfollow", d, info.get("handle"), now, gestures)
        _append_event(ev, events_path)
        print(f"[reciprocity] lost follower @{info.get('handle', '?')}")

    # Inbound engagement since the last watermark.
    inbound_today = None
    watermark = parse_ts(state.get("notif_watermark")) or (now - timedelta(days=1))
    try:
        notifs = fetch_notifications_fn(jwt, watermark)
    except Exception as e:
        print(f"[reciprocity] notifications fetch failed (non-fatal): {e}", file=sys.stderr)
        notifs = None
    if notifs is not None:
        total = 0
        accounts: set[str] = set()
        reached_first: set[str] = set()
        newest = watermark
        for n in notifs:
            at = parse_ts(n.get("indexedAt"))
            if at is None:
                continue
            # Advance past every notification we inspected, including follows
            # and other ignored reasons. Otherwise a quiet day of follow-only
            # notices leaves the watermark stale and replays the same pages on
            # every future run.
            newest = max(newest, at)
            reason = n.get("reason")
            if reason not in INBOUND_REASONS:
                continue
            author = n.get("author") or {}
            a_did = author.get("did")
            if not a_did or a_did == did:
                continue
            ev = make_event(reason, a_did, author.get("handle"), at, gestures)
            _append_event(ev, events_path)
            total += 1
            accounts.add(a_did)
            if ev["attributed"] in PROACTIVE:
                reached_first.add(a_did)
        inbound_today = (total, len(accounts), len(reached_first))
        state["notif_watermark"] = _iso(max(newest, watermark))
        print(f"[reciprocity] inbound: {total} event(s) from {len(accounts)} account(s), "
              f"{len(reached_first)} previously reached")

    # Persist tonight's snapshot: keep first_seen for survivors, stamp newcomers.
    state["followers"] = {
        d: (prev[d] if d in prev else {"handle": h, "first_seen": _iso(now)})
        for d, h in current.items()
    }
    for d, h in current.items():
        if h:
            state["followers"][d]["handle"] = h
    state["snapshot_at"] = _iso(now)
    _save_state(state, state_path)
    print(f"[reciprocity] followers {len(prev)} → {len(current)} (+{len(new)} / -{len(lost)})")

    floor = max(seeded, now - timedelta(days=SUMMARY_DAYS))
    events = _load_events(floor, events_path)
    return summarize(events, gestures, floor, inbound_today)


if __name__ == "__main__":
    text = run()
    if text:
        print(text)
