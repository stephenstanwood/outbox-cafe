"""Auto-delete every Tumblr post for the cafe at midnight.

Stephen's call (2026-05-19): the cafe is meant to be fully ephemeral. Bluesky
already wiped daily; Tumblr now joins. Every night at midnight PT the cafe
clears its public face on both platforms so each new day starts with a fresh
feed.

Skips pinned posts as a defensive measure (if a pinned welcome ever lands on
Tumblr later, we don't want to nuke it). Caps deletes per run at 200.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENGAGEMENT_SNAPSHOT = ROOT / "data" / "post_engagement.jsonl"

TUMBLR_BASE = "https://api.tumblr.com/v2"
MAX_DELETES_PER_RUN = 200
LIST_LIMIT = 20  # Tumblr posts endpoint caps at 20


def _q(s: Any) -> str:
    return urllib.parse.quote(str(s), safe="")


def _oauth_header(method: str, url: str, *, extra_params: dict[str, str] | None = None) -> str:
    """OAuth 1.0a header. For x-www-form-urlencoded bodies, pass body params via
    extra_params so they're folded into the signature base (required by spec)."""
    oauth_params = {
        "oauth_consumer_key": os.environ["TUMBLR_CONSUMER_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": os.environ["TUMBLR_OAUTH_TOKEN"],
        "oauth_version": "1.0",
    }
    all_params: dict[str, str] = {**oauth_params, **(extra_params or {})}
    param_str = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(all_params.items()))
    base = f"{method.upper()}&{_q(url)}&{_q(param_str)}"
    key = f"{_q(os.environ['TUMBLR_CONSUMER_SECRET'])}&{_q(os.environ['TUMBLR_OAUTH_TOKEN_SECRET'])}"
    oauth_params["oauth_signature"] = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    return "OAuth " + ", ".join(f'{k}="{_q(v)}"' for k, v in oauth_params.items())


def _snapshot_engagement(post: dict[str, Any]) -> None:
    """Freeze final note count before delete so the reflection loop can still
    see what landed weeks ago. Best-effort; never raises."""
    try:
        entry = {
            "platform": "tumblr",
            "id": post.get("id"),
            "post_url": post.get("post_url"),
            "note_count": post.get("note_count", 0),
            "snapshot_ts": datetime.now(timezone.utc).isoformat(),
        }
        if entry["id"] is None:
            return
        ENGAGEMENT_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        with ENGAGEMENT_SNAPSHOT.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[cleanup_tumblr] engagement snapshot failed (non-fatal): {e}", file=sys.stderr)


def _list_posts(blog: str) -> list[dict[str, Any]] | None:
    """List the most recent posts via api_key auth (simpler than OAuth for GET)."""
    consumer_key = os.environ["TUMBLR_CONSUMER_KEY"]
    url = (
        f"{TUMBLR_BASE}/blog/{blog}.tumblr.com/posts"
        f"?api_key={consumer_key}&limit={LIST_LIMIT}&npf=false"
    )
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"[cleanup_tumblr] list HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[cleanup_tumblr] list failed: {e}", file=sys.stderr)
        return None
    return (data.get("response") or {}).get("posts") or []


def _delete_post(blog: str, post_id: int) -> bool:
    url = f"{TUMBLR_BASE}/blog/{blog}.tumblr.com/post/delete"
    fields = {"id": str(post_id)}
    auth = _oauth_header("POST", url, extra_params=fields)
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": auth,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            json.load(r)
        return True
    except urllib.error.HTTPError as e:
        print(f"[cleanup_tumblr] delete {post_id} HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[cleanup_tumblr] delete {post_id} failed: {e}", file=sys.stderr)
    return False


def cleanup() -> int:
    """Delete every post on the cafe's Tumblr blog (skipping any pinned post).

    Re-fetches offset=0 each loop iteration since deletions shift the list.
    Terminates when a page returns only pinned posts (no eligible deletes) or
    when the API returns empty. Returns count deleted.
    """
    blog = os.environ.get("TUMBLR_BLOG_NAME")
    if not blog or not all(
        os.environ.get(k)
        for k in (
            "TUMBLR_CONSUMER_KEY", "TUMBLR_CONSUMER_SECRET",
            "TUMBLR_OAUTH_TOKEN", "TUMBLR_OAUTH_TOKEN_SECRET",
        )
    ):
        print("[cleanup_tumblr] tumblr env vars missing — skip", file=sys.stderr)
        return 0

    deleted = 0
    pages = 0
    while pages < 50:  # defensive bound: 50 * 20 = 1000 candidate posts max
        pages += 1
        posts = _list_posts(blog)
        if posts is None:
            break
        if not posts:
            break

        any_deleted_this_page = False
        for p in posts:
            if p.get("is_pinned"):
                continue
            pid = p.get("id")
            if pid is None:
                continue
            _snapshot_engagement(p)
            if _delete_post(blog, int(pid)):
                deleted += 1
                any_deleted_this_page = True
                print(f"[cleanup_tumblr] deleted {pid} ({p.get('post_url','')})")
            if deleted >= MAX_DELETES_PER_RUN:
                print(f"[cleanup_tumblr] hit per-run cap {MAX_DELETES_PER_RUN} — stop", file=sys.stderr)
                return deleted

        if not any_deleted_this_page:
            break

    if deleted == 0:
        print("[cleanup_tumblr] nothing to delete")
    return deleted


if __name__ == "__main__":
    sys.exit(0 if cleanup() >= 0 else 1)
