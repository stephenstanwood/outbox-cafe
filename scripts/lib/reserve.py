"""Reserve drawer — a per-Mini buffer of valid runner-up gen candidates.

Every healthy multi-candidate gen produces N valid HTML pages and publishes ONE
(the judge's pick); the other valid candidates are thrown away. This stashes them,
for free, so that during a Claude cap or a total-exhaustion window — when the gen
would otherwise publish a generic counter-card — we can publish a real (if
not-the-first-choice) page instead.

Why this shape:
  * Fill is free. The candidate already exists; stashing it costs no extra Claude
    call, so it never adds load during the very weeks we keep hitting weekly caps.
  * Drain is free. The page is pre-built, so draining works even while fully
    capped — exactly when it's needed.
  * Per-Mini, gitignored, ephemeral. Drained pages become committed archive HTML
    at publish time; the buffer itself never leaves the Mini (like runs.jsonl and
    the engage/like/follow state files).

Fill rate matters as much as capacity. Observed cap windows run long — 9 slots
(2026-07-24→26) and 17 slots (2026-07-29→08-02, which drained the then-2-entry
buffer dry after 2 slots and served 15 counter-cards). At 4 gens/day, stashing one
runner-up per gen needs 4+ days of uninterrupted health to cover a 17-slot window;
stashing every valid runner-up (2 per healthy gen) halves that to ~2 days, using
pages that were being discarded anyway. Fill stays free either way.

Stored HTML is RAW candidate output (pre-injection): a drained entry flows through
the same inject_spec_meta/og/nav/reload pipeline as a fresh gen, under its own new
archive filename, using the stashed spec — so the published page is internally
consistent. Entries carry their spec so the cabinet dims, history, and poster all
match the page that actually ships.

Age bound: a candidate may embed external image URLs (fal.ai / unsplash) the model
chose to reference. Those are the same persistence assumption every cafe page
already makes, but a long-buffered entry's images are older at publish time, so
drain only ever serves entries within MAX_AGE_DAYS and deletes the stale ones.
Carte-blanche and image-less candidates carry no such risk.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.io import atomic_write_json

RESERVE_DIR = Path(__file__).resolve().parents[2] / "data" / "reserve"

# Buffer size. The worst observed cap window is 17 consecutive counter-cards
# (2026-07-29→08-02); 32 covers that with margin and, at two stashes per healthy
# gen, still fills in ~4 days while staying inside MAX_AGE_DAYS. An entry is
# ~35KB of HTML, so a full buffer is ~1MB on the Mini.
DEFAULT_CAP = 32

# Only ever serve entries this fresh (see module docstring on embedded images).
MAX_AGE_DAYS = 21

# Runner-ups from ONE gen share the same rolled subject, era, and palette, so
# draining them back to back would show visitors two near-identical drops four
# hours apart. Entry filenames are an ORDERING key only (the age bound reads the
# payload `ts`), so each additional sibling is filed this far forward: later gens'
# runner-ups sort in between, and the drain order stays varied. 12h = 3 gen slots.
SIBLING_SPREAD_HOURS = 12


def _entry_files() -> list[Path]:
    """Reserve entry files in drain order (sort by filename, which is the
    ts-derived ordering key — see SIBLING_SPREAD_HOURS)."""
    if not RESERVE_DIR.exists():
        return []
    return sorted(RESERVE_DIR.glob("*.json"))


def count() -> int:
    """How many entries are currently buffered (for logging/observability)."""
    return len(_entry_files())


def stash(html: str, spec: dict, *, cap: int = DEFAULT_CAP) -> bool:
    """Buffer one valid runner-up candidate. Best-effort — any failure returns
    False and is non-fatal (the happy path must never break to save a spare)."""
    return stash_all([html], spec, cap=cap) == 1


def stash_all(candidates: "list[str]", spec: dict, *, cap: int = DEFAULT_CAP) -> int:
    """Buffer every valid runner-up from one gen; returns how many were stored.

    Siblings are filed SIBLING_SPREAD_HOURS apart in drain order so a cap window
    never serves two near-identical pages back to back. Best-effort — a failure on
    any one entry is non-fatal (the happy path must never break to save a spare)."""
    stored = 0
    now = datetime.now(timezone.utc)
    # Strip any fallback bookkeeping so a drained spec reads as a clean roll.
    clean_spec = {k: v for k, v in spec.items()
                  if k not in ("limit_fallback", "fallback_reason", "file")}
    for i, html in enumerate(candidates):
        try:
            # Filename is the ordering key: real time for the first sibling, then
            # spread forward. The payload `ts` stays the true stash time so the
            # age bound in drain() measures actual freshness, not the sort offset.
            order_key = now + timedelta(hours=i * SIBLING_SPREAD_HOURS)
            path = RESERVE_DIR / f"{order_key.strftime('%Y%m%dT%H%M%S%f')}.json"
            atomic_write_json(path, {"html": html, "spec": clean_spec,
                                     "ts": now.isoformat()}, indent=None)
            stored += 1
        except Exception as e:  # noqa: BLE001 — a spare must never break a gen
            print(f"  reserve stash failed (non-fatal): {e}", file=sys.stderr)
    if stored:
        try:
            _prune(cap)
        except Exception as e:  # noqa: BLE001
            print(f"  reserve prune failed (non-fatal): {e}", file=sys.stderr)
    return stored


def _prune(cap: int) -> None:
    """Keep the buffer at or under `cap` by deleting the oldest entries."""
    files = _entry_files()
    for f in files[:max(0, len(files) - cap)]:
        try:
            f.unlink()
        except OSError:
            pass


def drain(*, max_age_days: int = MAX_AGE_DAYS) -> "tuple[str, dict] | None":
    """Pop and return the oldest fresh entry as (html, spec), deleting its file.

    Stale (over-age) or corrupt entries are deleted and skipped. Returns None if
    the buffer holds nothing usable. Never raises — a drain failure just leaves
    the caller with its counter-card."""
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        for f in _entry_files():
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                _unlink(f)
                continue
            ts_raw = data.get("ts", "")
            try:
                age_ok = datetime.fromisoformat(ts_raw).timestamp() >= cutoff
            except (TypeError, ValueError):
                age_ok = False
            html, spec = data.get("html"), data.get("spec")
            if not age_ok or not isinstance(html, str) or not isinstance(spec, dict):
                _unlink(f)  # stale / malformed — retire it and try the next
                continue
            _unlink(f)  # claim it before returning so it's never served twice
            return html, spec
        return None
    except Exception as e:  # noqa: BLE001 — a drain miss must never crash a gen
        print(f"  reserve drain failed (non-fatal): {e}", file=sys.stderr)
        return None


def _unlink(f: Path) -> None:
    try:
        os.remove(f)
    except OSError:
        pass
