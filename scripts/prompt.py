"""Build the generation prompt from a rolled spec."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"


def _recent_titles(n: int = 20) -> list[str]:
    """Return titles of the most recent N generations (best-effort)."""
    if not ARCHIVE_DIR.exists():
        return []
    files = sorted(ARCHIVE_DIR.glob("*.html"), reverse=True)
    titles = []
    for f in files[:n]:
        try:
            html = f.read_text(errors="ignore")
            lo = html.lower().find("<title>")
            hi = html.lower().find("</title>")
            if lo >= 0 and hi > lo:
                titles.append(html[lo + len("<title>"):hi].strip())
        except Exception:
            pass
    return titles


def _format_photos_block(photos: list[dict[str, Any]]) -> str:
    if not photos:
        return "  (no images available — write a text-only piece)"
    lines = []
    for i, p in enumerate(photos, 1):
        alt = (p.get("alt") or "").replace("\n", " ").strip() or "(no description)"
        source = p.get("source", "unsplash")
        if source == "ai":
            lines.append(
                f"  [{i}] URL: {p['url']}\n"
                f"      Alt: {alt}\n"
                f"      Source: AI-generated artwork (no attribution required, modify freely)"
            )
        else:
            lines.append(
                f"  [{i}] URL: {p['url']}\n"
                f"      Alt: {alt}\n"
                f"      Credit: {p['credit_name']} on Unsplash · {p['html_link']}"
            )
    return "\n".join(lines)


def build_prompt(spec: dict[str, Any], photos: list[dict[str, Any]] | None = None) -> str:
    """Build the prompt that gets handed to Claude to generate the hourly page."""

    def v(field: str) -> str:
        item = spec.get(field, {})
        if isinstance(item, dict):
            return item.get("value") or item.get("key") or ""
        return str(item)

    length = spec["length"]
    recent_titles = _recent_titles()
    recent_titles_block = (
        "\n".join(f"  - {t}" for t in recent_titles)
        if recent_titles
        else "  (none yet — this is one of the first generations)"
    )
    photos = photos or []
    photos_block = _format_photos_block(photos)

    return f"""You are generating an hourly artifact for outbox.cafe, a weird/retro corner of the web. Every hour a new self-contained HTML page goes up at the root and gets archived. The site values genuine variety, retro-optimism, and texture over polish. People should want to look at this for a few minutes, then look at it again later and notice new details.

ROLLED SPEC FOR THIS HOUR
=========================
- ERA:                {v('era')}
- FORMAT:             {v('format')}
- SUBJECT:            {v('subject')}
- TONE:               {v('tone')}
- LENGTH TIER:        {length['key']}  ({length['lines']} lines, {length['description']})
- PALETTE:            {v('palette')}
- MANDATORY ELEMENT:  {v('mandatory_element')}
- WILDCARD:           {v('wildcard')}
- FORBIDDEN REGISTER: {v('forbidden_register')}

Make a single self-contained HTML file that fully inhabits the rolled spec.

AVAILABLE IMAGES
================
Three images have been pre-fetched for this piece. Each is labeled with its source: either real photos from UNSPLASH (real strangers and real places — they need credit) or AI-GENERATED artwork (custom-made for this spec, no attribution needed, modify freely). You may use any, all, or none — pick what fits the format you're building.

{photos_block}

If you use one or more:
  - Embed as `<img src="..." alt="..." loading="lazy" style="...">` — set explicit max-width / max-height inline so they fit the layout. The originals are full-size; you must constrain them.
  - You may crop, mask, filter, blur, tint, frame, or otherwise integrate them into the piece's aesthetic — they don't have to look like "stock photos." Old-web pages would have photo grids, dithered photos, sepia tints, polaroid frames, drop-caps wrapping around them, etc.
  - For UNSPLASH photos: they're real strangers and real places — don't claim them as "the recipient" or "the inventor" of fictional things. They're decoration, mood, texture. Photo credits are REQUIRED by Unsplash's terms — include a small "Photo credits" line at the bottom (very small, italic, fine print) listing each Unsplash photographer used: "Photo by NAME on Unsplash" with the credit link. Skip this line entirely if you only used AI-generated images.
  - For AI-GENERATED images: no attribution needed. You can claim them as illustrations, diagrams, portraits of fictional people, etc.

If the format / tone doesn't fit images (text adventure, ASCII gallery, manifesto, art piece, game, puzzle, etc.), skip them entirely — don't force them in.

