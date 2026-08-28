"""Canon scout — the universe grows itself.

Nightly pass (called from nightly_digest, after reflect): read yesterday's
gens, ask Claude whether any of them invented a proper noun worth keeping as
recurring cafe-universe background (see data/canon.json), and append AT MOST
one per night. The prompt hook in prompt.py then starts offering it to future
gens as an optional easter egg.

Conservative by design: usually the answer is NONE, the canon is capped, and
names must be reusable across eras (a 1923 telegram office is era-locked; a
cousin who claims things is forever).

The pass also keeps the roster LIVING. Canon filled up on 2026-08-20 and the
scout went permanently silent — no new regular could ever join again, while
names that one page invented and no later page ever picked up held their seats
forever. So each night, before scouting, the roster is refreshed against what
actually turned up in the archive, and at most one element that has had its
share of chances and never once been taken up retires to make room. See
lib/canon.py for the rules.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lib import canon as canon_lib
from lib.llm import run_claude, strip_fences

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
CANON_PATH = ROOT / "data" / "canon.json"
PT = ZoneInfo("America/Los_Angeles")

CANON_CAP = canon_lib.CAP
EXCERPT_CHARS = 1800

SCOUT_PROMPT = """You curate the small recurring background universe of outbox.cafe — fictional figures, places, and objects that quietly reappear across otherwise-unrelated generated pages (a missing cat on a flyer, somebody's cousin, a takeout place). Existing canon:

{existing}

Below are text excerpts from yesterday's generated pages. Decide whether ANY of them invented something canon-worthy:
- a memorable proper noun (person, creature, place, object, institution)
- reusable across wildly different eras and formats (not locked to one page's premise)
- background-sized: charming as a passing mention, too small to be a subject
- NOT already in canon (no near-duplicates either)
- NOT a real person, brand, or place

Give the name EXACTLY as it would be printed on a page — "Wilf Deaver", not "Wilf". A bare short first name reads as nothing when a later page drops it in, and cannot be told apart from ordinary prose.

Most days the right answer is NONE — canon grows slowly or it means nothing.

YESTERDAY'S PAGES
{excerpts}

OUTPUT — exactly one of:
NONE
{{"name": "...", "hint": "one reusable sentence: who/what it is and the one detail that makes it charming, written so any future page in any era could drop it in"}}

The hint must be under 200 characters. Output the bare word NONE or the bare JSON object. Nothing else."""


def _strip_html(html: str) -> str:
    s = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _yesterdays_gens() -> list[tuple[str, str]]:
    cutoff = datetime.now(tz=PT) - timedelta(hours=26)
    out: list[tuple[str, str]] = []
    for f in sorted(ARCHIVE_DIR.glob("*.html"), reverse=True):
        if f.name == "index.html":
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})", f.stem)
        if not m:
            continue
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M").replace(tzinfo=PT)
        except ValueError:
            continue
        if dt < cutoff:
            break
        out.append((f.name, _strip_html(f.read_text(errors="ignore"))[:EXCERPT_CHARS]))
    return out


def _archive_pages() -> list[tuple[str, str]]:
    """Every archived page as (filename, visible text). Used to check which
    canon elements have actually been picked up by a later gen."""
    out: list[tuple[str, str]] = []
    for f in sorted(ARCHIVE_DIR.glob("*.html")):
        if f.name == "index.html":
            continue
        try:
            out.append((f.name, _strip_html(f.read_text(errors="ignore"))))
        except Exception:
            continue
    return out


def refresh_and_retire() -> str | None:
    """Refresh each element's recurrence count, then retire at most one.

    An element retires only when ALL of these hold:
      * it was promoted by the scout (the founding cast is never on probation)
      * the roster is full, so the seat is actually worth something
      * no page published after it joined has ever mentioned it
      * it has had its run: either genuinely OFFERED to PROBATION_OFFERS gens,
        or simply sat through PROBATION_PAGES postings without once surfacing

    Retired entries are moved, not deleted: their record stays under `retired`.
    Returns the retired name, or None.
    """
    canon = canon_lib.load()
    elements = canon_lib.active(canon)
    if not elements:
        return None

    pages = _archive_pages()
    offers = canon_lib.load_offers()

    # Refresh the honest record first — `seen` is what the offer weighting in
    # prompt.py reads, and what makes a retirement auditable after the fact.
    changed = False
    for el in elements:
        n = canon_lib.count_recurrences(el, pages)
        if el.get("seen") != n:
            el["seen"] = n
            changed = True

    victim = None
    if len(elements) >= CANON_CAP:
        eligible = [
            el for el in elements
            if not canon_lib.is_founding(el)
            and el.get("seen") == 0
            and (canon_lib.offers_for(el.get("name") or "", offers) >= canon_lib.PROBATION_OFFERS
                 or canon_lib.pages_since(el, pages) >= canon_lib.PROBATION_PAGES)
        ]
        if eligible:
            # The one that has had the most chances and taken none of them;
            # longest-sitting first on a tie.
            eligible.sort(
                key=lambda el: (-canon_lib.offers_for(el.get("name") or "", offers),
                                canon_lib.joined(el))
            )
            victim = eligible[0]

    if victim is not None:
        elements = [el for el in elements if el is not victim]
        victim["retired"] = datetime.now(tz=PT).date().isoformat()
        victim["offers"] = canon_lib.offers_for(victim.get("name") or "", offers)
        victim["waited"] = canon_lib.pages_since(victim, pages)
        canon["retired"] = canon_lib.retired(canon) + [victim]
        changed = True

    if changed:
        canon["elements"] = elements
        canon_lib.save(canon)

    if victim is not None:
        name = str(victim.get("name") or "")
        print(f"[canon] {name!r} never turned up in {victim['waited']} postings "
              f"({victim['offers']} offers) — retired")
        return name
    return None


def run() -> str | None:
    """Scout once. Returns the new canon name if one was added, else None.

    Scouting only — callers run `refresh_and_retire()` FIRST so a seat freed
    tonight is available to tonight's nomination (see nightly_digest and
    __main__). Keeping the two separate means neither can be run twice by
    accident, which for retirement would cost an extra name a night.
    """
    try:
        canon = json.loads(CANON_PATH.read_text())
    except Exception:
        canon = {"elements": []}
    elements = canon.get("elements") or []
    if len(elements) >= CANON_CAP:
        print(f"[canon] at cap ({CANON_CAP}) — nobody is due to retire yet")
        return None

    gens = _yesterdays_gens()
    if not gens:
        print("[canon] no gens in window")
        return None

    existing_names = [e.get("name", "") for e in elements]
    prompt = SCOUT_PROMPT.format(
        existing="\n".join(f"- {n}" for n in existing_names) or "(empty)",
        excerpts="\n\n".join(f"=== {name} ===\n{text}" for name, text in gens),
    )
    res = run_claude(prompt, model="opus", timeout=180)
    if not res.ok:
        print(res.log_line("canon"), file=sys.stderr)
        return None

    out = strip_fences(res.text)
    if not out or out.upper().startswith("NONE"):
        print("[canon] scout says NONE")
        return None
    try:
        start, end = out.find("{"), out.rfind("}")
        data = json.loads(out[start:end + 1])
        name = str(data["name"]).strip()
        hint = str(data["hint"]).strip()
    except Exception:
        print(f"[canon] unparseable scout output: {out[:160]!r}", file=sys.stderr)
        return None
    if not name or not hint or len(name) > 60 or len(hint) > 220:
        print(f"[canon] rejected malformed nomination {name!r}", file=sys.stderr)
        return None
    # The scout occasionally returns a slug ("ferncliff-gravel-pit") instead of
    # the name as it would actually appear on a page. A slug reads wrong when
    # the prompt offers it to a gen, and it never matches page text, so /regulars/
    # can't find its sightings. Reject the shape; a real name gets re-nominated.
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", name):
        print(f"[canon] rejected slug-shaped name {name!r}", file=sys.stderr)
        return None
    # A bare short first name ("Wilf", "Ferd", "Col") can never be told apart
    # from ordinary prose, so /regulars/ can never confirm a sighting and the
    # entry holds a seat it can never earn. Same class as the slug shape: the
    # name is wrong, not the nomination — a fuller phrasing gets re-nominated.
    if not canon_lib.is_verifiable(name):
        print(f"[canon] rejected unverifiable bare name {name!r}", file=sys.stderr)
        return None
    lower_existing = {n.lower() for n in existing_names}
    if name.lower() in lower_existing or any(name.lower() in n or n in name.lower() for n in lower_existing):
        print(f"[canon] {name!r} duplicates existing canon — skipped")
        return None

    elements.append({"name": name, "hint": hint, "added": datetime.now(tz=PT).date().isoformat(), "by": "scout"})
    canon["elements"] = elements
    CANON_PATH.write_text(json.dumps(canon, indent=2, ensure_ascii=False) + "\n")
    print(f"[canon] welcomed {name!r} to the universe")
    return name


if __name__ == "__main__":
    refresh_and_retire()
    run()
