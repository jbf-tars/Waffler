"""Shared logging helper for src/ modules.

Mirrors ``app.py:_log_to_file`` so modules under ``src/`` can write into
the same ``~/.waffler-hosted/app.log`` file without importing from
``app.py`` (which would create a circular import for the bundled build).

Use this from any module in ``src/`` that needs its diagnostic output
captured in the central log — until now those modules used ``print()``
which only landed on stdout, and stdout isn't captured by the packaged
.app bundle. The 16:22:17 dual-tap repro showed up in ``app.log`` only
as ``Recording started`` lines from ``app.py``; the smart_hotkey-level
``[HOTKEY] …`` prints were lost. This module fixes that.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

try:
    from data_paths import data_file
except ImportError:  # imported as src.log_util
    from src.data_paths import data_file


def _log_path() -> Path:
    """app.log in the data folder, resolved per call (see data_paths)."""
    return data_file("app.log")


def log(msg: str) -> None:
    """Append ``msg`` to ``app.log`` with an HH:MM:SS prefix.

    Best-effort: file I/O errors are swallowed so an unwritable disk can
    never crash the audio/event-tap thread. Also echoes to stdout so a
    developer running from source sees the line live.
    """
    try:
        log_path = _log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}  {msg}\n")
    except Exception:
        pass
    print(msg)


def transcript_for_log(text: str, *, allowed: bool) -> str:
    """Render a transcript for ``app.log``, redacting the words unless allowed.

    ``app.log`` is the file ``download_logs`` zips into a bug report, so
    transcribed speech must not reach it unless the user opts in with
    ``logging.log_transcripts: true``. Callers pass ``allowed`` explicitly
    rather than reading config in here: modules under ``src/`` must not depend
    on app-level state, and a required keyword makes the leak hard to
    reintroduce by accident.
    """
    if allowed:
        return text
    return f"{len(text)} chars"
