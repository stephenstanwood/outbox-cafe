"""Shared Bluesky (atproto) client.

One request builder + one login, replacing ~9 near-identical inline `_bsky`/`_auth`
helpers across the posting/engagement/ritual scripts. The logic is copied verbatim
from engage_bsky's originals so behavior is unchanged; login() gains the transient
retry (timeouts) that only engage had, applied everywhere now.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://bsky.social/xrpc"


def request(path: str, *, data=None, headers=None, method=None, timeout: int = 30) -> dict:
    """Build + send an XRPC request, return parsed JSON. dict/list data → JSON body;
    bytes data → raw body (e.g. uploadBlob)."""
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if isinstance(data, (dict, list)):
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, bytes):
        body = data
    req = urllib.request.Request(f"{BASE}{path}", data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def login(handle: str | None = None, app_pw: str | None = None, *, timeout: int = 30) -> tuple[str, str]:
    """createSession → (did, accessJwt). Reads BSKY_HANDLE / BSKY_APP_PASSWORD from
    env if not passed. Retries transient failures (e.g. bsky's periodic auth-endpoint
    timeouts) up to 3x with backoff, but re-raises HTTPError immediately — a 4xx/5xx
    (bad creds) won't succeed on retry."""
    handle = handle or os.environ["BSKY_HANDLE"]
    app_pw = app_pw or os.environ["BSKY_APP_PASSWORD"]
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            sess = request(
                "/com.atproto.server.createSession",
                data={"identifier": handle, "password": app_pw},
                method="POST",
                timeout=timeout,
            )
            return sess["did"], sess["accessJwt"]
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    assert last_err is not None
    raise last_err


def create_post(did: str, jwt: str, record: dict, *, attempts: int = 3,
                timeout: int = 30) -> dict:
    """createRecord for an `app.bsky.feed.post`, retrying transient failures
    without ever double-posting.

    bsky.social periodically times out for a few minutes (see login()). A
    timeout on createRecord is ambiguous — the post may have landed and only
    the response was lost — so a naive retry can post twice. Before each retry
    this lists the repo's newest posts and, if one carries this record's exact
    `createdAt` (unique to this call), treats it as the success it was.
    HTTPError is re-raised immediately: a 4xx/5xx won't clear on retry.

    Pancake's 2026-09-12 act 2 was lost to exactly one such timeout with no
    retry, leaving the Saturday sequence without its middle on Bluesky.
    """
    auth = {"Authorization": f"Bearer {jwt}"}
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            return request(
                "/com.atproto.repo.createRecord",
                data={"repo": did, "collection": "app.bsky.feed.post", "record": record},
                headers=auth,
                method="POST",
                timeout=timeout,
            )
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last_err = e
            if attempt == attempts - 1:
                break
            time.sleep(3 * (attempt + 1))
            found = _find_post_by_created_at(did, jwt, record.get("createdAt", ""))
            if found:
                return found
    assert last_err is not None
    raise last_err


def _find_post_by_created_at(did: str, jwt: str, created_at: str) -> dict | None:
    """Newest few posts in the repo; the one whose createdAt matches, if any.
    Best-effort — any failure here just means we go ahead and retry."""
    if not created_at:
        return None
    try:
        import urllib.parse
        q = urllib.parse.urlencode({"repo": did, "collection": "app.bsky.feed.post", "limit": 10})
        resp = request(f"/com.atproto.repo.listRecords?{q}",
                       headers={"Authorization": f"Bearer {jwt}"}, timeout=20)
    except Exception:
        return None
    for rec in resp.get("records") or []:
        if (rec.get("value") or {}).get("createdAt") == created_at:
            return {"uri": rec.get("uri"), "cid": rec.get("cid")}
    return None
