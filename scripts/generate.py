"""Main hourly entry point for outbox.cafe.

Rolls a spec, calls Claude via the local `claude` CLI (uses Max OAuth, no API $),
writes the HTML to archive/ and copies to index.html, refreshes the cabinet
listing, appends the spec to history, optionally commits and pushes.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from prompt import build_prompt
from spec import (
    append_history,
    format_spec_for_human,
    load_dimensions,
    roll_spec,
)

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
INDEX_PATH = ROOT / "index.html"
CABINET_PATH = ARCHIVE_DIR / "index.html"

PT = ZoneInfo("America/Los_Angeles")


def call_claude(prompt: str, model: str | None = None, timeout: int = 600) -> str:
    """Call the local claude CLI in --print mode with tools disabled.

    Without --tools "", claude operates agentically — it picks up Write/Edit
    and modifies files itself rather than printing the result. We want pure
    text-out: prompt in, HTML out.
    """
    cmd = [
        "claude",
        "--print",
        "--tools", "",
        "--permission-mode", "plan",
    ]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude failed (exit {result.returncode}): {result.stderr[:500]}")
    return result.stdout


def extract_html(raw: str) -> str:
    """Pull the HTML document out of Claude's response, in case it wrapped it."""
    s = raw.strip()
    # If wrapped in code fences, strip them
    if s.startswith("```"):
        # remove opening fence
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
        s = s.strip()
    # Trim anything before <!DOCTYPE or <html
    m = re.search(r"<!doctype html|<html", s, re.IGNORECASE)
    if m:
        s = s[m.start():]
    return s.strip()


def looks_like_html(s: str) -> bool:
    head = s[:300].lower()
    return "<html" in head and "</html>" in s.lower()


def filename_for_now() -> str:
    now = datetime.now(tz=PT)
    return now.strftime("%Y-%m-%dT%H-%M.html")


def extract_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else "(untitled)"


def _extract_palette(html: str, max_colors: int = 3) -> list[str]:
    """Pull distinct mid-saturation hex colors from a generated page's CSS."""
    out: list[str] = []
    seen: set[str] = set()
    for hx in re.findall(r"#[0-9a-fA-F]{6}\b", html):
        hx = hx.lower()
        if hx in seen:
            continue
        seen.add(hx)
        r, g, b = int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)
        if max(r, g, b) < 40 or min(r, g, b) > 230:
            continue  # too dark / too light
        if max(r, g, b) - min(r, g, b) < 25:
            continue  # too gray
        out.append(hx)
        if len(out) >= max_colors:
            break
    if not out:
        out = ["#cb6446", "#a8d8ea", "#dfb35d"]
    return out


