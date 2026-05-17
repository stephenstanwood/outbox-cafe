"""Generate per-gen images via fal.ai FLUX schnell, given a rolled spec.

Returns image dicts in the same shape as images.py's fetch_images() so the
prompt block treats them interchangeably — but with source='ai' so the
prompt template can skip the photographer-credit requirement.

FLUX schnell ($0.003/image) is plenty for inline page art and finishes in
a few seconds per image.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

FAL_BASE = "https://fal.run"
FLUX_SCHNELL = "/fal-ai/flux/schnell"


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
