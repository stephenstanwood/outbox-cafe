"""Auto-delete the cafe's Bluesky posts.

Stephen's call (2026-05-19): every day is a new day. The midnight cleanup
cron passes `--hours 0` to wipe everything (drops, ambient, throwbacks,
replies, wild replies) so the morning starts with a fresh feed. The pinned
welcome post is always exempt. Idempotent.

Manual `python3 scripts/cleanup_bsky.py` (no args) keeps the older 24h
default in case Stephen ever wants graceful rollover instead of a wipe.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BSKY_BASE = "https://bsky.social/xrpc"
DEFAULT_DELETE_AFTER_HOURS = 24
MAX_DELETES_PER_RUN = 50    # safety cap so a runaway loop can't nuke everything
ENGAGEMENT_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "post_engagement.jsonl"


def _snapshot_engagement(post: dict) -> None:
    """Freeze the post's final engagement counts before deletion so the reflection
    loop can still see what landed weeks ago."""
    try:
        entry = {
            "uri": post.get("uri"),
            "like_count": post.get("likeCount", 0),
            "reply_count": post.get("replyCount", 0),
            "repost_count": post.get("repostCount", 0),
            "quote_count": post.get("quoteCount", 0),
            "snapshot_ts": datetime.now(timezone.utc).isoformat(),
        }
        if not entry["uri"]:
            return
        ENGAGEMENT_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        with ENGAGEMENT_SNAPSHOT.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[cleanup] engagement snapshot failed (non-fatal): {e}", file=sys.stderr)


def _req(path: str, *, data=None, headers=None, method=None) -> dict:
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if isinstance(data, (dict, list)):
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(f"{BSKY_BASE}{path}", data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def cleanup(hours: int = DEFAULT_DELETE_AFTER_HOURS) -> int:
    """Delete every post we authored that's older than `hours`. Skip the pinned post.

    Returns count of records deleted.
    """
    handle = os.environ.get("BSKY_HANDLE")
    pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        print("[cleanup] BSKY_HANDLE / BSKY_APP_PASSWORD missing — skip", file=sys.stderr)
        return 0

    try:
        sess = _req(
            "/com.atproto.server.createSession",
            data={"identifier": handle, "password": pw},
            method="POST",
        )
    except Exception as e:
        print(f"[cleanup] auth failed: {e}", file=sys.stderr)
        return 0
    did = sess["did"]
    jwt = sess["accessJwt"]
    auth = {"Authorization": f"Bearer {jwt}"}

    # Pinned post — never delete (this is the welcome-to-the-cafe intro post).
    pinned_uri = ""
    try:
        prof = _req(
            f"/app.bsky.actor.getProfile?actor={urllib.parse.quote(handle)}",
            headers=auth,
        )
        pinned_uri = ((prof.get("pinnedPost") or {}).get("uri")) or ""
    except Exception as e:
        print(f"[cleanup] couldn't fetch pinned post (continuing — nothing exempt): {e}", file=sys.stderr)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    deleted = 0
    cursor: str | None = None
    pages = 0

    while pages < 20:  # bound paging defensively
        pages += 1
        path = f"/app.bsky.feed.getAuthorFeed?actor={did}&limit=100&filter=posts_with_replies"
        if cursor:
            path += f"&cursor={urllib.parse.quote(cursor)}"
        try:
            feed = _req(path, headers=auth)
        except Exception as e:
            print(f"[cleanup] getAuthorFeed failed: {e}", file=sys.stderr)
            break
        items = feed.get("feed") or []
        if not items:
            break

        for item in items:
            post = item.get("post") or {}
            uri = post.get("uri") or ""
            author = (post.get("author") or {}).get("did") or ""
            if not uri or author != did:
                # Not ours (shouldn't happen in our own feed, but be safe with reposts/etc)
                continue
            if uri == pinned_uri:
                continue
            idx = post.get("indexedAt") or ""
            try:
                idx_dt = datetime.fromisoformat(idx.replace("Z", "+00:00"))
            except Exception:
                continue
            if idx_dt > cutoff:
                continue
            rkey = uri.rsplit("/", 1)[-1]
            _snapshot_engagement(post)
            try:
                _req(
                    "/com.atproto.repo.deleteRecord",
                    data={
                        "repo": did,
                        "collection": "app.bsky.feed.post",
                        "rkey": rkey,
                    },
                    headers=auth,
                    method="POST",
                )
                deleted += 1
                print(f"[cleanup] deleted {uri} (indexed {idx})")
            except urllib.error.HTTPError as e:
                print(f"[cleanup] delete HTTP {e.code} for {uri}: {e.read().decode()[:200]}", file=sys.stderr)
            except Exception as e:
                print(f"[cleanup] delete failed for {uri}: {e}", file=sys.stderr)
            if deleted >= MAX_DELETES_PER_RUN:
                print(f"[cleanup] hit per-run cap {MAX_DELETES_PER_RUN} — stop", file=sys.stderr)
                return deleted

        cursor = feed.get("cursor")
        if not cursor:
            break

    if deleted == 0:
        print("[cleanup] nothing old enough to delete")
    return deleted


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=DEFAULT_DELETE_AFTER_HOURS)
    args = p.parse_args()
    sys.exit(0 if cleanup(args.hours) >= 0 else 1)
