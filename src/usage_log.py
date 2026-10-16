"""The usage record: one JSON row per API call, appended to ``usage.jsonl``.

Waffler used to keep this in ``usage.json``, a JSON list that was read, added
to and written back whole for every row, twice per dictation, both times
before the paste. That cost about 60 ms at 3,300 dictations and about 370 ms
at 20,000, and it grew with every day of use. Now a row is one line added to
the end of the file, whatever its size:

- ``append`` writes one line. A line is a complete JSON object, so a crash can
  only ever tear the last one. The next append ends a torn line with a line
  break first, so the new row starts on a line of its own.
- ``read`` returns the rows as dicts, the same rows ``usage.json`` held. A
  line that is torn, blank or not an object is skipped, never fatal.
- ``import_legacy`` moves the rows of an old ``usage.json`` into the new file
  once. It is safe to stop at any point and safe to run again: rows already in
  the new file are not added twice, and the old file is only set aside (as
  ``usage.backup-<time>.json``) after every row is in the new one.

Nothing here knows about prices or the window; app.py does those.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from pathlib import Path

try:
    from atomic_json import read_json_for_update, replace_with_retry
except ImportError:  # imported as src.usage_log
    from src.atomic_json import read_json_for_update, replace_with_retry

FILE = "usage.jsonl"          # the record, one row per line
LEGACY_FILE = "usage.json"    # the older record: one JSON list


def _line(row: dict) -> bytes:
    # ASCII only (the default escaping): a line is then one line in any
    # reader, whatever the characters in a row.
    return (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")


# A row that cannot be written because another program briefly holds the file
# (antivirus, a backup tool: "Access is denied" on Windows) is tried again: 20 +
# 40 + 80 + 160 ms at most, as atomic_json does for a replace.
APPEND_ATTEMPTS = 5
APPEND_BACKOFF_S = 0.02


def _append_once(path, data: bytes) -> None:
    # "a+b": every write goes to the end; the read is to see how the file ends.
    with open(path, "a+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                data = b"\n" + data         # end a line a crash left torn
        f.write(data)


def append(path, row: dict, *, sleep=None) -> None:
    """Add one row at the end of the file. Cost does not depend on its size."""
    data = _line(row)
    for i in range(APPEND_ATTEMPTS):
        try:
            _append_once(path, data)
            return
        except PermissionError:
            if i == APPEND_ATTEMPTS - 1:
                raise
            (sleep or time.sleep)(APPEND_BACKOFF_S * (2 ** i))


def read(path) -> list:
    """Every row in the file, oldest first. [] when it is missing or empty."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    rows = []
    # Only "\n" ends a line: str.splitlines() also splits on characters that
    # can sit inside a value.
    for line in raw.decode("utf-8-sig", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue                        # a torn line
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_all(path, rows) -> None:
    """Replace the whole record with ``rows`` (atomic). Used to reset it and
    by scripts/recost_usage.py; never on the dictation path."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            for row in rows:
                f.write(_line(row))
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _key(row: dict) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def _backup_name(legacy: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = legacy.with_name(f"usage.backup-{stamp}.json")
    n = 1
    while out.exists():
        n += 1
        out = legacy.with_name(f"usage.backup-{stamp}-{n}.json")
    return out


def import_legacy(legacy, path, log=None):
    """Move the rows of an old usage.json into ``path``, once.

    Returns None when there is no old file, else how many rows it added. The
    steps, each safe to stop after:

    1. Read the old file. One that cannot be parsed is kept aside by
       ``read_json_for_update`` (as ``usage.unreadable-<time>.json``), as the
       Journal's file always was, and counts as no rows.
    2. Work out which of its rows the new file does not already hold. A row
       is "held" once for each time it is there, so two real rows that look
       the same are both kept.
    3. Write them: the whole new file in one atomic step if it does not exist
       yet, else added to the end of it.
    4. Only then set the old file aside as ``usage.backup-<time>.json``. If we
       stop before this, the old file is still there and the next call finds
       every row already held and just sets it aside.

    The backup is Waffler's own record of what it spent, not what anyone said:
    Delete all my data removes it (``usage.backup-*.json``).
    """
    legacy, path = Path(legacy), Path(path)
    if not legacy.exists():
        return None
    old, kept = read_json_for_update(legacy, list)
    if kept is not None and log:
        log(f"[usage] usage.json could not be read; kept as {kept.name}")
    old = [r for r in old if isinstance(r, dict)]
    if not legacy.exists():                  # set aside as unreadable
        return 0
    held = Counter(_key(r) for r in read(path))
    extra = []
    for row in old:
        k = _key(row)
        if held[k] > 0:
            held[k] -= 1
        else:
            extra.append(row)
    if extra:
        if path.exists():
            for row in extra:
                append(path, row)
        else:
            write_all(path, extra)
    if old:
        replace_with_retry(legacy, _backup_name(legacy))
    else:
        legacy.unlink()                      # an empty list: nothing to keep
    if log:
        log(f"[usage] moved {len(extra)} row(s) from usage.json to {path.name}")
    return len(extra)


_gave_up: set = set()


def try_import_legacy(legacy, path, log=None):
    """``import_legacy`` for the dictation path: never raises, and after a
    failure (the old file locked by another program, say) does not try again
    in this run, so a dictation never pays for the same failed import twice.
    The next start tries again, and the import is safe to repeat. Returns
    what ``import_legacy`` does, or None."""
    key = str(legacy)
    if key in _gave_up:
        return None
    try:
        return import_legacy(legacy, path, log)
    except Exception as e:
        _gave_up.add(key)
        if log:
            log(f"[usage] old usage.json not moved this time ({type(e).__name__})")
        return None
