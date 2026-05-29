"""Shared Tumblr OAuth 1.0a signer.

Replaces 5 near-identical inline signers (cleanup_tumblr, doris_muffin, like_loop,
pancake_sequence, mr_quiet_slip). The algorithm is HMAC-SHA1 over the standard
OAuth 1.0a base string; a wrong signature is a silent 401, so this is proved
byte-identical to each original before migrating (see the equivalence test).

Creds from env (TUMBLR_CONSUMER_KEY/SECRET, TUMBLR_OAUTH_TOKEN/_SECRET).
`params` = extra params (query string or x-www-form-urlencoded body) folded into
the signature base, per spec. `token_secret` overrides the env token secret
(mr_quiet passes it explicitly).
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
