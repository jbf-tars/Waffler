"""Export the Journal as a file (Settings, Privacy and data, "Export history").

Three formats, all written from history.json and nothing else (never the keys,
settings or logs):

- ``json``: every entry as it is kept, oldest first, one list, so nothing is
  lost to a tool that wants the lot. Two fields are left out because they are
  about this computer, not about what was said: ``audio_path`` (a path with
  the user name in it) and ``error`` (a provider's raw message, which can
  quote part of a key).
- ``csv``: one row per entry with the columns timestamp, app, language,
  words and text, for a spreadsheet. It opens in Numbers and Excel with the
  accents right (a byte order mark says UTF-8), and a cell that starts with
  ``=``, ``+``, ``-`` or ``@`` (or a tab or carriage return) gets a ``'`` in
  front, so a spreadsheet shows it as text instead of running it as a formula
  (CSV injection).
- ``txt``: dated paragraphs, for reading.

The file goes on the Desktop (home if there is none), as the logs zip does,
and is shown in its folder. It is the user's own copy: Delete all my data does
not touch it, and the Privacy page says so.
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

try:
    from atomic_json import replace_with_retry
    from command_mode import engine_for
    from history_meta import words_of
except ImportError:  # imported as src.history_export
    from src.atomic_json import replace_with_retry
    from src.command_mode import engine_for
    from src.history_meta import words_of

FORMATS = ("json", "csv", "txt")
LABELS = {"json": "JSON", "csv": "CSV", "txt": "Plain text"}
CSV_COLUMNS = ("timestamp", "app", "language", "words", "text")
# Left out of the JSON: about this computer, or a provider's raw message.
PRIVATE_FIELDS = ("audio_path", "error")
# A spreadsheet runs a cell that starts with one of these (OWASP "CSV injection").
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _entries(history) -> list:
    return [h for h in (history or []) if isinstance(h, dict)]


def render_json(history) -> str:
    kept = [{k: v for k, v in h.items() if k not in PRIVATE_FIELDS} for h in _entries(history)]
    return json.dumps(kept, ensure_ascii=False, indent=2) + "\n"


def defuse(cell) -> str:
    """A cell's text, made safe to open in a spreadsheet."""
    text = str(cell if cell is not None else "").replace("\x00", "")
    if text.startswith(_FORMULA_STARTS):
        return "'" + text
    return text


def render_csv(history) -> str:
    buf = io.StringIO(newline="")
    out = csv.writer(buf, lineterminator="\r\n")
    out.writerow(CSV_COLUMNS)
    for h in _entries(history):
        words = h.get("word_count")
        text = words_of(h)
        out.writerow([
            defuse(h.get("timestamp", "")),
            defuse(h.get("app", "")),
            defuse(h.get("lang", "")),
            words if isinstance(words, int) and not isinstance(words, bool)
            else (0 if h.get("failed") else len(text.split())),
            defuse(text),
        ])
    return buf.getvalue()


def _heading(h: dict) -> str:
    ts = str(h.get("timestamp", ""))
    try:
        when = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        when = ts or "Unknown time"
    app = " ".join(str(h.get("app") or "").split())
    head = f"{when} ({app})" if app else when
    if h.get("kind") == "transform":
        name = " ".join(str(h.get("transform_name") or "").split())
        head += f", rewritten with {name}" if name else ", rewritten"
    elif h.get("kind") == "command":
        action = h.get("action")
        if action == "ask":
            head += f", searched {engine_for(h.get('engine')).label}"
        else:
            head += ", drafted by voice" if action == "draft" else ", edited by voice"
    return head


def render_txt(history, now: datetime | None = None) -> str:
    items = _entries(history)
    now = now or datetime.now()
    lines = ["Waffler history",
             f"Exported {now.strftime('%Y-%m-%d %H:%M')}",
             f"{len(items)} {'entry' if len(items) == 1 else 'entries'}, oldest first",
             ""]
    for h in items:
        lines.append(_heading(h))
        lines.append(words_of(h))
        lines.append("")
    return "\n".join(lines)


def render(history, fmt: str, now: datetime | None = None) -> bytes:
    """The file's bytes. ``fmt`` is one of FORMATS (ValueError otherwise)."""
    if fmt == "json":
        return render_json(history).encode("utf-8")
    if fmt == "csv":
        return render_csv(history).encode("utf-8-sig")
    if fmt == "txt":
        return render_txt(history, now).encode("utf-8")
    raise ValueError(f"unknown export format: {fmt!r}")


def desktop_folder(home=None) -> Path:
    """The Desktop, or the home folder when there is none (a machine with no
    desktop)."""
    home = Path(home) if home is not None else Path.home()
    desktop = home / "Desktop"
    return desktop if desktop.exists() else home


def unique_path(folder, fmt: str, now: datetime | None = None) -> Path:
    """waffler-history-<time>.<ext>, with -2, -3 ... when that exists."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%dT%H-%M-%S")
    path = Path(folder) / f"waffler-history-{stamp}.{fmt}"
    n = 2
    while path.exists() and n < 1000:
        path = Path(folder) / f"waffler-history-{stamp}-{n}.{fmt}"
        n += 1
    return path


def write_file(history, fmt: str, folder, now: datetime | None = None) -> Path:
    """Write the export into ``folder`` and return its path. The file is
    written whole under a temporary name first, so a half-written export is
    never left where the user looks."""
    data = render(history, fmt, now)
    path = unique_path(folder, fmt, now)
    fd, tmp = tempfile.mkstemp(dir=Path(folder), suffix=".tmp")
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


def reveal_in_folder(path, system: str) -> bool:
    """Show the file in Finder or Explorer, selected. Never raises: the file
    is saved either way. ``system`` is platform.system()."""
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            return False
        return True
    except Exception:
        return False
