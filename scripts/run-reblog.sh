#!/bin/bash
# Tumblr reblog cycle — cafe finds aesthetic-aligned posts and reblogs in
# staff voice. Conservative caps (3/run, 6/day).
#
# Cron entry (3x/day, staggered from drops):
#   30 10,14,20 * * * /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-reblog.sh >> /Users/stephenstanwood/logs/outbox-reblog.log 2>&1
set -eo pipefail

LOCK_DIR="/tmp/outbox-cafe-reblog.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 600 ]; then
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): reblog already running — skipping"
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
echo "===== $(date -Iseconds) reblog ====="
python3 scripts/reblog_tumblr.py
