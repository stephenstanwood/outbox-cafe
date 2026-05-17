"""Backfill <meta name="outbox-spec-*"> tags into existing archive HTML files.

Matches archive filenames (Pacific time) to history.jsonl entries (UTC ISO timestamps)
within a generous time window, then injects the meta block.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate import inject_spec_meta, ARCHIVE_DIR, ROOT  # noqa: E402

HISTORY_PATH = ROOT / "data" / "history.jsonl"
PT = ZoneInfo("America/Los_Angeles")


def load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    return [json.loads(l) for l in HISTORY_PATH.read_text().splitlines() if l.strip()]


def filename_to_utc(stem: str) -> datetime | None:
    """e.g. '2026-05-17T05-25' (Pacific) -> UTC datetime."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})", stem)
    if not m:
        return None
    date_part, hh, mm = m.groups()
    dt_pt = datetime.fromisoformat(f"{date_part}T{hh}:{mm}:00").replace(tzinfo=PT)
    return dt_pt.astimezone(ZoneInfo("UTC"))


def closest_history_match(file_utc: datetime, history: list[dict]) -> dict | None:
    """Find the latest history entry whose generated_at <= file_utc, within 15 min.

    History timestamps record when the spec was *rolled* — Claude generation can take
    30-90+ seconds, so the file is written 1-8 minutes after the history entry.
    """
    best = None
    best_delta = timedelta(minutes=15)
    for h in history:
        try:
            h_dt = datetime.fromisoformat(h["generated_at"])
        except Exception:
            continue
        if h_dt > file_utc:
            continue  # spec rolled AFTER file was written — wrong entry
        delta = file_utc - h_dt
        if delta < best_delta:
            best = h
            best_delta = delta
    return best


def strip_outbox_meta(html: str) -> str:
    """Remove all <meta name="outbox-spec-*" ...> tags so backfill can overwrite."""
    import re
    return re.sub(
        r'\s*<meta\s+name="outbox-spec-[a-z]+"\s+content="[^"]*"\s*/?>',
        "",
        html,
        flags=re.IGNORECASE,
    )


def main() -> int:
    history = load_history()
    if not history:
        print("no history.jsonl — nothing to backfill")
        return 0

    force = "--force" in sys.argv
    changed = 0
    skipped = 0
    for f in sorted(ARCHIVE_DIR.glob("*.html")):
        if f.name == "index.html":
            continue
        html = f.read_text(errors="ignore")
        if 'outbox-spec-era' in html:
            if not force:
                skipped += 1
                continue
            html = strip_outbox_meta(html)
        file_utc = filename_to_utc(f.stem)
        if not file_utc:
            print(f"  skip (unparseable filename): {f.name}")
            continue
        match = closest_history_match(file_utc, history)
        if not match:
            print(f"  skip (no history match): {f.name}")
            continue
        new_html = inject_spec_meta(html, match)
        f.write_text(new_html)
        changed += 1
        era = match.get("era", {}).get("value", "?")[:40] if isinstance(match.get("era"), dict) else "?"
        fmt = match.get("format", {}).get("value", "?")[:40] if isinstance(match.get("format"), dict) else "?"
        print(f"  ✓ {f.name} ← {era} · {fmt}")

    print(f"\nbackfilled {changed} files, skipped {skipped} (already had meta)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
