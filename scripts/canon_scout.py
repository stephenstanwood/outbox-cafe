"""Canon scout — the universe grows itself.

Nightly pass (called from nightly_digest, after reflect): read yesterday's
gens, ask Claude whether any of them invented a proper noun worth keeping as
recurring cafe-universe background (see data/canon.json), and append AT MOST
one per night. The prompt hook in prompt.py then starts offering it to future
gens as an optional easter egg.

Conservative by design: usually the answer is NONE, the canon is capped, and
names must be reusable across eras (a 1923 telegram office is era-locked; a
cousin who claims things is forever).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lib.llm import claude_cmd, strip_fences

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
CANON_PATH = ROOT / "data" / "canon.json"
PT = ZoneInfo("America/Los_Angeles")

CANON_CAP = 40
EXCERPT_CHARS = 1800

SCOUT_PROMPT = """You curate the small recurring background universe of outbox.cafe — fictional figures, places, and objects that quietly reappear across otherwise-unrelated generated pages (a missing cat on a flyer, somebody's cousin, a takeout place). Existing canon:

{existing}

Below are text excerpts from yesterday's generated pages. Decide whether ANY of them invented something canon-worthy:
- a memorable proper noun (person, creature, place, object, institution)
- reusable across wildly different eras and formats (not locked to one page's premise)
- background-sized: charming as a passing mention, too small to be a subject
- NOT already in canon (no near-duplicates either)
- NOT a real person, brand, or place

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


def run() -> str | None:
    """Scout once. Returns the new canon name if one was added, else None."""
    try:
        canon = json.loads(CANON_PATH.read_text())
    except Exception:
        canon = {"elements": []}
    elements = canon.get("elements") or []
    if len(elements) >= CANON_CAP:
        print(f"[canon] at cap ({CANON_CAP}) — not scouting")
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
    try:
        result = subprocess.run(
            claude_cmd("opus"), input=prompt,
            capture_output=True, text=True, timeout=180,
        )
    except Exception as e:
        print(f"[canon] claude failed: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"[canon] claude exit {result.returncode}", file=sys.stderr)
        return None

    out = strip_fences(result.stdout)
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
    run()
