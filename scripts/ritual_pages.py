"""Build the on-site homes for the weekly rituals.

The cafe's recurring rituals (Mr. Quiet's Sunday slip, Doris's muffin column)
have been generating real, persistent content for weeks — committed to the
repo, posted to social, and then never surfaced on outbox.cafe itself. These
pages fix that: the site gets returning-visitor surfaces built from content
that already exists.

- /slips/    — "the slip drawer": every slip image from archive/slips/
- /columns/  — "the muffin column": every column text from archive/columns/

Both pages are rebuilt by the ritual scripts right after they post (so the
page updates the moment a new slip/column lands) and by generate.py on every
gen (so a manual edit or backfill propagates without waiting for Sunday).
Deterministic output — rebuilding with unchanged inputs produces identical
HTML, so the gen cron's `git add -A` stays quiet.
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
from datetime import datetime
from pathlib import Path

from lib import canon as canon_lib

ROOT = Path(__file__).resolve().parent.parent
SLIPS_DIR = ROOT / "archive" / "slips"
COLUMNS_DIR = ROOT / "archive" / "columns"
SLIPS_PAGE = ROOT / "slips" / "index.html"
COLUMNS_PAGE = ROOT / "columns" / "index.html"
GUESTBOOK_DATA = ROOT / "data" / "guestbook.jsonl"
GUESTBOOK_PAGE = ROOT / "guestbook" / "index.html"
ARCHIVE_DIR = ROOT / "archive"
REGULARS_PAGE = ROOT / "regulars" / "index.html"

# Shared look — same paper/ink palette as /about/ so the cafe's non-gen pages
# read as one room.
_BASE_CSS = """
  :root {
    --ink: #1a1612;
    --paper: #f4ecdc;
    --paper-2: #ede2c8;
    --accent: #b8473a;
    --dim: #7a6a4c;
    --teal: #1d4d57;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; color: var(--ink); }
  body {
    min-height: 100vh;
    font-family: "Georgia", "Times New Roman", serif;
    font-size: 17px; line-height: 1.55;
    background:
      radial-gradient(circle at 18% 22%, rgba(184,71,58,0.04) 0 40%, transparent 60%),
      radial-gradient(circle at 82% 78%, rgba(29,77,87,0.05) 0 38%, transparent 58%),
      var(--paper);
    padding: 42px 18px 80px;
  }
  main { max-width: 860px; margin: 0 auto; }
  header.hero { text-align: center; margin-bottom: 34px; }
  header.hero h1 {
    font-family: "Georgia", serif;
    font-size: 40px; line-height: 1.05;
    margin: 0 0 8px; letter-spacing: -0.01em;
  }
  header.hero .sub { color: var(--dim); font-style: italic; font-size: 16px; }
  a { color: var(--accent); }
  footer {
    margin-top: 54px; padding-top: 16px;
    border-top: 1px dashed var(--dim);
    color: var(--dim); font-size: 13px; text-align: center; line-height: 1.8;
  }
  footer a { margin: 0 8px; }
"""


def _head(title: str, description: str, path: str, extra_css: str) -> str:
    safe_title = _html.escape(title)
    safe_desc = _html.escape(description)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} · outbox.cafe</title>
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="alternate" type="application/rss+xml" title="outbox.cafe" href="/feed.xml">
<meta property="og:title" content="{safe_title} · outbox.cafe">
<meta property="og:description" content="{safe_desc}">
<meta property="og:url" content="https://outbox.cafe{path}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="outbox.cafe">
<style>{_BASE_CSS}{extra_css}</style>
</head>
<body>
<main>
"""


_FOOTER = """
<footer>
  <a href="/">the front door</a> ·
  <a href="/archive/">the collection</a> ·
  <a href="/about/">who's who</a>
</footer>
</main>
</body>
</html>
"""


def _pretty_date(stem: str) -> str:
    """'2026-05-31' → 'sunday · may 31, 2026' (site voice: lowercase)."""
    try:
        d = datetime.strptime(stem, "%Y-%m-%d")
    except ValueError:
        return stem
    return f"{d.strftime('%A').lower()} · {d.strftime('%B').lower()} {d.day}, {d.year}"


def _rot(stem: str, spread: float = 1.6) -> float:
    """Small deterministic rotation per item — dropped on the counter, not filed."""
    h = int.from_bytes(hashlib.md5(stem.encode()).digest()[:2], "big")
    return ((h % 200) / 100.0 - 1.0) * spread


_SLIPS_CSS = """
  .drawer {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    gap: 26px 22px;
  }
  figure.slip {
    margin: 0;
    transform: rotate(var(--rot, 0deg));
  }
  figure.slip img {
    width: 100%; height: auto; display: block;
    border: 1px solid rgba(0,0,0,0.12);
    box-shadow: 0 2px 6px rgba(0,0,0,0.10), 0 10px 22px rgba(0,0,0,0.10);
    background: var(--paper-2);
  }
  figure.slip figcaption {
    margin-top: 8px; text-align: center;
    font-family: "Courier New", ui-monospace, monospace;
    font-size: 12px; color: var(--dim); letter-spacing: 0.04em;
  }
  .empty { text-align: center; color: var(--dim); font-style: italic; padding: 40px 0; }
"""


def rebuild_slips_page() -> None:
    """Rebuild /slips/ — the slip drawer."""
    slips = sorted(SLIPS_DIR.glob("*.png"), reverse=True) if SLIPS_DIR.exists() else []

    cards = []
    for f in slips:
        date = _pretty_date(f.stem)
        cards.append(
            f'  <figure class="slip" style="--rot:{_rot(f.stem):.2f}deg">\n'
            f'    <img src="/archive/slips/{f.name}" alt="a typewritten slip of paper on the cafe counter, dated {date}" loading="lazy">\n'
            f'    <figcaption>{date}</figcaption>\n'
            f'  </figure>'
        )
    body = (
        '<div class="drawer">\n' + "\n".join(cards) + "\n</div>"
        if cards
        else '<p class="empty">the drawer is empty. check back on a sunday.</p>'
    )

    page = (
        _head(
            "the slip drawer",
            "mr. quiet doesn't speak. on sundays, a slip of paper appears on the counter. we keep them all.",
            "/slips/",
            _SLIPS_CSS,
        )
        + """
<header class="hero">
  <h1>the slip drawer</h1>
  <div class="sub">mr. quiet doesn't speak. on sundays, a slip of paper appears on the counter.<br>we keep every one in this drawer.</div>
</header>

"""
        + body
        + _FOOTER
    )
    SLIPS_PAGE.parent.mkdir(parents=True, exist_ok=True)
    SLIPS_PAGE.write_text(page)


_COLUMNS_CSS = """
  article.column {
    background: var(--paper-2);
    border-left: 4px solid var(--teal);
    padding: 24px 26px 18px;
    margin: 0 0 34px;
    transform: rotate(var(--rot, 0deg));
    box-shadow: 0 2px 6px rgba(0,0,0,0.07);
  }
  article.column h2 {
    margin: 0 0 2px; font-size: 23px; line-height: 1.2; letter-spacing: -0.01em;
  }
  article.column .date {
    font-family: "Courier New", ui-monospace, monospace;
    font-size: 12px; color: var(--dim); letter-spacing: 0.04em;
    margin-bottom: 14px;
  }
  article.column p { margin: 0 0 13px; }
  article.column .signoff { font-style: italic; color: var(--dim); margin-top: 4px; }
  .empty { text-align: center; color: var(--dim); font-style: italic; padding: 40px 0; }
"""


def _parse_column(text: str) -> tuple[str, list[str], str]:
    """Split a column file into (title, paragraphs, signoff)."""
    text = text.strip()
    lines = text.split("\n", 1)
    title = lines[0].strip()
    body = lines[1].strip() if len(lines) > 1 else ""
    signoff = ""
    m = re.search(r"\n?\s*(—\s*Doris\.?)\s*$", body)
    if m:
        signoff = m.group(1).strip()
        body = body[: m.start()].rstrip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    return title, paragraphs, signoff or "—Doris"


def rebuild_columns_page() -> None:
    """Rebuild /columns/ — Doris's muffin column, back issues."""
    files = sorted(COLUMNS_DIR.glob("*.txt"), reverse=True) if COLUMNS_DIR.exists() else []

    articles = []
    for f in files:
        try:
            title, paragraphs, signoff = _parse_column(f.read_text(errors="ignore"))
        except Exception:
            continue
        paras_html = "\n".join(
            f"    <p>{_html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs
        )
        articles.append(
            f'  <article class="column" style="--rot:{_rot(f.stem, 0.4):.2f}deg">\n'
            f"    <h2>{_html.escape(title)}</h2>\n"
            f'    <div class="date">{_pretty_date(f.stem)}</div>\n'
            f"{paras_html}\n"
            f'    <p class="signoff">{_html.escape(signoff)}</p>\n'
            f"  </article>"
        )
    body = (
        "\n".join(articles)
        if articles
        else '<p class="empty">no back issues yet. doris files on sundays, mostly.</p>'
    )

    page = (
        _head(
            "the muffin column",
            "doris files a weekly column about the cafe's muffins. she has opinions. back issues, newest first.",
            "/columns/",
            _COLUMNS_CSS,
        )
        + """
<header class="hero">
  <h1>the muffin column</h1>
  <div class="sub">doris files a column most sundays. she has opinions about baked goods.<br>back issues below, newest first.</div>
</header>

"""
        + body
        + _FOOTER
    )
    COLUMNS_PAGE.parent.mkdir(parents=True, exist_ok=True)
    COLUMNS_PAGE.write_text(page)


_GUESTBOOK_CSS = """
  .sign-card {
    background: var(--paper-2);
    border: 1px solid rgba(0,0,0,0.10);
    box-shadow: 0 2px 6px rgba(0,0,0,0.07);
    padding: 22px 24px;
    margin: 0 0 40px;
    transform: rotate(-0.3deg);
  }
  .sign-card label {
    display: block; font-size: 13px; color: var(--dim);
    letter-spacing: 0.04em; margin: 12px 0 4px;
    font-family: "Courier New", ui-monospace, monospace;
  }
  .sign-card input[type=text], .sign-card textarea {
    width: 100%; border: 1px solid rgba(0,0,0,0.25); background: #fdf9ec;
    color: var(--ink); font: 16px/1.5 "Georgia", serif; padding: 8px 10px;
  }
  .sign-card textarea { min-height: 84px; resize: vertical; }
  .sign-card .hp { position: absolute; left: -9999px; height: 1px; overflow: hidden; }
  .sign-card button {
    margin-top: 14px; padding: 9px 22px; cursor: pointer;
    background: var(--ink); color: var(--paper);
    border: 2px solid var(--gold, #c89a3e);
    font: 600 14px "Courier New", ui-monospace, monospace; letter-spacing: 0.08em;
    box-shadow: 3px 3px 0 var(--gold, #c89a3e);
  }
  .sign-card button:active { transform: translate(2px, 2px); box-shadow: 1px 1px 0 var(--gold, #c89a3e); }
  .sign-card .form-note { font-size: 13px; color: var(--dim); font-style: italic; margin-top: 10px; }
  .sign-card .result { font-style: italic; margin-top: 12px; }
  .entry {
    border-bottom: 1px dashed rgba(0,0,0,0.18);
    padding: 18px 4px;
    transform: rotate(var(--rot, 0deg));
  }
  .entry .who {
    font-weight: 700;
  }
  .entry .when {
    font-family: "Courier New", ui-monospace, monospace;
    font-size: 12px; color: var(--dim); margin-left: 8px; letter-spacing: 0.04em;
  }
  .entry .said { margin: 6px 0 0; }
  .entry .reply {
    margin: 10px 0 0 22px; padding-left: 12px;
    border-left: 3px solid var(--teal);
    font-style: italic; color: #3c4a42;
  }
  .empty { text-align: center; color: var(--dim); font-style: italic; padding: 40px 0; }
"""

_GUESTBOOK_FORM = """
<div class="sign-card">
  <form id="sign-form">
    <label for="gb-name">your name</label>
    <input type="text" id="gb-name" name="name" maxlength="40" required>
    <label for="gb-message">your note</label>
    <textarea id="gb-message" name="message" maxlength="280" required></textarea>
    <div class="hp" aria-hidden="true">
      <label for="gb-website">website</label>
      <input type="text" id="gb-website" name="website" tabindex="-1" autocomplete="off">
    </div>
    <button type="submit">leave it on the counter</button>
    <div class="form-note">notes appear once a cat has read them — usually within the hour. words only, no links.</div>
    <div class="result" id="sign-result" role="status"></div>
  </form>
</div>
<script>
  (function () {
    var form = document.getElementById('sign-form');
    var result = document.getElementById('sign-result');
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      result.textContent = '…';
      fetch('/api/sign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: document.getElementById('gb-name').value,
          message: document.getElementById('gb-message').value,
          website: document.getElementById('gb-website').value
        })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.ok) {
          form.querySelectorAll('input,textarea,button').forEach(function (el) { el.disabled = true; });
          result.textContent = d.note || 'your note is on the counter.';
        } else {
          result.textContent = d.error || 'something went sideways — try again?';
        }
      }).catch(function () {
        result.textContent = 'the mail is having a moment — try again in a bit.';
      });
    });
  })();
</script>
"""


def rebuild_guestbook_page() -> None:
    """Rebuild /guestbook/ from data/guestbook.jsonl (approved entries only —
    the reviewer cron is the only writer of that file)."""
    entries: list[dict] = []
    if GUESTBOOK_DATA.exists():
        for line in GUESTBOOK_DATA.read_text(errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("name") and e.get("message"):
                entries.append(e)
    entries.reverse()  # newest first
    entries = entries[:300]

    blocks = []
    for e in entries:
        name = _html.escape(str(e["name"]))
        message = _html.escape(str(e["message"]))
        when = ""
        ts = str(e.get("ts", ""))
        if ts[:10]:
            when = f'<span class="when">{_pretty_date(ts[:10])}</span>'
        reply_html = ""
        if e.get("reply"):
            reply_html = (
                f'\n    <div class="reply">{_html.escape(str(e["reply"]))}</div>'
            )
        blocks.append(
            f'  <div class="entry" style="--rot:{_rot(str(e.get("id", name)) , 0.5):.2f}deg">\n'
            f'    <span class="who">{name}</span>{when}\n'
            f'    <p class="said">{message}</p>{reply_html}\n'
            f"  </div>"
        )
    body = "\n".join(blocks) if blocks else '<p class="empty">no notes yet. the pen is right there.</p>'

    page = (
        _head(
            "the guestbook",
            "the door is open. leave a note on the counter — a cat will read it.",
            "/guestbook/",
            _GUESTBOOK_CSS,
        )
        + """
<header class="hero">
  <h1>the guestbook</h1>
  <div class="sub">the sign above the door says you're welcome inside.<br>leave a note. a cat reads every one.</div>
</header>

"""
        + _GUESTBOOK_FORM
        + body
        + _FOOTER
    )
    GUESTBOOK_PAGE.parent.mkdir(parents=True, exist_ok=True)
    GUESTBOOK_PAGE.write_text(page)


_REGULARS_CSS = """
  .intro {
    background: var(--paper-2);
    padding: 20px 24px;
    border-left: 4px solid var(--teal);
    margin: 0 0 34px;
    font-size: 16px;
  }
  .intro p { margin: 0 0 10px; }
  .intro p:last-child { margin-bottom: 0; }
  article.reg {
    margin: 0 0 26px;
    padding: 16px 20px 14px;
    background: var(--paper-2);
    border-left: 4px solid var(--accent);
    transform: rotate(var(--rot, 0deg));
  }
  article.reg .name {
    font-size: 21px; font-weight: bold; line-height: 1.2;
    margin-bottom: 3px;
  }
  article.reg .hint {
    color: var(--ink); font-style: italic; font-size: 15.5px;
    line-height: 1.5; margin-bottom: 10px;
  }
  article.reg .count {
    font-family: "Courier New", ui-monospace, monospace;
    font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--dim); margin-bottom: 6px;
  }
  ul.sightings { list-style: none; margin: 0; padding: 0; }
  ul.sightings li { margin: 0 0 4px; font-size: 15px; line-height: 1.45; }
  ul.sightings .when {
    font-family: "Courier New", ui-monospace, monospace;
    font-size: 11px; color: var(--dim); letter-spacing: 0.03em;
    white-space: nowrap;
  }
  ul.sightings .more { color: var(--dim); font-style: italic; font-size: 14px; }
  h2.section {
    font-family: "Georgia", serif;
    font-size: 22px;
    margin: 44px 0 14px;
    border-bottom: 1px dashed var(--dim);
    padding-bottom: 8px;
  }
  .waiting {
    background: var(--paper-2);
    padding: 16px 20px;
    border-left: 4px solid var(--dim);
    font-size: 15.5px; line-height: 1.6;
  }
  .waiting .who { font-style: italic; }
  .empty { text-align: center; color: var(--dim); font-style: italic; padding: 40px 0; }
  @media (max-width: 540px) {
    article.reg { padding: 14px 16px 12px; transform: none; }
    article.reg .name { font-size: 19px; }
  }
"""


def _page_text(raw: str) -> str:
    """Visible text of a gen page — tags, scripts and styles removed."""
    s = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", _html.unescape(s))


def _page_title(raw: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', raw, re.IGNORECASE)
    if not m:
        return ""
    return re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip()


def _gen_date(stem: str) -> str:
    """'2026-07-21T12-04' → 'july 21, 2026' (site voice: lowercase)."""
    try:
        d = datetime.strptime(stem[:10], "%Y-%m-%d")
    except ValueError:
        return stem
    return f"{d.strftime('%B').lower()} {d.day}, {d.year}"


def _sort_name(name: str) -> str:
    """Alphabetise 'the Good Wok' under G, like any decent index."""
    return re.sub(r"^the\s+", "", name, flags=re.IGNORECASE).lower()


def _trim_restatement(hint: str, name: str) -> str:
    """Drop a leading restatement of the name ('Wren & Halloway, Instrument
    Makers — a maker's mark…' → 'a maker's mark…'), which reads as a stutter
    once the name is already the heading. Only fires when a dash/colon gives a
    clean cut point, so a hint that merely opens with the name stays whole."""
    if not hint.lower().startswith(name.lower()):
        return hint
    m = re.search(r"\s*[—–:-]\s+", hint[: len(name) + 60])
    if not m or m.start() < len(name):
        return hint
    rest = hint[m.end():].strip()
    return rest or hint


def _lead_lower(s: str) -> str:
    """Site voice runs lowercase. Only ever touches a leading article, so a
    name that opens the sentence ('Wren & Halloway — a maker's mark') is safe."""
    first = s.split(" ", 1)[0]
    return s[0].lower() + s[1:] if first in ("A", "An", "The") else s


def rebuild_regulars_page() -> None:
    """Rebuild /regulars/ — the names that turn up in more than one posting.

    The cafe has been quietly accumulating a shared cast (a shy cat, a takeout
    place, an address where everything is for sale) that recurs across pages,
    with no way for anyone to notice the pattern or find the other appearances.
    This page is the list kept behind the counter: each regular, and every
    posting it turns up in, linked. Sightings are found by scanning the visible
    text of every archived page, so the index keeps itself honest — a name only
    appears here because it actually showed up somewhere.
    """
    # Only the ACTIVE roster. A retired element never became a regular — that
    # is the whole meaning of retirement — so it stops being listed here and
    # stops being "expected back". Its one appearance stays on the page that
    # made it; nothing is rewritten.
    try:
        elements = canon_lib.active()
    except Exception:
        elements = []

    pages: list[tuple[str, str, str]] = []  # (filename, title, text)
    for f in sorted(ARCHIVE_DIR.glob("*.html"), reverse=True):
        if f.name == "index.html":
            continue
        try:
            raw = f.read_text(errors="ignore")
        except Exception:
            continue
        pages.append((f.name, _page_title(raw) or f.stem, _page_text(raw)))

    regulars: list[dict] = []
    for el in elements:
        name = (el.get("name") or "").strip()
        hint = (el.get("hint") or "").strip()
        if not name or not hint:
            continue
        # What counts as a sighting lives in lib/canon.py, so this page, the
        # scout's retirement verdict, and the offer weighting can never drift
        # apart on the question of whether a name actually turned up.
        pats = canon_lib.sighting_patterns(el)
        seen = [
            (fn, title) for fn, title, text in pages
            if any(p.search(text) for p in pats)
        ]
        regulars.append({
            "name": name,
            "hint": _lead_lower(_trim_restatement(hint, name)),
            "seen": seen,
        })

    # Most-present first — the page reads as a list of who's around, and the
    # ones still waiting for a first sighting get their own quiet section.
    around = sorted(
        (r for r in regulars if r["seen"]),
        key=lambda r: (-len(r["seen"]), _sort_name(r["name"])),
    )
    waiting = sorted((r for r in regulars if not r["seen"]), key=lambda r: _sort_name(r["name"]))

    MAX_SHOWN = 12
    cards = []
    for r in around:
        n = len(r["seen"])
        shown = r["seen"][:MAX_SHOWN]
        rest = n - len(shown)
        items = "\n".join(
            f'      <li><a href="/archive/{fn}">{_html.escape(title)}</a> '
            f'<span class="when">{_gen_date(fn[:-5])}</span></li>'
            for fn, title in shown
        )
        if rest:
            items += (
                f'\n      <li class="more">…and {rest} more</li>'
            )
        count = "one sighting" if n == 1 else f"{n} sightings"
        cards.append(
            f'  <article class="reg" style="--rot:{_rot(r["name"], 0.35):.2f}deg">\n'
            f'    <div class="name">{_html.escape(r["name"])}</div>\n'
            f'    <div class="hint">{_html.escape(r["hint"])}</div>\n'
            f'    <div class="count">{count}</div>\n'
            f'    <ul class="sightings">\n{items}\n    </ul>\n'
            f"  </article>"
        )

    if cards:
        body = "\n".join(cards)
    else:
        body = '  <p class="empty">nobody has turned up twice yet. give it a week.</p>'

    if waiting:
        names = " · ".join(f'<span class="who">{_html.escape(r["name"])}</span>' for r in waiting)
        body += (
            '\n\n<h2 class="section">not seen lately</h2>\n'
            '<div class="waiting">\n'
            "  <p>still on the list, still expected back:</p>\n"
            f"  <p>{names}</p>\n"
            "</div>"
        )

    page = (
        _head(
            "the regulars",
            "who's around at outbox.cafe, and where they've turned up.",
            "/regulars/",
            _REGULARS_CSS,
        )
        + """<header class="hero">
  <h1>the regulars</h1>
  <div class="sub">who's around, and where they've turned up</div>
</header>

<div class="intro">
  <p>none of this was planned. somebody uses a name, and then somebody else uses it again, and after a while it's just part of the place — a cat nobody has seen since 1998, a takeout counter with an item on the menu that costs nothing, a house where the chairs are always for sale.</p>
  <p>this is the list we keep behind the counter, with every posting they've turned up in.</p>
</div>

"""
        + body
        + "\n"
        + _FOOTER
    )
    REGULARS_PAGE.parent.mkdir(parents=True, exist_ok=True)
    REGULARS_PAGE.write_text(page)


def rebuild_ritual_pages() -> None:
    """Rebuild the ritual + guestbook pages. Best-effort per page."""
    import sys
    for fn in (rebuild_slips_page, rebuild_columns_page, rebuild_guestbook_page,
               rebuild_regulars_page):
        try:
            fn()
        except Exception as e:
            print(f"[ritual_pages] {fn.__name__} failed (non-fatal): {e}", file=sys.stderr)


if __name__ == "__main__":
    rebuild_ritual_pages()
    print(f"wrote {SLIPS_PAGE.relative_to(ROOT)}, {COLUMNS_PAGE.relative_to(ROOT)}, {GUESTBOOK_PAGE.relative_to(ROOT)}")
