"""Atomic JSON writes that survive a briefly locked file on Windows.

``history.json`` and ``usage.json`` are rewritten through a temporary file and
``os.replace``. On Windows that replace fails with ``PermissionError``
("[WinError 5] Access is denied") while any other handle has the target open:
the UI thread reading history for the stats panel, an antivirus scan, the
search indexer or a sync client. Real dictations logged "Pipeline error:
Access is denied" 6 times, and each one was lost, because the write sat on
the dictation's critical path.

The lock is almost always gone within milliseconds, so the replace is retried
a few times with a short, growing wait before giving up.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

# 50 + 100 + 200 + 400 ms: under a second in the worst case.
REPLACE_ATTEMPTS = 5
REPLACE_BACKOFF_S = 0.05


def replace_with_retry(src, dst, *, attempts: int = REPLACE_ATTEMPTS,
                       backoff_s: float = REPLACE_BACKOFF_S, sleep=time.sleep) -> None:
    """``os.replace(src, dst)``, retried on ``PermissionError`` only."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            sleep(backoff_s * (2 ** i))


def write_json_atomic(path, data, *, indent: int = 2, sleep=time.sleep) -> None:
    """Write ``data`` as UTF-8 JSON to ``path`` via a temp file and a retried
    atomic replace. The temp file is removed if anything fails."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        replace_with_retry(tmp, path, sleep=sleep)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
