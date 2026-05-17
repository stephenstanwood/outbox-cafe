"""Post a hourly gen to Bluesky in the voice of a randomly-picked staff persona.

Each post:
- Picks a staff member (weighted)
- Calls Claude to write the post in that staff member's voice, given the
  gen's title + snippet + URL
- Authenticates with Bluesky using BSKY_HANDLE + BSKY_APP_PASSWORD
- Uploads the thumbnail as an image blob (alt text = staff member's
  one-line take, fallback to title)
- Posts text + image + clickable URL facet

Best-effort: any failure prints to stderr and returns False, never raises.
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_PATH = ROOT / "data" / "personas.json"
BSKY_BASE = "https://bsky.social/xrpc"
POST_MAX_CHARS = 300

# Drop announcements have a configurable skip rate (per personas.json post_types.drop_announcement.skip_rate)
DEFAULT_SKIP_RATE = 0.30


def _load_personas() -> dict[str, Any]:
    return json.loads(PERSONAS_PATH.read_text())


def _pick_staff(rng: random.Random) -> dict[str, Any]:
    data = _load_personas()
    staff = data["staff"]
    weights = [s["weight"] for s in staff]
    return rng.choices(staff, weights=weights, k=1)[0]


def _extract_snippet(html: str, max_chars: int = 400) -> str:
    """Grab some visible-feeling text from the HTML body for prompt context."""
    # Strip <script> and <style> blocks
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    # decode common html entities crudely
    t = m.group(1)
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\s+", " ", t).strip()


PROMPT_TEMPLATE = """You are {name} ({full_name}), an employee or fixture at outbox.cafe — a constantly-evolving weird/retro corner of the internet that posts a new artifact every hour. You're writing a short Bluesky post about the new piece that just went up.

YOUR VOICE
{tone}

TOPICS YOU TEND TO POST ABOUT
{topics}

EXAMPLES OF YOUR PAST POSTS (study the rhythm, length, and voice carefully — match this exactly)
{examples}

HOUSE RULES (apply to every post, no exceptions)
- Relentlessly positive. Never sour.
- Never fight people. Never break character.
- Never reference AI, bots, models, or that this is automated.
- Don't post about politics, current events, real death/illness, financial advice, or religion.

THE NEW PIECE (just went up at the cafe)
Title: {title}
Snippet from the page: {snippet}

YOUR TASK
Write a short Bluesky post in your voice about this new piece. Under 200 characters (the URL will be added separately on a new line — do NOT include any URL in your text). Don't have to mention the title directly — be oblique, quote something specific, react to a detail, or use one of your usual topics as an entry point. End with your signoff exactly as written ({signoff!r}) — or no signoff if it's empty. Use lowercase / capitals / punctuation exactly the way your examples do.

