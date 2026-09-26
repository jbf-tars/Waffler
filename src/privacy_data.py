"""Settings, Privacy and data (3.15, plan SR9): what Waffler keeps, and deleting it.

Four jobs, each small and testable without the app:

- History retention. ``history_keep_days`` in settings.json: 0 (the default)
  keeps every dictation, as Waffler always has; 30, 90 or 365 keep that many
  days. Not sent entries are never pruned: they are waiting for Try again or
  Delete, and their recordings are counted in "Not sent yet".
- "Delete all my data": history, usage, recent recordings, recordings not
  sent and the logs. Keys, the words list and settings stay. Deleting the
  keys as well is the full reset (factory_reset in app.py), which the window
  asks about separately.
- Old transcript lines in app.log. Versions before the transcript redaction
  logged the first 80 characters of each dictation ("Done: <text>") and of
  the setup practice ("Wizard transcription: <text>"). On the owner's PC
  that was 2,011 lines. They are removed once, on the first start of 3.15.
  Today's lines ("Done: 12 words, 64 chars", "Wizard transcription: 31
  chars") are left alone.
- Log rotation. app.log was never rotated (4.5 MB on the owner's PC). At
  start-up a log over ``LOG_MAX_BYTES`` becomes app.log.1, replacing any
  older one.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

try:
    from atomic_json import replace_with_retry, write_json_atomic
except ImportError:  # imported as src.privacy_data
    from src.atomic_json import replace_with_retry, write_json_atomic


# ── History retention ────────────────────────────────────────────────────────

HISTORY_SETTING = "history_keep_days"
HISTORY_CHOICES = (0, 365, 90, 30)          # 0 = keep everything


def history_keep_days(settings: dict | None) -> int:
    """The chosen retention in days; 0 (keep everything) unless a known
    choice is set, so a stray value can never delete history."""
    try:
        v = int((settings or {}).get(HISTORY_SETTING, 0) or 0)
    except (TypeError, ValueError):
        return 0
    return v if v in HISTORY_CHOICES else 0


def _stamp(entry) -> datetime | None:
    try:
        return datetime.fromisoformat(str(entry.get("timestamp", "")))
    except (TypeError, ValueError, AttributeError):
        return None


def _prunable(entry, cutoff: datetime) -> bool:
    """An entry older than the cutoff that is not a Not sent recording.
    An entry whose time can't be read is kept."""
    if not isinstance(entry, dict) or entry.get("failed"):
        return False
    t = _stamp(entry)
    if t is None:
        return False
    if t.tzinfo is not None:
        t = t.replace(tzinfo=None)
    return t < cutoff


def prune_history(history: list, keep_days: int, now: datetime | None = None):
    """Returns (kept entries, how many were removed). ``keep_days`` 0 keeps all."""
    if not keep_days or keep_days not in HISTORY_CHOICES:
        return list(history), 0
    cutoff = (now or datetime.now()) - timedelta(days=keep_days)
    kept = [e for e in history if not _prunable(e, cutoff)]
    return kept, len(history) - len(kept)


def count_older(history: list, keep_days: int, now: datetime | None = None) -> int:
    """How many dictations a retention of ``keep_days`` would delete now."""
    return prune_history(history, keep_days, now)[1]


# ── Delete all my data ───────────────────────────────────────────────────────

# Files and folders in the data folder. Keys (.env), settings.json,
# config.json, setup_complete.json and vocab.json are not here: they stay.
DATA_FILES = ("history.json", "usage.json", "quality.jsonl",
              "app.log", "app.log.1", "crash.log", "hotkey.log")
DATA_GLOBS = ("usage.backup-*.json",)
DATA_FOLDERS = ("debug_audio", "unsent")


def _remove_file(p: Path) -> bool:
    """Delete a file; if another handle holds it open (Windows: crash.log is
    held by the crash handler), empty it instead. True when nothing is left
    in it."""
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        try:
            with open(p, "w", encoding="utf-8"):
                pass
            return True
        except OSError:
            return False


def delete_my_data(data_dir) -> dict:
    """Delete history, usage, recordings (recent and not sent) and logs.

    history.json and usage.json are emptied (written as ``[]``) rather than
    removed, so a reader never meets a missing file halfway. Returns
    {"ok": bool, "failed": [names that could not be deleted]}.
    """
    d = Path(data_dir)
    failed = []
    if not d.is_dir():
        return {"ok": True, "failed": []}
    for name in ("history.json", "usage.json"):
        p = d / name
        if p.exists():
            try:
                write_json_atomic(p, [])
            except OSError:
                failed.append(name)
    for name in DATA_FILES:
        if name in ("history.json", "usage.json"):
            continue
        p = d / name
        if p.exists() and not _remove_file(p):
            failed.append(name)
    for pattern in DATA_GLOBS:
        for p in d.glob(pattern):
            if not _remove_file(p):
                failed.append(p.name)
    for name in DATA_FOLDERS:
        p = d / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            if p.exists() and any(p.iterdir()):
                failed.append(name)
    return {"ok": not failed, "failed": failed}


