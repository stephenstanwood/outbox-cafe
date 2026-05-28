"""Post a fresh gen to Bluesky in the voice of a randomly-picked staff persona.

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

from lib.llm import claude_cmd

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_PATH = ROOT / "data" / "personas.json"
BSKY_BASE = "https://bsky.social/xrpc"
POST_MAX_CHARS = 300
# Bluesky's uploadBlob cap is 1,000,000 bytes; leave headroom for protocol overhead.
BSKY_BLOB_MAX = 950_000

# Drop announcements have a configurable skip rate (per personas.json post_types.drop_announcement.skip_rate)
DEFAULT_SKIP_RATE = 0.30


def _load_personas() -> dict[str, Any]:
    return json.loads(PERSONAS_PATH.read_text())


def _pick_staff(rng: random.Random) -> dict[str, Any]:
    data = _load_personas()
    staff = data["staff"]
    from voice_weights import adjusted_weights
    return rng.choices(staff, weights=adjusted_weights(staff), k=1)[0]


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


THROWBACK_PROMPT_TEMPLATE = """You are {name}, staff at outbox.cafe. The cafe is a small place on the internet, run by cats. You're at the corkboard right now and your eye landed on an old poster that's been pinned up for a while.

ABOUT YOU
{species}

YOUR VOICE
{tone}

YOUR PAST POSTS (study rhythm, length, voice — match exactly, including your most chaotic examples)
{examples}

THE POSTER YOUR EYE LANDED ON
{title!r} — says, in part:
{snippet}

YOUR TASK
Post a single short thing on bluesky. NOT an announcement. NOT "found this" or "from the archive" or any meta talk about the cafe. Just react like a cat at the corkboard would — quote a fragment, fixate on one detail, drift onto something the poster reminded you of, or write a sentence that has almost nothing to do with it. Stay weird. Stay specific. Surprise yourself.

UNDER 200 CHARACTERS. DO NOT include any URL. DO NOT include hashtags. End with your signoff exactly as written ({signoff!r}) — or no signoff if empty. Match your typical capitalization, punctuation, rhythm. If your examples sometimes break grammar, break grammar.

