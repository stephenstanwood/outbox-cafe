#!/bin/bash
# Wrapper for the Bluesky engagement loop on the Mac Mini.
# Sources the same env files the gen wrapper uses, then runs engage_bsky.py.
#
# Cron entry (every 15 min, 24/7):
#   */15 * * * * /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-engage.sh >> /Users/stephenstanwood/logs/outbox-engage.log 2>&1
set -eo pipefail

LOCK_DIR="/tmp/outbox-cafe-engage.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 600 ]; then
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): engage already running — skipping"
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

cd "$REPO_DIR"
echo
echo "===== $(date -Iseconds) engage ====="
python3 scripts/engage_bsky.py
