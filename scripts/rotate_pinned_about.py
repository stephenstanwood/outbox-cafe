#!/usr/bin/env python3
"""
One-shot: rotate the bsky pinned post to a new version that points to
/about/ (the staff roster), and update the profile description to also
link /about/.

Pattern (per playbook): createRecord new post → putRecord profile with
new description + pinnedPost ref → deleteRecord old pinned post.

Run on the Mini with both env files sourced:
  set -a && . ~/Projects/mini-claude-proxy/.env && . ~/Projects/outbox-cafe/.env && set +a
  python3 scripts/rotate_pinned_about.py
"""

from __future__ import annotations

import os
import sys
import urllib.error
from datetime import datetime, timezone

from lib import bsky


NEW_PINNED_TEXT = (
    "hello. m here. small place on the internet, staffed entirely by cats. "
    "fresh posting four times a day — catalog, tiny game, small confusion. "
    "sign-offs vary because we all post; there's a roster at "
    "https://outbox.cafe/about/ if you want to know who's who. -M"
)

NEW_DESCRIPTION = (
    "a small place on the internet. a new post four times a day. door is "
    "propped open with a paperback. (run by cats — staff roster at "
    "https://outbox.cafe/about/) - https://outbox.cafe/"
)


def req(path: str, *, data=None, headers=None, method="GET"):
    try:
        return bsky.request(path, data=data, headers=headers, method=method)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code} on {path}: {e.read().decode('utf-8','ignore')[:500]}\n")
        raise


def link_facets(text: str) -> list[dict]:
    """Return facets array for every http(s) URL in `text`. Byte-indexed."""
    out = []
    i = 0
    while True:
        # search for http
        idx = text.find("http", i)
        if idx < 0:
            break
        # find end of URL (whitespace or end)
        end = idx
        while end < len(text) and not text[end].isspace():
            end += 1
        # strip trailing punctuation that's not part of URL
        url = text[idx:end].rstrip(").,;:!?")
        # compute byte offsets
        byte_start = len(text[:idx].encode("utf-8"))
        byte_end = byte_start + len(url.encode("utf-8"))
        out.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        })
        i = end
    return out


def main():
    handle = os.environ.get("BSKY_HANDLE")
    pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        sys.exit("missing BSKY_HANDLE / BSKY_APP_PASSWORD")

    # 1. auth (retries transient bsky auth-endpoint timeouts)
    did, jwt = bsky.login(handle, pw)
    auth = {"Authorization": f"Bearer {jwt}"}
    print(f"auth ok: {handle} → {did}")

    # 2. fetch current profile (so we preserve avatar/banner/displayName)
    prof = req(
        "/com.atproto.repo.getRecord"
        f"?repo={did}&collection=app.bsky.actor.profile&rkey=self",
    )
    profile_record = prof["value"]
    profile_cid = prof["cid"]
    old_pinned = profile_record.get("pinnedPost")
    print(f"old pinned uri: {old_pinned.get('uri') if old_pinned else '(none)'}")
    print(f"old description: {profile_record.get('description','(none)')[:120]}")

    # 3. create new pinned post
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    pinned_record = {
        "$type": "app.bsky.feed.post",
        "text": NEW_PINNED_TEXT,
        "createdAt": now,
        "langs": ["en"],
        "facets": link_facets(NEW_PINNED_TEXT),
    }
    create_resp = req(
        "/com.atproto.repo.createRecord",
        data={
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": pinned_record,
        },
        headers=auth,
        method="POST",
    )
    new_uri = create_resp["uri"]
    new_cid = create_resp["cid"]
    print(f"new pinned post: {new_uri}")

    # 4. putRecord profile with new description + pinnedPost ref
    profile_record["description"] = NEW_DESCRIPTION
    profile_record["pinnedPost"] = {"uri": new_uri, "cid": new_cid}
    req(
        "/com.atproto.repo.putRecord",
        data={
            "repo": did,
            "collection": "app.bsky.actor.profile",
            "rkey": "self",
            "record": profile_record,
            "swapRecord": profile_cid,
        },
        headers=auth,
        method="POST",
    )
    print("profile updated (description + pinned ref)")

    # 5. delete old pinned post
    if old_pinned and old_pinned.get("uri"):
        old_rkey = old_pinned["uri"].rsplit("/", 1)[-1]
        try:
            req(
                "/com.atproto.repo.deleteRecord",
                data={
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "rkey": old_rkey,
                },
                headers=auth,
                method="POST",
            )
            print(f"deleted old pinned post: {old_pinned['uri']}")
        except Exception as e:
            print(f"(non-fatal) couldn't delete old pinned: {e}")

    print("done.")


if __name__ == "__main__":
    main()
