"""
Pancake's Saturday sequence — three posts across one Saturday that
read like Pancake gradually finding her words.

Pancake (the cafe cat, calico, lives in the window) normally posts at
random — half nonsense keyboard-mash ("asdfjklasdfjkl"), half
surprisingly poetic single sentences. This sequence STRUCTURES that
duality: across Saturday, three posts at fixed times produce a slow
progression from mash → drift → clean line. Reads like she's trying to
type something and gradually getting there.

Acts:
- 07:00 PT — Act 1: pure keyboard mash. ~12-30 chars.
- 13:00 PT — Act 2: drift. Fragments of words mixed with mash.
- 19:00 PT — Act 3: one clean perfect poetic sentence.

Single script, three cron entries. Decides which act based on current
hour. Idempotent via post_log (refuses to repeat the same act today).

Posts to bsky AND tumblr (Pancake is featured on both). All three acts
get swept at midnight along with everything else — the sequence is a
Saturday-only Schelling thing, not an accumulating archive.
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
from lib import bsky, tumblr

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
POST_LOG = DATA / "post_log.jsonl"

ACT_PROMPTS = {
    1: """You are Pancake, the cafe cat at outbox.cafe — calico, lives in the window. You are posting on Bluesky right now.

This is ACT 1 of a Saturday sequence. You are walking on the keyboard. Output PURE KEYBOARD MASH — random letters that look like a cat actually walked on a laptop. No real words. No real sentences. Lowercase. Maybe a few spaces. 14-30 characters total.

GOOD examples (study the rhythm):
- "asdjklfjslkfjsljf"
- "lkj sdf lkjasdf fff"
- "qwerty asdf zxcvbnm"
- "fjsdkl;fjkdf;l"
- "aaa skdfj lkjsdf"

End with " —Pancake" (one space, em dash, name).

OUTPUT THE POST ONLY. No quotes around it. No explanation. Just the mash + —Pancake.""",

    2: """You are Pancake, the cafe cat at outbox.cafe — calico, lives in the window. You are posting on Bluesky right now.

This is ACT 2 of a Saturday sequence. You are STARTING to type real words but they keep dissolving back into mash. Output a single short post that mixes keyboard nonsense with partial words and one or two real ones. Like Pancake is almost there but not quite. 30-90 characters total.

GOOD examples (study the rhythm):
- "the sun. the sun. asdfjkl the. sun."
- "wormmmm. asdf. the bird. fjsdkl."
- "i think the. lkjsdf. the chair."
- "warm. warm warm. asdfjsdfk warm."

Lowercase. Short. Drift in and out. End with " —Pancake".

OUTPUT THE POST ONLY.""",

    3: """You are Pancake, the cafe cat at outbox.cafe — calico, lives in the window. You are posting on Bluesky right now.

This is ACT 3 of a Saturday sequence — the payoff. You have finally found the words. Output ONE clean, perfect, single-sentence poetic observation. Pancake at her best. 60-200 characters. Lowercase. No mash. No drift. Pure clarity.

GOOD examples (study the rhythm and feel):
- "the sunbeam moves slowly enough that i can stay in it for an hour. that is a deal. —Pancake"
- "the bird is back. it has a small worm. it has not noticed me yet. —Pancake"
- "an envelope is just a small room you can fold up. —Pancake"
- "the high shelf knows what it knows. —Pancake"

Topics you tend toward: sunbeams, the high shelf, a bird visible through the window, envelopes, the smell of the espresso machine warming up, soft small things.

Lowercase. One sentence (period optional). End with " —Pancake".