# Hardcoded "decorations" pinned to the corkboard alongside real entries.
# These aren't links — they're texture. Inserted at intervals.
DECORATIONS = [
    {
        "kind": "lost-pet",
        "html": '''
            <strong>LOST CAT</strong>
            <div class="big">PEPPER</div>
            <div>last seen behind the bus stop on 4th. answers to NOTHING. is shy.</div>
            <div class="big-num">REWARD: ONE BIG HUG</div>
            <div class="tearaways">
                <span>555-PEPR</span><span>555-PEPR</span><span>555-PEPR</span>
                <span>555-PEPR</span><span>555-PEPR</span><span>555-PEPR</span>
            </div>
        '''
    },
    {
        "kind": "garage-sale",
        "html": '''
            <strong>GARAGE SALE</strong>
            <div class="big">SAT 8AM</div>
            <div>everything. literally everything. the chairs. the photos. the lamps. a kettle.</div>
            <div class="small-line">429 Persimmon Ln · cash only · please don't haggle on the kettle</div>
        '''
    },
    {
        "kind": "takeout-menu",
        "html": '''
            <strong>THE GOOD WOK</strong>
            <div class="small-line">delivery 11–9 · cash or check</div>
            <ul class="menu-list">
                <li>C1. Lo Mein <span>$5.25</span></li>
                <li>C2. Sesame Tofu <span>$6.75</span></li>
                <li>C3. Wonton Soup <span>$3.50</span></li>
                <li>C4. ???  <span>$0.00</span></li>
                <li>C5. Almond Cookies (6) <span>$2.00</span></li>
            </ul>
        '''
    },
    {
        "kind": "polaroid",
        "html": '''
            <div class="polaroid-pic"></div>
            <div class="polaroid-cap">untitled, 1998</div>
        '''
    },
    {
        "kind": "punch-card",
        "html": '''
            <strong>OUTBOX.CAFE</strong>
            <div class="small-line">buy 9 of anything, get the 10th free</div>
            <div class="punches">
                <span class="p">●</span><span class="p">●</span><span class="p">●</span>
                <span class="p">●</span><span class="p">●</span><span class="p">○</span>
                <span class="p">○</span><span class="p">○</span><span class="p">○</span>
                <span class="p">○</span>
            </div>
            <div class="small-line">good luck out there</div>
        '''
    },
    {
        "kind": "kid-drawing",
        "html": '''
            <div class="drawing">
                <div class="sun">☀</div>
                <div class="stick">
                    <div class="head">◯</div>
                    <div class="body">│</div>
                    <div class="arms">─</div>
                    <div class="legs">⋀</div>
                </div>
                <div class="grass">⌒⌒⌒⌒⌒⌒⌒⌒⌒⌒</div>
            </div>
            <div class="small-line">By: Margot, age 4 ½</div>
        '''
    },
    {
        "kind": "postit",
        "html": '''
            <div class="postit-msg">back in 5 — M.</div>
        '''
    },
    {
        "kind": "quote",
        "html": '''
            <div class="quote-mark">“</div>
            <div class="quote-body">if the door is open, you are welcome inside.</div>
            <div class="quote-attr">— sign above the door, est. ~1962</div>
        '''
    },
    {
        "kind": "band-flyer",
        "html": '''
            <div class="band-loud">DELI MUSTARD</div>
            <div class="small-line">+ HALF KAREN + the EUGENE EUGENES</div>
            <div class="big-line">FRI · 9PM · DRINK ALL THE WATER</div>
            <div class="small-line">$3 SHOW · NO COVER FOR BIRTHDAYS · ASK ABOUT MARGOT</div>
        '''
    },
    {
        "kind": "weather",
        "html": '''
            <strong>FORECAST</strong>
            <div class="big">CHANCE OF FOG, EVENTUALLY</div>
            <div class="small-line">temperatures will not surprise you. wind: gentle. moods: variable.</div>
        '''
    },
]


