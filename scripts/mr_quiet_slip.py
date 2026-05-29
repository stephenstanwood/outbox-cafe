#!/usr/bin/env python3
"""
Mr. Quiet's Sunday Slip — a recurring weekly ritual at 9am PT.

Mr. Quiet (the cat at the back booth) communicates only by leaving slips
of paper on the counter. Every Sunday morning, a single fortune-cookie
aphorism appears in his voice, rendered onto a paper-slip image and
posted to bsky + tumblr.

Why this exists: the cafe is good at moment-by-moment vibes but light on
RECURRENCE — fixed-time rituals people come back to see. A weekly slip
gives the audience a Sunday Schelling point: "what does the slip say
this week?"

The image is rendered with PIL (not generative AI) because the aphorism
text must be exact. Recraft/FLUX mangle one-line text reliably.

Posts ride the daily-fresh-feed convention — Sunday's slip gets swept by
the midnight cleanup. Each slip is also archived locally to
archive/slips/YYYY-MM-DD.png so the cafe's repo keeps a record.

Run on the Mini via scripts/run-slip.sh; cron `0 9 * * 0` (Sun 9am PT).
"""

from __future__ import annotations

import json
import os
import random
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Reuse the bsky OAuth helpers + tumblr OAuth helpers from existing scripts
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

ROOT = SCRIPT_DIR.parent
PERSONAS_PATH = ROOT / "data" / "personas.json"

from lib.llm import claude_cmd
from lib import bsky
SLIPS_DIR = ROOT / "archive" / "slips"
SLIPS_DIR.mkdir(parents=True, exist_ok=True)

BSKY_BASE = "https://bsky.social/xrpc"

# Typewriter font on the Mini — universal macOS.
COURIER_PATHS = [
    "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/Library/Fonts/Courier New.ttf",
]


# ---------- Aphorism generation ----------

APHORISM_PROMPT = """You are Mr. Quiet, the small formal black cat who sits at the back booth of outbox.cafe every day. You never speak. Occasionally a slip of paper appears on the counter with a few words on it.

Today is Sunday morning. A new slip needs to appear on the counter.

Write ONE single line — a fortune-cookie aphorism that takes itself seriously. Cryptic but earnest. Never threatening or weird. Never current events. Never about the cafe's automation or AI. One sentence, one line.

Voice anchors (your past slips):
- "kindness compounds at a higher rate than coffee."
- "the door opens for everyone."
- "today's posting reminds you that you are not the only one trying."
- "the chair you sit in is also resting."
- "what almost made you laugh just gave you a gift."
- "the pictures remember."

Hard requirements:
- One line only. ≤80 characters.
- No quotation marks around it.
- No prefix ("Today:", "Slip:", "Here is:", etc.) — just the line.
- No author tag, no sign-off, no "—Mr. Quiet."
- Period at the end is fine. Question mark is fine. No exclamation marks.
- Original — do NOT repeat any of the voice anchors above.

Output: just the line. Nothing else."""


