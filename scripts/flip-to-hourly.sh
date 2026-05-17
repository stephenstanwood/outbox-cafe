#!/bin/bash
# One-shot: flip outbox-cafe cron from stash mode (*/10) back to hourly (0)
# and remove this flip-trigger entry. Scheduled to fire Monday 04:00 PT after
# the weekly Max usage cycle rolls over.
set -e

TMP=$(mktemp)
crontab -l \
  | grep -v 'flip-to-hourly' \
  | sed -E \
      -e 's|^\*/10 \* \* \* \* (/Users/stephenstanwood/Projects/outbox-cafe/scripts/run-on-mini\.sh.*)$|0 * * * * \1|' \
      -e 's|^# outbox\.cafe.*$|# outbox.cafe — hourly generation, 24/7|' \
  > "$TMP"
crontab "$TMP"
rm "$TMP"

echo "$(date -Iseconds): flipped outbox cron to hourly, removed flip trigger" \
  >> "$HOME/logs/outbox-cafe.log"
