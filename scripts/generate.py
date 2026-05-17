"""Main hourly entry point for outbox.cafe.

Rolls a spec, calls Claude via the local `claude` CLI (uses Max OAuth, no API $),
writes the HTML to archive/ and copies to index.html, refreshes the cabinet
listing, appends the spec to history, optionally commits and pushes.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from prompt import build_prompt
from spec import (
    append_history,
    format_spec_for_human,
    load_dimensions,
    roll_spec,
)

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
INDEX_PATH = ROOT / "index.html"
CABINET_PATH = ARCHIVE_DIR / "index.html"

PT = ZoneInfo("America/Los_Angeles")


def call_claude(prompt: str, model: str | None = None, timeout: int = 600) -> str:
    """Call the local claude CLI in --print mode with tools disabled.

    Without --tools "", claude operates agentically — it picks up Write/Edit
    and modifies files itself rather than printing the result. We want pure
    text-out: prompt in, HTML out.
    """
    cmd = [
        "claude",
        "--print",
        "--tools", "",
        "--permission-mode", "plan",
    ]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude failed (exit {result.returncode}): {result.stderr[:500]}")
    return result.stdout


def extract_html(raw: str) -> str:
    """Pull the HTML document out of Claude's response, in case it wrapped it."""
    s = raw.strip()
    # If wrapped in code fences, strip them
    if s.startswith("```"):
        # remove opening fence
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
        s = s.strip()
    # Trim anything before <!DOCTYPE or <html
    m = re.search(r"<!doctype html|<html", s, re.IGNORECASE)
    if m:
        s = s[m.start():]
    return s.strip()


def looks_like_html(s: str) -> bool:
    head = s[:300].lower()
    return "<html" in head and "</html>" in s.lower()


def filename_for_now() -> str:
    now = datetime.now(tz=PT)
    return now.strftime("%Y-%m-%dT%H-%M.html")


def extract_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else "(untitled)"


def rebuild_cabinet() -> None:
    """Rebuild archive/index.html as a chronological list of all past pieces."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for f in sorted(ARCHIVE_DIR.glob("*.html"), reverse=True):
        if f.name == "index.html":
            continue
        try:
            html = f.read_text(errors="ignore")
            title = extract_title(html)
            # extract spec watermark if present (era · format · tone)
            wm = ""
            m = re.search(
                r"(era|format|tone)[^<]*?·[^<]*?·[^<]*",
                html,
                re.IGNORECASE,
            )
            if m:
                wm = m.group(0).strip()
            entries.append({
                "file": f.name,
                "title": title,
                "watermark": wm,
                "stamp": f.stem,  # e.g. 2026-05-17T14
            })
        except Exception:
            pass

    rows_html = "\n".join(
        f'  <li><a href="./{e["file"]}"><span class="stamp">{e["stamp"]}</span> '
        f'<span class="title">{e["title"]}</span></a>'
        + (f'<div class="wm">{e["watermark"]}</div>' if e["watermark"] else "")
        + "</li>"
        for e in entries
    )

    count = len(entries)
    cabinet_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>the cabinet · outbox.cafe</title>
<style>
  :root {{
    --bg: #f8f3e8;
    --ink: #2a2418;
    --accent: #b8473a;
    --dim: #8a7a5c;
    --line: #d4c4a4;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--ink);
    font-family: ui-monospace, "SF Mono", "Menlo", "Courier New", monospace;
    font-size: 15px; line-height: 1.5; }}
  body {{ padding: 28px 18px 80px; }}
  .wrap {{ max-width: 760px; margin: 0 auto; }}
  header {{ border-bottom: 2px solid var(--ink); padding-bottom: 12px; margin-bottom: 18px; }}
  header h1 {{ margin: 0 0 4px; font-size: 22px; font-weight: 700; letter-spacing: 1px; }}
  header .sub {{ color: var(--dim); font-size: 13px; }}
  header a {{ color: var(--accent); }}
  .count {{ color: var(--dim); font-size: 13px; margin: 14px 0 8px; }}
  ul.entries {{ list-style: none; padding: 0; margin: 0; }}
  ul.entries li {{ padding: 10px 0; border-bottom: 1px dotted var(--line); }}
  ul.entries a {{ display: block; color: var(--ink); text-decoration: none; }}
  ul.entries a:hover {{ color: var(--accent); }}
  .stamp {{ color: var(--dim); font-size: 12px; margin-right: 10px; letter-spacing: 0.5px; }}
  .title {{ font-weight: 600; }}
  .wm {{ color: var(--dim); font-size: 12px; margin-top: 2px; font-style: italic; }}
  footer {{ margin-top: 36px; color: var(--dim); font-size: 12px; text-align: center; line-height: 1.6; }}
  footer a {{ color: var(--accent); }}
  @media (max-width: 540px) {{
    .stamp {{ display: block; margin: 0 0 2px; }}
  }}
</style>
</head>
<body>
<div class="wrap">

<header>
<h1>THE CABINET</h1>
<div class="sub">everything we've put up so far · <a href="/">latest →</a></div>
</header>

<div class="count">{count} piece{'s' if count != 1 else ''} on the corkboard</div>

<ul class="entries">
{rows_html if entries else '  <li class="wm">no pieces filed yet. check back at the top of the hour.</li>'}
</ul>

<footer>
  outbox.cafe · something new at the top of every hour · the cabinet keeps the rest
</footer>

</div>
</body>
</html>
"""
    CABINET_PATH.write_text(cabinet_html)


def git_commit_and_push(message: str) -> None:
    """Stage everything, commit, push. Quiet on failure."""
    subprocess.run(["git", "-C", str(ROOT), "add", "-A"], check=True)
    # No-op if nothing to commit
    diff = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "--quiet"],
    )
    if diff.returncode == 0:
        print("nothing to commit")
        return
    subprocess.run(
        ["git", "-C", str(ROOT), "commit", "-m", message],
        check=True,
    )
    subprocess.run(["git", "-C", str(ROOT), "push"], check=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--commit", action="store_true", help="git commit + push after writing")
    p.add_argument("--dry-run", action="store_true", help="roll spec + print prompt, don't call Claude")
    p.add_argument("--model", default=None, help="override claude model (default: account default)")
    args = p.parse_args()

    spec = roll_spec(seed=args.seed)
    print("=" * 60)
    print(f"hourly generation @ {datetime.now(tz=PT).isoformat()}")
    print("=" * 60)
    print(format_spec_for_human(spec))
    print()

    prompt = build_prompt(spec)

    if args.dry_run:
        print("---- PROMPT ----")
        print(prompt)
        return 0

    print("calling claude (this may take 30-90s for larger pieces) ...")
    raw = call_claude(prompt, model=args.model)
    html = extract_html(raw)

    if not looks_like_html(html):
        # Save to a debug file but don't overwrite index
        debug = ROOT / "data" / "last_bad_output.txt"
        debug.write_text(raw)
        print(f"output did not look like HTML; raw saved to {debug}", file=sys.stderr)
        return 2

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_file = ARCHIVE_DIR / filename_for_now()
    archive_file.write_text(html)
    shutil.copyfile(archive_file, INDEX_PATH)

    append_history(spec)
    rebuild_cabinet()

    title = extract_title(html)
    print(f"\n✓ wrote {archive_file.name} — {title}")

    if args.commit:
        msg = f"hourly: {title}"[:72]
        git_commit_and_push(msg)
        print("✓ committed and pushed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
