"""Recent recordings: the last few dictations' audio, kept on this computer.

Waffler keeps the audio of the last 10 dictations in the data folder
(debug_audio/), because "half my words are missing" can't be looked into
without it. A take can now be 20 minutes (about 38 MB), so the folder is also
held to a total size: the newest recordings that fit in ``MAX_TOTAL_BYTES``
are kept, and always the latest one. It never leaves the computer and is left out of the logs
bundle. Owner decision D9 (3.15): keep doing that, but say so in
Settings, Privacy and data, with a switch to stop keeping them and a
"Delete now" button.

The switch is ``keep_recent_audio`` in settings.json, on unless set off.
Turning it off stops new recordings being kept; "Delete now" removes the
ones already there.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

DIRNAME = "debug_audio"
KEEP = 10
# Ten takes of the longest length are about 380 MB. The folder is pruned to the
# newest that fit in this, as well as to KEEP: ten ordinary takes are a few
# megabytes and are all kept.
MAX_TOTAL_BYTES = 300 * 1024 * 1024
SETTING = "keep_recent_audio"
_PATTERN = "rec-*.wav"
# rec-20261016-143012.wav (or -2, -3 ... when two takes end in the same
# second): the only shape keep() gives a file. A Journal entry names its
# recording by this ("Redo this take"), and only a name of this shape
# that is a file inside the folder is ever opened.
_NAME = re.compile(r"^rec-\d{8}-\d{6}(?:-\d{1,3})?\.wav$")


def enabled(settings: dict | None) -> bool:
    """On unless the user switched it off."""
    return (settings or {}).get(SETTING, True) is not False


def folder(data_dir) -> Path:
    return Path(data_dir) / DIRNAME


def files(data_dir) -> list:
    """The kept recordings, oldest first."""
    d = folder(data_dir)
    if not d.is_dir():
        return []
    found = []
    for p in d.glob(_PATTERN):
        try:
            found.append((p.stat().st_mtime, p))
        except OSError:
            pass
    return [p for _t, p in sorted(found)]


def keep(data_dir, audio_bytes: bytes, settings: dict | None, now: datetime | None = None,
         limit: int = KEEP, max_bytes: int = MAX_TOTAL_BYTES):
    """Save one recording and prune to the newest ``limit`` that fit in
    ``max_bytes`` (the latest is always kept, however large). Returns the
    path, or None when switched off or there is no audio. Pruning goes by
    modified time, so names never decide which file is newest."""
    if not audio_bytes or not enabled(settings):
        return None
    d = folder(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    # Two short takes can end in the same second. Each keeps its own file: a
    # Journal entry points at its recording by name, and the second
    # must not overwrite the first's.
    path = d / f"rec-{stamp}.wav"
    n = 2
    while path.exists() and n < 1000:
        path = d / f"rec-{stamp}-{n}.wav"
        n += 1
    path.write_bytes(audio_bytes)
    for old in files(data_dir)[:-limit] if limit > 0 else files(data_dir):
        try:
            old.unlink()
        except OSError:
            pass
    if max_bytes > 0:
        _prune_to_size(data_dir, max_bytes)
    return path


def _prune_to_size(data_dir, max_bytes: int) -> None:
    """Delete the oldest recordings until the rest fit in ``max_bytes``. The
    newest is never deleted here, so a single very long take is kept."""
    total = 0
    newest_first = list(reversed(files(data_dir)))
    for i, p in enumerate(newest_first):
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if i > 0 and total + size > max_bytes:
            for old in newest_first[i:]:
                try:
                    old.unlink()
                except OSError:
                    pass
            return
        total += size


def is_valid_name(name) -> bool:
    return isinstance(name, str) and bool(_NAME.fullmatch(name))


def resolve(data_dir, name):
    """The recording's path when ``name`` is well formed and the file is
    there inside the folder; otherwise None."""
    if not is_valid_name(name):
        return None
    base = folder(data_dir).resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        return None
    return path


def names(data_dir) -> set:
    """The names of the kept recordings (one listing, for a page of entries)."""
    return {p.name for p in files(data_dir) if is_valid_name(p.name)}


def take_out(data_dir, name):
    """Delete one recording and return (name, its bytes, its modified time)
    so a deletion can be undone; None when it is not there or cannot be
    removed."""
    path = resolve(data_dir, name)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
        data = path.read_bytes()
        path.unlink()
    except OSError:
        return None
    return (name, data, mtime)


def put_back(data_dir, taken) -> bool:
    """Undo take_out. The recording gets its old modified time, so it is
    still the one that rotates out first. Not put back when the folder has
    filled up again since (newer recordings have the room)."""
    if not taken or not is_valid_name(taken[0]) or len(files(data_dir)) >= KEEP:
        return False
    name, data, mtime = taken
    d = folder(data_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_bytes(data)
        os.utime(path, (mtime, mtime))
        return True
    except OSError:
        return False


def delete_all(data_dir) -> int:
    """Delete every kept recording. Returns how many went."""
    n = 0
    for p in files(data_dir):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def summary(data_dir, settings: dict | None) -> dict:
    """What Settings shows: on or off, how many are kept, and the limit."""
    return {"enabled": enabled(settings), "count": len(files(data_dir)), "keep": KEEP}
