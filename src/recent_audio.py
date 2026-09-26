"""Recent recordings: the last few dictations' audio, kept on this computer.

Waffler keeps the audio of the last 10 dictations in the data folder
(debug_audio/), because "half my words are missing" can't be looked into
without it. It never leaves the computer and is left out of the logs
bundle. Owner decision D9 (3.15): keep doing that, but say so in
Settings, Privacy and data, with a switch to stop keeping them and a
"Delete now" button.

The switch is ``keep_recent_audio`` in settings.json, on unless set off.
Turning it off stops new recordings being kept; "Delete now" removes the
ones already there.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

DIRNAME = "debug_audio"
KEEP = 10
SETTING = "keep_recent_audio"
_PATTERN = "rec-*.wav"


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
         limit: int = KEEP):
    """Save one recording and prune to the newest ``limit``. Returns the
    path, or None when switched off or there is no audio. Pruning goes by
    modified time, so names never decide which file is newest."""
    if not audio_bytes or not enabled(settings):
        return None
    d = folder(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = d / f"rec-{stamp}.wav"
    path.write_bytes(audio_bytes)
    for old in files(data_dir)[:-limit] if limit > 0 else files(data_dir):
        try:
            old.unlink()
        except OSError:
            pass
    return path


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
