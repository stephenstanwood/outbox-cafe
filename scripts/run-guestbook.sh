#!/bin/bash
# Guestbook review — read queued counter notes, moderate, publish approved.
#
# Cron entry (hourly at :41, dodging the gen/engage/reblog slots):
#   41 * * * * /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-guestbook.sh >> /Users/stephenstanwood/logs/outbox-guestbook.log 2>&1
set -eo pipefail

LOCK_DIR="/tmp/outbox-cafe-guestbook.lock"
if [ -d "$LOCK_DIR" ]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 1800 ]; then rm -rf "$LOCK_DIR"; fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date -Iseconds): guestbook review already running — skipping"; exit 0
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
echo "===== $(date -Iseconds) guestbook ====="
python3 scripts/guestbook_review.py

# Publish approved notes: page + data file. Pull first so an hourly push
# doesn't race the 4x/day gen commits.
if [ -n "$(git status --porcelain data/guestbook.jsonl guestbook 2>/dev/null)" ]; then
  git pull --rebase --quiet || true
  git add data/guestbook.jsonl guestbook/
  git -c user.email="outbox@outbox.cafe" -c user.name="outbox.cafe" \
      commit -m "guestbook: $(date +%Y-%m-%d\ %H:%M)" || true
  git push || true
fi
