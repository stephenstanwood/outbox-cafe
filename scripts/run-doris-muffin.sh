#!/bin/bash
# Doris's Sunday Muffin Column — long-form weekly Tumblr column at 3pm PT.
#
# Cron entry (Sun 3:06pm PT — off the :00 grid the engage loop fires on):
#   6 15 * * 0 .../run-doris-muffin.sh >> ~/logs/outbox-doris.log 2>&1
set -eo pipefail

# Lock staleness 20 min — generation retries back off across ~6 min.
LOCK_DIR="/tmp/outbox-cafe-doris.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 1200 ]; then rm -rf "$LOCK_DIR"; fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): doris already running — skipping"; exit 0
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
echo "===== $(date -Iseconds) doris-muffin ====="
python3 scripts/doris_muffin.py

if [ -n "$(git status --porcelain archive/columns columns 2>/dev/null)" ]; then
  git add archive/columns/ columns/
  git -c user.email="outbox@outbox.cafe" -c user.name="outbox.cafe" \
      commit -m "column: $(date +%Y-%m-%d) — Doris" || true
  git push || true
fi
