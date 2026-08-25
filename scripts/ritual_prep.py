#!/usr/bin/env python3
"""Write the weekend's ritual text on a weekday, while the usage window is fresh.

The three weekly rituals — Pancake's Saturday sequence, Mr. Quiet's Sunday slip,
Doris's Sunday column — used to call Claude at post time. They post on the
weekend; the Claude weekly window resets Monday 12am PT and the daily gens
routinely spend it by midweek. So the rituals were asking for tokens at the one
moment of the week there were reliably none, and all three died five consecutive
weekends (2026-07-25 → 2026-08-23) logging `You've hit your weekly limit`.

This run fills `data/ritual_cache.json` with everything the coming weekend
needs. The rituals then take their text out of the drawer and post it with no
Claude call at all, so a spent window can't silence them. Full reasoning in
scripts/lib/ritual_cache.py.

Idempotent by ISO week: it only generates what's missing, so it's a near-free
no-op once the week is filled. `scripts/run-nightly.sh` therefore just calls it
every night at 2:30am ahead of the digest — no dedicated cron entry. Monday's
run does the work (2:30am Monday is ~2.5h past the weekly reset, the freshest
the window ever is) and the rest of the week costs a JSON read, so a failed
night self-heals on the next one. ISO weeks run Mon→Sun, so every run in a week
and the weekend rituals that drain it share one key.

    Manual fill:    python3 scripts/ritual_prep.py
    See status:     python3 scripts/ritual_prep.py --status
    Rebuild one:    python3 scripts/ritual_prep.py --only slip --force

Exit 0 when the drawer is full for the week, 1 when something is still missing
(the rituals' own live-generation fallback still covers that case).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib import ritual_cache
from lib.llm import run_claude, usage_limited

# Same backoff the rituals use — spread tries over ~5 min to ride out a blip.
RETRY_SLEEPS = (45, 90, 180)


def _specs() -> "dict[str, tuple[str, str, int, object]]":
    """item -> (prompt, model, timeout, extractor).

    Imported from the ritual scripts themselves rather than copied, so the
    prompt and the validation can never drift from what the ritual would have
    used generating live.
    """
    import doris_muffin
    import mr_quiet_slip
    import pancake_sequence

    specs: "dict[str, tuple[str, str, int, object]]" = {
        "slip": (
            mr_quiet_slip.APHORISM_PROMPT, "opus", 120,
            mr_quiet_slip.extract_aphorism,
        ),
        "doris": (
            doris_muffin.COLUMN_PROMPT, "opus", 180,
            doris_muffin.extract_column,
        ),
    }
    for act in (1, 2, 3):
        specs[f"pancake_{act}"] = (
            pancake_sequence.ACT_PROMPTS[act], "opus", 60,
            (lambda a: lambda out: pancake_sequence.extract_act(a, out))(act),
        )
    return specs


def _generate(item: str, prompt: str, model: str, timeout: int, extract, max_tries: int = 3) -> str | None:
    for attempt in range(max_tries):
        if attempt > 0:
            hint = usage_limited(model)
            if hint:
                print(f"[prep] {item}: usage limit (resets {hint}) — stopping", file=sys.stderr)
                return None
            delay = RETRY_SLEEPS[min(attempt - 1, len(RETRY_SLEEPS) - 1)]
            print(f"[prep] {item}: retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
        res = run_claude(prompt, model=model, timeout=timeout)
        if not res.ok:
            print(res.log_line(f"prep/{item}"), file=sys.stderr)
            continue
        text = extract(res.text)
        if text:
            return text
        print(f"[prep] {item}: output failed validation (try {attempt+1}): {res.text[:160]!r}", file=sys.stderr)
    return None


def _print_status() -> int:
    data = ritual_cache.load()
    week = ritual_cache.week_key()
    print(f"[prep] week {week} (cache holds {data.get('week')})")
    for item in ritual_cache.ITEMS:
        entry = (data.get("items") or {}).get(item) if data.get("week") == week else None
        if not entry:
            print(f"  {item:<12} — empty")
        elif entry.get("consumed_at"):
            print(f"  {item:<12} — consumed {entry['consumed_at'][:19]}")
        else:
            print(f"  {item:<12} ✓ ready: {entry['text'][:70]!r}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--status" in args:
        return _print_status()

    force = "--force" in args
    only = None
    if "--only" in args:
        i = args.index("--only")
        if i + 1 < len(args):
            only = args[i + 1]
            if only not in ritual_cache.ITEMS:
                print(f"[prep] unknown item {only!r}; known: {', '.join(ritual_cache.ITEMS)}", file=sys.stderr)
                return 2

    targets = (only,) if only else ritual_cache.ITEMS
    todo = list(targets) if force else ritual_cache.missing(targets)

    week = ritual_cache.week_key()
    if not todo:
        print(f"[prep] {week}: ritual drawer already full — nothing to generate")
        return 0

    print(f"[prep] {week}: generating {len(todo)} item(s): {', '.join(todo)}")
    specs = _specs()
    made, failed = [], []
    for item in todo:
        prompt, model, timeout, extract = specs[item]
        # A spent window stays spent — don't walk the rest of the list into it.
        hint = usage_limited(model)
        if hint:
            print(f"[prep] {item}: skipped — usage limit already seen (resets {hint})", file=sys.stderr)
            failed.append(item)
            continue
        text = _generate(item, prompt, model, timeout, extract)
        if text:
            ritual_cache.store(item, text)
            made.append(item)
            print(f"[prep] ✓ {item}: {text[:90]!r}")
        else:
            failed.append(item)

    still_missing = ritual_cache.missing(ritual_cache.ITEMS)
    print(f"[prep] done. generated={len(made)} failed={len(failed)} still_missing={still_missing or 'none'}")
    return 1 if still_missing else 0


if __name__ == "__main__":
    sys.exit(main())
