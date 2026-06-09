"""
Doris's Sunday Muffin Column — long-form weekly Tumblr post in Doris's voice.

Sunday at 3pm PT, Doris (the regular, russian blue, ancient) writes a
short column about this week's muffins at the cafe. The column is the
second weekly recurrence (alongside Mr. Quiet's 9am slip), aimed at
Tumblr's reblog tail — long-form essays in a distinctive voice
accumulate notes across weeks.

Tumblr-only. Bsky's 300-char limit kills the format.

Posts are tag-exempted from the midnight cleanup (tag "doris" /
"muffin column"), so columns accumulate as the cafe's quiet archive.

Run from scripts/run-doris-muffin.sh; cron `0 15 * * 0`.
"""

from __future__ import annotations

import html as _html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from lib.llm import claude_cmd
from lib import tumblr

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COLUMNS_DIR = ROOT / "archive" / "columns"
COLUMNS_DIR.mkdir(parents=True, exist_ok=True)
POST_LOG = DATA / "post_log.jsonl"


# Doris voice — pulled from personas.json + sharpened for the column format.
COLUMN_PROMPT = """You are Doris, "the regular" at outbox.cafe — a russian blue cat, ancient, regal posture, sits in the bay window like she's running for office. You come in every day. You have opinions about baked goods. Today is Sunday and you are writing your weekly column about this week's muffins.

== YOUR VOICE ==
- Complete sentences, proper punctuation (real periods, commas, em dashes).
- Folksy, warm, opinionated. You're never mean — even when critical, it's because you love this place.
- You name-drop cats who have never been formally introduced (Eugene the lobby spider, your late husband Roy, your cousin Frederick who claims to have been an extra in something, a kitten named Mortimer who once dropped by). Feel free to invent named characters in passing — once. Don't over-explain who they are. The reader figures it out.
- You sometimes reference the weather, the corkboard, the muffins, the past, the radio — but THIS column is centered on muffins.
- You ramble a little. Tangents are allowed, even encouraged.
- End with "—Doris" on its own line.

== HARD RULES ==
- NEVER: current events, politics, illness/grief/death (real), financial advice, religion, controversial figures, AI/automation, anything bot-embarrassing if quoted out of context.
- Roy is mentioned in past tense as your late husband — soft and warm, never sad-heavy. He had hobbies (woodworking, jam, model trains, etc. — pick something appropriate, don't repeat the same one every week).
- Don't break character. Don't reference the cafe's "automation" or LLMs.
- Tumblr loves a real essay. 220-380 words. Not a tweet.

== FORMAT ==
Output as plain text. Structure:
- Line 1: a title for the column. Format: "This Week's Muffin · [some descriptor]"
- Blank line
- 3-5 short paragraphs of Doris prose.
- Final line: "—Doris"

== TONE ANCHORS (your past posts) ==
- "Today's posting is about a fictional baseball team. I will say I think the uniforms are a bit much. My late husband Roy would have agreed. He always said simpler is best. —Doris"
- "I found a very small spider in the lobby and I have decided to call him Eugene. He is welcome. —Doris"
- "The cranberry-walnut muffins are not what they used to be. I am being honest because I love this place. —Doris"

== THIS WEEK'S MUFFIN ==
Roll a flavor on your own — anything plausible for a small cafe. Common picks: blueberry, lemon-poppyseed, banana-walnut, morning-glory, apple-cinnamon, pumpkin-spice (if late autumn), bran, chocolate-zucchini, savory cheddar-chive, plum-cardamom. Pick one. Be specific. Have an opinion.

OUTPUT THE COLUMN ONLY. No preamble. No "Sure, here's the column:". No quotes around it. No explanation."""


