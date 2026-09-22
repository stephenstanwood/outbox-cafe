"""Shared Tumblr OAuth 1.0a signer + multipart builder.

Replaces the near-identical inline signers that used to live in cleanup_tumblr,
doris_muffin, like_loop, pancake_sequence, mr_quiet_slip, post_tumblr and
reblog_tumblr. The algorithm is HMAC-SHA1 over the standard OAuth 1.0a base
string; a wrong signature is a silent 401, so this was proved byte-identical
to each original before migrating.

Creds from env (TUMBLR_CONSUMER_KEY/SECRET, TUMBLR_OAUTH_TOKEN/_SECRET).
`params` = extra params folded into the signature base, per spec:
- GET query params: fold them in.
- x-www-form-urlencoded POST bodies: fold them in.
- multipart/form-data POST bodies: do NOT fold (only oauth_* params sign).
- JSON POST bodies (NPF /posts): do NOT fold.
`token_secret` overrides the env token secret (mr_quiet passes it explicitly).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.tumblr.com/v2"


def _q(s) -> str:
    return urllib.parse.quote(str(s), safe="")


def oauth_header(method: str, url: str, *, params: dict | None = None,
                 token_secret: str | None = None) -> str:
    oauth = {
        "oauth_consumer_key": os.environ["TUMBLR_CONSUMER_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": os.environ["TUMBLR_OAUTH_TOKEN"],
        "oauth_version": "1.0",
    }
    all_params = {**(params or {}), **oauth}
    param_str = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(all_params.items()))
    base = f"{method.upper()}&{_q(url)}&{_q(param_str)}"
    ts = token_secret if token_secret is not None else os.environ["TUMBLR_OAUTH_TOKEN_SECRET"]
    key = f"{_q(os.environ['TUMBLR_CONSUMER_SECRET'])}&{_q(ts)}"
    oauth["oauth_signature"] = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    return "OAuth " + ", ".join(f'{k}="{_q(v)}"' for k, v in oauth.items())


def build_multipart(
    fields: dict[str, str],
    image_bytes: bytes,
    image_name: str = "thumb.png",
) -> tuple[bytes, str]:
    """Multipart body for the legacy /post endpoint: simple form fields + a `data`
    image file part. Returns (body, content_type). Sign the request WITHOUT
    folding `fields` into the signature (multipart bodies are excluded per spec)."""
    boundary = "----outboxcafe" + secrets.token_hex(12)
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="data"; filename="{image_name}"\r\n'.encode())
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(image_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# create_post — the only way to make a post. Retries transient failures
# without double-posting.
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _plain(s: str) -> str:
    """HTML → comparable plaintext: tags out, entities decoded, space collapsed."""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", s or ""))).strip().lower()


def _fingerprint(fields: dict) -> str:
    """A distinctive prefix of the words this post carries. Tumblr rewrites the
    HTML it stores, so this compares normalized text, not markup — and only the
    head of it, since the tail is where rewriting shows up."""
    parts = [fields.get("title") or "", fields.get("caption") or "", fields.get("body") or ""]
    return _plain(" ".join(p for p in parts if p))[:120]


def _post_text(post: dict) -> str:
    """Comparable plaintext for a post as the API hands it back. Legacy fields
    first; NPF `content` blocks too, in case the blog serves those instead."""
    parts = [post.get("title") or "", post.get("caption") or "", post.get("body") or ""]
    for block in post.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return _plain(" ".join(p for p in parts if p))


def _find_recent_post(blog: str, fingerprint: str, since: float) -> dict | None:
    """The newest post carrying this fingerprint, if it's already there.
    Best-effort — any failure here just means we go ahead and retry, because a
    duplicate post (swept at midnight anyway) beats a drop that never lands."""
    if not fingerprint:
        return None
    try:
        url = f"{BASE}/blog/{blog}.tumblr.com/posts?limit=8&npf=false"
        req = urllib.request.Request(
            url, headers={"Authorization": oauth_header("GET", f"{BASE}/blog/{blog}.tumblr.com/posts",
                                                        params={"limit": "8", "npf": "false"})})
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.load(r)
    except Exception:
        return None
    for post in (resp.get("response") or {}).get("posts") or []:
        if float(post.get("timestamp") or 0) < since - 60:
            continue
        if _post_text(post).startswith(fingerprint):
            return post
    return None


def create_post(blog: str, fields: dict[str, str], *,
                image_bytes: bytes | None = None, image_name: str = "thumb.png",
                attempts: int = 3, timeout: int = 30) -> dict:
    """POST to the legacy /post endpoint, retrying transient failures.

    Tumblr's write path periodically times out (`<urlopen error The write
    operation timed out>`). A timeout is ambiguous — the post may have landed
    and only the response was lost — so a naive retry can post twice. Before
    each retry this lists the blog's newest posts and, if one already carries
    this post's words, treats it as the success it was. HTTPError is re-raised
    immediately: a 4xx/5xx won't clear on retry.

    Every attempt signs fresh (OAuth nonces are single-use). `fields` are the
    legacy form fields; pass `image_bytes` for a photo post and they go up as
    multipart, otherwise urlencoded — the two sign differently, per spec.

    The 2026-09-22 12:11 drop was lost to exactly one such timeout with no
    retry, and left that gen with no Tumblr post at all.
    """
    url = f"{BASE}/blog/{blog}.tumblr.com/post"
    fp = _fingerprint(fields)
    started = time.time()
    last_err: Exception | None = None

    for attempt in range(attempts):
        if image_bytes is not None:
            body, ctype = build_multipart(fields, image_bytes, image_name)
            auth = oauth_header("POST", url)  # multipart: fields NOT in signature
        else:
            body = urllib.parse.urlencode(fields).encode()
            ctype = "application/x-www-form-urlencoded"
            auth = oauth_header("POST", url, params=fields)  # urlencoded: fields sign
        req = urllib.request.Request(
            url, data=body,
            headers={"Authorization": auth, "Content-Type": ctype},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last_err = e
            if attempt == attempts - 1:
                break
            # Sleep first: the posts listing lags a create by a few seconds
            # (same stale cache that makes deletes look undone), so checking
            # immediately would miss a post that did land.
            time.sleep(5 * (attempt + 1))
            found = _find_recent_post(blog, fp, started)
            if found:
                return {"response": {"id": found.get("id"), "recovered": True}}

    assert last_err is not None
    raise last_err
