#!/bin/bash
# Fill the weekend's ritual drawer while the Claude weekly window is still fresh.
#
# The rituals post Sat/Sun; the weekly usage window resets Monday 12am PT and the
# daily gens routinely spend it by midweek. Generating on the day meant all three
# rituals died five weekends running. This writes the text on a weekday instead.
#
# SCHEDULING: there is no cron entry for this script, and it doesn't need one —
# scripts/run-nightly.sh calls `ritual_prep.py` directly every night at 2:30am,
# before the digest. That slot is strictly better than a dedicated one: 2:30am
# Monday is ~2.5h after the weekly window resets, the freshest it ever is. The
# run is a no-op once the drawer is full for the week, so nightly repetition is
# free and a failed night self-heals on the next.
#
# This wrapper exists for manual runs (it sources the same env the cron does):
#   ./scripts/run-ritual-prep.sh
# and as a drop-in if the prep ever wants its own schedule again, e.g.
#   23 5 * * 1-5 /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-ritual-prep.sh >> /Users/stephenstanwood/logs/outbox-ritual-prep.log 2>&1
set -eo pipefail

# Lock staleness 20 min — five generations with backoff can legitimately run long.
LOCK_DIR="/tmp/outbox-cafe-ritual-prep.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 1200 ]; then
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): ritual-prep already running — skipping"
  exit 0
fi
trap "rmdir '$LOCK_DIR' 2>/dev/null" EXIT

REPO_DIR="$HOME/Projects/outbox-cafe"
PROXY_ENV="$HOME/Projects/mini-claude-proxy/.env"

if [ -f "$PROXY_ENV" ]; then
  set -a
  . "$PROXY_ENV"
  set +a
fi
if [ -f "$REPO_DIR/.env" ]; then
  set -a
  . "$REPO_DIR/.env"
  set +a
fi

export PATH="/opt/homebrew/bin:$HOME/.bun/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
export PYTHONUNBUFFERED=1

cd "$REPO_DIR"
echo
echo "===== $(date -Iseconds) ritual-prep ====="

# Exit 1 just means the drawer isn't full yet — a later weekday run retries, and
# the rituals still fall back to live generation. Never fail the cron over it.
python3 scripts/ritual_prep.py || true

# The drawer is per-Mini runtime state (gitignored) — nothing to commit.
