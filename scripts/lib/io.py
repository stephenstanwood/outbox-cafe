"""Atomic file writes for per-Mini state files.

Multiple cron jobs (engage every 15 min, likes/reblog every 3h) can fire at
overlapping times and write the same state file. A bare `write_text` can be
interrupted mid-write (leaving truncated JSON) or lose a concurrent update.
Writing to a temp file and `os.replace`-ing it is atomic on POSIX, so a reader
always sees either the old or the new file — never a half-written one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_text(path: "str | Path", text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)  # atomic rename on POSIX


def atomic_write_json(path: "str | Path", obj: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(obj, indent=indent))
