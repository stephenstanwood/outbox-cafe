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
import os
import secrets
import time
import urllib.parse

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