OUTPUT ONLY THE POST TEXT. No preamble, no quotes around it, no explanation."""


def _call_claude_for_post(staff: dict[str, Any], title: str, snippet: str) -> str | None:
    """Have Claude write the post text in staff's voice. Returns text or None on failure."""
    prompt = PROMPT_TEMPLATE.format(
        name=staff["name"],
        full_name=staff["full_name"],
        tone=staff["tone"],
        topics="\n".join(f"- {t}" for t in staff["topics"]),
        examples="\n\n".join(staff["examples"]),
        title=title,
        snippet=snippet or "(no snippet)",
        signoff=staff.get("signoff", ""),
    )
    try:
        result = subprocess.run(
            ["claude", "--print", "--tools", "", "--permission-mode", "plan"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        print(f"[post_bsky] claude subprocess failed: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"[post_bsky] claude exit {result.returncode}: {result.stderr[:200]}", file=sys.stderr)
        return None
    text = result.stdout.strip()
    # Strip stray code fences / quotes that LLMs sometimes wrap with
    text = re.sub(r"^```[a-z]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip()
    if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
        text = text[1:-1]
    return text


def _bsky_request(path: str, *, data=None, headers=None, method=None) -> dict:
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if isinstance(data, (dict, list)):
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, bytes):
        body = data
    req = urllib.request.Request(f"{BSKY_BASE}{path}", data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _find_url_byterange(text: str, url: str) -> tuple[int, int] | None:
    """Find the UTF-8 byte offsets of `url` in `text`. Bsky facets are byte-indexed."""
    idx = text.find(url)
    if idx < 0:
        return None
    prefix_bytes = text[:idx].encode("utf-8")
    url_bytes = url.encode("utf-8")
    return len(prefix_bytes), len(prefix_bytes) + len(url_bytes)


def post_drop(
    archive_html_path: Path,
    thumb_png_path: Path | None,
    base_url: str = "https://outbox.cafe",
    seed: int | None = None,
) -> bool:
    """Post a hourly drop announcement to Bluesky. Best-effort; returns False on any skip/failure."""
    handle = os.environ.get("BSKY_HANDLE")
    app_pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not app_pw:
        print("[post_bsky] skip: BSKY_HANDLE / BSKY_APP_PASSWORD not set", file=sys.stderr)
        return False

    rng = random.Random(seed) if seed is not None else random.Random()

    # Skip-rate honors personas.json post_types.drop_announcement.skip_rate
    skip_rate = DEFAULT_SKIP_RATE
    try:
        personas = _load_personas()
        skip_rate = float(personas.get("post_types", {}).get("drop_announcement", {}).get("skip_rate", DEFAULT_SKIP_RATE))
    except Exception:
        pass
    if rng.random() < skip_rate:
        print(f"[post_bsky] skip (random skip-rate {skip_rate})", file=sys.stderr)
        return False

    try:
        html = archive_html_path.read_text()
    except Exception as e:
        print(f"[post_bsky] couldn't read {archive_html_path}: {e}", file=sys.stderr)
        return False

    title = _extract_title(html) or "(untitled)"
    snippet = _extract_snippet(html, max_chars=400)
    archive_url = f"{base_url}/archive/{archive_html_path.name}"

    staff = _pick_staff(rng)
    print(f"[post_bsky] persona={staff['name']} title={title[:60]!r}", file=sys.stderr)

    body_text = _call_claude_for_post(staff, title, snippet)
    if not body_text:
        print("[post_bsky] claude returned no text — skipping", file=sys.stderr)
        return False

    # Stitch URL onto the post on its own line
    full_text = body_text + "\n\n" + archive_url
    if len(full_text.encode("utf-8")) > POST_MAX_CHARS * 4:
        # If somehow huge, truncate body
        budget = POST_MAX_CHARS - len(archive_url) - 4
        body_text = body_text[:budget].rstrip()
        full_text = body_text + "\n\n" + archive_url

    # Authenticate
    try:
        sess = _bsky_request(
            "/com.atproto.server.createSession",
            data={"identifier": handle, "password": app_pw},
            method="POST",
        )
    except Exception as e:
        print(f"[post_bsky] auth failed: {e}", file=sys.stderr)
        return False
    did = sess["did"]
    jwt = sess["accessJwt"]
    auth = {"Authorization": f"Bearer {jwt}"}

    # Upload thumb as image blob (if present)
    image_embed = None
    if thumb_png_path and thumb_png_path.exists():
        try:
            img_bytes = thumb_png_path.read_bytes()
            blob_resp = _bsky_request(
                "/com.atproto.repo.uploadBlob",
                data=img_bytes,
                headers={**auth, "Content-Type": "image/png"},
                method="POST",
            )
            blob = blob_resp["blob"]
            image_embed = {
                "$type": "app.bsky.embed.images",
                "images": [
                    {
                        "image": blob,
                        "alt": title[:300] if title else "the new piece at outbox.cafe",
                    }
                ],
            }
        except Exception as e:
            print(f"[post_bsky] image upload failed (continuing without): {e}", file=sys.stderr)

    # Build facets so the URL is clickable
    facets = []
    rng_url = _find_url_byterange(full_text, archive_url)
    if rng_url:
        facets.append({
            "index": {"byteStart": rng_url[0], "byteEnd": rng_url[1]},
            "features": [
                {"$type": "app.bsky.richtext.facet#link", "uri": archive_url}
            ],
        })

    from datetime import datetime, timezone
    record = {
        "$type": "app.bsky.feed.post",
        "text": full_text,
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
            data={
                "repo": did,
                "collection": "app.bsky.feed.post",
                "record": record,
            },
            headers=auth,
            method="POST",
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:500]
        print(f"[post_bsky] createRecord HTTP {e.code}: {body}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[post_bsky] createRecord failed: {e}", file=sys.stderr)
        return False

    print(f"[post_bsky] posted: {resp.get('uri','?')}", file=sys.stderr)
    return True


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("html", help="path to archive/YYYY-MM-DDTHH-MM.html")
    p.add_argument("--thumb", help="path to thumbnail PNG (optional)")
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    html_path = Path(args.html)
    thumb_path = Path(args.thumb) if args.thumb else None
    if thumb_path is None:
        guessed = ROOT / "archive" / "thumbs" / (html_path.stem + ".png")
        if guessed.exists():
            thumb_path = guessed
    ok = post_drop(html_path, thumb_path, seed=args.seed)
    sys.exit(0 if ok else 1)