def _generate_aphorism(model: str = "opus", max_tries: int = 3) -> str | None:
    """Call Claude headless and return ONE clean aphorism line, or None."""
    for attempt in range(max_tries):
        try:
            result = subprocess.run(
                claude_cmd(model),
                input=APHORISM_PROMPT,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as e:
            print(f"[slip] claude call failed (try {attempt+1}): {e}", file=sys.stderr)
            continue
        if result.returncode != 0:
            print(f"[slip] claude exit {result.returncode}: {result.stderr[:200]}", file=sys.stderr)
            continue
        out = (result.stdout or "").strip()
        # Pull the first non-empty line, strip quotes/fences/sign-offs
        for raw_line in out.splitlines():
            line = raw_line.strip()
            line = re.sub(r"^[\"'`*_>]+|[\"'`*_]+$", "", line).strip()
            line = re.sub(r"^```[a-z]*\s*", "", line)
            line = re.sub(r"\s*```\s*$", "", line)
            # Reject obvious prefixes
            if re.match(r"^(here|slip|today|aphorism|line)[:.\s]", line, re.IGNORECASE):
                continue
            # Reject sign-offs
            if re.search(r"—\s*Mr\.\s*Quiet|\bMr\.?\s*Quiet\s*$", line):
                continue
            # Reject if too short or too long
            if 8 <= len(line) <= 110 and "!" not in line:
                return line
        print(f"[slip] no usable line in output (try {attempt+1}); raw: {out[:200]!r}", file=sys.stderr)
    return None


# ---------- Slip image rendering ----------

def _font_path() -> str:
    for p in COURIER_PATHS:
        if Path(p).exists():
            return p
    raise FileNotFoundError("no Courier font found in known paths")


def _render_slip(line: str, out_path: Path) -> Path:
    """Render the aphorism onto a paper-slip image. Returns out_path."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import math

    W = H = 1200

    # Cream/aged paper background with subtle noise
    bg = Image.new("RGB", (W, H), (236, 226, 202))
    # Subtle grain noise
    noise = Image.new("L", (W // 4, H // 4))
    npx = noise.load()
    rng = random.Random(line)  # deterministic per-aphorism noise
    for y in range(noise.height):
        for x in range(noise.width):
            npx[x, y] = 128 + rng.randint(-18, 18)
    noise = noise.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(2.5))
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    bg = Image.blend(bg, noise_rgb, 0.10)

    # Subtle warm vignette
    vignette = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vignette)
    for r in range(0, 400, 4):
        vd.ellipse(
            (W//2 - 600 - r, H//2 - 600 - r, W//2 + 600 + r, H//2 + 600 + r),
            outline=min(255, r * 1),
        )
    vignette = vignette.filter(ImageFilter.GaussianBlur(60))
    bg.paste((180, 160, 130), (0, 0), vignette)

    # The slip itself: an off-white card, slightly rotated, with a shadow
    slip_w, slip_h = 820, 480
    slip = Image.new("RGB", (slip_w, slip_h), (250, 246, 234))
    # Tiny paper-grain noise on the slip too
    sn = Image.new("L", (slip_w // 3, slip_h // 3))
    spx = sn.load()
    rng2 = random.Random(line + "slip")
    for y in range(sn.height):
        for x in range(sn.width):
            spx[x, y] = 200 + rng2.randint(-10, 10)
    sn = sn.resize((slip_w, slip_h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.2))
    sn_rgb = Image.merge("RGB", (sn, sn, sn))
    slip = Image.blend(slip, sn_rgb, 0.10)

    # Two faint ledger lines on the slip — just a hint of "slip of paper"
    sd = ImageDraw.Draw(slip)
    line_color = (210, 195, 165)
    sd.rectangle((40, 60, slip_w - 40, 62), fill=line_color)
    sd.rectangle((40, slip_h - 60, slip_w - 40, slip_h - 58), fill=line_color)

    # Text on the slip, multi-line wrapped, vertically centered
    font_path = _font_path()
    font = ImageFont.truetype(font_path, 44)

    # Word-wrap to fit slip width with padding
    max_text_w = slip_w - 140
    words = line.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] <= max_text_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    # Render lines centered
    line_h = font.getbbox("Mg")[3] - font.getbbox("Mg")[1] + 14
    total_h = line_h * len(lines)
    start_y = (slip_h - total_h) // 2
    text_color = (35, 28, 22)
    for i, ln in enumerate(lines):
        bbox = font.getbbox(ln)
        tw = bbox[2] - bbox[0]
        x = (slip_w - tw) // 2
        # Tiny per-char jitter to feel typewritten (only on a small fraction)
        sd.text((x, start_y + i * line_h), ln, font=font, fill=text_color)

    # Rotate slip slightly (looks dropped on counter, not laid flat)
    angle = rng.uniform(-3.2, 3.2)
    slip_r = slip.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(236, 226, 202))

    # Shadow under the slip
    shadow = Image.new("RGBA", slip_r.size, (0, 0, 0, 0))
    shd = ImageDraw.Draw(shadow)
    shd.rectangle((0, 0, slip_r.width, slip_r.height), fill=(40, 30, 20, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))

    # Paste shadow then slip onto bg, centered
    sx = (W - slip_r.width) // 2
    sy = (H - slip_r.height) // 2 + 8
    bg.paste(shadow, (sx + 8, sy + 14), shadow)
    bg.paste(slip_r, (sx, sy))

    bg.save(out_path, "PNG", optimize=True)
    return out_path


# ---------- Bluesky post ----------

def _bsky_request(path: str, *, data=None, headers=None, method="POST"):
    return bsky.request(path, data=data, headers=headers, method=method)


def _prepare_for_bsky(path: Path) -> tuple[bytes, str]:
    """Bsky blob limit is ~1MB. Recompress if needed."""
    raw = path.read_bytes()
    if len(raw) <= 950_000:
        return raw, "image/png"
    from io import BytesIO
    from PIL import Image
    img = Image.open(BytesIO(raw))
    if img.mode != "RGB":
        img = img.convert("RGB")
    for q in (90, 82, 72, 60):
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if len(data) <= 950_000:
            return data, "image/jpeg"
    return data, "image/jpeg"


def post_to_bsky(text: str, image_path: Path) -> str | None:
    handle = os.environ.get("BSKY_HANDLE")
    pw = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        print("[slip] bsky creds missing — skip bsky", file=sys.stderr)
        return None
    try:
        sess = _bsky_request(
            "/com.atproto.server.createSession",
            data={"identifier": handle, "password": pw},
        )
    except Exception as e:
        print(f"[slip] bsky auth failed: {e}", file=sys.stderr)
        return None
    did = sess["did"]
    auth = {"Authorization": f"Bearer {sess['accessJwt']}"}

    try:
        img_bytes, ct = _prepare_for_bsky(image_path)
        blob_resp = _bsky_request(
            "/com.atproto.repo.uploadBlob",
            data=img_bytes,
            headers={**auth, "Content-Type": ct},
        )
        blob = blob_resp["blob"]
    except Exception as e:
        print(f"[slip] bsky uploadBlob failed: {e}", file=sys.stderr)
        return None

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
        "embed": {
            "$type": "app.bsky.embed.images",
            "images": [{
                "image": blob,
                "alt": f"a slip of paper on the counter: {text}",
            }],
        },
    }
    try:
        resp = _bsky_request(
            "/com.atproto.repo.createRecord",
            data={"repo": did, "collection": "app.bsky.feed.post", "record": record},
            headers=auth,
        )
    except Exception as e:
        print(f"[slip] bsky createRecord failed: {e}", file=sys.stderr)
        return None
    uri = resp.get("uri")
    print(f"[slip] bsky posted: {uri}", file=sys.stderr)
    return uri


# ---------- Tumblr post ----------

# OAuth 1.0a — same as post_tumblr.py
import hashlib
import hmac
import base64
import time as _time


def _q(s: str) -> str:
    return urllib.parse.quote(s, safe="-._~")


def _oauth_header(method: str, url: str, oauth_token_secret: str) -> str:
    params = {
        "oauth_consumer_key": os.environ["TUMBLR_CONSUMER_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(_time.time())),
        "oauth_token": os.environ["TUMBLR_OAUTH_TOKEN"],
        "oauth_version": "1.0",
    }
    base_string = "&".join([
        method.upper(),
        _q(url),
        _q("&".join(f"{k}={_q(v)}" for k, v in sorted(params.items()))),
    ])
    key = f"{_q(os.environ['TUMBLR_CONSUMER_SECRET'])}&{_q(oauth_token_secret)}"
    sig = hmac.new(key.encode(), base_string.encode(), hashlib.sha1).digest()
    params["oauth_signature"] = base64.b64encode(sig).decode()
    return "OAuth " + ", ".join(f'{k}="{_q(v)}"' for k, v in params.items())


def _tumblr_multipart(fields: dict, image_bytes: bytes, image_name: str) -> tuple[bytes, str]:
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


def post_to_tumblr(text: str, image_path: Path) -> str | None:
    blog = os.environ.get("TUMBLR_BLOG_NAME")
    needed = ("TUMBLR_CONSUMER_KEY", "TUMBLR_CONSUMER_SECRET",
              "TUMBLR_OAUTH_TOKEN", "TUMBLR_OAUTH_TOKEN_SECRET")
    if not blog or not all(os.environ.get(k) for k in needed):
        print("[slip] tumblr creds missing — skip tumblr", file=sys.stderr)
        return None

    url = f"https://api.tumblr.com/v2/blog/{blog}.tumblr.com/post"
    auth = _oauth_header("POST", url, os.environ["TUMBLR_OAUTH_TOKEN_SECRET"])

    import html as _html
    caption_html = f"<p>{_html.escape(text)}</p>"
    tags = ["mr quiet", "the cafe", "fortune cookie", "weekly slip", "outbox cafe", "slip of paper"]
    fields = {
        "type": "photo",
        "caption": caption_html,
        "tags": ",".join(tags),
    }
    body, ctype = _tumblr_multipart(fields, image_path.read_bytes(), image_path.name)
    req = urllib.request.Request(
        url, data=body, headers={"Authorization": auth, "Content-Type": ctype}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")[:500]
        print(f"[slip] tumblr HTTP {e.code}: {err}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[slip] tumblr post failed: {e}", file=sys.stderr)
        return None
    post_id = (resp.get("response") or {}).get("id")
    post_url = f"https://{blog}.tumblr.com/post/{post_id}" if post_id else None
    print(f"[slip] tumblr posted: {post_url}", file=sys.stderr)
    return post_url


# ---------- Already-posted check (idempotency) ----------

POST_LOG = ROOT / "data" / "post_log.jsonl"


def _slip_posted_today() -> bool:
    """Return True if a slip post (type=slip*) has been logged today PT."""
    if not POST_LOG.exists():
        return False
    today_pt = datetime.now(timezone.utc).astimezone().date()
    try:
        for raw in POST_LOG.read_text().splitlines()[-200:]:
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except Exception:
                continue
            t = entry.get("type", "")
            if not t.startswith("slip"):
                continue
            ts = entry.get("ts", "")
            try:
                d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
            except Exception:
                continue
            if d == today_pt:
                return True
    except Exception:
        pass
    return False


# ---------- Main ----------

def main():
    if _slip_posted_today() and "--force" not in sys.argv:
        print("[slip] already posted today — skipping (pass --force to override)")
        return 0

    # Generate aphorism (opus by default per memory)
    line = _generate_aphorism(model="opus")
    if not line:
        print("[slip] failed to generate aphorism — aborting", file=sys.stderr)
        return 1

    print(f"[slip] line: {line!r}")

    # Render slip image
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    img_path = SLIPS_DIR / f"{today}.png"
    _render_slip(line, img_path)
    print(f"[slip] image → {img_path}")

    # Post to both platforms
    bsky_uri = post_to_bsky(line, img_path)
    tumblr_url = post_to_tumblr(line, img_path)

    # Log
    try:
        from post_log import log as post_log
        if bsky_uri:
            post_log("slip_bsky", persona="Mr. Quiet", uri=bsky_uri, subject="weekly_slip", text=line)
        if tumblr_url:
            post_log("slip_tumblr", persona="Mr. Quiet", uri=tumblr_url, subject="weekly_slip", text=line)
    except Exception as e:
        print(f"[slip] post_log failed (non-fatal): {e}", file=sys.stderr)

    # Non-zero exit only if BOTH platforms failed — single-platform fails are best-effort.
    if not bsky_uri and not tumblr_url:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
