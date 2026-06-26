# outbox.cafe — project context

Weird/retro generative site, 4 gens/day. Mac Mini cron rolls a spec, hands to Claude, writes HTML, commits, pushes, Vercel deploys.

## Where things live

- **Source of truth: Mac Mini.** Cron, OAuth token, logs, locks all live on Mini.
  - SSH: `stephenstanwood@10.0.0.234` (local) / `100.117.24.89` (Tailscale)
  - Logs: `~/logs/outbox-cafe.log` (appended forever — `tail -100` for recent)
  - Cron entry: `crontab -l` — currently `0 4,8,12,16 * * *` (4am, 8am, 12pm, 4pm PT)
  - Lock: `/tmp/outbox-cafe-run.lock` (mkdir-based, stale after 15 min)
- **Hosting: Vercel.** Auto-deploys on push to `main`. Static + one function (`api/sign.js`, guestbook intake). `vercel.json` sets `"installCommand": ""` so deploys skip npm install — `@vercel/blob` in package.json is a Mini-only dep (`scripts/blob_queue.js`).
- **OAuth token:** sourced from `~/Projects/mini-claude-proxy/.env` (`CLAUDE_CODE_OAUTH_TOKEN`).
  The wrapper does `set -a; . $PROXY_ENV; . $REPO/.env; set +a` — must use `set -a` to actually export.
- **Builder task (laptop):** Claude scheduled task `outbox-cafe-builder` runs Tue+Fri 1pm — health-checks the cafe, then ships one self-chosen improvement. Prompt: `~/.claude/scheduled-tasks/outbox-cafe-builder/SKILL.md`.
- **Cat-signal DMs are OFF by default.** Stephen does not want outbox.cafe breakage/quota/ritual/guestbook DMs. `scripts/cat_signal.py` logs and returns false unless `OUTBOX_CAT_SIGNAL_DMS=1` is explicitly set for a temporary test.

## Critical gotchas (each one cost real hours)

### Never pass `--permission-mode plan` to headless `claude --print`

Plan mode biases the model to emit planning text ("Let me create the plan file...") instead of the requested HTML/JSON/reply. With `--tools ""` there are no tools to permission-check anyway — plan mode is pure downside. ~50% of gens silently failed for over a day until this was found. Pattern across all call sites:

```python
cmd = ["claude", "--print", "--tools", "", "--model", "sonnet"]   # ✓
# NOT: ["claude", "--print", "--tools", "", "--permission-mode", "plan", ...]
```

### Discord bot HTTP gotcha if DMs are ever re-enabled

Bare `urllib` UA (`Python-urllib/3.x`) trips Cloudflare's browser-fingerprint block → **HTTP 403 / error 1010**. Required format per Discord API:

```python
"User-Agent": "DiscordBot (https://outbox.cafe, 0.1)"
```

Affects `scripts/cat_signal.py` and any other direct Discord API call we add.

### Cron schedule

Currently `0 4,8,12,16 * * *` on the Mini — 4 gens/day at 4am, 8am, 12pm, 4pm PT. Single-flight lock prevents pileup. Past schedules used: `*/10 * * * *` (stash mode during Max usage cycles), `0 * * * *` (hourly); the flip-to-hourly helper script from that era has been deleted.

### Gen runner pulls must autostash

`scripts/run-on-mini.sh` must use `git pull --rebase --autostash`. The nightly
canon scout can leave `data/canon.json` dirty before the next scheduled drop;
plain `git pull --rebase` aborts before generation and wedged all four 2026-06-26
gens until the runner was fixed.

### Claude weekly-limit fallback

When the Claude CLI is hard-capped (`You've hit your weekly limit ... resets ...`),
`generate.py` now publishes a deterministic counter-card fallback drop instead of
leaving the archive silent. It records `limit_fallback: true` in
`data/history.jsonl`/`data/runs.jsonl` and still rebuilds the cabinet/feed/sitemap.
It does not DM by default because the central cat-signal sender is disabled.
Social captioners still call Claude directly, so they skip posting while capped;
the site heartbeat is the priority.

### Weekly ritual crons run at :06 (2026-06-09)

Slip is `6 9 * * 0`, Doris is `6 15 * * 0` — staggered off the :00 grid the
every-15-min engage loop fires on, after both rituals failed silently on two
Sundays (5/24, 6/7) with `claude exit 1`. Ritual scripts now retry with
backoff across ~6 min and log both output streams on failure. The old cat-signal
calls are now quiet unless `OUTBOX_CAT_SIGNAL_DMS=1` is explicitly set.
An off-Mini GitHub Actions heartbeat
(`.github/workflows/heartbeat.yml`) alerts if no `drop:` commit lands for 14h
(threshold accommodates the 12h overnight gap).

### Guestbook (2026-06-09)

The cafe's only UGC surface, pre-moderated. `/guestbook/` form → `api/sign.js` (honeypot, link-reject, length caps, 20s throttle, no IP stored) → unguessable JSON blob under `guestbook/queue/` on Vercel Blob. Mini cron `41 * * * * scripts/run-guestbook.sh` moderates the queue via LLM (injection-armored prompt, REJECT-when-unsure), appends approved notes to `data/guestbook.jsonl`, rebuilds the page, commits. Queue blobs are deleted only after a durable outcome; ≥50 queued = spam wave → log and hold, but no DM unless cat-signal DMs are explicitly re-enabled. Never weaken the moderation gate or add UGC surfaces beyond this one.

### Carte blanche + canon (2026-06-09)

~10% of gens roll a carte-blanche spec (`scripts/spec.py`): every dimension reads "builder's choice", the prompt drops to only the rules that always apply, and image prefetch is skipped (screenshot fallback covers social). `data/canon.json` is the cafe's recurring universe (Pepper, The Good Wok, 429 Persimmon Ln, ...); 18% of prompts offer one element as an optional easter egg. The nightly digest's canon scout (`scripts/canon_scout.py`) reads the day's gens and may promote at most ONE new element per night (cap 40).

### Midnight cleanup (2026-05-19)

`0 0 * * * scripts/run-cleanup.sh` wipes every bsky + tumblr post nightly. Pinned welcome on each platform is exempt. New day = fresh feed. The bsky engage loop no longer does its own probabilistic cleanup — midnight is the single canonical wipe. If the cron misses a night, posts pile up visibly until the next firing; no rolling rescue.

## Common ops

- **Manual gen** (no commit, just verify): on Mini, `cd ~/Projects/outbox-cafe && set -a && . ~/Projects/mini-claude-proxy/.env && [ -f .env ] && . .env; set +a && python3 scripts/generate.py`
- **Trigger fresh push gen**: same as above + `--commit`
- **Clear stale lock**: `rm -rf /tmp/outbox-cafe-run.lock`
- **Force midnight cleanup now**: on Mini, `~/Projects/outbox-cafe/scripts/run-cleanup.sh`
- **Check what broke**: `grep -nE '✓ wrote|did not look like HTML|TimeoutExpired' ~/logs/outbox-cafe.log | tail -40`
- **Temporarily test cat-signal DMs**: `OUTBOX_CAT_SIGNAL_DMS=1 python3 scripts/cat_signal.py --key test 'msg'`

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
