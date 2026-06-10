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

ROOT = Path(__file__).resolve().parent.parent
SLIPS_DIR = ROOT / "archive" / "slips"
COLUMNS_DIR = ROOT / "archive" / "columns"
SLIPS_PAGE = ROOT / "slips" / "index.html"
COLUMNS_PAGE = ROOT / "columns" / "index.html"
GUESTBOOK_DATA = ROOT / "data" / "guestbook.jsonl"
GUESTBOOK_PAGE = ROOT / "guestbook" / "index.html"

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


def rebuild_ritual_pages() -> None:
    """Rebuild the ritual + guestbook pages. Best-effort per page."""
    import sys
    for fn in (rebuild_slips_page, rebuild_columns_page, rebuild_guestbook_page):
        try:
            fn()
        except Exception as e:
            print(f"[ritual_pages] {fn.__name__} failed (non-fatal): {e}", file=sys.stderr)


if __name__ == "__main__":
    rebuild_ritual_pages()
    print(f"wrote {SLIPS_PAGE.relative_to(ROOT)}, {COLUMNS_PAGE.relative_to(ROOT)}, {GUESTBOOK_PAGE.relative_to(ROOT)}")
