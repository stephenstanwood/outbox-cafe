#!/bin/bash
# Pancake's Saturday sequence — three-act progression from keyboard mash
# to a clean poetic line. Script auto-detects act based on time of day.
#
# Cron entries (Saturday only):
#   0 7 * * 6  .../run-pancake.sh >> ~/logs/outbox-pancake.log 2>&1   # Act 1
#   0 13 * * 6 .../run-pancake.sh >> ~/logs/outbox-pancake.log 2>&1   # Act 2
#   0 19 * * 6 .../run-pancake.sh >> ~/logs/outbox-pancake.log 2>&1   # Act 3
set -eo pipefail

LOCK_DIR="/tmp/outbox-cafe-pancake.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 600 ]; then rm -rf "$LOCK_DIR"; fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): pancake already running — skipping"; exit 0
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
echo "===== $(date -Iseconds) pancake ====="
python3 scripts/pancake_sequence.py "$@"
