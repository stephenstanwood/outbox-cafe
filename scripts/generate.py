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
THUMBS_DIR = ARCHIVE_DIR / "thumbs"
INDEX_PATH = ROOT / "index.html"
CABINET_PATH = ARCHIVE_DIR / "index.html"
SHOT_SCRIPT = ROOT / "scripts" / "screenshot.js"

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


RELOAD_SCRIPT = """
<script>
(function () {
  // At the next top-of-hour, poll for new content (title change) every 12s.
  // Reload as soon as it changes, or after 3 min if it didn't.
  // UTC top-of-hour == PT top-of-hour (PT offset is a whole number).
  function msUntilNextHour() {
    var now = new Date();
    var next = new Date(now);
    next.setUTCMinutes(0, 0, 0);
    next.setUTCHours(next.getUTCHours() + 1);
    return next - now;
  }
  var initialTitle = document.title;
  setTimeout(function () {
    var attempts = 0;
    var iv = setInterval(function () {
      attempts++;
      if (attempts > 15) {
        clearInterval(iv);
        window.location.reload();
        return;
      }
      fetch(window.location.pathname + '?_t=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { return r.text(); })
        .then(function (text) {
          var m = text.match(/<title>([^<]+)<\\/title>/);
          if (m && m[1] !== initialTitle) {
            clearInterval(iv);
            window.location.reload();
          }
        })
        .catch(function () {});
    }, 12000);
  }, msUntilNextHour());
})();
</script>
"""


