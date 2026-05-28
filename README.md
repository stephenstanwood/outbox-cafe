# outbox.cafe

Something new four times a day.

A weird/retro-leaning corner of the web. Four times a day (4am, 8am, noon, 4pm
Pacific) the cafe puts up a new self-contained piece. Old ones live in `/archive`.

## How it works

The Mac Mini runs a scheduled task four times a day. The task:

1. Rolls a random *spec* across several dimensions (era, format, subject, tone,
   length, palette, mandatory element, forbidden register) with anti-bias
   mechanics so it doesn't converge, plus a code-side format bucket so variety
   is forced rather than hoped for.
2. Hands the spec to Claude via the local `claude` CLI (Max OAuth → $0 marginal
   cost), in an MCP-isolated, settings-free context so the gen stays pure
   text-out. See `scripts/lib/llm.py`.
3. Writes the result to `archive/YYYY-MM-DDTHH-MM.html` and copies it to
   `index.html`, then rebuilds the cabinet listing, RSS feed, and sitemap.
4. Commits, pushes, Vercel deploys (static site, no build step).
5. Announces the drop on the cafe's social presences (Bluesky + Tumblr) with a
   purpose-built poster image, in a randomly-picked staff voice.

Separately, a 15-minute engagement loop replies to mentions and leaves small
in-character observations, and several weekly rituals recur on their own cron
slots (Mr. Quiet's Sunday slip, Doris's muffin column, Pancake's Saturday
sequence, a Tumblr reblog cycle, a likes loop). Bluesky + Tumblr feeds are
wiped nightly at midnight PT — a fresh feed every day — with pinned welcomes
and a few tagged ritual posts exempt.

## Voice

The cafe (the place) is the brand. Multiple inconsistent staff voices post under
one handle. Three rules:

1. **Relentlessly positive.**
2. **Never fight people.**
3. **Very very random.**

No politics, no real public figures, no real brands. The canonical staff roster
lives at `/about/`.

## Local dev

```
python3 scripts/generate.py             # generate one piece, no commit
python3 scripts/generate.py --dry-run   # roll a spec + print the prompt, don't call Claude
python3 scripts/generate.py --commit    # generate + commit + push
```

## Layout

```
.
├── index.html              latest generation
├── archive/                all past generations
│   ├── index.html          the cabinet (browseable list)
│   ├── thumbs/             cabinet thumbnails
│   └── YYYY-MM-DDTHH-MM.html
├── scripts/
│   ├── generate.py         entry point (gen → archive → cabinet/feed → commit → post)
│   ├── spec.py             spec roller (anti-bias + format buckets)
│   ├── prompt.py           prompt builder
│   ├── lib/llm.py          shared, MCP-isolated `claude` wrapper (opus by default)
│   ├── post_bsky.py        Bluesky drop announcement
│   ├── post_tumblr.py      Tumblr cross-post
│   ├── engage_bsky.py      mentions/replies/ambient/wild engagement loop
│   └── …                   rituals, cleanup, reblog, likes, reflection
└── data/
    ├── dimensions.json     all the dimension tables
    ├── personas.json       cafe staff voices
    └── history.jsonl       rolling log of past specs
```
```