HOUSE RULES
- Never sour, never combative, never corrective.
- Never reference AI, bots, models, or automation.
- Never say "weird" or "retro" or "vintage" about the cafe (the cafe doesn't describe itself).
- Skip politics, current events, real death/illness, finance, religion.

OUTPUT THE POST TEXT ONLY. No preamble, no quotes around it, no commentary."""


PROMPT_TEMPLATE = """You are {name}, staff at outbox.cafe. The cafe is a small place on the internet, run by cats. A new poster just went up on the corkboard. You're going to post something on bluesky right now.

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
Post a single short thing. It can react to that poster — obliquely, off-center, fixating on one strange detail, quoting one fragment, mishearing something — OR it can be totally unrelated: something you're noticing in the cafe right now, a memory, a small thought, a sentence that doesn't quite make sense. Whatever a cat would actually post.

CRITICAL: DO NOT announce the poster. NEVER say "new piece is up", "today's posting", "just dropped", "check this out", "the cafe just posted", or any variation. NEVER mention "outbox.cafe" or describe what the cafe is. NEVER use the words "weird" or "retro" or "vintage" about the cafe — the cafe doesn't describe itself. NEVER include a URL. NEVER use hashtags.

Stay weird. Stay specific. Surprise yourself. UNDER 200 CHARACTERS. End with your signoff exactly as written ({signoff!r}) — or no signoff if empty. Match your typical capitalization, punctuation, rhythm. If your examples sometimes break grammar, break grammar.

HOUSE RULES
- Never sour, never combative, never corrective.
- Never reference AI, bots, models, or automation.
- Skip politics, current events, real death/illness, finance, religion.

OUTPUT THE POST TEXT ONLY. No preamble, no quotes around it, no commentary."""


def _call_claude_for_post(
    staff: dict[str, Any],
    title: str,
    snippet: str,
    template: str = PROMPT_TEMPLATE,
) -> str | None:
    """Have Claude write the post text in staff's voice. Returns text or None on failure."""
    prompt = template.format(
        name=staff["name"],
        full_name=staff["full_name"],
        species=staff.get("species", "(unspecified)"),
        tone=staff["tone"],
        topics="\n".join(f"- {t}" for t in staff["topics"]),
        examples="\n\n".join(staff["examples"]),
        title=title,
        snippet=snippet or "(no snippet)",
        signoff=staff.get("signoff", ""),
    )
    try:
        result = subprocess.run(
            # Short persona-voiced post — opus now (Max OAuth = $0 marginal cost)
            claude_cmd("opus"),
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


def _prepare_image_for_bsky(path: Path) -> tuple[bytes, str]:
    """Return (bytes, content_type) for an image guaranteed to be under BSKY_BLOB_MAX.

    If the original PNG is already small enough, returns it untouched. Otherwise
    re-encodes as JPEG, walking down a quality ladder and then downscaling until
    it fits.
    """
    raw = path.read_bytes()
    if len(raw) <= BSKY_BLOB_MAX:
        return raw, "image/png"

    from io import BytesIO
    from PIL import Image

    img = Image.open(BytesIO(raw))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    width = img.width
    best: bytes | None = None
    while width >= 600:
        scaled = img if width == img.width else img.resize(
            (width, round(img.height * width / img.width)), Image.LANCZOS
        )
        for quality in (88, 80, 72, 64, 56):
            buf = BytesIO()
            scaled.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
            data = buf.getvalue()
            if len(data) <= BSKY_BLOB_MAX:
                return data, "image/jpeg"
            best = data
        width = int(width * 0.85)

    # Fell through every step; return the smallest thing we produced.
    return best or raw, "image/jpeg"


def post_drop(
    archive_html_path: Path,
    thumb_png_path: Path | None,
    base_url: str = "https://outbox.cafe",
    seed: int | None = None,
    kind: str = "drop",
) -> bool:
    """Post a piece to Bluesky in a staff cat's voice. Best-effort; False on skip/failure.

    kind="drop": new drop announcement (uses PROMPT_TEMPLATE + skip-rate from personas.json)
    kind="throwback": resurfacing an older archive entry (uses THROWBACK_PROMPT_TEMPLATE + no skip-rate)
    """
    handle = os.environ.get("BSKY_HANDLE")
    app_pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not app_pw:
        print("[post_bsky] skip: BSKY_HANDLE / BSKY_APP_PASSWORD not set", file=sys.stderr)
        return False

    rng = random.Random(seed) if seed is not None else random.Random()

    # Drops honor skip-rate from personas.json; throwbacks are opt-in by the caller.
    if kind == "drop":
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

    template = THROWBACK_PROMPT_TEMPLATE if kind == "throwback" else PROMPT_TEMPLATE
    body_text = _call_claude_for_post(staff, title, snippet, template=template)
    if not body_text:
        print("[post_bsky] claude returned no text — skipping", file=sys.stderr)
        return False

    # No URL in body — outbound links kill engagement and the cafe URL lives in the
    # bio. The thumbnail image is the visual hook; followers profile-click for the rest.
    # Scrub a URL if Claude included one anyway despite the prompt's prohibition.
    body_text = re.sub(r"\bhttps?://\S+", "", body_text).strip()
    body_text = re.sub(r"\s{3,}", "\n\n", body_text).strip()
    full_text = body_text
    if len(full_text.encode("utf-8")) > POST_MAX_CHARS * 4:
        full_text = body_text[:POST_MAX_CHARS].rstrip()

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
            img_bytes, content_type = _prepare_image_for_bsky(thumb_png_path)
            blob_resp = _bsky_request(
                "/com.atproto.repo.uploadBlob",
                data=img_bytes,
                headers={**auth, "Content-Type": content_type},
                method="POST",
            )
            blob = blob_resp["blob"]
            image_embed = {
                "$type": "app.bsky.embed.images",
                "images": [
                    {
                        "image": blob,
                        "alt": title[:300] if title else "the corkboard",
                    }
                ],
            }
        except Exception as e:
            print(f"[post_bsky] image upload failed (continuing without): {e}", file=sys.stderr)

    from datetime import datetime, timezone
    record = {
        "$type": "app.bsky.feed.post",
        "text": full_text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
    }
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

    print(f"[post_bsky] posted ({kind}): {resp.get('uri','?')}", file=sys.stderr)
    try:
        from post_log import log as post_log
        post_log(
            kind,
            persona=staff["name"],
            uri=resp.get("uri"),
            subject=f"our:{archive_html_path.name}",
            text=body_text,
        )
    except Exception:
        pass
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
        for candidate in (
            ROOT / "archive" / "social" / (html_path.stem + ".png"),
            ROOT / "archive" / "thumbs" / (html_path.stem + ".png"),
        ):
            if candidate.exists():
                thumb_path = candidate
                break
    ok = post_drop(html_path, thumb_path, seed=args.seed)
    sys.exit(0 if ok else 1)