def inject_reload(html: str) -> str:
    """Insert the hourly reload script before </body>."""
    if re.search(r"</body\s*>", html, re.IGNORECASE):
        return re.sub(
            r"(</body\s*>)",
            RELOAD_SCRIPT + r"\1",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    return html + RELOAD_SCRIPT


NAV_SCRIPT_TAG = '\n<script src="/nav.js" defer></script>\n'


def inject_nav(html: str) -> str:
    """Insert the nav.js script tag before </body>. Idempotent."""
    if 'src="/nav.js"' in html:
        return html
    if re.search(r"</body\s*>", html, re.IGNORECASE):
        return re.sub(
            r"(</body\s*>)",
            NAV_SCRIPT_TAG + r"\1",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    return html + NAV_SCRIPT_TAG


def inject_spec_meta(html: str, spec: dict) -> str:
    """Inject canonical spec as <meta> tags into <head> for reliable extraction by the cabinet."""

    def v(field: str) -> str:
        item = spec.get(field, {})
        if isinstance(item, dict):
            return item.get("value") or item.get("key") or ""
        return str(item)

    length_key = spec.get("length", {}).get("key", "") if isinstance(spec.get("length"), dict) else ""

    meta_block = (
        f'\n  <meta name="outbox-spec-era" content="{v("era")}">'
        f'\n  <meta name="outbox-spec-format" content="{v("format")}">'
        f'\n  <meta name="outbox-spec-subject" content="{v("subject")}">'
        f'\n  <meta name="outbox-spec-tone" content="{v("tone")}">'
        f'\n  <meta name="outbox-spec-length" content="{length_key}">'
        f'\n  <meta name="outbox-spec-palette" content="{v("palette")}">'
        f'\n  <meta name="outbox-spec-wildcard" content="{v("wildcard")}">'
        f'\n  <meta name="outbox-spec-forbidden" content="{v("forbidden_register")}">'
    )

    # Insert right after the opening <head> tag
    m = re.search(r"(<head[^>]*>)", html, re.IGNORECASE)
    if m:
        idx = m.end()
        return html[:idx] + meta_block + html[idx:]
    # Fallback: no <head> tag found, leave HTML untouched
    return html


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


def take_screenshot(html_file: Path, output: Path) -> bool:
    """Call screenshot.js to capture a viewport PNG. Non-fatal on failure."""
    if not SHOT_SCRIPT.exists():
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["node", str(SHOT_SCRIPT), str(html_file), str(output)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return output.exists()
    except subprocess.CalledProcessError as e:
        print(f"screenshot failed (non-fatal): {e.stderr[:200]}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("node not in PATH — skipping screenshot", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("screenshot timed out — skipping", file=sys.stderr)
        return False


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


RARITY_TIERS = [
    # (label, stars, weight, css-class)
    ("COMMON",      "★",      50, "rarity-c"),
    ("UNCOMMON",    "★★",     28, "rarity-u"),
    ("RARE",        "★★★",    14, "rarity-r"),
    ("HOLO RARE",   "★★★★",   6,  "rarity-h"),
    ("FIRST EDITION","★★★★★", 2,  "rarity-f"),
]


TAGLINES = [
    "still warm", "found in a drawer", "well-loved", "as-is", "kept in a shoebox",
    "smells faintly of toast", "from the back room", "not in the catalog",
    "officially unsponsored", "approved by Doris", "found behind the radiator",
    "no batteries included", "patent pending forever", "from a private collection",
    "good for what ails you", "ships flat", "small but bossy", "do not refrigerate",
    "best before whenever", "limited to one person", "guaranteed to occur",
    "rumored, never seen", "ask about the smell", "extra weird",
    "may contain feelings", "made yesterday", "made tuesday",
    "fits in a pocket", "loud but kind", "vouched for",
    "comes with a small thanks", "the good kind", "as advertised, mostly",
    "kept in a cigar box", "kept in the freezer",
    "perfect for nothing", "perfect for everything",
    "free with purchase", "not for resale (it is)",
    "untested but charming", "no refunds, no questions",
    "lightly haunted", "previously enjoyed", "from grandma's basement",
    "this side up", "do not read in mirror", "remove tag before use",
    "void where prohibited", "wash in cold", "hang to dry",
    "may settle in shipping", "comes pre-loved",
    "trust the process", "trust the cat", "ask Doris",
    "shake gently", "do not shake", "store at room temperature",
    "comes with a story", "comes with a sticker", "the last one",
    "actually the last one", "okay this is the last one for real",
    "smells like a library", "smells like rain", "smells like nothing",
    "looks fine to us", "looks better in person",
    "use as directed", "use as desired", "use as needed",
]


STICKERS = [
    # text, css-class, weight
    ("NEW!",         "stk-new",       10),
    ("$1.25",        "stk-price",     8),
    ("$0.99",        "stk-price",     8),
    ("$3.50",        "stk-price",     5),
    ("5¢",           "stk-price-tiny", 4),
    ("RARE",         "stk-rare",      5),
    ("VINTAGE",      "stk-vintage",   5),
    ("MINT",         "stk-mint",      4),
    ("USED",         "stk-used",      6),
    ("PROMO",        "stk-promo",     4),
    ("FOIL",         "stk-foil",      2),
    ("AUTHENTIC",    "stk-authentic", 3),
    ("FREE!",        "stk-free",      4),
    ("REDUCED",      "stk-reduced",   3),
    ("LIMITED",      "stk-limited",   2),
    ("BEST IN SHOW", "stk-show",      1),
    ("SUNDAY ONLY",  "stk-sunday",    2),
    ("MUST GO",      "stk-mustgo",    3),
    ("AS-IS",        "stk-asis",      4),
    ("ASK FOR M.",   "stk-askm",      2),
    ("HANDMADE",     "stk-handmade",  3),
    ("LOCAL",        "stk-local",     3),
    ("FRAGILE",      "stk-fragile",   3),
]

STICKER_POSITIONS = [
    "top:6px; right:6px; transform:rotate(8deg);",
    "top:6px; left:6px; transform:rotate(-7deg);",
    "bottom:8px; right:8px; transform:rotate(-5deg);",
    "bottom:8px; left:8px; transform:rotate(6deg);",
    "top:50%; right:-10px; transform:translateY(-50%) rotate(12deg);",
    "top:34%; left:-8px; transform:rotate(-14deg);",
]


def _pick_rarity(h: int) -> tuple[str, str, str]:
    """Deterministic rarity from a hash int."""
    bucket = h % sum(w for _, _, w, _ in RARITY_TIERS)
    acc = 0
    for label, stars, weight, cls in RARITY_TIERS:
        acc += weight
        if bucket < acc:
            return (label, stars, cls)
    return RARITY_TIERS[0][0], RARITY_TIERS[0][1], RARITY_TIERS[0][3]


def _split_watermark(wm: str) -> tuple[str, str, str]:
    """Parse 'era · format · tone' (or any · -separated string) into 3 parts."""
    parts = [p.strip() for p in re.split(r"\s*[·•]\s*", wm) if p.strip()]
    parts = [re.sub(r"^(era|format|tone)\s*:?\s*", "", p, flags=re.I) for p in parts]
    while len(parts) < 3:
        parts.append("")
    return parts[0][:60], parts[1][:60], parts[2][:60]


def _extract_meta(html: str, field: str) -> str:
    """Extract a single <meta name="outbox-spec-FIELD" content="..."> value."""
    m = re.search(
        rf'<meta\s+name="outbox-spec-{field}"\s+content="([^"]*)"',
        html,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""


def _condense(s: str, max_chars: int = 50) -> str:
    """Take the first slash/comma-separated segment and trim a trailing meta descriptor.

    Spec values often look like 'conspiratorial / paranoid / underlined words' or
    '1996 Web 1.0 banner-ad era, hit counters, under construction GIFs' — the first
    segment is the canonical short label, the rest is engine guts.
    """
    if not s:
        return ""
    first = re.split(r"\s*[/,]\s*", s, maxsplit=1)[0].strip()
    # Drop trailing meta words that describe the *style* rather than name it
    first = re.sub(
        r"\s+(era|tone|voice|moments?|points?|words?|energy)$",
        "",
        first,
        flags=re.IGNORECASE,
    ).strip()
    if len(first) > max_chars:
        first = first[: max_chars - 1].rstrip() + "…"
    return first


def rebuild_cabinet() -> None:
    """Rebuild archive/index.html as a trading-card collection page."""
    import hashlib

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
        # Prefer the canonical meta tags injected at generation time.
        era_raw = _extract_meta(html, "era")
        fmt_raw = _extract_meta(html, "format")
        tone_raw = _extract_meta(html, "tone")
        if not (era_raw or fmt_raw or tone_raw):
            # Fallback: parse the spec watermark line from the page footer
            wm = ""
            m = re.search(
                r"(era|format|tone)[^<]*?·[^<]*?·[^<]*",
                html,
                re.IGNORECASE,
            )
            if m:
                wm = re.sub(r"\s+", " ", m.group(0).strip())[:200]
            era_raw, fmt_raw, tone_raw = _split_watermark(wm)
        # Condense for card display — strip meta-descriptive tails
        era = _condense(era_raw, 32)
        fmt = _condense(fmt_raw, 38)
        tone = _condense(tone_raw, 28)
        palette = _extract_palette(html)
        digest = hashlib.md5(f.stem.encode()).digest()
        h = int.from_bytes(digest[:4], "big")
        rot = ((digest[4] % 7) - 3) * 0.5  # -1.5..+1.5 (subtler than corkboard)
        rarity_label, rarity_stars, rarity_cls = _pick_rarity(h)
        thumb = THUMBS_DIR / (f.stem + ".png")
        has_thumb = thumb.exists()
        entries.append({
            "file": f.name,
            "title": title,
            "stamp": f.stem,
            "palette": palette,
            "era": era,
            "format": fmt,
            "tone": tone,
            "rot": rot,
            "rarity_label": rarity_label,
            "rarity_stars": rarity_stars,
            "rarity_cls": rarity_cls,
            "has_thumb": has_thumb,
            # absolute paths so the browser resolves correctly when Vercel
            # serves /archive without a trailing slash
            "thumb_path": f"/archive/thumbs/{f.stem}.png" if has_thumb else "",
            "page_path": f"/archive/{f.name}",
            "hash": h,
        })

    total = len(entries)

    def card_number(idx: int) -> str:
        # Newest is highest number; reversed list shows newest first.
        return f"{total - idx:03d}/∞"

    # Weighted choice helpers — deterministic by card hash so the cabinet
    # doesn't churn visually between rebuilds.
    def deterministic_pick(pool: list, h: int) -> any:
        total = sum(w for *_, w in pool) if isinstance(pool[0], tuple) else len(pool)
        if isinstance(pool[0], tuple):
            bucket = h % total
            acc = 0
            for item in pool:
                acc += item[-1]
                if bucket < acc:
                    return item
            return pool[-1]
        return pool[h % len(pool)]

    def pick_stickers(h: int) -> list[tuple[str, str, str]]:
        # 40% 0 stickers, 50% 1 sticker, 10% 2 stickers (deterministic)
        n_bucket = h % 10
        n = 0 if n_bucket < 4 else (1 if n_bucket < 9 else 2)
        if n == 0:
            return []
        chosen = []
        used_positions = set()
        for i in range(n):
            sub_hash = (h >> (8 * (i + 1))) & 0xFFFFFFFF
            text, cls, _ = deterministic_pick(STICKERS, sub_hash)
            pos = STICKER_POSITIONS[sub_hash % len(STICKER_POSITIONS)]
            if pos in used_positions:
                continue
            used_positions.add(pos)
            chosen.append((text, cls, pos))
        return chosen

    def render_card(idx: int, e: dict) -> str:
        c1 = e["palette"][0] if e["palette"] else "#cb6446"
        c2 = e["palette"][1] if len(e["palette"]) > 1 else c1
        c3 = e["palette"][2] if len(e["palette"]) > 2 else c2
        palette_dots = "".join(
            f'<i style="background:{c}"></i>' for c in e["palette"]
        )
        if e["has_thumb"]:
            art = f'<img class="card-art-img" src="{e["thumb_path"]}" alt="" loading="lazy">'
        else:
            art = '<div class="card-art-placeholder">no preview yet</div>'
        stamp_pretty = e["stamp"].replace("T", " · ") + " PT"
        tagline = TAGLINES[e["hash"] % len(TAGLINES)]
        stickers_html = "".join(
            f'<span class="sticker {cls}" style="{pos}">{text}</span>'
            for text, cls, pos in pick_stickers(e["hash"])
        )
        return f'''
        <a class="card {e["rarity_cls"]}" href="{e["page_path"]}"
           style="--c1:{c1}; --c2:{c2}; --c3:{c3}; --rot:{e['rot']:.2f}deg;">
          <div class="card-inner">
            <div class="card-top">
              <span class="card-num">No.{card_number(idx)}</span>
              <span class="card-rarity" title="{e['rarity_label']}">{e['rarity_stars']}</span>
            </div>
            <div class="card-art">
              {art}
              {stickers_html}
            </div>
            <h3 class="card-name">{e["title"]}</h3>
            <div class="card-tag">{tagline}</div>
            <div class="card-foot">
              <span class="card-stamp">{stamp_pretty}</span>
              <span class="card-palette">{palette_dots}</span>
            </div>
          </div>
          <div class="card-shine" aria-hidden="true"></div>
        </a>'''

    cards_html = "\n".join(render_card(i, e) for i, e in enumerate(entries))
    count = total

    # Emit the chronological file list for nav.js (oldest first → newest last)
    chrono_files = sorted(
        (f.name for f in ARCHIVE_DIR.glob("*.html") if f.name != "index.html")
    )
    (ARCHIVE_DIR / "list.json").write_text(json.dumps(chrono_files))

    # Make sure every existing archive page has the nav.js script tag.
    # inject_nav is idempotent — files that already have it are unchanged.
    for f in ARCHIVE_DIR.glob("*.html"):
        if f.name == "index.html":
            continue
        try:
            content = f.read_text(errors="ignore")
        except Exception:
            continue
        new_content = inject_nav(content)
        if new_content != content:
            f.write_text(new_content)

    cabinet_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>THE COLLECTION · outbox.cafe</title>
<style>
  :root {{
    --ink: #1a1612;
    --paper: #f4ecdc;
    --paper-2: #ede2c8;
    --gold: #c89a3e;
    --accent: #b8473a;
    --dim: #7a6a4c;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; color: var(--ink); }}
  body {{
    min-height: 100vh;
    padding: 22px 14px 80px;
    font-family: "Georgia", "Times New Roman", serif;
    font-size: 15px; line-height: 1.45;
    background:
      radial-gradient(circle at 20% 30%, rgba(184,71,58,0.04) 0 40%, transparent 60%),
      radial-gradient(circle at 80% 70%, rgba(200,154,62,0.05) 0 35%, transparent 55%),
      repeating-linear-gradient(45deg, rgba(0,0,0,0.012) 0 2px, transparent 2px 5px),
      var(--paper);
  }}

  /* HEADER — chunky stencil block */
  header.hero {{
    max-width: 1240px;
    margin: 0 auto 28px;
    padding: 22px 18px 18px;
    text-align: center;
    position: relative;
  }}
  header.hero h1 {{
    margin: 0;
    font-family: "Impact", "Anton", "Arial Black", "Helvetica Neue", sans-serif;
    font-size: clamp(44px, 9vw, 96px);
    letter-spacing: clamp(2px, 0.5vw, 5px);
    line-height: 0.95;
    color: var(--ink);
    text-shadow:
      3px 3px 0 var(--accent),
      6px 6px 0 var(--gold),
      9px 9px 0 rgba(0,0,0,0.10);
    transform: skewX(-3deg);
  }}
  header.hero .sub {{
    margin: 14px 0 0; font-style: italic; color: var(--dim);
    font-size: clamp(13px, 1.5vw, 16px);
  }}
  header.hero .meta {{
    display: inline-flex; gap: 14px; margin-top: 12px; padding: 6px 14px;
    background: var(--ink); color: var(--paper);
    font-family: "Courier New", ui-monospace, monospace;
    font-size: 12px; letter-spacing: 2px;
    border: 2px solid var(--gold);
    box-shadow: 4px 4px 0 var(--gold);
  }}
  header.hero .meta a {{ color: var(--gold); text-decoration: none; }}
  header.hero .meta a:hover {{ text-decoration: underline; }}

  /* CARD GRID */
  .grid {{
    max-width: 1240px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 22px 18px;
    padding: 0 4px;
  }}

  /* CARD */
  .card {{
    position: relative;
    display: block;
    text-decoration: none;
    color: var(--ink);
    aspect-ratio: 4 / 5;
    background: var(--paper);
    border-radius: 12px;
    padding: 0;
    overflow: visible;
    transform: rotate(var(--rot, 0deg));
    box-shadow:
      0 1px 0 rgba(255,255,255,0.6) inset,
      0 2px 6px rgba(0,0,0,0.10),
      0 8px 18px rgba(0,0,0,0.14);
    transition: transform 0.18s ease, box-shadow 0.18s ease;
    isolation: isolate;
  }}
  .card:hover {{
    transform: rotate(0deg) translateY(-4px) scale(1.025);
    box-shadow:
      0 14px 30px rgba(0,0,0,0.22),
      0 4px 8px rgba(0,0,0,0.10);
    z-index: 10;
  }}

  /* Outer frame in palette color */
  .card::before {{
    content: "";
    position: absolute; inset: 0;
    border-radius: 12px;
    padding: 6px;
    background: linear-gradient(135deg, var(--c1, #cb6446) 0%, var(--c2, #cb6446) 50%, var(--c3, #cb6446) 100%);
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor;
            mask-composite: exclude;
    pointer-events: none;
    z-index: 1;
  }}

  .card-inner {{
    position: relative;
    z-index: 2;
    padding: 8px 10px 10px;
    height: 100%;
    display: flex; flex-direction: column;
    background: var(--paper);
    border-radius: 8px;
    margin: 4px;
    overflow: hidden;
  }}

  .card-top {{
    display: flex; justify-content: space-between; align-items: center;
    font-family: "Courier New", monospace; font-size: 10px;
    color: var(--dim); letter-spacing: 1.2px;
    margin-bottom: 6px;
  }}
  .card-num {{ font-weight: 700; }}
  .card-rarity {{ color: var(--gold); letter-spacing: 1px; font-size: 11px; }}

  .card-art {{
    width: 100%;
    aspect-ratio: 4 / 3;
    background: #2a1c0a;
    border: 2px solid var(--ink);
    border-radius: 4px;
    overflow: visible;  /* let stickers poke out */
    position: relative;
    box-shadow: inset 0 2px 6px rgba(0,0,0,0.4);
  }}
  .card-art-img {{
    width: 100%; height: 100%; object-fit: cover; object-position: top center; display: block;
    border-radius: 2px;
  }}
  .card-art-placeholder {{
    width: 100%; height: 100%;
    display: grid; place-items: center;
    color: rgba(255,255,255,0.5);
    font-family: "Courier New", monospace; font-size: 11px;
    background: repeating-linear-gradient(45deg, #3a2a18 0 8px, #2a1c0a 8px 16px);
    letter-spacing: 1px;
  }}

  .card-name {{
    margin: 10px 0 2px;
    font-size: 15px;
    font-weight: 700;
    font-family: "Georgia", serif;
    line-height: 1.2;
    color: var(--ink);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}

  .card-tag {{
    margin: 2px 0 6px;
    font-family: "Georgia", serif;
    font-size: 12px;
    font-style: italic;
    color: var(--dim);
    line-height: 1.3;
    display: -webkit-box;
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .card-tag::before {{ content: "— "; }}

  .card-foot {{
    margin-top: auto;
    display: flex; justify-content: space-between; align-items: center;
    padding-top: 4px; border-top: 1px solid rgba(0,0,0,0.08);
    font-family: "Courier New", monospace; font-size: 9px; color: var(--dim);
    letter-spacing: 0.5px;
  }}
  .card-palette {{ display: inline-flex; gap: 3px; }}
  .card-palette i {{
    display: inline-block; width: 10px; height: 10px; border-radius: 50%;
    border: 1px solid rgba(0,0,0,0.25);
    box-shadow: inset 0 1px 1px rgba(255,255,255,0.4);
  }}

  /* STICKERS — overlaid on the art frame */
  .sticker {{
    position: absolute;
    z-index: 4;
    padding: 3px 7px;
    font-family: "Impact", "Arial Black", sans-serif;
    font-size: 11px;
    letter-spacing: 1px;
    line-height: 1;
    text-align: center;
    white-space: nowrap;
    pointer-events: none;
    box-shadow: 1px 1px 0 rgba(0,0,0,0.3), 2px 3px 5px rgba(0,0,0,0.25);
  }}
  .stk-new {{
    background: #ffeb3b; color: #c43d15;
    border: 2px solid #c43d15;
    clip-path: polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%);
    padding: 8px 12px;
    font-size: 14px;
  }}
  .stk-price, .stk-price-tiny {{
    background: #fff177; color: #2a2418;
    border: 1px dashed #c43d15;
    font-family: "Courier New", monospace;
    font-weight: 700;
  }}
  .stk-price-tiny {{ font-size: 9px; padding: 2px 5px; }}
  .stk-rare {{
    background: rgba(196, 61, 21, 0.92); color: #fff;
    border-radius: 50% / 30%;
    padding: 5px 12px;
    font-style: italic;
    font-size: 12px;
  }}
  .stk-vintage {{
    background: #828f4a; color: #fffbe6;
    border: 1px solid #4f5a1f;
    font-size: 10px;
  }}
  .stk-mint {{
    background: #a8e6cf; color: #1f5a35;
    border: 1px solid #1f5a35;
    font-size: 10px;
  }}
  .stk-used {{
    background: #b89669; color: #2a1810;
    border: 1px solid #6b5230;
    opacity: 0.92;
    font-size: 10px;
  }}
  .stk-promo {{
    background: #ff44cc; color: #fff;
    border: 1px solid #8a1166;
    font-size: 10px;
  }}
  .stk-foil {{
    background: linear-gradient(135deg, #ff6b9d, #ffd966, #66e09b, #7ad9ff, #ff6b9d);
    background-size: 200% 200%;
    color: #1a1612;
    text-shadow: 1px 1px 0 #fff;
    border: 1px solid #1a1612;
    animation: foil-shift 4s linear infinite;
    font-size: 10px;
  }}
  @keyframes foil-shift {{
    to {{ background-position: 200% 200%; }}
  }}
  .stk-authentic {{
    background: rgba(31, 90, 53, 0.92); color: #fffbe6;
    border-radius: 50% / 35%;
    padding: 5px 10px;
    font-style: italic;
    font-size: 10px;
  }}
  .stk-free {{
    background: #ff8c4a; color: #fff;
    border: 1px solid #c43d15;
    font-size: 11px;
  }}
  .stk-reduced {{
    background: #fffbe6; color: #c43d15;
    border: 2px solid #c43d15;
    text-decoration: line-through;
    text-decoration-color: #c43d15;
    font-size: 10px;
  }}
  .stk-limited {{
    background: #1a1612; color: #ffd966;
    font-size: 9px;
  }}
  .stk-show {{
    background: #c89a3e; color: #1a1612;
    border-radius: 50%;
    padding: 6px 8px;
    font-size: 8px;
    line-height: 1;
    text-align: center;
    width: 50px; height: 50px;
    display: grid; place-items: center;
    box-shadow: 0 0 0 2px #1a1612 inset, 1px 1px 0 rgba(0,0,0,0.3);
  }}
  .stk-sunday {{
    background: #c8e3f5; color: #093540;
    border: 1px solid #093540;
    font-family: "Brush Script MT", cursive;
    font-style: normal;
    font-size: 13px;
    padding: 3px 9px;
  }}
  .stk-mustgo {{
    background: #c43d15; color: #fff;
    font-size: 11px;
    font-style: italic;
  }}
  .stk-asis {{
    background: transparent; color: #2a1810;
    border: 1px solid #2a1810;
    font-size: 10px;
    font-family: "Courier New", monospace;
  }}
  .stk-askm {{
    background: #fdf9ec; color: #2a2418;
    border: 1px dashed #2a2418;
    font-family: "Brush Script MT", cursive;
    font-style: normal;
    font-size: 12px;
    padding: 3px 8px;
  }}
  .stk-handmade {{
    background: #f3e7c3; color: #5a4a30;
    border: 1px solid #5a4a30;
    font-style: italic;
    font-size: 10px;
  }}
  .stk-local {{
    background: #d4e6c8; color: #2d5016;
    border: 1px solid #2d5016;
    font-size: 10px;
  }}
  .stk-fragile {{
    background: #fff; color: #c43d15;
    border: 1px solid #c43d15;
    font-size: 9px;
    letter-spacing: 2px;
  }}

  /* Holographic shine — animates on hover */
  .card-shine {{
    position: absolute; inset: 0;
    border-radius: 12px;
    pointer-events: none;
    z-index: 3;
    opacity: 0;
    background: linear-gradient(115deg,
      transparent 30%,
      rgba(255,255,255,0.40) 45%,
      rgba(150,220,255,0.30) 50%,
      rgba(255,180,255,0.30) 55%,
      transparent 70%);
    background-size: 200% 200%;
    background-position: 100% 0%;
    transition: opacity 0.25s ease;
    mix-blend-mode: overlay;
  }}
  .card:hover .card-shine {{ opacity: 1; animation: shine 1.6s ease-in-out forwards; }}
  @keyframes shine {{ to {{ background-position: 0% 100%; }} }}

  /* RARITY: visual variants */
  .rarity-c {{}}  /* common: nothing extra */
  .rarity-u {{}}  /* uncommon: nothing extra (still subtle) */
  .rarity-r .card-rarity, .rarity-h .card-rarity, .rarity-f .card-rarity {{
    color: var(--accent);
    text-shadow: 1px 1px 0 var(--gold);
  }}
  .rarity-h::after, .rarity-f::after {{
    content: "";
    position: absolute; inset: 0;
    border-radius: 12px;
    pointer-events: none; z-index: 4;
    background: conic-gradient(
      from 0deg,
      rgba(255,140,255,0.06),
      rgba(140,200,255,0.06),
      rgba(255,255,140,0.06),
      rgba(140,255,200,0.06),
      rgba(255,140,255,0.06));
    mix-blend-mode: overlay;
    opacity: 0.7;
    animation: holo-spin 18s linear infinite;
  }}
  @keyframes holo-spin {{ to {{ transform: rotate(360deg); }} }}
  .rarity-f::before {{
    background: linear-gradient(135deg, var(--gold) 0%, var(--c1, #cb6446) 50%, var(--gold) 100%) !important;
  }}
  .rarity-f .card-num::after {{
    content: " · 1ST ED";
    color: var(--gold); font-weight: 700;
  }}

  /* FOOTER */
  footer {{
    max-width: 940px;
    margin: 48px auto 0;
    padding: 16px 22px;
    border-top: 1px dashed var(--dim);
    color: var(--dim);
    font-size: 12px;
    text-align: center;
    line-height: 1.7;
  }}
  footer a {{ color: var(--accent); }}

  /* Mobile */
  @media (max-width: 640px) {{
    body {{ padding: 16px 10px 60px; }}
    .grid {{ gap: 16px 12px; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }}
    .card-name {{ font-size: 13px; }}
    .card:hover {{ transform: rotate(0) translateY(-2px) scale(1.01); }}
  }}
</style>
</head>
<body>

<header class="hero">
  <h1>THE&nbsp;COLLECTION</h1>
  <div class="sub">everything we've put up at outbox.cafe · cards drop hourly · gotta look at 'em all</div>
  <div class="meta">
    <span>SET: 2026</span>
    <span>{count} / ∞</span>
    <a href="/">→ NEWEST CARD</a>
  </div>
</header>

<main class="grid">
{cards_html if entries else '<p style="text-align:center;color:var(--dim);font-size:14px;padding:40px;">no cards yet. the first one drops at the top of the hour.</p>'}
</main>

<footer>
  outbox.cafe · trading cards mint themselves at the top of every hour<br>
  <small>rarity is randomly assigned at mint. 1st-edition cards have a gold border. holographics shimmer in the dark.</small>
</footer>

</body>
</html>
"""
    CABINET_PATH.write_text(inject_reload(cabinet_html))


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

    # Inject the canonical spec as meta tags so the cabinet can read it reliably
    html = inject_spec_meta(html, spec)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_file = ARCHIVE_DIR / filename_for_now()
    # Archive entries get the nav.js chrome so visitors can flip ← → through history.
    archive_file.write_text(inject_nav(html))
    # Homepage adds the auto-reload script on top of nav (it watches for the next hour).
    INDEX_PATH.write_text(inject_reload(inject_nav(html)))
    # Record the produced filename in the spec so future matchups are unambiguous
    spec["file"] = archive_file.name

    # Screenshot for the cabinet — non-fatal on failure
    shot_path = THUMBS_DIR / (archive_file.stem + ".png")
    if take_screenshot(archive_file, shot_path):
        print(f"  thumbnail → {shot_path.name}")

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
