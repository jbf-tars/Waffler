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


_LOG_PATH = Path.home() / ".waffler-hosted" / "app.log"


def log(msg: str) -> None:
    """Append ``msg`` to ``app.log`` with an HH:MM:SS prefix.

    Best-effort: file I/O errors are swallowed so an unwritable disk can
    never crash the audio/event-tap thread. Also echoes to stdout so a
    developer running from source sees the line live.
    """
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H:%M:%S")
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts}  {msg}\n")
    except Exception:
        pass
    print(msg)
