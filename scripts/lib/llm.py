"""Shared `claude --print` wrapper for every headless call in outbox.cafe.

Every script that shells out to the `claude` CLI MUST build its command via
`claude_cmd()` (or call `call_claude`). Centralizing the invocation fixes a
whole class of failures in one place:

  * MCP isolation. Without --strict-mcp-config the headless model inherits the
    machine's full ambient MCP environment — on the Mini that's Gmail, Google
    Drive, Calendar, Discord, the Vercel plugin: 30+ tool definitions injected
    into every gen's context. That clutter once derailed gens into "let me write
    the plan" prose instead of HTML (see data/last_bad_output.txt). We pin an
    EMPTY mcp config so no servers load. Verified to strip all MCP tools on
    Mini CLI 2.1.119 / laptop 2.1.118.
  * No settings bleed. --setting-sources '' keeps the user + project CLAUDE.md
    and hooks out of the creative context (we don't want Stephen's global prefs
    leaking into a 1933 box-office ledger).
  * Never --permission-mode plan. It biases toward planning text. We never pass it.
  * Model default is opus. Max OAuth = $0 marginal cost, so the best model is
    free. Helpers used to run haiku; everything is opus now.
"""
from __future__ import annotations

import re
import subprocess
import sys

# Flags that isolate a headless gen from the machine's interactive environment.
# Order matters only for readability. `--tools ""` disables built-in tools so
# the model emits text instead of trying to Write/Edit files.
_ISOLATION = [
    "--tools", "",
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
    "--setting-sources", "",
]


def claude_cmd(model: str = "opus") -> list[str]:
    """The isolated `claude --print` command line. Use everywhere we shell out.

    Pass the result straight to subprocess.run(..., input=prompt, text=True).
    """
    return ["claude", "--print", *_ISOLATION, "--model", model]


def call_claude(prompt: str, model: str = "opus", timeout: int = 120) -> str:
    """Run claude in print mode and return stdout. Raises RuntimeError on failure.

    Use this when the caller wants to handle/propagate failures itself (e.g. the
    main generator's retry loop). Helpers that should never crash the cron want
    `call_claude_or_none` instead.
    """
    result = subprocess.run(
        claude_cmd(model),
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = "\n".join(
            part.strip()
            for part in (result.stderr, result.stdout)
            if part and part.strip()
        )
        raise RuntimeError(
            f"claude failed (exit {result.returncode}): {detail[:500]}"
        )
    return result.stdout


def call_claude_or_none(prompt: str, model: str = "opus", timeout: int = 120) -> str | None:
    """Best-effort variant for engagement/posting helpers: returns None on ANY
    failure (subprocess error, nonzero exit, timeout) instead of raising, so a
    flaky call never aborts a cron run."""
    try:
        return call_claude(prompt, model=model, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — helpers must never crash the cron
        print(f"[llm] claude call failed: {e}", file=sys.stderr)
        return None


def strip_fences(text: str) -> str:
    """Strip a leading ```lang fence, trailing ```, and a wrapping pair of quotes."""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    t = t.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"' and t.count('"') == 2:
        t = t[1:-1].strip()
    return t


# Unambiguous refusal / off-voice markers. A cafe cat never says "I cannot" or
# "as an AI" — if any of these lead the output, treat it as a decline so a stray
# apology never lands on the public timeline. Conservative on purpose: a skipped
# reply costs nothing; a posted "I'm sorry, I can't help with that" is the bad case.
_REFUSAL_MARKERS = (
    "i can't", "i cannot", "i can not", "i won't", "i will not",
    "as an ai", "as a language model", "i'm not able", "i am not able",
    "i'm unable", "i am unable", "i don't feel comfortable",
    "i'm sorry, but", "sorry, but i", "i'm not comfortable",
)


def is_nopost(text: str | None) -> bool:
    """True if the model declined (or effectively declined) to produce a post.

    Robust against the model wrapping the NOPOST token in prose, or emitting a
    soft refusal instead of the bare token. Empty / refusal-ish → NOPOST.
    """
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if "NOPOST" in stripped.upper()[:40]:
        return True
    head = stripped.lower()[:60]
    return any(marker in head for marker in _REFUSAL_MARKERS)
