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


def build_prompt(spec: dict[str, Any]) -> str:
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

HARD REQUIREMENTS
=================
1. Single self-contained HTML file. All CSS and JS embedded — no external assets, no <img> tags pointing to URLs (you can <img> a data: URL if you really want, but text/CSS only is preferred). No external fonts.
2. Mobile responsive — must read on a phone without horizontal scroll (unless the spec REQUIRES horizontal scroll as the mandatory element).
3. <title> tag at the top with a real, interesting title that fits the piece. Not "Untitled" or "Generated Page" — give it a name.
4. <meta name="viewport" content="width=device-width, initial-scale=1"> in <head>.
5. Mandatory element must be present and functional. If it's the hit counter, it must actually increment in localStorage. If it's a form, it must actually do something locally.
6. Include a small footer with a hyperlink to "/archive", labeled however fits the piece — "the cabinet," "more like this," "back issues," "see the rest," "the corkboard," "other postings," etc. Choose freely.
7. **NEVER print the rolled spec or its dimensions anywhere visible on the page.** The era, format, tone, palette, mandatory element, wildcard, and forbidden register are your internal brief — readers must not see them. No "filed under: 2003 LJ · catalog · gossipy" footers, no "this piece's mood: anxious" metadata, no "type: weather report" tags, no "spec watermarks" of any kind. The reader should experience the piece as a real thing on its own terms, not as the output of a generator. (Engine metadata is recorded separately via meta tags — that's the only place it belongs.)
8. JavaScript must not throw errors. localStorage usage is fine.
9. Honor the wildcard if it's present.
10. Honor the forbidden register: do NOT use that aesthetic, voice, or set of metaphors anywhere on this page. The forbidden register exists because it's where this generator tends to converge — actively push the other direction.

CONTENT VALUES
==============
- Texture over polish. Beauty is fine; sterility is not.
- Tone can be any of the rolled options including melancholy, anxious, dreamy, etc — but NEVER cruel, cynical, or punching at any group. Default to warmth, curiosity, optimism.
- Avoid: politics, real public figures, anything mean. Fictional people fine. Specific real places fine. Real brands generally avoid.
- Make up names, dates, prices, and details with confidence. Specific beats vague.
- The piece should reward a second look. Hide small things in the text, in alt text, in fine print, in CSS comments.
- Don't reference "AI" or this being generated. The cafe doesn't talk about its plumbing.

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