def rebuild_cabinet() -> None:
    """Rebuild archive/index.html as a weird pinned-corkboard scrapbook."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for f in sorted(ARCHIVE_DIR.glob("*.html"), reverse=True):
        if f.name == "index.html":
            continue
        try:
            html = f.read_text(errors="ignore")
        except Exception:
            continue
        title = extract_title(html)
        wm = ""
        m = re.search(
            r"(era|format|tone)[^<]*?·[^<]*?·[^<]*",
            html,
            re.IGNORECASE,
        )
        if m:
            wm = m.group(0).strip()
            wm = re.sub(r"\s+", " ", wm)[:140]
        palette = _extract_palette(html)
        # Stable per-card variety via md5 — sum-of-ords collides too easily
        import hashlib
        digest = hashlib.md5(f.stem.encode()).digest()
        h = int.from_bytes(digest[:4], "big")
        rot = ((digest[4] % 9) - 4) * 0.9  # -3.6..+3.6
        paper_idx = digest[5] % 8
        nudge_x = (digest[6] % 9) - 4  # -4..+4 px
        nudge_y = (digest[7] % 9) - 4
        entries.append({
            "file": f.name,
            "title": title,
            "watermark": wm,
            "stamp": f.stem,
            "palette": palette,
            "rot": rot,
            "paper": paper_idx,
            "nx": nudge_x,
            "ny": nudge_y,
            "hash": h,
        })

    # Interleave decorations every ~5 entries
    items: list[dict] = []
    for i, e in enumerate(entries):
        items.append({"type": "entry", **e})
        if (i + 1) % 5 == 0:
            deco_idx = (i // 5) % len(DECORATIONS)
            d = DECORATIONS[deco_idx]
            h = (i + 1) * 17 + deco_idx * 7
            items.append({
                "type": "decor",
                "kind": d["kind"],
                "html": d["html"],
                "rot": ((h % 11) - 5) * 0.8,
                "nx": ((h // 5) % 9) - 4,
                "ny": ((h // 9) % 9) - 4,
            })

    count = len(entries)

    def render_entry(e: dict) -> str:
        palette_dots = "".join(
            f'<i style="background:{c}"></i>' for c in e["palette"]
        )
        stamp_pretty = e["stamp"].replace("T", " · ")
        title_html = e["title"]
        return f'''
        <article class="card paper-{e['paper']}" style="--rot:{e['rot']:.2f}deg; --nx:{e['nx']}px; --ny:{e['ny']}px;">
          <span class="pin"></span>
          <a class="card-link" href="./{e['file']}">
            <h3 class="card-title">{title_html}</h3>
            <div class="card-stamp">{stamp_pretty} PT</div>
            {f'<div class="card-wm">{e["watermark"]}</div>' if e["watermark"] else ''}
            <div class="palette">{palette_dots}</div>
          </a>
        </article>'''

    def render_decor(d: dict) -> str:
        return f'''
        <article class="card decor decor-{d['kind']}" style="--rot:{d['rot']:.2f}deg; --nx:{d['nx']}px; --ny:{d['ny']}px;" aria-hidden="true">
          <span class="pin pin-{d['kind']}"></span>
          {d['html']}
        </article>'''

    cards_html = "\n".join(
        render_entry(it) if it["type"] == "entry" else render_decor(it)
        for it in items
    )

    cabinet_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>THE CORKBOARD · outbox.cafe</title>
<style>
  :root {{
    --ink: #2a2418;
    --dim: #8a7a5c;
    --accent: #b8473a;
    --cork-a: #d4b485;
    --cork-b: #b89669;
    --cork-c: #c2a479;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; color: var(--ink);
    font-family: "Georgia", "Times New Roman", serif;
    font-size: 15px; line-height: 1.45; }}
  body {{
    min-height: 100vh;
    padding: 18px 14px 100px;
    background:
      radial-gradient(circle at 12% 18%, rgba(0,0,0,0.10) 0 2px, transparent 3px),
      radial-gradient(circle at 38% 62%, rgba(0,0,0,0.08) 0 1.5px, transparent 2.5px),
      radial-gradient(circle at 71% 33%, rgba(0,0,0,0.12) 0 2px, transparent 3px),
      radial-gradient(circle at 84% 79%, rgba(0,0,0,0.07) 0 1.5px, transparent 2.5px),
      radial-gradient(circle at 22% 88%, rgba(0,0,0,0.09) 0 2px, transparent 3px),
      radial-gradient(circle at 56% 5%, rgba(0,0,0,0.06) 0 1.5px, transparent 2.5px),
      linear-gradient(135deg, var(--cork-a) 0%, var(--cork-c) 50%, var(--cork-b) 100%);
    background-size: 130px 130px, 90px 90px, 160px 160px, 110px 110px, 140px 140px, 100px 100px, 100% 100%;
  }}

  /* HEADER ribbon */
  header.ribbon {{
    max-width: 920px;
    margin: 0 auto 24px;
    text-align: center;
    background: #fdf9ec;
    border: 3px double #2a2418;
    padding: 18px 14px 14px;
    transform: rotate(-0.4deg);
    box-shadow: 0 6px 0 rgba(0,0,0,0.18);
    position: relative;
  }}
  header.ribbon::before, header.ribbon::after {{
    content: ""; position: absolute; top: -7px;
    width: 14px; height: 14px; border-radius: 50%;
    background: radial-gradient(circle at 30% 30%, #ff8c4a, #c43d15 60%, #6b1a05);
    box-shadow: inset 0 -2px 2px rgba(0,0,0,0.4);
  }}
  header.ribbon::before {{ left: 10%; }}
  header.ribbon::after {{ right: 10%; background: radial-gradient(circle at 30% 30%, #ffe066, #d09f10 60%, #7a5a08); }}
  header.ribbon h1 {{
    margin: 0; font-family: "Impact", "Anton", "Arial Black", sans-serif;
    font-size: clamp(36px, 7vw, 64px); letter-spacing: 3px;
    color: #2a2418; text-shadow: 3px 3px 0 #cb6446;
  }}
  header.ribbon .sub {{ color: #6b5a3c; font-style: italic; margin-top: 6px; font-size: 14px; }}
  header.ribbon .meta {{ display: inline-block; margin-top: 10px; padding: 3px 10px;
    background: #2a2418; color: #fdf9ec; font-family: "Courier New", monospace;
    font-size: 12px; letter-spacing: 1px; transform: rotate(-1deg); }}
  header.ribbon .latest-link {{ display: inline-block; margin-left: 10px; color: #b8473a; font-weight: bold; transform: rotate(0.8deg); }}

  /* The cork "board" */
  .board {{
    max-width: 1120px;
    margin: 0 auto;
    column-count: 3;
    column-gap: 16px;
  }}
  @media (max-width: 880px) {{ .board {{ column-count: 2; }} }}
  @media (max-width: 540px) {{ .board {{ column-count: 1; }} }}

  /* Cards */
  .card {{
    break-inside: avoid;
    position: relative;
    display: block;
    margin: 0 0 18px;
    padding: 18px 16px 14px;
    transform: rotate(var(--rot, 0deg)) translate(var(--nx, 0), var(--ny, 0));
    box-shadow:
      0 1px 0 rgba(255,255,255,0.5) inset,
      0 6px 14px rgba(0,0,0,0.20),
      0 2px 4px rgba(0,0,0,0.15);
    transition: transform 0.18s ease, box-shadow 0.18s ease;
  }}
  .card:hover {{
    transform: rotate(0deg) translate(0,0) scale(1.04);
    box-shadow: 0 10px 22px rgba(0,0,0,0.30), 0 3px 6px rgba(0,0,0,0.18);
    z-index: 10;
  }}

  /* Paper styles — vary so the wall feels real */
  .paper-0 {{ background: #fdf9ec; }}            /* cream index card */
  .paper-1 {{ background: #f3e7c3; }}            /* manila */
  .paper-2 {{ background: #ffd7e3; }}            /* pink message slip */
  .paper-3 {{ background: #e3dcfb; }}            /* lavender */
  .paper-4 {{ background: linear-gradient(180deg, #fffbcc 0 24px, #fff4a2 24px); }}  /* yellow legal */
  .paper-5 {{ background: #c8e3f5; }}            /* robin's egg */
  .paper-6 {{ background: #d4e6c8; }}            /* faded sage */
  .paper-7 {{ background: #ffb866; color: #2a1810; }}  /* loud orange */

  .paper-0::before, .paper-1::before, .paper-2::before, .paper-3::before,
  .paper-5::before, .paper-6::before, .paper-7::before {{
    content: ""; position: absolute; left: 14px; right: 14px; top: 28px; bottom: 22px;
    border-top: 1px solid rgba(0,0,0,0.10);
    border-bottom: 1px solid rgba(0,0,0,0.06);
    pointer-events: none;
  }}

  /* Pin / thumbtack */
  .pin {{
    position: absolute; top: -6px; left: 50%;
    width: 14px; height: 14px; margin-left: -7px;
    border-radius: 50%;
    background: radial-gradient(circle at 30% 30%, #ff7a4a, #c43d15 55%, #5a1004);
    box-shadow: 0 1px 1px rgba(0,0,0,0.4), inset -1px -1px 2px rgba(0,0,0,0.4);
    z-index: 5;
  }}
  .card:nth-child(3n) .pin {{ background: radial-gradient(circle at 30% 30%, #ffd966, #d09f10 55%, #5a3f04); }}
  .card:nth-child(3n+1) .pin {{ background: radial-gradient(circle at 30% 30%, #7ad9ff, #1c79b8 55%, #0a3a5a); }}
  .card:nth-child(4n) .pin {{ background: radial-gradient(circle at 30% 30%, #66e09b, #1f7a3d 55%, #053a18); }}

  /* Card content */
  .card-link {{ display: block; text-decoration: none; color: inherit; }}
  .card-title {{ margin: 0 0 8px; font-size: 17px; font-weight: 700;
    color: #2a2418; line-height: 1.25;
    font-family: "Georgia", "Times New Roman", serif; }}
  .card-stamp {{ font-family: "Courier New", monospace; font-size: 11px;
    color: #8a7a5c; letter-spacing: 0.5px; margin-bottom: 8px; }}
  .card-wm {{ font-size: 12px; color: #6b5a3c; font-style: italic; margin-bottom: 10px; line-height: 1.4; }}
  .palette {{ display: flex; gap: 4px; margin-top: 8px; }}
  .palette i {{
    display: inline-block; width: 14px; height: 14px; border-radius: 50%;
    border: 1px solid rgba(0,0,0,0.25);
    box-shadow: inset 0 1px 1px rgba(255,255,255,0.4);
  }}

  /* DECORATIONS */
  .decor {{ font-family: "Georgia", "Times New Roman", serif; }}
  .decor strong {{ display: block; font-size: 15px; letter-spacing: 2px; margin-bottom: 4px; }}
  .decor .big {{ font-family: "Impact", "Arial Black", sans-serif; font-size: 28px; line-height: 1.05; letter-spacing: 1px; margin: 4px 0; }}
  .decor .big-num {{ font-family: "Impact", "Arial Black", sans-serif; font-size: 16px; margin-top: 8px; color: #c43d15; }}
  .decor .small-line {{ font-size: 11px; color: #6b5a3c; margin-top: 6px; }}
  .decor .big-line {{ font-family: "Impact", sans-serif; font-size: 14px; letter-spacing: 1px; }}

  /* lost-pet tear-aways */
  .tearaways {{ display: flex; flex-wrap: wrap; gap: 0; margin: 10px -16px -14px; border-top: 1px dashed #8a7a5c; padding-top: 4px; }}
  .tearaways span {{ flex: 1 1 50px; font-family: "Courier New", monospace; font-size: 9px; padding: 4px 2px; text-align: center; color: #6b5a3c;
    border-right: 1px dashed #8a7a5c; transform: rotate(90deg); }}
  .tearaways span:last-child {{ border-right: 0; }}

  /* takeout menu */
  .decor-takeout-menu {{ background: #fff3d8 !important; padding: 14px 14px 12px !important; }}
  .menu-list {{ list-style: none; padding: 0; margin: 8px 0 0; font-size: 13px; }}
  .menu-list li {{ display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px dotted #b89669; }}

  /* polaroid */
  .decor-polaroid {{ background: #fefcf4 !important; padding: 14px 14px 36px !important; }}
  .polaroid-pic {{
    aspect-ratio: 4 / 3; width: 100%;
    background:
      radial-gradient(ellipse at center, rgba(255,255,255,0.4), transparent 60%),
      linear-gradient(135deg, #8a7a5c 0%, #5a4a30 100%);
    border: 1px solid #2a2418;
  }}
  .polaroid-cap {{ position: absolute; bottom: 8px; left: 0; right: 0; text-align: center;
    font-family: "Brush Script MT", "Lucida Handwriting", cursive; font-size: 16px; color: #2a2418; }}

  /* punch card */
  .punches {{ display: flex; gap: 6px; margin: 8px 0 4px; flex-wrap: wrap; justify-content: center; font-size: 22px; color: #2a2418; }}
  .punches .p {{ width: 22px; height: 22px; display: grid; place-items: center;
    border: 1px solid #2a2418; border-radius: 50%; }}

  /* kid drawing */
  .drawing {{ text-align: center; font-size: 30px; line-height: 1.0; font-family: "Comic Sans MS", "Marker Felt", sans-serif; color: #2a2418; }}
  .drawing .sun {{ color: #d09f10; font-size: 36px; }}
  .drawing .stick > * {{ display: block; }}
  .drawing .head {{ font-size: 28px; }}
  .drawing .body {{ font-size: 36px; margin-top: -4px; }}
  .drawing .arms {{ font-size: 28px; margin-top: -10px; }}
  .drawing .legs {{ font-size: 28px; margin-top: -6px; }}
  .drawing .grass {{ font-size: 18px; color: #1f7a3d; margin-top: 4px; }}

  /* post-it */
  .decor-postit {{ background: #fff4a2 !important; padding: 26px 16px !important; }}
  .postit-msg {{ font-family: "Brush Script MT", "Lucida Handwriting", cursive; font-size: 24px; text-align: center; color: #2a2418; }}

  /* quote card */
  .decor-quote {{ background: #fdf9ec !important; }}
  .quote-mark {{ font-family: "Georgia", serif; font-size: 60px; line-height: 0.5; color: #b8473a; margin-bottom: 6px; }}
  .quote-body {{ font-style: italic; font-size: 15px; }}
  .quote-attr {{ font-size: 11px; color: #6b5a3c; margin-top: 8px; }}

  /* band flyer — xerox aesthetic */
  .decor-band-flyer {{ background: #f0e2c0 !important; }}
  .band-loud {{ font-family: "Impact", sans-serif; font-size: 36px; line-height: 1.0; letter-spacing: 1px; transform: skewX(-6deg); color: #2a2418; }}

  /* weather */
  .decor-weather {{ background: #c8e3f5 !important; }}

  /* Pin variants — different colored thumbtacks on decorations */
  .pin-lost-pet {{ background: radial-gradient(circle at 30% 30%, #ff8c4a, #c43d15 55%, #5a1004) !important; }}
  .pin-postit {{ display: none; }}  /* post-its are sticky, not pinned */

  /* FOOTER */
  footer {{
    max-width: 920px; margin: 36px auto 0; padding: 14px 18px;
    background: rgba(253, 249, 236, 0.85);
    border: 1px dashed #2a2418;
    color: #2a2418; font-size: 12px; text-align: center;
    transform: rotate(0.3deg);
  }}
  footer a {{ color: #b8473a; }}

  /* Mobile tweaks */
  @media (max-width: 540px) {{
    body {{ padding: 14px 10px 80px; }}
    .card {{ margin: 0 0 22px; }}
    .card:hover {{ transform: rotate(0) scale(1.02); }}
  }}
</style>
</head>
<body>

<header class="ribbon">
  <h1>THE&nbsp;CORKBOARD</h1>
  <div class="sub">everything we've pinned up at outbox.cafe</div>
  <div>
    <span class="meta">{count} PIECE{'S' if count != 1 else ''} ON THE WALL</span>
    <a class="latest-link" href="/">→ latest piece</a>
  </div>
</header>

<main class="board">
{cards_html if entries else '<p style="text-align:center;color:#fdf9ec;font-size:14px;background:rgba(0,0,0,0.3);padding:14px;max-width:500px;margin:60px auto;border-radius:2px;">nothing pinned yet. check back at the top of the hour.</p>'}
</main>

<footer>
  outbox.cafe · something new at the top of every hour · the corkboard keeps the rest<br>
  <small>note: not all items on the corkboard are clickable. some things are just here.</small>
</footer>

</body>
</html>
"""
    CABINET_PATH.write_text(cabinet_html)


