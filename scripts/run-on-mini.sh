#!/bin/bash
# Wrapper for the hourly (or more frequent) generation task on the Mac Mini.
# Source claude OAuth token from the proxy env, ensure PATH includes claude,
# pull any external changes, run one generation, commit+push.
#
# Cron entry (every 10 min during 4am-8pm Pacific):
#   */10 4-19 * * * /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-on-mini.sh >> /Users/stephenstanwood/logs/outbox-cafe.log 2>&1

set -eo pipefail

REPO_DIR="$HOME/Projects/outbox-cafe"
PROXY_ENV="$HOME/Projects/mini-claude-proxy/.env"

# Pick up CLAUDE_CODE_OAUTH_TOKEN (SSH/cron can't read the keychain)
if [ -f "$PROXY_ENV" ]; then
  set -a
  . "$PROXY_ENV"
  set +a
fi

# Ensure claude is in PATH for cron/launchd
export PATH="/opt/homebrew/bin:$HOME/.bun/bin:$HOME/.local/bin:/usr/local/bin:$PATH"

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
