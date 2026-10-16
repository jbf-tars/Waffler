"""A mark for each take that is being turned into text, so a crash loses none.

A recording is on disk as soon as it is captured (src/recent_audio.py keeps the
last ten), but nothing said it was still waiting for its words. If Waffler
crashed, lost power or was force quit between the key coming up and the words
reaching the Journal, the recording sat in the folder with no card and no one
knew.

Now, right after a take is kept, its name is written to ``inflight.json`` in
the data folder, and removed when the dictation ends however it ends (words
saved, not sent, silence, cancelled). A mark that survives to the next start
means the take never finished. If its recording is still there and it is not
already in the Journal, the start-up adds a "Not sent" card for it
(reason ``interrupted``) with its own copy of the recording in ``unsent/``, so
Try again works as for any other.

The file holds names and times, no words:

    {"version": 1, "takes": [{"audio": "rec-20261016-143012.wav",
                              "at": "2026-10-16T14:30:12"}]}

It is small and read once at start-up. A mark that is cut short, empty,
hand-edited or not what it should be is thrown away, never trusted: at worst a
take is not recovered. Writing it never fails a dictation.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

try:
    import recent_audio
    import unsent
    from atomic_json import write_json_atomic
except ImportError:  # imported as src.take_marker
    from src import recent_audio, unsent
    from src.atomic_json import write_json_atomic

FILE = "inflight.json"
VERSION = 1
# More than the recordings kept (10) can be recovered, so a damaged file
# cannot make a flood of cards.
MAX_TAKES = recent_audio.KEEP

_lock = threading.Lock()


def _path(data_dir) -> Path:
    return Path(data_dir) / FILE


def _read(path: Path) -> list:
    """The takes in the file, each {"audio", "at"} with a well-formed name.
    [] for anything that is not the file we wrote."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("takes"), list):
        return []
    out, seen = [], set()
    for t in data["takes"]:
        name = t.get("audio") if isinstance(t, dict) else None
        if not recent_audio.is_valid_name(name) or name in seen:
            continue
        seen.add(name)
        at = t.get("at")
        out.append({"audio": name, "at": at if isinstance(at, str) else ""})
    return out[-MAX_TAKES:]


def _write(path: Path, takes: list):
    if not takes:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    write_json_atomic(path, {"version": VERSION, "takes": takes}, indent=None)


def note(data_dir, audio_name: str, now: datetime | None = None):
    """A take has been kept and is about to be turned into text."""
    if not recent_audio.is_valid_name(audio_name):
        return
    with _lock:
        path = _path(data_dir)
        takes = [t for t in _read(path) if t["audio"] != audio_name]
        takes.append({"audio": audio_name,
                      "at": (now or datetime.now()).isoformat(timespec="seconds")})
        _write(path, takes[-MAX_TAKES:])


def clear(data_dir, audio_name: str):
    """The take is done, whatever became of it."""
    with _lock:
        path = _path(data_dir)
        if not path.exists():
            return
        _write(path, [t for t in _read(path) if t["audio"] != audio_name])


def note_safely(data_dir, audio_name, log=None):
    """``note`` for the dictation path: never raises."""
    try:
        note(data_dir, audio_name)
    except Exception as e:
        if log:
            log(f"[marker] not written ({type(e).__name__})")


def clear_safely(data_dir, audio_name, log=None):
    """``clear`` for the dictation path: never raises."""
    try:
        clear(data_dir, audio_name)
    except Exception as e:
        if log:
            log(f"[marker] not cleared ({type(e).__name__})")


def forget_all(data_dir):
    """Remove the file (the start-up has dealt with what it held)."""
    with _lock:
        try:
            _path(data_dir).unlink()
        except FileNotFoundError:
            pass


def _take_time(take: dict, wav: Path) -> str:
    """When the take was made, as a Journal timestamp: the mark's time, else
    the time in the recording's name, else its file time."""
    try:
        return datetime.fromisoformat(take["at"]).isoformat(timespec="seconds")
    except (KeyError, TypeError, ValueError):
        pass
    try:
        return datetime.strptime(wav.name[4:19], "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
    except ValueError:
        return datetime.fromtimestamp(wav.stat().st_mtime).isoformat(timespec="seconds")


def pending(data_dir) -> list:
    """The takes a stopped Waffler left marked (empty when there is no file
    or it is no good). Cheap: one small file."""
    path = _path(data_dir)
    if not path.exists():
        return []
    return _read(path)


def recover(data_dir, history: list, log=None) -> list:
    """Journal entries for the marked takes that never finished.

    A take is skipped when its recording is gone (it rotated out, or recent
    recordings are off) or when the Journal already has an entry for that
    recording. For each of the rest, its recording is copied into ``unsent/``
    and a Not sent entry (reason ``interrupted``) is returned for the caller
    to add; this function does not write the Journal and does not remove the
    mark. A copy that fails skips that take (and is logged), so the next
    start tries again.
    """
    takes = pending(data_dir)
    if not takes:
        return []
    linked = {h.get("audio") for h in history if isinstance(h, dict) and h.get("audio")}
    unsent_dir = Path(data_dir) / unsent.UNSENT_DIRNAME
    entries = []
    for take in takes:
        name = take["audio"]
        wav = recent_audio.resolve(data_dir, name)
        if wav is None or name in linked:
            continue
        try:
            unsent_dir.mkdir(parents=True, exist_ok=True)
            copy = unsent.new_unsent_path(unsent_dir)
            shutil.copyfile(wav, copy)
            seconds = max(0.0, (copy.stat().st_size - 44) / 32000.0)
        except OSError as e:
            if log:
                log(f"[marker] could not keep the interrupted take ({type(e).__name__})")
            continue
        entries.append({
            "timestamp": _take_time(take, wav),
            "text": "",
            "styled": unsent.STYLED_NOTE,
            "word_count": 0,
            "failed": True,
            "error": unsent.REASON_INTERRUPTED,
            "audio_path": str(copy),
            "unsent_id": copy.name,
            "not_sent_reason": unsent.REASON_INTERRUPTED,
            "provider_name": "",
            "will_retry": False,
            "audio": name,
            **({"duration_s": round(seconds, 1)} if seconds > 0 else {}),
        })
    return entries