def git_commit_and_push(message: str) -> None:
    """Stage everything, commit, push. Quiet on failure."""
    subprocess.run(["git", "-C", str(ROOT), "add", "-A"], check=True)
    # No-op if nothing to commit
    diff = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "--quiet"],
    )
    if diff.returncode == 0:
        print("nothing to commit")
        return
    subprocess.run(
        ["git", "-C", str(ROOT), "commit", "-m", message],
        check=True,
    )
    subprocess.run(["git", "-C", str(ROOT), "push"], check=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--commit", action="store_true", help="git commit + push after writing")
    p.add_argument("--dry-run", action="store_true", help="roll spec + print prompt, don't call Claude")
    p.add_argument("--model", default=None, help="override claude model (default: account default)")
    args = p.parse_args()

    spec = roll_spec(seed=args.seed)
    print("=" * 60)
    print(f"hourly generation @ {datetime.now(tz=PT).isoformat()}")
    print("=" * 60)
    print(format_spec_for_human(spec))
    print()

    prompt = build_prompt(spec)

    if args.dry_run:
        print("---- PROMPT ----")
        print(prompt)
        return 0

    print("calling claude (this may take 30-90s for larger pieces) ...")
    raw = call_claude(prompt, model=args.model)
    html = extract_html(raw)

    if not looks_like_html(html):
        # Save to a debug file but don't overwrite index
        debug = ROOT / "data" / "last_bad_output.txt"
        debug.write_text(raw)
        print(f"output did not look like HTML; raw saved to {debug}", file=sys.stderr)
        return 2

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_file = ARCHIVE_DIR / filename_for_now()
    archive_file.write_text(html)
    shutil.copyfile(archive_file, INDEX_PATH)

    append_history(spec)
    rebuild_cabinet()

    title = extract_title(html)
    print(f"\n✓ wrote {archive_file.name} — {title}")

    if args.commit:
        msg = f"hourly: {title}"[:72]
        git_commit_and_push(msg)
        print("✓ committed and pushed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
