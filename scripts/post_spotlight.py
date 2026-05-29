"""Spotlight a flagged gen on Bluesky + Tumblr.

Unlike the daily cat-staff posts (post_bsky / post_tumblr), spotlights:
- Include the permalink (with a clickable bsky facet) so people can find it
- Use a direct, non-persona voice — text is supplied at call time, not LLM-generated
- Are not subject to the daily skip-rate dice
- Are reserved for gens Stephen specifically flags as worth pointing at

Reads bsky text from --bsky-text or, if omitted, stdin first chunk separated
by '---' from tumblr text. Both auth setups are reused from post_bsky /
post_tumblr; env vars must be present in the calling shell.

Defaults to dry-run. Pass --post to actually publish.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Reuse the network plumbing already proven in the existing posters.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_bsky import (  # noqa: E402
    BSKY_BASE,
    _bsky_request,
    _extract_title,
    _find_url_byterange,
    _prepare_image_for_bsky,
)
from post_tumblr import _build_multipart_legacy, _oauth_header  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://outbox.cafe"
DEFAULT_TAGS = "outbox cafe,small web,weird internet,generative,html"


def _post_bluesky(text: str, archive_url: str, thumb_path: Path | None, title: str) -> bool:
    handle = os.environ.get("BSKY_HANDLE")
    app_pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not app_pw:
        print("[spotlight/bsky] skip: BSKY_HANDLE / BSKY_APP_PASSWORD not set", file=sys.stderr)
        return False

    try:
        sess = _bsky_request(
            "/com.atproto.server.createSession",
            data={"identifier": handle, "password": app_pw},
            method="POST",
        )
    except Exception as e:
        print(f"[spotlight/bsky] auth failed: {e}", file=sys.stderr)
        return False
    did = sess["did"]
    auth = {"Authorization": f"Bearer {sess['accessJwt']}"}

    image_embed = None
    if thumb_path and thumb_path.exists():
        try:
            img_bytes, content_type = _prepare_image_for_bsky(thumb_path)
            blob = _bsky_request(
                "/com.atproto.repo.uploadBlob",
                data=img_bytes,
                headers={**auth, "Content-Type": content_type},
                method="POST",
            )["blob"]
            image_embed = {
                "$type": "app.bsky.embed.images",
                "images": [{"image": blob, "alt": (f"Illustrated cover for {title}"[:300]
                                                   if title else "An illustrated cover from outbox.cafe")}],
            }
        except Exception as e:
            print(f"[spotlight/bsky] image upload failed (continuing without): {e}", file=sys.stderr)

    facets = []
    span = _find_url_byterange(text, archive_url)
    if span:
        start, end = span
        facets.append({
            "index": {"byteStart": start, "byteEnd": end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": archive_url}],
        })

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
    }
    if facets:
        record["facets"] = facets
    if image_embed:
        record["embed"] = image_embed

    try:
        resp = _bsky_request(
            "/com.atproto.repo.createRecord",
            data={"repo": did, "collection": "app.bsky.feed.post", "record": record},
            headers=auth,
            method="POST",
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:500]
        print(f"[spotlight/bsky] createRecord HTTP {e.code}: {body}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[spotlight/bsky] createRecord failed: {e}", file=sys.stderr)
        return False

    print(f"[spotlight/bsky] posted: {resp.get('uri','?')}", file=sys.stderr)
    try:
        from post_log import log as post_log
        post_log("spotlight_bsky", persona="(spotlight)", uri=resp.get("uri"),
                 subject=archive_url, text=text)
    except Exception:
        pass
    return True


def _post_tumblr(text: str, archive_url: str, thumb_path: Path | None,
                 title: str, tags: str) -> bool:
    blog = os.environ.get("TUMBLR_BLOG_NAME")
    required = ("TUMBLR_CONSUMER_KEY", "TUMBLR_CONSUMER_SECRET",
                "TUMBLR_OAUTH_TOKEN", "TUMBLR_OAUTH_TOKEN_SECRET")
    if not blog or not all(os.environ.get(k) for k in required):
        print("[spotlight/tumblr] skip: tumblr env vars missing", file=sys.stderr)
        return False

    # Render the body with paragraphs and a real <a> link so the permalink is clickable
    # inside the post (Tumblr only auto-links URLs in some surfaces).
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    body_parts = []
    for p in paragraphs:
        safe = _html.escape(p).replace("\n", "<br>")
        # Linkify the bare archive URL if it appears in this paragraph.
        if archive_url in p:
            safe = safe.replace(
                _html.escape(archive_url),
                f'<a href="{archive_url}">{_html.escape(archive_url)}</a>',
            )
        body_parts.append(f"<p>{safe}</p>")
    caption_html = "".join(body_parts)

    url = f"https://api.tumblr.com/v2/blog/{blog}.tumblr.com/post"
    auth_header = _oauth_header("POST", url, os.environ["TUMBLR_OAUTH_TOKEN_SECRET"])

    try:
        if thumb_path and thumb_path.exists():
            fields = {
                "type": "photo",
                "caption": caption_html,
                "tags": tags,
                # Tying the image to the archive URL makes the photo itself a tap-target
                # back to the gen — desirable for spotlights (unlike daily drops).
                "link": archive_url,
            }
            body, ctype = _build_multipart_legacy(
                fields, thumb_path.read_bytes(), thumb_path.name
            )
        else:
            fields = {
                "type": "text",
                "title": title[:200],
                "body": caption_html,
                "tags": tags,
            }
            body = urllib.parse.urlencode(fields).encode()
            ctype = "application/x-www-form-urlencoded"
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": auth_header, "Content-Type": ctype},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")[:500]
        print(f"[spotlight/tumblr] HTTP {e.code}: {err_body}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[spotlight/tumblr] post failed: {e}", file=sys.stderr)
        return False

    post_id = (resp.get("response") or {}).get("id")
    post_url = f"https://{blog}.tumblr.com/post/{post_id}" if post_id else "(no id)"
    print(f"[spotlight/tumblr] posted: {post_url}", file=sys.stderr)
    try:
        from post_log import log as post_log
        post_log("spotlight_tumblr", persona="(spotlight)", uri=post_url,
                 subject=archive_url, text=text)
    except Exception:
        pass
    return True


def _resolve_thumb(html_path: Path) -> Path | None:
    for sub in ("social", "thumbs"):
        candidate = ROOT / "archive" / sub / (html_path.stem + ".png")
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("html", help="path to archive/YYYY-MM-DDTHH-MM.html")
    p.add_argument("--bsky-text", help="bluesky post text (≤300 chars including URL)")
    p.add_argument("--tumblr-text", help="tumblr post text (longer ok)")
    p.add_argument("--tags", default=DEFAULT_TAGS, help="comma-separated tumblr tags")
    p.add_argument("--thumb", help="override thumbnail path")
    p.add_argument("--post", action="store_true",
                   help="actually post (default is dry-run preview)")
    p.add_argument("--skip-bsky", action="store_true")
    p.add_argument("--skip-tumblr", action="store_true")
    args = p.parse_args()

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"[spotlight] not found: {html_path}", file=sys.stderr)
        return 1

    title = _extract_title(html_path.read_text()) or "(untitled)"
    archive_url = f"{BASE_URL}/archive/{html_path.name}"
    thumb_path = Path(args.thumb) if args.thumb else _resolve_thumb(html_path)

    if not args.bsky_text and not args.tumblr_text:
        print("[spotlight] need --bsky-text and/or --tumblr-text", file=sys.stderr)
        return 1

    print(f"[spotlight] gen: {html_path.name}", file=sys.stderr)
    print(f"[spotlight] title: {title}", file=sys.stderr)
    print(f"[spotlight] url:   {archive_url}", file=sys.stderr)
    print(f"[spotlight] thumb: {thumb_path}", file=sys.stderr)
    print("", file=sys.stderr)

    if args.bsky_text:
        print("--- bluesky ---", file=sys.stderr)
        print(args.bsky_text, file=sys.stderr)
        print(f"({len(args.bsky_text)} chars)", file=sys.stderr)
        print("", file=sys.stderr)
    if args.tumblr_text:
        print("--- tumblr ---", file=sys.stderr)
        print(args.tumblr_text, file=sys.stderr)
        print(f"(tags: {args.tags})", file=sys.stderr)
        print("", file=sys.stderr)

    if not args.post:
        print("[spotlight] dry-run only. Re-run with --post to actually publish.", file=sys.stderr)
        return 0

    results = []
    if args.bsky_text and not args.skip_bsky:
        results.append(("bsky", _post_bluesky(args.bsky_text, archive_url, thumb_path, title)))
    if args.tumblr_text and not args.skip_tumblr:
        results.append(("tumblr", _post_tumblr(args.tumblr_text, archive_url, thumb_path,
                                               title, args.tags)))

    failed = [name for name, ok in results if not ok]
    if failed:
        print(f"[spotlight] failures: {', '.join(failed)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
