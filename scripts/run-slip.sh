#!/bin/bash
# Mr. Quiet's Sunday Slip — a weekly fortune-cookie aphorism on a paper
# slip image, posted to bsky + tumblr.
#
# Cron entry (Sun 9:06am PT, weekly — off the :00 grid the engage loop fires on):
#   6 9 * * 0 /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-slip.sh >> /Users/stephenstanwood/logs/outbox-slip.log 2>&1
set -eo pipefail

# Lock staleness 20 min — generation retries back off across ~6 min, so a
# legitimate run can exceed the old 10-min window.
LOCK_DIR="/tmp/outbox-cafe-slip.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 1200 ]; then
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): slip already running — skipping"
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
echo "===== $(date -Iseconds) slip ====="
python3 scripts/mr_quiet_slip.py

# Commit + push the archived slip image + the rebuilt /slips/ page.
if [ -n "$(git status --porcelain archive/slips slips 2>/dev/null)" ]; then
  git add archive/slips/ slips/
  git -c user.email="outbox@outbox.cafe" -c user.name="outbox.cafe" \
      commit -m "slip: $(date +%Y-%m-%d) — Mr. Quiet" || true
  git push || true
fi
