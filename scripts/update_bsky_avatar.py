#!/usr/bin/env python3
"""Update the Bluesky profile avatar from a local PNG.

Run on the Mini with the usual env sourced:

  set -a && . ~/Projects/mini-Codex-proxy/.env && . ~/Projects/outbox-cafe/.env && set +a
  python3 scripts/update_bsky_avatar.py favicon.png
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from pathlib import Path

from lib import bsky


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?", default="favicon.png", help="PNG avatar path")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.is_absolute():
        image_path = ROOT / image_path
    if not image_path.exists():
        sys.exit(f"missing avatar image: {image_path}")

    handle = os.environ.get("BSKY_HANDLE")
    app_pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not app_pw:
        sys.exit("missing BSKY_HANDLE / BSKY_APP_PASSWORD")

    did, jwt = bsky.login(handle, app_pw)
    auth = {"Authorization": f"Bearer {jwt}"}
    print(f"auth ok: {handle} -> {did}")

    profile = bsky.request(
        "/com.atproto.repo.getRecord"
        f"?repo={urllib.parse.quote(did)}&collection=app.bsky.actor.profile&rkey=self",
        headers=auth,
    )
    record = profile["value"]
    cid = profile["cid"]
    old_avatar = record.get("avatar", {}).get("ref", {}).get("$link", "(none)")

    blob = bsky.request(
        "/com.atproto.repo.uploadBlob",
        data=image_path.read_bytes(),
        headers={**auth, "Content-Type": "image/png"},
        method="POST",
    )["blob"]
    record["avatar"] = blob

    bsky.request(
        "/com.atproto.repo.putRecord",
        data={
            "repo": did,
            "collection": "app.bsky.actor.profile",
            "rkey": "self",
            "record": record,
            "swapRecord": cid,
        },
        headers=auth,
        method="POST",
    )

    new_avatar = blob.get("ref", {}).get("$link", "(unknown)")
    print(f"avatar updated: {old_avatar} -> {new_avatar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
