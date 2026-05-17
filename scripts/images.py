"""Fetch Unsplash images for the rolled spec subject.

Free tier: 50 requests/hour. We use ~6/hour during stash mode (one per gen),
~1/hour in production, so well within limit.

Returns up to N photo objects with the URL, alt text, and the photographer
credit info Unsplash's free-tier terms require us to display.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

UNSPLASH_API = "https://api.unsplash.com"


def fetch_images(
    query: str,
    count: int = 3,
    orientation: str | None = None,
) -> list[dict[str, Any]]:
    """Search Unsplash for `query`. Returns up to `count` image dicts:
    { url, alt, credit_name, credit_username, credit_link, html_link }
    Empty list on missing key, network failure, or zero results.
    """
    key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not key:
        return []
    query = (query or "").strip()
    if not query:
        return []
    params: list[tuple[str, str]] = [
        ("query", query),
        ("per_page", str(count)),
        ("content_filter", "high"),
    ]
    if orientation in ("landscape", "portrait", "squarish"):
        params.append(("orientation", orientation))
    qs = urllib.parse.urlencode(params)
    url = f"{UNSPLASH_API}/search/photos?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Client-ID {key}",
            "Accept-Version": "v1",
            "User-Agent": "outbox-cafe-bot/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for photo in data.get("results", [])[:count]:
        # Trigger Unsplash's download-tracking pixel per their guidelines.
        # Fire-and-forget; do not block generation on this.
        try:
            dl = photo.get("links", {}).get("download_location")
            if dl:
                dl_req = urllib.request.Request(
                    dl,
                    headers={"Authorization": f"Client-ID {key}"},
                )
                urllib.request.urlopen(dl_req, timeout=5).read()
        except Exception:
            pass
        urls = photo.get("urls", {}) or {}
        user = photo.get("user", {}) or {}
        out.append({
            "url": urls.get("regular") or urls.get("small") or "",
            "alt": (photo.get("alt_description") or photo.get("description") or "").strip(),
            "credit_name": user.get("name") or user.get("username") or "",
            "credit_username": user.get("username") or "",
            "credit_link": (user.get("links") or {}).get("html") or "",
            "html_link": (photo.get("links") or {}).get("html") or "",
        })
    return out


def derive_query(spec: dict[str, Any]) -> str:
    """Build an Unsplash search query from the rolled spec.

    Most weight on the subject. The era hints style but the subject is the
    real topic. Strip articles and meta-phrasing.
    """
    def v(field: str) -> str:
        item = spec.get(field, {})
        if isinstance(item, dict):
            return item.get("value") or item.get("key") or ""
        return str(item)

    subject = v("subject")
    # Drop leading article + truncate after first parenthesis/comma
    import re
    subject = re.sub(r"^(a|an|the|one)\s+", "", subject, flags=re.I)
    subject = re.split(r"[(,]", subject, maxsplit=1)[0].strip()
    return subject[:80]


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "lighthouse"
    photos = fetch_images(q, count=3)
    print(f"query: {q!r}  ({len(photos)} results)")
    for p in photos:
        print(f"  {p['url'][:80]}  — {p['credit_name']} ({p['alt'][:40]})")