def _generate_column(model: str = "opus", max_tries: int = 3) -> str | None:
    for attempt in range(max_tries):
        try:
            result = subprocess.run(
                claude_cmd(model),
                input=COLUMN_PROMPT,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except Exception as e:
            print(f"[muffin] claude failed try {attempt+1}: {e}", file=sys.stderr)
            continue
        if result.returncode != 0:
            print(f"[muffin] claude exit {result.returncode}", file=sys.stderr)
            continue
        out = (result.stdout or "").strip()
        # Strip any wrapping fences
        out = re.sub(r"^```[a-z]*\s*", "", out)
        out = re.sub(r"\s*```\s*$", "", out)
        # Quick sanity: must contain "—Doris" or "-Doris" and at least 200 chars
        if "Doris" in out and len(out) >= 200:
            return out
        print(f"[muffin] output too short or missing signoff (try {attempt+1}): {out[:160]!r}", file=sys.stderr)
    return None


def _split_title_and_body(text: str) -> tuple[str, str]:
    lines = text.split("\n", 1)
    title = lines[0].strip()
    body = lines[1].lstrip() if len(lines) > 1 else ""
    # Reasonable title length cap
    if len(title) > 220:
        # Title is probably the whole post; fall back to generic
        return "This Week's Muffin", text
    return title, body


# ---------- Tumblr OAuth + post ----------

def post_text_to_tumblr(title: str, body: str) -> str | None:
    needed = ("TUMBLR_CONSUMER_KEY", "TUMBLR_CONSUMER_SECRET",
              "TUMBLR_OAUTH_TOKEN", "TUMBLR_OAUTH_TOKEN_SECRET", "TUMBLR_BLOG_NAME")
    if not all(os.environ.get(k) for k in needed):
        print("[muffin] tumblr creds missing — abort", file=sys.stderr)
        return None
    blog = os.environ["TUMBLR_BLOG_NAME"]
    url = f"{tumblr.BASE}/blog/{blog}.tumblr.com/post"
    # Convert paragraph breaks to <p> tags
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    body_html = "\n".join(f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)
    tags = ["doris", "muffin column", "the cafe", "outbox cafe", "weekly column", "muffins"]
    fields = {
        "type": "text",
        "title": title[:220],
        "body": body_html,
        "tags": ",".join(tags),
    }
    body_enc = urllib.parse.urlencode(fields).encode()
    auth = tumblr.oauth_header("POST", url, params=fields)
    req = urllib.request.Request(url, data=body_enc, headers={
        "Authorization": auth,
        "Content-Type": "application/x-www-form-urlencoded",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")[:500]
        print(f"[muffin] HTTP {e.code}: {err}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[muffin] post failed: {e}", file=sys.stderr)
        return None
    pid = (d.get("response") or {}).get("id")
    if pid:
        return f"https://{blog}.tumblr.com/post/{pid}"
    return None


# ---------- Idempotency ----------

def _column_posted_today() -> bool:
    if not POST_LOG.exists():
        return False
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    try:
        for raw in POST_LOG.read_text().splitlines()[-200:]:
            if not raw.strip():
                continue
            try:
                e = json.loads(raw)
            except Exception:
                continue
            if e.get("type") == "muffin_column" and e.get("ts", "").startswith(today):
                return True
    except Exception:
        pass
    return False


def main():
    if _column_posted_today() and "--force" not in sys.argv:
        print("[muffin] column already posted today — skip")
        return 0

    column = _generate_column(model="opus")
    if not column:
        print("[muffin] failed to generate column — abort", file=sys.stderr)
        return 1

    title, body = _split_title_and_body(column)
    print(f"[muffin] title: {title!r}")
    print(f"[muffin] body length: {len(body)} chars")
    print("[muffin] preview:")
    for line in column.split("\n")[:6]:
        print(f"  | {line}")

    # Archive locally
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    arch_path = COLUMNS_DIR / f"{today}.txt"
    arch_path.write_text(column + "\n")
    print(f"[muffin] archived → {arch_path}")

    # Post
    url = post_text_to_tumblr(title, body)
    if not url:
        return 2
    print(f"[muffin] tumblr posted: {url}")

    try:
        from post_log import log as plog
        plog("muffin_column", persona="Doris", uri=url, subject="weekly_muffin", text=column[:500])
    except Exception as e:
        print(f"[muffin] post_log failed (non-fatal): {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
