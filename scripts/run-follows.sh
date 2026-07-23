#!/bin/bash
# Gentle autonomous follow loop — bsky only.
#
# Cron entry (every 3 hours, offset from the likes loop):
#   47 */3 * * * /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-follows.sh >> /Users/stephenstanwood/logs/outbox-follows.log 2>&1
set -eo pipefail

LOCK_DIR="/tmp/outbox-cafe-follows.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 600 ]; then
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): follows already running — skipping"
  exit 0
fi
trap "rmdir '$LOCK_DIR' 2>/dev/null" EXIT

REPO_DIR="$HOME/Projects/outbox-cafe"
PROXY_ENV="$HOME/Projects/mini-claude-proxy/.env"

if [ -f "$PROXY_ENV" ]; then set -a; . "$PROXY_ENV"; set +a; fi
if [ -f "$REPO_DIR/.env" ]; then set -a; . "$REPO_DIR/.env"; set +a; fi

export PATH="/opt/homebrew/bin:$HOME/.bun/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
export PYTHONUNBUFFERED=1

cd "$REPO_DIR"
echo
echo "===== $(date -Iseconds) follows ====="
python3 scripts/follow_loop.py
