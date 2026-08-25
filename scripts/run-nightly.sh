#!/bin/bash
# Wrapper for outbox.cafe nightly digest on the Mac Mini.
# Runs at 03:00 PT so Stephen sees the summary when he wakes at 04:00.
#
# Cron entry:
#   0 3 * * * /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-nightly.sh >> /Users/stephenstanwood/logs/outbox-nightly.log 2>&1
set -eo pipefail

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
echo "===== $(date -Iseconds) nightly ====="

# Top up the weekend ritual drawer BEFORE the digest runs.
#
# The weekend rituals (Pancake Sat, slip + column Sun) used to call Claude at
# post time and all three died five weekends running on `You've hit your weekly
# limit` — the weekly window resets Monday 12am PT and the daily gens spend it
# well before Saturday. Pre-generating the text on a weeknight sidesteps that
# entirely; 2:30am Monday is the freshest the window ever is.
#
# Runs nightly rather than once: it's a no-op (one JSON read) whenever the
# drawer is already full for the ISO week, so a failed night self-heals on the
# next one, and even a Saturday 2:30am run still beats Pancake's 7am act 1.
# Ordered before the digest so the digest's drawer warning reports post-prep
# state instead of raising a gap this run is about to fill.
# Never fail the nightly over it — exit 1 only means "still short a few items",
# and the rituals keep their live-generation fallback either way.
python3 scripts/ritual_prep.py || true

python3 scripts/nightly_digest.py
