"""Canon — the cafe's small recurring universe, and the rules that keep it living.

`data/canon.json` holds the elements a gen may be offered as an optional
background easter egg (see prompt.py). Three separate places need to agree on
what canon *is*, and until now only one of them knew the rules:

  * prompt.py    — which element to offer this gen
  * canon_scout  — whether a new nomination is worth keeping, and whether an
                   old one ever caught on
  * /regulars/   — which elements actually turned up in a posting

This module is the shared answer. It owns the roster (active vs retired), the
sighting-matching rules, and the offer log that records how many real chances
an element has had.

WHY RETIREMENT EXISTS
The scout welcomes at most one element a night and stops entirely at the cap.
Canon reached 40 on 2026-08-20 and the scout has been a no-op every night
since — the universe could never grow again. Meanwhile a name that one page
invented and no later page ever picked up was holding a seat forever. So the
roster turns over: an element that has had its run and never once been taken up
quietly stops being on the list, and the seat goes to somebody new. Nothing is
deleted — retired entries keep their record under `retired`.

Turnover cannot run away. Retirement only fires when the roster is FULL and
takes at most one element a night, and the scout only promotes when there is
room, so the roster hovers at cap and loses somebody only when somebody new is
ready to take the seat.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CANON_PATH = ROOT / "data" / "canon.json"
OFFERS_PATH = ROOT / "data" / "canon_offers.json"

# Size of the active roster. Small on purpose: every element added dilutes the
# share of gens the others get offered to, and an element nobody is ever
# offered can't become a regular.
CAP = 40

# How many times an element must have been genuinely offered to a gen before
# "it never caught on" is a fair verdict. Below this it simply hasn't had its
# chance yet, however long it has been sitting in the file.
PROBATION_OFFERS = 4

# The backstop, and on 2026-08-28 the only clause that can actually fire.
# Measured exposure at that point: a uniform pick over 40 names at an 18% offer
# rate gave each element ~0.018 offers a day, so NOT ONE scout-added element
# had yet accumulated a single expected offer — 26 of 40 had never been picked
# up by any later page, and the reason was dilution, not quality. An offers-only
# probation would therefore never have fired and the roster would have stayed
# frozen for months. So: an element that has sat through this many postings
# without once turning up is not functioning as canon, whether it was offered
# and skipped or never offered at all, and the seat is worth more to somebody
# new. ~150 pages is a bit over five weeks at four gens a day, and the offer
# weighting in prompt.py means every future newcomer gets real chances well
# inside that window.
PROBATION_PAGES = 150


# ---------------------------------------------------------------- roster ----

def load() -> dict:
    try:
        data = json.loads(CANON_PATH.read_text())
    except Exception:
        return {"elements": []}
    if not isinstance(data, dict):
        return {"elements": []}
    return data


def save(canon: dict) -> None:
    CANON_PATH.write_text(json.dumps(canon, indent=2, ensure_ascii=False) + "\n")


def active(canon: dict | None = None) -> list[dict]:
    """Elements currently on the roster — the ones a gen may be offered."""
    c = canon if canon is not None else load()
    els = c.get("elements")
    return [e for e in els if isinstance(e, dict)] if isinstance(els, list) else []


def retired(canon: dict | None = None) -> list[dict]:
    c = canon if canon is not None else load()
    els = c.get("retired")
    return [e for e in els if isinstance(e, dict)] if isinstance(els, list) else []


def joined(el: dict) -> str:
    """The date an element joined the roster. The founding cast predates the
    scout and carries no `added` — they are never on probation."""
    return str(el.get("added") or "")


def is_founding(el: dict) -> bool:
    return not joined(el)


# -------------------------------------------------------------- sightings ---

def sighting_terms(el: dict) -> list[str]:
    """The exact phrasings that count as a sighting of this element.

    `aka`, when set, REPLACES the bare name — that's how an entry whose name is
    an ordinary first name ("Frederick" -> "cousin Frederick") stays honest.
    A short one-word alphabetic term is claimed by too much ordinary prose to
    count on its own: "Pepper" would collect salt-and-pepper and Dr. Pepper,
    "Eugene" would collect Eugene, Oregon.
    """
    name = str(el.get("name") or "").strip()
    terms = [str(a).strip() for a in (el.get("aka") or [])] or ([name] if name else [])
    return [t for t in terms if t and (" " in t or not t.isalpha() or len(t) >= 7)]


def is_verifiable(name: str) -> bool:
    """Could a sighting of this name ever be confirmed on a page?

    A bare short first name ("Wilf", "Ferd", "Col") reads as nothing when a gen
    drops it into a page and can never be matched, so it holds a roster seat it
    can never earn. Canon wants the name as it would actually appear.
    """
    return bool(sighting_terms({"name": name}))


def sighting_patterns(el: dict) -> list[re.Pattern]:
    return [
        re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        for t in sighting_terms(el)
    ]


def _page_date(stem: str) -> str:
    return stem[:10] if re.match(r"\d{4}-\d{2}-\d{2}", stem) else ""


def pages_since(el: dict, pages: list[tuple[str, str]]) -> int:
    """How many pages have been published since this element joined — i.e. how
    many chances the roster has had to bring it up."""
    since = joined(el)
    if not since:
        return len(pages)
    return sum(1 for fn, _ in pages if _page_date(fn) > since)


def count_recurrences(el: dict, pages: list[tuple[str, str]]) -> int:
    """How many pages published AFTER this element joined mention it.

    The birth page is deliberately excluded: a scout-promoted element is
    promoted *from* a page, so its first sighting is guaranteed and proves
    nothing. A recurrence is a later gen choosing to pick it up.

    `pages` is [(filename, visible_text), ...].
    """
    pats = sighting_patterns(el)
    if not pats:
        return 0
    since = joined(el)
    n = 0
    for fn, text in pages:
        if since and _page_date(fn) <= since:
            continue
        if any(p.search(text) for p in pats):
            n += 1
    return n


# ------------------------------------------------------------- offer log ----

def load_offers() -> dict:
    try:
        data = json.loads(OFFERS_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def offers_for(name: str, offers: dict | None = None) -> int:
    o = offers if offers is not None else load_offers()
    rec = o.get(name)
    if isinstance(rec, dict):
        try:
            return int(rec.get("offers") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def record_offer(name: str, when: datetime | None = None) -> None:
    """Note that `name` was offered to a gen. Best-effort: this runs on the
    gen-critical path, so it must never raise."""
    try:
        offers = load_offers()
        rec = offers.get(name)
        rec = rec if isinstance(rec, dict) else {}
        rec["offers"] = int(rec.get("offers") or 0) + 1
        rec["last"] = (when or datetime.now()).isoformat(timespec="seconds")
        offers[name] = rec
        OFFERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = OFFERS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(offers, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(OFFERS_PATH)
    except Exception:
        pass
