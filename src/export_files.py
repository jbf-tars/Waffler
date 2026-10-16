"""Saving an export for the person to find: a file on the Desktop, shown in
its folder.

The Journal export (src/history_export.py), the Vocabulary export
(src/dictionary.py) and the Snippets export (src/snippets_io.py) all end the
same way as the logs bundle does: a file on the Desktop (the home folder when
there is no Desktop), written whole under a temporary name first so a half
written file is never left where the person looks, and revealed in Finder or
Explorer. It is the person's own copy: Delete all my data does not touch it,
and the Privacy page says so.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

try:
    from atomic_json import replace_with_retry
    from history_export import desktop_folder, reveal_in_folder
except ImportError:  # imported as src.export_files
    from src.atomic_json import replace_with_retry
    from src.history_export import desktop_folder, reveal_in_folder

__all__ = ["desktop_folder", "reveal_in_folder", "unique_path", "save"]


def unique_path(folder, stem: str, ext: str, now: datetime | None = None) -> Path:
    """<stem>-<time>.<ext> in ``folder``, with -2, -3 ... when that exists."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%dT%H-%M-%S")
    path = Path(folder) / f"{stem}-{stamp}.{ext}"
    n = 2
    while path.exists() and n < 1000:
        path = Path(folder) / f"{stem}-{stamp}-{n}.{ext}"
        n += 1
    return path


def save(data: bytes, stem: str, ext: str, folder=None, now: datetime | None = None) -> Path:
    """Write ``data`` to a new file in ``folder`` (the Desktop by default) and
    return its path. Raises OSError if it cannot be written; nothing is left
    behind then."""
    folder = Path(folder) if folder is not None else desktop_folder()
    path = unique_path(folder, stem, ext, now)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
