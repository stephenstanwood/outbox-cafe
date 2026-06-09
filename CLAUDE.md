# outbox.cafe — project context

Weird/retro generative site, 4 gens/day. Mac Mini cron rolls a spec, hands to Claude, writes HTML, commits, pushes, Vercel deploys.

## Where things live

- **Source of truth: Mac Mini.** Cron, OAuth token, logs, locks all live on Mini.
  - SSH: `stephenstanwood@10.0.0.234` (local) / `100.117.24.89` (Tailscale)
  - Logs: `~/logs/outbox-cafe.log` (appended forever — `tail -100` for recent)
  - Cron entry: `crontab -l` — currently `0 4,8,12,16 * * *` (4am, 8am, 12pm, 4pm PT)
  - Lock: `/tmp/outbox-cafe-run.lock` (mkdir-based, stale after 15 min)
- **Hosting: Vercel.** Auto-deploys on push to `main`. No vercel.ts/.json — it's static.
- **OAuth token:** sourced from `~/Projects/mini-claude-proxy/.env` (`CLAUDE_CODE_OAUTH_TOKEN`).
  The wrapper does `set -a; . $PROXY_ENV; . $REPO/.env; set +a` — must use `set -a` to actually export.

## Critical gotchas (each one cost real hours)

### Never pass `--permission-mode plan` to headless `claude --print`

Plan mode biases the model to emit planning text ("Let me create the plan file...") instead of the requested HTML/JSON/reply. With `--tools ""` there are no tools to permission-check anyway — plan mode is pure downside. ~50% of gens silently failed for over a day until this was found. Pattern across all call sites:

```python
cmd = ["claude", "--print", "--tools", "", "--model", "sonnet"]   # ✓
# NOT: ["claude", "--print", "--tools", "", "--permission-mode", "plan", ...]
```

### Discord bot HTTP requires a `DiscordBot` User-Agent

Bare `urllib` UA (`Python-urllib/3.x`) trips Cloudflare's browser-fingerprint block → **HTTP 403 / error 1010**. Required format per Discord API:

```python
"User-Agent": "DiscordBot (https://outbox.cafe, 0.1)"
```

Affects `scripts/cat_signal.py` and any other direct Discord API call we add.

### Cron schedule

Currently `0 4,8,12,16 * * *` on the Mini — 4 gens/day at 4am, 8am, 12pm, 4pm PT. Single-flight lock prevents pileup. Past schedules used: `*/10 * * * *` (stash mode during Max usage cycles), `0 * * * *` (hourly); the flip-to-hourly helper script from that era has been deleted.

### Midnight cleanup (2026-05-19)

`0 0 * * * scripts/run-cleanup.sh` wipes every bsky + tumblr post nightly. Pinned welcome on each platform is exempt. New day = fresh feed. The bsky engage loop no longer does its own probabilistic cleanup — midnight is the single canonical wipe. If the cron misses a night, posts pile up visibly until the next firing; no rolling rescue.

## Common ops

- **Manual gen** (no commit, just verify): on Mini, `cd ~/Projects/outbox-cafe && set -a && . ~/Projects/mini-claude-proxy/.env && [ -f .env ] && . .env; set +a && python3 scripts/generate.py`
- **Trigger fresh push gen**: same as above + `--commit`
- **Clear stale lock**: `rm -rf /tmp/outbox-cafe-run.lock`
- **Force midnight cleanup now**: on Mini, `~/Projects/outbox-cafe/scripts/run-cleanup.sh`
- **Check what broke**: `grep -nE '✓ wrote|did not look like HTML|TimeoutExpired' ~/logs/outbox-cafe.log | tail -40`
- **Send a test cat-signal**: `python3 scripts/cat_signal.py --key test 'msg'`

## Voice / content guardrails

The cafe is the brand — multiple inconsistent staff voices under one handle. Three rules: relentlessly positive, never fight people, very very random. No politics, no real public figures, no real brands generally. Spec rolls across seven dimensions with anti-bias mechanics; forbidden register pushes against the generator's documented defaults (dry deadpan, archival/museum metaphors, dispatcher voice, SCP register).

## Social posting philosophy (carried from SBT)

> "We give people the most useful info possible, not just try to drive straight back to our site at every turn. If we consistently deliver, they trust us and seek us out." — Stephen, on SBS social

Translated for outbox.cafe (all our content is ours, so the rule isn't "link out" but "let the post be the post"):

- **The post is the point.** Posts stand alone as content — quotes, fragments, specific details, the thumbnail. A follower scrolling should get something whole without clicking.
- **No "read →" CTA, no clickable funnel photo.** Tumblr profile bio carries the cafe URL for anyone curious.
- **No URLs in body text.** Bluesky comment is right: outbound links kill engagement. The thumb is the visual.
- **No meta-announcement phrasing.** "Found this", "from the archive", "new piece is up", "just dropped" — all banned. The post is content, not a teaser for content elsewhere.
- **Excerpts and images are the inspiration source.** Cats are sharing what they're noticing, not advertising the cafe.
- **Exception (not the default):** a truly interactive piece (game/puzzle/toy/tuner) that can't live in a post form — those CAN link. Add a heuristic on the spec format if/when we want to surface that.