# ── Old transcript lines in app.log ──────────────────────────────────────────

LOG_SCRUB_SETTING = "log_transcripts_scrubbed"

_TS = re.compile(r"^\d\d:\d\d:\d\d  ")
# Old lines, never today's: "Done: 12 words, 64 chars" and
# "Wizard transcription: 31 chars" are what 3.14 and later write.
_OLD = re.compile(r"^(\d\d:\d\d:\d\d  )(Done|Wizard transcription): (.*)$")
_NEW_TAIL = {"Done": re.compile(r"^\d+ words, \d+ chars$"),
             "Wizard transcription": re.compile(r"^\d+ chars$")}
_OLD_SLICE = 80      # the old code logged text[:80]


def scrub_transcript_lines(lines: list):
    """Remove the old transcript lines. Returns (lines kept, lines removed).

    The old code logged ``text[:80]``, so a transcript with a line break in
    its first 80 characters carried on over the next lines, which have no
    time stamp. Those go too, up to the 80 characters the slice allowed;
    anything after that is some other output and stays.
    """
    out, removed, i, n = [], 0, 0, len(lines)
    while i < n:
        m = _OLD.match(lines[i])
        # On Windows app.log is written in text mode, so each line ends in
        # "\r\n": the "\r" is not part of what was logged.
        if not m or _NEW_TAIL[m.group(2)].match(m.group(3).rstrip("\r")):
            out.append(lines[i])
            i += 1
            continue
        removed += 1
        used = len(m.group(3).rstrip("\r"))
        i += 1
        while i < n and not _TS.match(lines[i]) and lines[i].strip():
            used += 1 + len(lines[i].rstrip("\r"))    # the line break counts too
            if used > _OLD_SLICE:
                break
            removed += 1
            i += 1
    return out, removed


def scrub_log_file(path) -> int:
    """Rewrite app.log without the old transcript lines. Returns how many
    lines went (0 when there were none, and then the file is untouched).
    The rewrite is atomic; on a locked file it raises and the log is left
    as it was, to be tried again next start."""
    p = Path(path)
    if not p.exists():
        return 0
    raw = p.read_bytes()
    text = raw.decode("utf-8", errors="surrogateescape")
    keep_end = text.endswith("\n")
    lines = text.split("\n")
    if keep_end:
        lines = lines[:-1]
    kept, removed = scrub_transcript_lines(lines)
    if not removed:
        return 0
    body = "\n".join(kept) + ("\n" if keep_end and kept else "")
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body.encode("utf-8", errors="surrogateescape"))
        replace_with_retry(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return removed


# ── Log rotation ─────────────────────────────────────────────────────────────

LOG_MAX_BYTES = 5 * 1024 * 1024


def rotate_log(path, max_bytes: int = LOG_MAX_BYTES) -> bool:
    """Move a log over ``max_bytes`` to ``<name>.1`` (replacing an older
    one), so a fresh one starts. True when it rotated."""
    p = Path(path)
    try:
        if not p.exists() or p.stat().st_size <= max_bytes:
            return False
        replace_with_retry(p, p.with_name(p.name + ".1"))
        return True
    except OSError:
        return False


def tidy_logs_at_start(data_dir, settings_path) -> dict:
    """Start-up: the one-time scrub (marked done in settings.json), then
    rotation. Never raises. Returns what happened, for one log line."""
    d = Path(data_dir)
    result = {"scrubbed": None, "rotated": False}
    sp = Path(settings_path)
    try:
        import json
        settings = json.loads(sp.read_text(encoding="utf-8-sig")) if sp.exists() else {}
        if not isinstance(settings, dict):
            settings = {}
    except Exception:
        settings = None                 # unreadable: don't overwrite it
    if settings is not None and not settings.get(LOG_SCRUB_SETTING):
        try:
            result["scrubbed"] = scrub_log_file(d / "app.log")
            settings[LOG_SCRUB_SETTING] = True
            write_json_atomic(sp, settings)
        except Exception:
            result["scrubbed"] = None   # try again next start
    result["rotated"] = rotate_log(d / "app.log")
    return result
