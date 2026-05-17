#!/bin/bash
# Wrapper for the hourly (or more frequent) generation task on the Mac Mini.
# Source claude OAuth token from the proxy env, ensure PATH includes claude,
# pull any external changes, run one generation, commit+push.
#
# Cron entry (hourly, 24/7):
#   0 * * * * /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-on-mini.sh >> /Users/stephenstanwood/logs/outbox-cafe.log 2>&1

set -eo pipefail

# Single-flight: if another run is in progress, skip this firing rather than
# pile up (gens take 1-4 min; cron fires every 10 min in stash mode).
# Atomic mkdir lock (macOS doesn't ship flock). Stale locks (>15 min) get cleared.
LOCK_DIR="/tmp/outbox-cafe-run.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 900 ]; then
    echo "$(date -Iseconds): clearing stale lock ($lock_age s old)"
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): another run is in progress — skipping"
  exit 0
fi
trap "rmdir '$LOCK_DIR' 2>/dev/null" EXIT

REPO_DIR="$HOME/Projects/outbox-cafe"
PROXY_ENV="$HOME/Projects/mini-claude-proxy/.env"

# Pick up CLAUDE_CODE_OAUTH_TOKEN (SSH/cron can't read the keychain)
if [ -f "$PROXY_ENV" ]; then
  set -a
  . "$PROXY_ENV"
  set +a
fi

# outbox-cafe specific env (UNSPLASH_ACCESS_KEY, future image-gen API keys, etc.)
if [ -f "$REPO_DIR/.env" ]; then
  set -a
  . "$REPO_DIR/.env"
  set +a
fi

# Ensure claude is in PATH for cron/launchd
export PATH="/opt/homebrew/bin:$HOME/.bun/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
# Disable Python's stdout buffering so we see progress live in the log
export PYTHONUNBUFFERED=1

cd "$REPO_DIR"

echo
echo "===== $(date -Iseconds) ====="

# Pull external changes before generating, to avoid push conflicts
git pull --rebase --quiet || {
  echo "git pull failed — aborting this run"
  exit 1
}

# Run one generation + commit + push
python3 scripts/generate.py --commit
