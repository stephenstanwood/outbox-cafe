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
from typing import Any

FAL_BASE = "https://fal.run"
FLUX_SCHNELL = "/fal-ai/flux/schnell"
RECRAFT_V3 = "/fal-ai/recraft-v3"

# Recraft styles to rotate the poster through. All of them respect "no text"
# better than FLUX and read as illustrated posters (not stock photos).
RECRAFT_POSTER_STYLES = [
    "digital_illustration",
    "vector_illustration",
    "digital_illustration/pixel_art",
    "digital_illustration/hand_drawn",
    "digital_illustration/grain",
    "vector_illustration/engraving",
]


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


def derive_ai_prompts(spec: dict[str, Any]) -> list[str]:
    """Build 3 distinct image prompts from the spec dimensions."""
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

    return [
        f"Wide establishing illustration of {subj}. {style}. {suffix}",
        f"Close-up evocative detail relating to {subj}. {style}. {suffix}",
        f"Abstract symbolic interpretation of {subj}. {style}. {suffix}",
    ]


def derive_poster_prompt(spec: dict[str, Any]) -> str:
    """A single bold composition for the social thumbnail — distinct from inline page art."""
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
    parts = [f"Poster artwork for {subj}"]
    if style:
        parts.append(style)
    parts.append(suffix)
    return ". ".join(parts)


def fetch_poster_image(
    spec: dict[str, Any],
    out_path: "Path",
    timeout: int = 60,
) -> bool:
    """Generate a dedicated social-poster image and save as PNG to `out_path`.

    Uses Recraft v3 (better text suppression than FLUX schnell, ~$0.04/gen).
    Falls back to FLUX schnell if Recraft errors out, so a Recraft outage
    doesn't drop the social poster entirely.

    Returns True on success. Best-effort: any failure logs and returns False.
    """
    from io import BytesIO
    from pathlib import Path  # noqa: F401  (typing-only above)

    key = os.environ.get("FAL_KEY")
    if not key:
        return False

    prompt = derive_poster_prompt(spec)
    img_bytes = _recraft_poster(prompt, key, timeout)
    if img_bytes is None:
        print("[images_ai/poster] recraft failed — falling back to FLUX schnell")
        img_bytes = _flux_poster(prompt, key, timeout)
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

    return True


def _recraft_poster(prompt: str, key: str, timeout: int) -> bytes | None:
    style = random.choice(RECRAFT_POSTER_STYLES)
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
            return r.read()
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
    for prompt in prompts:
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
            "alt": prompt[:240],
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
    for p in derive_ai_prompts(spec):
        print(" prompt:", p)
    print()
    imgs = fetch_ai_images(spec)
    print(f"{len(imgs)} image(s)")
    for im in imgs:
        print(" ", im["url"][:80], "-", im["alt"][:80])
