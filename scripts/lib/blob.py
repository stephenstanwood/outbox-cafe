"""Vercel Blob — host on-site images (cabinet thumbnails) off-repo so they don't
bloat git. Public store.

Two halves:
- URL construction (cabinet + OG tags) needs NO token. Random-suffix is disabled on
  upload, so every blob's public URL is deterministic: PUBLIC_BASE/<pathname>.
- Uploads need BLOB_READ_WRITE_TOKEN (Mini .env / Vercel project env).

Store: outbox-cafe-blob. PUBLIC_BASE is the store id (lowercased) — stable per store.
"""
from __future__ import annotations

import json
import os
import urllib.request

API = "https://blob.vercel-storage.com"
API_VERSION = "7"
PUBLIC_BASE = "https://ct21rptue7ejhsay.public.blob.vercel-storage.com"


def thumb_url(stem: str) -> str:
    """Deterministic public URL for a cabinet thumbnail (no token needed)."""
    return f"{PUBLIC_BASE}/thumbs/{stem}.webp"


def put_bytes(pathname: str, data: bytes, content_type: str) -> str:
    """Upload bytes to the blob store at `pathname`; return the public URL. Needs token."""
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN not set")
    req = urllib.request.Request(
        f"{API}/{pathname}",
        data=data,
        method="PUT",
        headers={
            "authorization": f"Bearer {token}",
            "x-api-version": API_VERSION,
            "x-content-type": content_type,
            "x-add-random-suffix": "0",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["url"]