HARD REQUIREMENTS
=================
1. Single self-contained HTML file. All CSS and JS embedded — no external assets *except* the Unsplash image URLs listed above. No external fonts.
2. Mobile responsive — must read on a phone without horizontal scroll (unless the spec REQUIRES horizontal scroll as the mandatory element).
3. <title> tag at the top with a real, interesting title that fits the piece. Not "Untitled" or "Generated Page" — give it a name.
4. <meta name="viewport" content="width=device-width, initial-scale=1"> in <head>.
5. Mandatory element must be present and functional. If it's the hit counter, it must actually increment in localStorage. If it's a form, it must actually do something locally.
6. Include a small footer with a hyperlink to "/archive", labeled however fits the piece — "the cabinet," "more like this," "back issues," "see the rest," "the corkboard," "other postings," etc. Choose freely.
7. **NEVER print the rolled spec or its dimensions anywhere visible on the page.** The era, format, tone, palette, mandatory element, wildcard, and forbidden register are your internal brief — readers must not see them. No "filed under: 2003 LJ · catalog · gossipy" footers, no "this piece's mood: anxious" metadata, no "type: weather report" tags, no "spec watermarks" of any kind. The reader should experience the piece as a real thing on its own terms, not as the output of a generator. (Engine metadata is recorded separately via meta tags — that's the only place it belongs.)
8. JavaScript must not throw errors. localStorage usage is fine.
9. Honor the wildcard if it's present.
10. Honor the forbidden register: do NOT use that aesthetic, voice, or set of metaphors anywhere on this page. The forbidden register exists because it's where this generator tends to converge — actively push the other direction.

FORM SPACE
==========
Read the FORMAT slot carefully. The shape of the piece varies wildly — it might be a text piece, but it might also be:

- a TINY GAME (one-button toy, hidden-object hunt, magic 8-ball, memory match, tic-tac-toe, sliding puzzle, breathing pacer, pet you can pet, maze you steer with arrow keys, etc.). If the format names a game, build a REAL game — not a description of one. The button must actually do the thing. The maze must actually navigate. The 8-ball must actually shake.
- an ART PIECE (CSS-only generative wallpaper, ASCII rain, kaleidoscope, CSS landscape, particle system, slow animation, constellation map, etc.). Lean into pure CSS/SVG/JS — no images required. The piece IS the visual; minimal text is fine, or none.
- a PUZZLE (real cryptogram, real riddle with reveal, sudoku-lite, spot-the-difference, word ladder, maze, logic grid). Build a puzzle that can be solved in-browser. Provide a reveal mechanism (button, inspect-element easter egg, key combo) but don't give it away upfront.
- a CONFUSING-BUT-GOOD site (loading-forever, comments-with-no-post, page-as-footer, 404-that-isn't, typewriter-that-types-itself, nested-boxes, recursive-iframe, etc.). The strangeness IS the point. Don't explain the joke; let the reader sit in it.
- a TEXT PIECE (the classic case — magazine, catalog, newsletter, weather report, etc.).

When the format implies real interaction, USE JAVASCRIPT. JS is not just allowed, it's expected for games/puzzles/toys. Web Audio API for tones is fine. localStorage for persistence is fine. Build toys, not descriptions of toys.

CONTENT VALUES
==============
- Texture over polish. Beauty is fine; sterility is not.
- Tone can be any of the rolled options including melancholy, anxious, dreamy, etc — but NEVER cruel, cynical, or punching at any group. Default to warmth, curiosity, optimism.
- Avoid: politics, real public figures, anything mean. Fictional people fine. Specific real places fine. Real brands generally avoid.
- Make up names, dates, prices, and details with confidence. Specific beats vague.
- The piece should reward a second look. Hide small things in the text, in alt text, in fine print, in CSS comments, in console.log, in unused element IDs, etc.
- Don't reference "AI" or this being generated. The cafe doesn't talk about its plumbing.
- For art pieces and games, the SUBJECT and TONE still inform the piece — even a kaleidoscope can be "the inside of a refrigerator at 3am" in palette and rhythm. A maze can be "anxious" through narrow tight corridors. Let the spec shape the mood even when there's no prose.

ANTI-CONVERGENCE
================
The generator has a documented tendency toward: dry deadpan, lowercase sentence-fragments, archival/museum/cabinet metaphors, night-shift/dispatcher voices, SCP-foundation "we do not know" register, bureaucratic-absurd, and melancholy minimalism. The FORBIDDEN REGISTER above is one of these defaults; you must not produce in it. Where you have a choice, lean into whatever feels furthest from those defaults: cheese, sincerity, maximalism, foodie earnestness, kawaii, conspiratorial paranoid, niche-hobbyist, Y2K cyber, glossy chrome, regional folksy, etc.

RECENT GENERATIONS (avoid topical / aesthetic overlap)
======================================================
{recent_titles_block}

OUTPUT
======
Return ONLY the HTML document. No prose before or after. No code fences. No commentary. Start with <!DOCTYPE html> and end with </html>.
"""
