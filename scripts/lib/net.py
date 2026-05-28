"""Small retry helper for IDEMPOTENT network calls.

Use ONLY for safe-to-repeat operations — auth/session creation, GETs. Never wrap
a create/post/like call: if the request succeeded but the response timed out,
retrying would double-post. bsky's createSession endpoint is the documented
offender (periodic multi-second timeouts that aren't a credential problem); a
few quick retries ride out a transient blip without dropping the announcement.
"""
from __future__ import annotations

import sys
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def with_retry(fn: "Callable[[], T]", *, attempts: int = 3, base_delay: float = 1.0,
               label: str = "request") -> T:
    """Call fn(); on exception, retry up to `attempts` times with exponential backoff.

    Re-raises the last exception if every attempt fails. Only use for idempotent fn.
    """
    last: "BaseException | None" = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < attempts:
                delay = base_delay * (2 ** (i - 1))
                print(f"[net] {label} attempt {i}/{attempts} failed: {e} — retry in {delay:.0f}s",
                      file=sys.stderr)
                time.sleep(delay)
    assert last is not None
    raise last
