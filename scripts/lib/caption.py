"""Zero-Claude caption fallback for the social posters.

The drop captions (`post_bsky`, `post_tumblr`) are written by Claude in a
staff cat's voice. When Claude is capped — the weekly window runs dry by
Thursday most weeks, and the 5-hour session cap bites at random — those calls
return nothing and the drop still lands on the site but never reaches Bluesky
or Tumblr. Between 2026-08-15 and 2026-09-15 that was **48 of 126 drops**, and
it clusters on Fri–Sun, so the feeds went dark most weekends while the site
kept posting on schedule.

This module is the same idea as the ritual drawer (CLAUDE.md, "Weekend rituals
must NEVER call Claude on the day"): separate the words from the posting. But a
drop's words already exist — they are on the page. `personas.json` says a drop
post is "sometimes a quote pulled from the piece", and the posting philosophy
says excerpts are the inspiration source, so a quoted fragment lifted straight
from the poster *is* an in-brand post, not a degraded one. This picks one
deterministically, with no LLM anywhere in the path, so a cap window can never
disable it.

Sources, in order: visible DOM text lines, then prose-looking string literals
inside <script> (many pages render their copy from JS), then the <title> alone
as the last resort. Every fragment passes the same house-rule gate the prompts
enforce (no URLs, no self-reference, no AI talk, no "weird/retro", no UI
instructions, no photo credits) and is scored for shape — length sweet spot,
terminal punctuation, not shouty, not a `·`-separated chrome line.
"""
from __future__ import annotations

import html as _html
import random
import re
from dataclasses import dataclass

# Fragments carrying any of these never post — same house rules as the prompts.
_BLOCK_RE = re.compile(
    r"https?://|www\.|\boutbox\b|\bclaude\b|\banthropic\b|\bchatgpt\b|\bllm\b|"
    r"\b(ai|a\.i\.)\b|\bbots?\b|\bautomat|\bweird\b|\bretro\b|\bvintage\b|"
    r"\bunsplash\b|\bphoto by\b|\bjavascript\b|\bbrowser\b|\bloading\b|"
    r"\bcookies?\b|\bcopyright\b|©|\ball rights reserved\b|"
    r"\b(click|tap|press|hold|drag|scroll|swipe|type|hit)\b[^.!?]{0,30}\b"
    r"(here|to|the|a|an|any|your|space|enter|arrow|key|button|screen|anywhere)\b|"
    r"\b(spacebar|space bar|arrow keys?|keyboard|mouse|trackpad|touchscreen)\b|"
    r"\b(backspace|shift|ctrl|esc|escape|enter|return) (key|to|lifts|clears|resets|undoes)\b|"
    r"^(keys?|controls?|shortcuts?|how to play|instructions?)\b|"
    r"\bwasd\b|←|→|↑|↓|\[\s*[a-z]\s*\]",
    re.IGNORECASE,
)

# Things that look like code / markup leaking out of a script literal.
_CODEISH_RE = re.compile(
    r"[{}<>=;\\|]|\$\{|\bfunction\b|=>|\bvar\b|\bconst\b|"
    r"\bnull\b|\bundefined\b|\d+px\b|\d+(vw|vh|em|rem)\b|rgba?\(|hsla?\(|"
    r"#[0-9a-f]{3,8}\b|\bdata-|\baria-|\bclassname\b|"
    r"\bsrc\b|\bhref\b|\bpng\b|\bwebp\b|\bsvg\b|\.js\b|\.css\b",
    re.IGNORECASE,
)

_SENTENCE_END = (".", "!", "?", "”", '"', "’", "'", ")")

_BLOCK_TAGS = (
    "p|div|li|h[1-6]|tr|td|th|section|article|aside|header|footer|main|nav|"
    "blockquote|dt|dd|pre|figcaption|caption|label|legend|summary|details|"
    "button|option|title"
)


@dataclass(frozen=True)
class Fragment:
    text: str
    source: str      # "dom" | "script" | "title"
    position: float  # 0..1 through its source, for a mild middle-of-page bias
    score: float = 0.0


def extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip()


