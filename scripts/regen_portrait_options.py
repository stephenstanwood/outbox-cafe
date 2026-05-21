#!/usr/bin/env python3
"""
One-shot: regenerate M. and Robin portraits for /about/ — the originals
had FLUX-mangled chalkboard text in the background. This run forbids
all text/signage/lettering and uses Recraft v3 (much better at text
suppression than FLUX schnell, per project memory).

Generates 4 variants per cat, saves to /tmp/portrait_options/, so Stephen
can pick the keeper from a small picker.

Run on the Mini:
  set -a && . ~/Projects/mini-claude-proxy/.env && . ~/Projects/outbox-cafe/.env && set +a
  python3 scripts/regen_portrait_options.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

FAL_BASE = "https://fal.run"
RECRAFT_V3 = "/fal-ai/recraft-v3"
STYLE = "digital_illustration"

NO_TEXT = (
    "Absolutely no text, no letters, no words, no signs, no menus, "
    "no chalkboards, no menu boards, no signage, no lettering, no labels, "
    "no writing of any kind anywhere in the image. "
)

CATS = {
    "m": {
        "label": "M.",
        "prompt": (
            "A portrait illustration of a tortoiseshell cat — orange and black "
            "patchy fur, yellow eyes, about eleven years old, slightly thick "
            "around the middle. She is the manager of a small cafe, sitting "
            "calmly on a tall wooden stool. Simple wooden cafe counter behind "
            "her with a few small bottles and jars (no labels). Warm cream "
            "background. Friendly, observant expression. Bold black outlines, "
            "flat color fills, hand-drawn storybook style."
        ),
    },
    "robin": {
        "label": "Robin",
        "prompt": (
            "A portrait illustration of a young orange tabby cat — bright "
            "orange fur with tabby stripes, big yellow eyes, white chest, "
            "small and full of stamina. He is the barista, standing alertly "
            "on a wooden cafe counter next to a copper-colored espresso "
            "machine (no labels or text on the machine). Warm cream "
            "background. Eager, enthusiastic expression. Bold black "
            "outlines, flat color fills, hand-drawn storybook style."
        ),
    },
}

OUT = Path("/tmp/portrait_options")
OUT.mkdir(parents=True, exist_ok=True)


def gen_one(prompt: str, key: str, idx: int, out_path: Path):
    body = json.dumps({
        "prompt": NO_TEXT + prompt,
        "image_size": "square_hd",
        "style": STYLE,
    }).encode()
    req = urllib.request.Request(
        f"{FAL_BASE}{RECRAFT_V3}",
        data=body,
        headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")[:300]
        print(f"  HTTP {e.code}: {err}")
        return False
    url = (data.get("images") or [{}])[0].get("url")
    if not url:
        print(f"  no image url: {json.dumps(data)[:200]}")
        return False
    with urllib.request.urlopen(url, timeout=60) as r:
        out_path.write_bytes(r.read())
    print(f"  → {out_path}")
    return True


def main():
    key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not key:
        sys.exit("missing FAL_KEY")

    n_variants = 4
    for cat_id, c in CATS.items():
        print(f"\n{c['label']} ({cat_id}) — {n_variants} variants")
        for i in range(1, n_variants + 1):
            out = OUT / f"{cat_id}-{i}.png"
            gen_one(c["prompt"], key, i, out)


if __name__ == "__main__":
    main()
