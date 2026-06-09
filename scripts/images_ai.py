"""Generate per-gen images via fal.ai, given a rolled spec.

Two models in play:
- FLUX schnell ($0.003/image) for the 3 inline page-art images. Fast, cheap,
  and these are small page filler — occasional mangled "text" inside an
  illustration isn't a big deal.
- Recraft v3 ($0.04/image) for the dedicated social poster. This is the
  thumb that hits Tumblr/Bluesky timelines, so text-mangling here is very
  visible. Recraft is purpose-built for posters and actually respects
  "no text" instructions in a way FLUX schnell does not.

Returns image dicts in the same shape as images.py's fetch_images() so the
prompt block treats them interchangeably — source='ai' tells the template
to skip the photographer-credit requirement.
"""
from __future__ import annotations

import json
import os
import random
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

FAL_BASE = "https://fal.run"
FLUX_SCHNELL = "/fal-ai/flux/schnell"
RECRAFT_V3 = "/fal-ai/recraft-v3"

# Recraft styles to rotate the poster through. Raster only — the
# `vector_illustration/*` styles return SVG, which PIL can't open and which
# Tumblr/Bluesky don't accept as raster thumbnails.
RECRAFT_POSTER_STYLES = [
    "digital_illustration",
    "digital_illustration/pixel_art",
    "digital_illustration/hand_drawn",
    "digital_illustration/grain",
]

# Pixel art has a strong prior to render the subject AS faux-text (game-UI
# labels, sprite signage). The generic "no text" suffix in derive_poster_prompt
# gets ignored at that resolution, so for pixel_art we lead with a louder
# negative — "SILVERHAND CLUB" mangled to "SIWFFHANO CLUE" is the failure mode.
PIXEL_ART_NO_TEXT_PREFIX = (
    "Pixel art illustration with absolutely no text, no letters, no words, "
    "no titles, no labels, no signs, no UI elements. "
)


def _v(spec: dict[str, Any], field: str) -> str:
    item = spec.get(field, {})
    if isinstance(item, dict):
        return item.get("value") or ""
    return str(item) if item else ""


def _clean_subject(subject: str) -> str:
    """Strip leading article + truncate at first paren/comma (same as derive_query)."""
    s = re.sub(r"^(a|an|the|one)\s+", "", subject, flags=re.I)
    s = re.split(r"[(,]", s, maxsplit=1)[0].strip()
    return s