def _dom_lines(html: str) -> list[str]:
    s = re.sub(r"<(script|style|noscript|svg|template|textarea)[^>]*>.*?</\1>",
               " ", html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.DOTALL)
    s = re.sub(rf"</({_BLOCK_TAGS})\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    out: list[str] = []
    for raw in s.split("\n"):
        line = re.sub(r"[ \t\r\f\v]+", " ", raw).strip()
        # Fix the " ," / " ." spacing that tag-stripping leaves behind.
        line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        if line:
            out.append(line)
    return out


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?:(?<=[.!?])|(?<=[.!?][”\"’')]))\s+(?=[A-Z“\"'(])", text)
    return [p.strip() for p in parts if p.strip()]


def _script_strings(html: str) -> list[str]:
    """Prose-looking string literals from inline <script> blocks. Many pages
    render their copy from JS, so this is often the only prose there is. Long
    literals (a whole paragraph in one template string) are split into
    sentences. Template fragments ("pounds of water, and every ounce") are
    common, so callers gate these harder than DOM lines — a script fragment
    must read as a complete sentence."""
    out: list[str] = []
    for sc in re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE):
        for m in re.finditer(
            r'"((?:[^"\\\n]|\\.){30,6000})"|\'((?:[^\'\\\n]|\\.){30,6000})\'|`((?:[^`\\]|\\.){30,6000})`',
            sc,
        ):
            v = m.group(1) or m.group(2) or m.group(3) or ""
            v = v.replace("\\n", " ").replace("\\'", "'").replace('\\"', '"')
            v = re.sub(r"\s+", " ", v).strip()
            if not v:
                continue
            if len(v) > 400:
                out.extend(_split_sentences(v))
            else:
                out.append(v)
    return out


def _clean_quotes(text: str) -> str:
    """A fragment that is already a quotation gets its outer quotes removed so
    the caption doesn't double-wrap it."""
    t = text.strip()
    for a, b in (('"', '"'), ("“", "”"), ("‘", "’"), ("'", "'")):
        if len(t) > 2 and t.startswith(a) and t.endswith(b) and t.count(a) + t.count(b) == 2:
            return t[1:-1].strip()
    return t


def _passes_gate(text: str, *, title: str, strict: bool) -> bool:
    if _BLOCK_RE.search(text):
        return False
    if _CODEISH_RE.search(text):
        return False
    if title and text.casefold() == title.casefold():
        return False
    words = text.split()
    if len(words) < 5:
        return False
    if not text[0].isalpha() and text[0] not in "“‘\"'(":
        return False
    letters = sum(c.isalpha() for c in text)
    if letters / max(len(text), 1) < 0.6:
        return False
    if strict:
        # Script literals must be a whole sentence, not a template piece.
        if not text.rstrip().endswith(_SENTENCE_END):
            return False
        if not text[0].isupper() and text[0] not in "“\"'":
            # lowercase-first is fine on the page (some pages are all lowercase)
            # but from a script string it usually means a mid-sentence fragment.
            return False
    return True


def _score(text: str, position: float, *, ideal_lo: int, ideal_hi: int) -> float:
    n = len(text)
    score = 0.0
    if ideal_lo <= n <= ideal_hi:
        score += 3.0
    else:
        score += 3.0 - min(3.0, abs(n - (ideal_lo if n < ideal_lo else ideal_hi)) / 40.0)
    if text.rstrip().endswith(_SENTENCE_END):
        score += 1.5
    if "," in text:
        score += 0.4
    if re.search(r"\d", text):
        score += 0.3
    if " · " in text or " | " in text or text.count("—") > 1:
        score -= 1.5   # chrome / meta lines
    letters = [c for c in text if c.isalpha()]
    if letters:
        upper = sum(c.isupper() for c in letters) / len(letters)
        if upper > 0.5:
            score -= 2.0
    if text.count("!") >= 3:
        score -= 0.5
    # Mild preference for the middle of the page over header/footer material.
    score += 0.6 * (1.0 - abs(position - 0.5) * 2)
    return score


def candidates(html: str, *, min_len: int, max_len: int,
               ideal_lo: int, ideal_hi: int) -> list[Fragment]:
    title = extract_title(html)
    seen: set[str] = set()
    frags: list[Fragment] = []

    def add(text: str, source: str, position: float, strict: bool) -> None:
        text = _clean_quotes(text)
        key = text.casefold()
        if key in seen or not (min_len <= len(text) <= max_len):
            return
        if not _passes_gate(text, title=title, strict=strict):
            return
        seen.add(key)
        frags.append(Fragment(text, source, position,
                              _score(text, position, ideal_lo=ideal_lo, ideal_hi=ideal_hi)))

    dom = _dom_lines(html)
    for i, line in enumerate(dom):
        add(line, "dom", i / max(len(dom) - 1, 1), strict=False)
    scripts = _script_strings(html)
    for i, s in enumerate(scripts):
        add(s, "script", i / max(len(scripts) - 1, 1), strict=True)
    frags.sort(key=lambda f: f.score, reverse=True)
    return frags


def pick_fragment(html: str, rng: random.Random | None = None, *,
                  min_len: int = 40, max_len: int = 200,
                  ideal_lo: int = 60, ideal_hi: int = 160,
                  top_n: int = 5) -> Fragment | None:
    """Best few fragments by score, one picked at random so repeated fallbacks
    on the same page (bsky + tumblr, or a throwback later) don't all quote the
    same line. Returns None only when the page has no usable prose at all."""
    rng = rng or random.Random()
    frags = candidates(html, min_len=min_len, max_len=max_len,
                       ideal_lo=ideal_lo, ideal_hi=ideal_hi)
    if not frags:
        return None
    pool = frags[:top_n]
    weights = [max(0.1, f.score - pool[-1].score + 1.0) for f in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def excerpt_caption(html: str, signoff: str = "", rng: random.Random | None = None, *,
                    platform: str = "bsky") -> tuple[str, str] | None:
    """Return (caption_text, source) with no LLM involved, or None if the page
    has nothing quotable and no title. `platform` sets the length envelope:
    bsky captions stay under ~200 chars like the prompt asks for; tumblr gets
    room for a longer passage."""
    if platform == "tumblr":
        frag = pick_fragment(html, rng, min_len=60, max_len=420, ideal_lo=120, ideal_hi=320)
    else:
        frag = pick_fragment(html, rng, min_len=40, max_len=200, ideal_lo=60, ideal_hi=160)
    if frag is not None:
        body = f"“{frag.text}”"
        source = frag.source
    else:
        title = extract_title(html)
        if not title or _BLOCK_RE.search(title):
            return None
        body = title
        source = "title"
    signoff = (signoff or "").strip()
    text = f"{body}\n\n{signoff}" if signoff else body
    return text, source
