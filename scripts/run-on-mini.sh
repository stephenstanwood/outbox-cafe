#!/bin/bash
# Wrapper for the scheduled generation task on the Mac Mini.
# Source claude OAuth token from the proxy env, ensure PATH includes claude,
# pull any external changes, run one generation, commit+push.
#
# Cron entry (4x/day at 4am, 8am, noon, 4pm PT):
#   0 4,8,12,16 * * * /Users/stephenstanwood/Projects/outbox-cafe/scripts/run-on-mini.sh >> /Users/stephenstanwood/logs/outbox-cafe.log 2>&1

set -eo pipefail

# Single-flight: if another run is in progress, skip this firing rather than
# pile up (gens take 1-4 min; cron currently fires 4x/day, well separated).
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

# Durable telemetry for runs that die before generate.py can write its own
# runs.jsonl line. Without it an aborted run leaves nothing but a log line
# nobody greps, and the nightly digest cheerfully reports "3 gens in last 24h".
# Gitignored per-Mini state, like every other loop state file.
record_abort() {
  local stage="$1" detail="$2" clean
  clean=$(printf '%s' "$detail" \
    | tr '\n\r\t' '   ' \
    | tr -d '\000-\037' \
    | sed 's/\\/\\\\/g; s/"/\\"/g' \
    | cut -c1-300)
  printf '{"ts":"%s","stage":"%s","detail":"%s"}\n' \
    "$(date -Iseconds)" "$stage" "$clean" \
    >> "$REPO_DIR/data/aborted_runs.jsonl" 2>/dev/null || true
}

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

# Pull external changes before generating, to avoid push conflicts. Autostash is
# required because local generated state (for example canon scout additions) can
# already be dirty before the next scheduled drop commits it.
#
# Retried, because a single transient network blip used to cost a whole drop: on
# 2026-09-01 the 4am run died on one "ssh: connect to host github.com port 22"
# and the cafe went 16h between drops. Nothing anywhere reported it — the GitHub
# heartbeat's threshold has to tolerate the 12h overnight gap, so a missed 4am
# slot is precisely the failure it cannot see.
pull_ok=""
pull_err=""
for attempt in 1 2 3; do
  if pull_err=$(git pull --rebase --autostash --quiet 2>&1); then
    pull_ok=1
    if [ "$attempt" -gt 1 ]; then
      echo "git pull succeeded on attempt $attempt"
    fi
    break
  fi
  echo "git pull attempt $attempt/3 failed: $(printf '%s' "$pull_err" | tr '\n\r\t' '   ' | cut -c1-200)"
  if [ "$attempt" -lt 3 ]; then
    sleep $(( attempt * 15 ))
  fi
done

if [ -z "$pull_ok" ]; then
  echo "git pull failed after 3 attempts — aborting this run"
  record_abort "git_pull" "$pull_err"
  exit 1
fi

# Run one generation + commit + push. generate.py writes its own runs.jsonl line
# on every outcome it survives (including the counter-card fallback), so only a
# hard non-zero exit needs recording here.
set +e
python3 scripts/generate.py --commit
gen_rc=$?
set -e
if [ "$gen_rc" -ne 0 ]; then
  echo "generate.py exited $gen_rc — recording aborted run"
  record_abort "generate" "generate.py exited $gen_rc"
fi
exit "$gen_rc"
