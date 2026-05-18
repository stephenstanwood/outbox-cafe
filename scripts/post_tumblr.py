"""Cross-post each fresh gen to Tumblr (outbox-cafe.tumblr.com).

Posting philosophy (carried from SBT — see project CLAUDE.md):
- The post is the point. We don't ship people off to our site at every turn.
- Posts stand alone: a quote, a fragment, a specific detail + the thumbnail
  image. Followers see content; trust builds; they seek us out.
- No "read more" CTA, no clickable funnel photo. Profile bio carries the URL.
- Exception: a truly interactive piece (game/puzzle/toy) that can't live in a
  post — those CAN link. Not the default.

Tumblr-specific differences from Bluesky:
- Posts stick around — Tumblr is an archive by nature. No auto-delete.
- Tags drive discovery — we attach a small set of small-web / generative tags.
- Captions can be longer than 200 chars. The cats can stretch out a bit.

Stays in cat-staff voice via personas.json. Per-gen skip rate honors
`post_types.tumblr_drop.skip_rate` (default 0.5) so the feed isn't an
hourly bot firehose. OAuth 1.0a, photo post with binary thumb upload.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html as _html
import json
import os
import random
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_PATH = ROOT / "data" / "personas.json"

DEFAULT_SKIP_RATE = 0.5
DEFAULT_TAGS = [
    "small web", "weird internet", "indie web", "generative",
    "html", "new on tumblr", "handmade web", "outbox cafe",
]


def _q(s: Any) -> str:
    return urllib.parse.quote(str(s), safe="")


def _load_personas() -> dict[str, Any]:
    return json.loads(PERSONAS_PATH.read_text())


def _pick_staff(rng: random.Random) -> dict[str, Any]:
    personas = _load_personas()
    staff = personas["staff"]
    weights = [s["weight"] for s in staff]
    return rng.choices(staff, weights=weights, k=1)[0]


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", m.group(1).strip()) if m else ""


def _extract_snippet(html: str, max_chars: int = 600) -> str:
    body = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


PROMPT_TEMPLATE = """You are {name}, staff at outbox.cafe. The cafe is a small place on the internet, run by cats. A new poster just went up on the corkboard. You're writing a short Tumblr post.

ABOUT YOU
{species}

YOUR VOICE
{tone}

YOUR PAST POSTS (study rhythm, length, voice — match exactly, including your most chaotic examples)
{examples}

ON THE CORKBOARD RIGHT NOW
{title!r} — says, in part:
{snippet}

YOUR TASK
Write 2-5 sentences that stand alone as great content. The post itself IS the thing — there's no link to click through to, no "read more". A follower scrolling Tumblr should get something whole from your post on its own.

Best moves: quote a striking fragment verbatim, fixate on one specific detail, lift a line and react to it, riff on a strange image or phrase from the poster. The thumbnail image will appear above your text and does the visual work — your words add texture, not summary.

Tumblr lets you stretch more than Bluesky — be a little wordier than a tweet, but don't pad. DO NOT include any URL. DO NOT include hashtags (those go in the tags field separately).

CRITICAL RULES
- Stay weird. Stay specific. Surprise yourself.
- Never describe the cafe as "weird", "retro", "vintage", etc. The cafe doesn't describe itself.
- Never reference AI, bots, models, or automation.
- Never sour, combative, or corrective.
- Never use "found this", "from the archive", "new piece", "just dropped", or any meta-announcement phrasing — the post is content, not a teaser.
- Skip politics, current events, real death/illness, finance, religion.