def derive_ai_prompts(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """Build 3 (prompt, alt) pairs from the spec dimensions.

    `prompt` is the generation instruction (with the "no text/watermark"
    boilerplate); `alt` is a clean human-readable description for screen readers
    — none of that boilerplate, just what the image depicts.
    """
    era = _v(spec, "era")
    subject = _v(spec, "subject")
    palette = _v(spec, "palette")
    tone = _v(spec, "tone")

    subj = _clean_subject(subject)

    style_parts = []
    if era:
        style_parts.append(era)
    if palette:
        style_parts.append(f"palette of {palette}")
    if tone:
        style_parts.append(f"{tone} mood")
    style = ", ".join(style_parts) if style_parts else ""

    suffix = "illustrated artwork, no text or captions, no watermarks, no logos"

    # alt: era's leading short token only (eras can be a paragraph) + the scene.
    era_short = re.split(r"[—,(]", era, maxsplit=1)[0].strip() if era else ""
    alt_tail = f" ({era_short})" if era_short else ""
    descs = [
        f"Wide establishing illustration of {subj}",
        f"Close-up evocative detail relating to {subj}",
        f"Abstract symbolic interpretation of {subj}",
    ]
    return [(f"{d}. {style}. {suffix}", f"{d}{alt_tail}") for d in descs]


def derive_poster_prompt(spec: dict[str, Any], max_chars: int | None = None) -> str:
    """A single bold composition for the social thumbnail — distinct from inline page art.

    If `max_chars` is set, the result is trimmed to fit. Recraft's API rejects
    prompts >1000 chars (HTTP 422), and spec dimensions can be verbose, so the
    Recraft path passes max_chars=990. Head ("Poster artwork for X") and the
    "no text" suffix are preserved; the style descriptors in the middle are
    truncated at a word boundary if needed.
    """
    era = _v(spec, "era")
    subject = _v(spec, "subject")
    palette = _v(spec, "palette")
    tone = _v(spec, "tone")
    subj = _clean_subject(subject)

    style_bits = []
    if era:
        style_bits.append(era)
    if palette:
        style_bits.append(f"palette of {palette}")
    if tone:
        style_bits.append(f"{tone} mood")
    style = ". ".join(style_bits)

    # Recraft respects "no text" reliably; FLUX does not. Both get the
    # explicit instruction so the prompt is the same regardless of which
    # model handles it.
    suffix = "single strong central subject, bold confident composition, square format, suitable as a cover image. absolutely no text, no captions, no letters, no typography, no words, no signs, no titles, no watermarks, no logos."
    head = f"Poster artwork for {subj}"

    if max_chars is not None:
        # Reserve room for head + suffix (and ". " joiners) — trim style to fit.
        overhead = len(head) + len(suffix) + 4
        remaining = max_chars - overhead
        if remaining <= 0:
            return ". ".join([head, suffix])
        if len(style) > remaining:
            style = style[:remaining].rsplit(" ", 1)[0]

    parts = [head]
    if style:
        parts.append(style)
    parts.append(suffix)
    return ". ".join(parts)


# Human-readable names for the Recraft styles, used in alt text.
_STYLE_HUMAN = {
    "digital_illustration": "digital illustration",
    "digital_illustration/pixel_art": "pixel-art illustration",
    "digital_illustration/hand_drawn": "hand-drawn illustration",
    "digital_illustration/grain": "grainy digital illustration",
}


def derive_poster_alt(spec: dict[str, Any], style: str | None = None) -> str:
    """Real alt text for the social poster: what the image actually depicts.

    Built from the same spec the generation prompt came from, minus all the
    "no text / no watermark" boilerplate — a screen-reader user should hear
    the scene, not the prompt engineering.
    """
    subj = _clean_subject(_v(spec, "subject")) or "the cafe's latest posting"
    era = _v(spec, "era")
    palette = _v(spec, "palette")
    era_short = re.split(r"[—,(]", era, maxsplit=1)[0].strip() if era else ""
    style_name = _STYLE_HUMAN.get(style or "", "illustration")
    parts = [f"Poster-style {style_name} of {subj}"]
    if era_short:
        parts.append(f"in a {era_short} style")
    desc = " ".join(parts)
    if palette:
        # First palette segment only — palettes can be long prose
        pal_short = re.split(r"[—,(;]", palette, maxsplit=1)[0].strip()
        if pal_short:
            desc += f", palette of {pal_short}"
    return desc[:290] + "."


def fetch_poster_image(
    spec: dict[str, Any],
    out_path: Path,
    timeout: int = 60,
) -> bool:
    """Generate a dedicated social-poster image and save as PNG to `out_path`.

    Uses Recraft v3 (better text suppression than FLUX schnell, ~$0.04/gen).
    Falls back to FLUX schnell if Recraft errors out, so a Recraft outage
    doesn't drop the social poster entirely.

    Also writes a `<stem>.alt.txt` sidecar next to the PNG with a real
    description of the image (derived from the spec), so the social posters
    can ship descriptive alt text long after this process exits (throwbacks,
    spotlights). Best-effort like the image itself.

    Returns True on success. Best-effort: any failure logs and returns False.
    """
    from io import BytesIO

    key = os.environ.get("FAL_KEY")
    if not key:
        return False

    # Recraft caps prompts at 1000 chars (HTTP 422 otherwise); FLUX has no
    # such limit, so the fallback gets the untrimmed version.
    style: str | None
    result = _recraft_poster(spec, key, timeout)
    if result is None:
        print("[images_ai/poster] recraft failed — falling back to FLUX schnell")
        img_bytes = _flux_poster(derive_poster_prompt(spec), key, timeout)
        style = None
    else:
        img_bytes, style = result
    if img_bytes is None:
        return False

    try:
        from PIL import Image
        img = Image.open(BytesIO(img_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, format="PNG", optimize=True)
    except Exception as e:
        print(f"[images_ai/poster] save failed: {e}")
        return False

    try:
        alt_path = out_path.with_suffix(".alt.txt")
        alt_path.write_text(derive_poster_alt(spec, style))
    except Exception as e:
        print(f"[images_ai/poster] alt sidecar failed (non-fatal): {e}")

    return True


def _recraft_poster(spec: dict[str, Any], key: str, timeout: int) -> "tuple[bytes, str] | None":
    style = random.choice(RECRAFT_POSTER_STYLES)
    prefix = PIXEL_ART_NO_TEXT_PREFIX if style == "digital_illustration/pixel_art" else ""
    prompt = prefix + derive_poster_prompt(spec, max_chars=990 - len(prefix))
    body = json.dumps({
        "prompt": prompt,
        "image_size": "square_hd",
        "style": style,
    }).encode()
    req = urllib.request.Request(
        f"{FAL_BASE}{RECRAFT_V3}",
        data=body,
        headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"[images_ai/poster] recraft HTTP {e.code}: {err}")
        return None
    except Exception as e:
        print(f"[images_ai/poster] recraft call failed: {e}")
        return None

    url = (data.get("images") or [{}])[0].get("url")
    if not url:
        print(f"[images_ai/poster] no image url in recraft response: {json.dumps(data)[:200]}")
        return None
    print(f"[images_ai/poster] recraft style={style}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read(), style
    except Exception as e:
        print(f"[images_ai/poster] download failed: {e}")
        return None


def _flux_poster(prompt: str, key: str, timeout: int) -> bytes | None:
    body = json.dumps({
        "prompt": prompt,
        "image_size": "square_hd",
        "num_images": 1,
        "num_inference_steps": 4,
        "enable_safety_checker": False,
    }).encode()
    req = urllib.request.Request(
        f"{FAL_BASE}{FLUX_SCHNELL}",
        data=body,
        headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception as e:
        print(f"[images_ai/poster] flux fallback failed: {e}")
        return None
    url = (data.get("images") or [{}])[0].get("url")
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        print(f"[images_ai/poster] flux download failed: {e}")
        return None


def fetch_ai_images(
    spec: dict[str, Any],
    count: int = 3,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Generate `count` images via fal.ai. Empty list on missing key or any failure."""
    key = os.environ.get("FAL_KEY")
    if not key:
        return []

    prompts = derive_ai_prompts(spec)[:count]
    out: list[dict[str, Any]] = []
    for prompt, alt in prompts:
        body = json.dumps({
            "prompt": prompt,
            "image_size": "square_hd",
            "num_images": 1,
            "num_inference_steps": 4,
            "enable_safety_checker": False,
        }).encode()
        req = urllib.request.Request(
            f"{FAL_BASE}{FLUX_SCHNELL}",
            data=body,
            headers={
                "Authorization": f"Key {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")[:200]
            print(f"[images_ai] fal HTTP {e.code}: {err}")
            continue
        except Exception as e:
            print(f"[images_ai] fal call failed: {e}")
            continue
        url = (data.get("images") or [{}])[0].get("url")
        if not url:
            print(f"[images_ai] no image url in response: {json.dumps(data)[:200]}")
            continue
        out.append({
            "url": url,
            "alt": alt[:240],
            "credit_name": "",
            "credit_username": "",
            "credit_link": "",
            "html_link": "",
            "source": "ai",
        })
    return out


if __name__ == "__main__":
    import sys
    if not sys.stdin.isatty():
        spec = json.loads(sys.stdin.read())
    else:
        spec = {
            "era": {"value": "1934 WPA poster"},
            "subject": {"value": "a lighthouse on a small island"},
            "palette": {"value": "burnt orange and deep teal"},
            "tone": {"value": "earnest"},
        }
    for p, a in derive_ai_prompts(spec):
        print(" prompt:", p)
        print(" alt:   ", a)
    print()
    imgs = fetch_ai_images(spec)
    print(f"{len(imgs)} image(s)")
    for im in imgs:
        print(" ", im["url"][:80], "-", im["alt"][:80])
