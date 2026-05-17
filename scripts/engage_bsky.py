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
PERSONAS_PATH = ROOT / "data" / "personas.json"
STATE_PATH = ROOT / "data" / "engage_state.json"
BSKY_BASE = "https://bsky.social/xrpc"
MAX_REPLIES_PER_RUN = 10  # safety cap so a backlog doesn't fire 50 replies at once
HANDLED_URI_CAP = 500


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
            ["claude", "--print", "--tools", "", "--permission-mode", "plan"],
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


def run() -> int:
    handle = os.environ.get("BSKY_HANDLE")
    pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        print("[engage] BSKY_HANDLE / BSKY_APP_PASSWORD missing — exiting", file=sys.stderr)
        return 1

    state = _load_state()
    handled: set[str] = set(state.get("handled_uris", []))
    last_seen = state.get("last_indexedAt")

    try:
        did, jwt = _auth()
    except Exception as e:
        print(f"[engage] auth failed: {e}", file=sys.stderr)
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
        if actions >= MAX_REPLIES_PER_RUN:
            print(f"[engage] hit cap of {MAX_REPLIES_PER_RUN} replies — stopping for this run")
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
                    actions += 1
                except urllib.error.HTTPError as e:
                    print(f"[engage] reply post HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
                except Exception as e:
                    print(f"[engage] reply post failed: {e}", file=sys.stderr)

        elif reason == "quote":
            try:
                _create_like(did, jwt, n_uri, n.get("cid"))
                print(f"[engage] liked quote from @{author}")
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

    if actions == 0:
        print("[engage] nothing new")
    return 0


if __name__ == "__main__":
    sys.exit(run())