OUTPUT JUST THE POST TEXT. No preamble, no quotes around it, no commentary. Match your typical capitalization, punctuation, and rhythm. End with your signoff exactly as written ({signoff!r}) — or no signoff if empty."""


def _call_claude(staff: dict[str, Any], title: str, snippet: str) -> str | None:
    prompt = PROMPT_TEMPLATE.format(
        name=staff["name"],
        species=staff.get("species", "(unspecified)"),
        tone=staff["tone"],
        examples="\n\n".join(staff["examples"]),
        title=title,
        snippet=snippet or "(no snippet)",
        signoff=staff.get("signoff", ""),
    )
    try:
        result = subprocess.run(
            ["claude", "--print", "--tools", "", "--model", "haiku"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        print(f"[post_tumblr] claude subprocess failed: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"[post_tumblr] claude exit {result.returncode}: {result.stderr[:200]}", file=sys.stderr)
        return None
    text = (result.stdout or "").strip()
    text = re.sub(r"^```[a-z]*\s*", "", text).strip()
    text = re.sub(r"\s*```\s*$", "", text).strip()
    if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
        text = text[1:-1].strip()
    # Scrub URLs/hashtags if the LLM ignored the instructions
    text = re.sub(r"\bhttps?://\S+", "", text).strip()
    text = re.sub(r"#\w+", "", text).strip()
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or None


def _oauth_header(method: str, url: str, oauth_token_secret: str = "") -> str:
    """OAuth 1.0a Authorization header. For multipart requests, only oauth_* params
    go into the signature base — form fields do NOT."""
    params = {
        "oauth_consumer_key": os.environ["TUMBLR_CONSUMER_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": os.environ["TUMBLR_OAUTH_TOKEN"],
        "oauth_version": "1.0",
    }
    param_str = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(params.items()))
    base = f"{method.upper()}&{_q(url)}&{_q(param_str)}"
    key = f"{_q(os.environ['TUMBLR_CONSUMER_SECRET'])}&{_q(oauth_token_secret)}"
    params["oauth_signature"] = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    return "OAuth " + ", ".join(f'{k}="{_q(v)}"' for k, v in params.items())


def _build_multipart_legacy(
    fields: dict[str, str],
    image_bytes: bytes,
    image_name: str = "thumb.png",
) -> tuple[bytes, str]:
    """Multipart body for the legacy /post endpoint: simple form fields + a `data` image file."""
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


def post_drop(
    archive_html_path: Path,
    thumb_png_path: Path | None,
    base_url: str = "https://outbox.cafe",
    seed: int | None = None,
    spec_format: str | None = None,
) -> bool:
    """Post a fresh gen to Tumblr in cat-staff voice. Best-effort; False on skip/failure."""
    blog = os.environ.get("TUMBLR_BLOG_NAME")
    if not blog or not all(
        os.environ.get(k)
        for k in ("TUMBLR_CONSUMER_KEY", "TUMBLR_CONSUMER_SECRET",
                  "TUMBLR_OAUTH_TOKEN", "TUMBLR_OAUTH_TOKEN_SECRET")
    ):
        print("[post_tumblr] skip: tumblr env vars missing", file=sys.stderr)
        return False

    rng = random.Random(seed) if seed is not None else random.Random()

    skip_rate = DEFAULT_SKIP_RATE
    try:
        personas = _load_personas()
        skip_rate = float(
            personas.get("post_types", {}).get("tumblr_drop", {}).get("skip_rate", DEFAULT_SKIP_RATE)
        )
    except Exception:
        pass
    if rng.random() < skip_rate:
        print(f"[post_tumblr] skip (random skip-rate {skip_rate})", file=sys.stderr)
        return False

    try:
        html_text = archive_html_path.read_text()
    except Exception as e:
        print(f"[post_tumblr] couldn't read {archive_html_path}: {e}", file=sys.stderr)
        return False

    title = _extract_title(html_text) or "(untitled)"
    snippet = _extract_snippet(html_text)
    archive_url = f"{base_url}/archive/{archive_html_path.name}"

    staff = _pick_staff(rng)
    print(f"[post_tumblr] persona={staff['name']} title={title[:60]!r}", file=sys.stderr)

    body_text = _call_claude(staff, title, snippet)
    if not body_text:
        print("[post_tumblr] claude returned no text — skipping", file=sys.stderr)
        return False

    tags = list(DEFAULT_TAGS)
    if spec_format:
        clean = re.sub(r"[^a-z0-9\s]", "", spec_format.lower()).strip()
        if clean and len(clean) <= 40:
            tags.insert(0, clean)

    # Caption: just the cat's words. No "read →", no link funnel — the post
    # stands alone. (Tumblr profile bio carries the cafe URL for anyone curious.)
    safe_text = _html.escape(body_text).replace("\n", "<br>")
    caption_html = f"<p>{safe_text}</p>"

    url = f"https://api.tumblr.com/v2/blog/{blog}.tumblr.com/post"
    auth = _oauth_header("POST", url, os.environ["TUMBLR_OAUTH_TOKEN_SECRET"])

    try:
        if thumb_png_path and thumb_png_path.exists():
            # No `link` field — we don't want the photo to be a clickable
            # funnel back to the archive page. Photo opens in Tumblr's
            # normal post view, which is fine.
            fields = {
                "type": "photo",
                "caption": caption_html,
                "tags": ",".join(tags),
            }
            body, ctype = _build_multipart_legacy(fields, thumb_png_path.read_bytes(), thumb_png_path.name)
        else:
            fields = {
                "type": "text",
                "title": title[:200],
                "body": caption_html,
                "tags": ",".join(tags),
            }
            body = urllib.parse.urlencode(fields).encode()
            ctype = "application/x-www-form-urlencoded"
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": auth, "Content-Type": ctype},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")[:500]
        print(f"[post_tumblr] HTTP {e.code}: {err_body}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[post_tumblr] post failed: {e}", file=sys.stderr)
        return False

    post_id = (resp.get("response") or {}).get("id")
    post_url = f"https://{blog}.tumblr.com/post/{post_id}" if post_id else "(no id)"
    print(f"[post_tumblr] posted: {post_url}", file=sys.stderr)
    try:
        from post_log import log as post_log
        post_log(
            "tumblr_drop",
            persona=staff["name"],
            uri=post_url,
            subject=f"our:{archive_html_path.name}",
            text=body_text,
        )
    except Exception:
        pass
    return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("html", help="path to an archive/*.html file")
    p.add_argument("--thumb", help="optional path to a thumbnail .png")
    p.add_argument("--seed", type=int)
    p.add_argument("--format", help="optional spec format tag")
    args = p.parse_args()
    ok = post_drop(
        Path(args.html),
        Path(args.thumb) if args.thumb else None,
        seed=args.seed,
        spec_format=args.format,
    )
    sys.exit(0 if ok else 1)
