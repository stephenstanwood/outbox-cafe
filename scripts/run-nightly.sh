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
python3 scripts/nightly_digest.py