OUTPUT THE POST ONLY.""",
}


def _act_for_now() -> int | None:
    """Decide which act to post based on current local hour. Returns 1, 2, 3, or None."""
    now = datetime.now(timezone.utc).astimezone()
    if now.weekday() != 5:  # 5 = Saturday
        return None
    h = now.hour
    if 6 <= h < 11:
        return 1
    if 11 <= h < 16:
        return 2
    if 17 <= h < 23:
        return 3
    return None


def _act_posted_today(act: int) -> bool:
    if not POST_LOG.exists():
        return False
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    key = f"pancake_seq_{act}"
    try:
        for raw in POST_LOG.read_text().splitlines()[-300:]:
            if not raw.strip():
                continue
            try:
                e = json.loads(raw)
            except Exception:
                continue
            if e.get("type", "").startswith(key) and e.get("ts", "").startswith(today):
                return True
    except Exception:
        pass
    return False


# Spread retries — transient claude blips outlast back-to-back tries
# (see the slip/doris Sunday failures, 5/24 + 6/7).
RETRY_SLEEPS = (45, 90, 180)


def _generate_act(act: int, max_tries: int = 4) -> str | None:
    import time
    prompt = ACT_PROMPTS[act]
    # opus now (Max OAuth = $0) — short outputs, but the best model is free
    for attempt in range(max_tries):
        if attempt > 0:
            delay = RETRY_SLEEPS[min(attempt - 1, len(RETRY_SLEEPS) - 1)]
            print(f"[pancake-{act}] retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
        try:
            result = subprocess.run(
                claude_cmd("opus"),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as e:
            print(f"[pancake-{act}] claude failed try {attempt+1}: {e}", file=sys.stderr)
            continue
        if result.returncode != 0:
            print(
                f"[pancake-{act}] claude exit {result.returncode}"
                f" stderr={result.stderr[:300]!r} stdout={result.stdout[:300]!r}",
                file=sys.stderr,
            )
            continue
        out = (result.stdout or "").strip()
        # First non-empty line; strip quotes
        for raw in out.splitlines():
            line = raw.strip()
            line = re.sub(r"^[\"'`]+|[\"'`]+$", "", line)
            if not line:
                continue
            # Must end with —Pancake (or close variant)
            if "Pancake" not in line:
                continue
            if len(line) > 280:
                continue
            # Act 1 sanity: must not contain too many real-looking words
            if act == 1:
                # Reject if too "sentence-like" (e.g., has actual periods or common words besides "asdf"-like)
                if re.search(r"\b(the|and|cat|cafe|sunbeam|warm)\b", line, re.IGNORECASE):
                    print(f"[pancake-1] rejected (too word-like): {line!r}", file=sys.stderr)
                    continue
            return line
        print(f"[pancake-{act}] no usable output try {attempt+1}: {out[:160]!r}", file=sys.stderr)
    return None


# ---------- Bsky ----------

def _bsky_req(path: str, *, data=None, headers=None, method="GET"):
    return bsky.request(path, data=data, headers=headers, method=method)


def post_to_bsky(text: str) -> str | None:
    if not (os.environ.get("BSKY_HANDLE") and os.environ.get("BSKY_APP_PASSWORD")):
        print("[pancake] bsky creds missing", file=sys.stderr)
        return None
    try:
        did, jwt = bsky.login()
    except Exception as e:
        print(f"[pancake] bsky auth failed: {e}", file=sys.stderr)
        return None
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en"],
    }
    try:
        resp = _bsky_req(
            "/com.atproto.repo.createRecord",
            data={"repo": did, "collection": "app.bsky.feed.post", "record": record},
            headers={"Authorization": f"Bearer {jwt}"},
            method="POST",
        )
    except Exception as e:
        print(f"[pancake] bsky createRecord failed: {e}", file=sys.stderr)
        return None
    return resp.get("uri")


# ---------- Tumblr ----------

def post_to_tumblr(text: str) -> str | None:
    needed = ("TUMBLR_CONSUMER_KEY", "TUMBLR_CONSUMER_SECRET",
              "TUMBLR_OAUTH_TOKEN", "TUMBLR_OAUTH_TOKEN_SECRET", "TUMBLR_BLOG_NAME")
    if not all(os.environ.get(k) for k in needed):
        return None
    blog = os.environ["TUMBLR_BLOG_NAME"]
    url = f"{tumblr.BASE}/blog/{blog}.tumblr.com/post"
    tags = ["pancake", "the cafe", "outbox cafe", "saturday"]
    fields = {
        "type": "text",
        "body": f"<p>{_html.escape(text)}</p>",
        "tags": ",".join(tags),
    }
    body = urllib.parse.urlencode(fields).encode()
    auth = tumblr.oauth_header("POST", url, params=fields)
    req = urllib.request.Request(url, data=body,
        headers={"Authorization": auth, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except Exception as e:
        print(f"[pancake] tumblr failed: {e}", file=sys.stderr)
        return None
    pid = (d.get("response") or {}).get("id")
    return f"https://{blog}.tumblr.com/post/{pid}" if pid else None


def main():
    # CLI override: --act N to force a specific act (for testing)
    act = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--act" and i + 1 <= len(sys.argv[1:]):
            try:
                act = int(sys.argv[i + 1])
            except Exception:
                pass
    if act is None:
        act = _act_for_now()
    if act is None:
        print("[pancake] not in a sequence window (Saturday morning/afternoon/evening) — skip")
        return 0
    if act not in ACT_PROMPTS:
        print(f"[pancake] invalid act {act}", file=sys.stderr)
        return 1

    if _act_posted_today(act) and "--force" not in sys.argv:
        print(f"[pancake] act {act} already posted today — skip")
        return 0

    print(f"[pancake] generating act {act}")
    text = _generate_act(act)
    if not text:
        print("[pancake] generation failed — abort", file=sys.stderr)
        try:
            from cat_signal import signal
            signal("ritual-pancake", f"Pancake's Saturday act {act} failed all generation retries — sequence has a hole today. Check ~/logs/outbox-pancake.log on the Mini.", priority="high")
        except Exception:
            pass
        return 1
    print(f"[pancake] text: {text!r}")

    bsky_uri = post_to_bsky(text)
    tumblr_url = post_to_tumblr(text)

    try:
        from post_log import log as plog
        if bsky_uri:
            plog(f"pancake_seq_{act}_bsky", persona="Pancake", uri=bsky_uri, subject=f"sequence_act_{act}", text=text)
        if tumblr_url:
            plog(f"pancake_seq_{act}_tumblr", persona="Pancake", uri=tumblr_url, subject=f"sequence_act_{act}", text=text)
    except Exception as e:
        print(f"[pancake] post_log failed (non-fatal): {e}", file=sys.stderr)

    if not bsky_uri and not tumblr_url:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
