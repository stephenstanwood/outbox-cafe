# outbox.cafe

Something new at the top of every hour.

A weird/retro-leaning corner of the web. Every hour (skipping 8pm–4am Pacific) the cafe puts up a new self-contained piece. Old ones live in `/archive`.

## How it works

The Mac Mini runs an hourly task. The task:

1. Rolls a random *spec* across seven dimensions (era, format, subject, tone, length, mandatory element, palette) with anti-bias mechanics so it doesn't converge.
2. Hands the spec to Claude (via the Mini Claude proxy → Max OAuth → $0 marginal cost).
3. Writes the result to `archive/YYYY-MM-DDTHH.html` and copies it to `index.html`.
4. Commits, pushes, Vercel deploys.
5. Posts to the cafe's social presences (Bluesky / Tumblr / Are.na).
6. Checks for mentions, replies through a guardrail-filtered moderator pass.

A manual-trigger button on the live site hits a Mini endpoint via Tailscale Funnel to fire an off-schedule generation.

## Voice

The cafe (the place) is the brand. Multiple inconsistent staff voices post under one handle. Three rules:

1. **Relentlessly positive.**
2. **Never fight people.**
3. **Very very random.**

## Local dev

```
python3 scripts/generate.py            # generate one piece, no commit
python3 scripts/generate.py --commit   # generate + commit + push
```

## Layout

```
.
├── index.html              latest generation
├── archive/                all past generations
│   ├── index.html          the cabinet (browseable list)
│   └── YYYY-MM-DDTHH.html
├── scripts/
│   ├── generate.py         entry point
│   ├── spec.py             spec roller (anti-bias)
│   ├── prompt.py           prompt builder
│   └── post.py             social posting
├── data/
│   ├── dimensions.json     all the dimension tables
│   ├── personas.json       cafe staff voices
│   └── history.jsonl       rolling log of past specs
└── README.md
```
