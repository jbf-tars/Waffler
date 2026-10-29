"""A tip when the Mac's microphone is set too low (3.16.3).

The owner's Mac had its input volume at 40 of 100. 56 of his last 57 takes
were lifted by the quiet-audio lift (transcribe_whisper.boost_quiet_speech):
his speech arrived at a median -42.5 dBFS, where clear dictation sits near
-20, and was raised by 21.5 dB, up to 30. The lift raises the room's noise
with the speech, and noisy near-silence at the end of a take is what Whisper
turns into "Thank you." Turning the input volume up fixes it at the source,
and only the person should do that: Waffler never changes the volume.

Each take's speech level is kept with its Journal entry (``speech_db``, the
level the lift measures, before it). When the median of the last TAKES of
them is under QUIET_LEVEL_DB, Waffler reads the Mac's input volume (osascript,
READ_TIMEOUT_S, at most once every CHECK_EVERY_S, never raising) and, under
LOW_VOLUME, shows a tip through the notifier (kind TIP_KIND: a tip, so the
Show tips switch, "Don't show again" and a quiet moment all apply, and at most
once a week) with a button that opens Sound settings at Input.

The time of the last reading and what it read are kept in STATE_FILE in the
data folder: a number and a time, nothing anyone said, deleted with "Delete
all my data". On Windows nothing is read and no tip is shown.

Everything the app provides is a hook (Hooks), so the rules are tested
without a Mac, a notifier or a thread.
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, NamedTuple, Optional

try:
    import notifier as _notifier
    import user_messages as _um
    from atomic_json import write_json_atomic
    from data_paths import data_dir as _data_dir
except ImportError:  # imported as src.quiet_mic
    from src import notifier as _notifier
    from src import user_messages as _um
    from src.atomic_json import write_json_atomic
    from src.data_paths import data_dir as _data_dir

TAKES = 10                   # the median is over this many dictations
QUIET_LEVEL_DB = -36.0       # under this median speech level, the microphone is set low
LOW_VOLUME = 70              # the Mac's input volume (0 to 100) under this is low
CHECK_EVERY_S = 24 * 3600.0  # the volume is read at most this often
READ_TIMEOUT_S = 2.0
WAIT_S = 30.0                # how long the tip waits for the dictation to be over
POLL_S = 0.5

TIP_KIND = "quiet_mic"
OPEN_SOUND_ACTION = "open_sound_input"       # the tip's button (app.py _on_toast_action)
SOUND_INPUT_URL = "x-apple.systempreferences:com.apple.preference.sound?input"
STATE_FILE = "mic_volume.json"
STATE_VERSION = 1
TIP_SECONDS = 15

_OSASCRIPT = ["/usr/bin/osascript", "-e", "input volume of (get volume settings)"]


# ── the levels ──────────────────────────────────────────────────────────────

def _level(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def levels_of(history, n: int = TAKES) -> list:
    """The ``speech_db`` of the last ``n`` dictations that have one, oldest
    first. A rewrite or a command is not a dictation."""
    if not isinstance(history, list):
        return []
    out = []
    for e in history:
        if isinstance(e, dict) and e.get("kind") not in ("transform", "command"):
            v = _level(e.get("speech_db"))
            if v is not None:
                out.append(v)
    return out[-n:]


def is_quiet(levels) -> bool:
    """TAKES levels, with a median under QUIET_LEVEL_DB."""
    levels = list(levels)
    return len(levels) >= TAKES and statistics.median(levels[-TAKES:]) < QUIET_LEVEL_DB


# ── the Mac ─────────────────────────────────────────────────────────────────

def read_input_volume(runner=subprocess.run, platform: Optional[str] = None) -> Optional[int]:
    """The Mac's input volume, 0 to 100, or None: not a Mac, an input with no
    volume of its own ("missing value"), a failure or READ_TIMEOUT_S passing.
    Never raises."""
    if (platform or sys.platform) != "darwin":
        return None
    try:
        done = runner(_OSASCRIPT, capture_output=True, text=True, timeout=READ_TIMEOUT_S)
        if done.returncode != 0:
            return None
        value = int(str(done.stdout).strip())
        return value if 0 <= value <= 100 else None
    except Exception:
        return None


def open_sound_settings(runner=subprocess.run) -> bool:
    """Open Sound settings at Input. Never raises."""
    try:
        runner(["/usr/bin/open", SOUND_INPUT_URL], check=False, capture_output=True, timeout=10)
        return True
    except Exception:
        return False


# ── what is kept ────────────────────────────────────────────────────────────

class VolumeLog:
    """The last reading and its time, in STATE_FILE in the data folder (asked
    for at each call), or at ``path``."""

    def __init__(self, path=None):
        self._path = path

    def path(self) -> Path:
        return Path(self._path) if self._path is not None else _data_dir() / STATE_FILE

    def load(self):
        """``(checked_at, volume)``, or None when there is no reading that can
        be used."""
        try:
            raw = json.loads(self.path().read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        at = _level(raw.get("checked_at"))
        vol = raw.get("volume")
        vol = vol if isinstance(vol, int) and not isinstance(vol, bool) and 0 <= vol <= 100 else None
        return None if at is None else (at, vol)

    def save(self, checked_at: float, volume: Optional[int]) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, {"version": STATE_VERSION, "checked_at": checked_at,
                              "volume": volume})

    def clear(self) -> None:
        try:
            self.path().unlink()
        except FileNotFoundError:
            pass


# ── the advisor ─────────────────────────────────────────────────────────────

class Hooks(NamedTuple):
    platform: str                                   # sys.platform
    history: Callable[[], list]                     # the Journal's entries
    could_show: Callable[[str], bool]               # notifier.Notifier.could_show
    quiet: Callable[[], bool]                       # no take recording or being turned into text
    toast_allowed: Callable[[str], bool]            # notifier.Notifier.request, as a yes or no
    show: Callable[..., None]                       # (heading, body, buttons, timeout)
    read_volume: Callable[[], Optional[int]] = read_input_volume
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = lambda line: None
    store: object = None                            # a VolumeLog


class QuietMicAdvisor:
    """Told the level of each take (``take_done``), on its own thread; says
    so when the microphone is set low. Never raises."""

    def __init__(self, hooks: Hooks):
        self.h = hooks
        self._lock = threading.Lock()
        self._levels = deque(maxlen=TAKES)
        self._seeded = False
        self._busy = False
        self._reading = None        # (time, volume): the file's, or ours if it could not be written

    def _store(self):
        return self.h.store if self.h.store is not None else VolumeLog()

    def take_done(self, level_db) -> None:
        """A dictation finished with this speech level (dBFS, or None), and
        its Journal entry is saved. On a Mac, once the last ten takes are
        quiet, read the input volume (at most once a day) and, if it is low,
        show the tip at the next quiet moment. The first call reads the last
        ten levels from the Journal (this take's among them); later ones are
        counted here."""
        try:
            if self.h.platform != "darwin":
                return
            level = _level(level_db)
            with self._lock:
                if not self._seeded:
                    self._seeded = True
                    try:
                        self._levels.extend(levels_of(self.h.history()))
                    except Exception as e:
                        self.h.log(f"[mic] earlier levels not read ({type(e).__name__})")
                elif level is not None:
                    self._levels.append(level)
                if level is None or not is_quiet(self._levels) or self._busy:
                    return
                self._busy = True
            try:
                self._check_and_tell()
            finally:
                with self._lock:
                    self._busy = False
        except Exception as e:
            try:
                self.h.log(f"[mic] volume tip skipped ({type(e).__name__})")
            except Exception:
                pass

    def _check_and_tell(self) -> None:
        if not self.h.could_show(TIP_KIND):
            return
        volume = self._todays_volume()
        if volume is None or volume >= LOW_VOLUME:
            return
        waited = 0.0
        while not self.h.quiet():
            if waited >= WAIT_S:
                return
            self.h.sleep(POLL_S)
            waited += POLL_S
        if not self.h.toast_allowed(TIP_KIND):
            return
        heading, body = _um.quiet_mic_message(volume)
        buttons = [{"label": _um.OPEN_SOUND_SETTINGS, "action": OPEN_SOUND_ACTION,
                    "kind": "primary"}] + _notifier.tip_buttons(TIP_KIND)
        self.h.show(heading, body, buttons, TIP_SECONDS)
        self.h.log(f"[mic] told the person the input volume is low ({volume})")

    def _todays_volume(self) -> Optional[int]:
        """Today's reading, or a new one when the last is a day old (or the
        clock went back). A failed reading counts as one, so it is not tried
        again until tomorrow."""
        store = self._store()
        now = self.h.clock()
        for last in (store.load(), self._reading):
            if last is not None and 0 <= now - last[0] < CHECK_EVERY_S:
                return last[1]
        volume = self.h.read_volume()
        self._reading = (now, volume)
        try:
            store.save(now, volume)
        except Exception as e:
            self.h.log(f"[mic] reading not kept ({type(e).__name__})")
        self.h.log(f"[mic] dictations are quiet; input volume read: "
                   f"{'unknown' if volume is None else volume}")
        return volume

    def forget(self) -> None:
        """Delete my data: the levels, and the reading."""
        with self._lock:
            self._levels.clear()
            self._seeded = False
            self._reading = None
        try:
            self._store().clear()
        except Exception as e:
            self.h.log(f"[mic] reading not deleted ({type(e).__name__})")
