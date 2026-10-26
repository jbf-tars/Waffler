"""What Waffler does when macOS's audio system stops answering.

The problem
-----------
A capture of a wedged Waffler (a macOS ``sample``) showed a deadlock inside
CoreAudio: one thread in ``AudioOutputUnitStop`` waiting on a lock, and
CoreAudio's own I/O thread, which holds it, waiting on a lock the first thread
holds. After that every call into CoreAudio from this process blocks for ever
(``coreaudiod`` itself was healthy). It covers the whole process, so nothing
inside Waffler can free it: the microphone is gone until the process is.

src/audio_guard.py and src/audio.py make sure no part of Waffler hangs on it
(every call into PortAudio and CoreAudio is bounded, the locks are let go, a
stop still returns what was recorded) and tell whoever registered, once, that
audio has wedged. This module is that someone.

What it does
------------
On a Mac, in the installed app, it:

  0. looks for ``CONFIRM_S`` seconds longer to see whether CoreAudio answers
     after all: a call that takes several seconds and returns (a Bluetooth
     headset changing profile) is slow, not deadlocked, and does not cost a
     restart. The recorder gives up on a call after 3 to 5 s so its locks are
     let go; this decides whether it was a deadlock;
  1. waits for a dictation that is being turned into text to finish (up to
     ``BUSY_WAIT_S``; it needs no audio), unless a take is being recorded;
  2. keeps what was being recorded, read from the recorder's buffer alone
     (``salvage``) and saved as a Not sent recording (``keep``), so the
     dictation is not lost, and sent again when Waffler is back;
  3. says, in plain words, that it is restarting (``show``);
  4. starts a small detached shell that waits for this process to be gone,
     ends it if it will not go, and opens the app again (``relaunch_argv``);
  5. ends the process with ``os._exit``. A normal shutdown would hang in the
     same audio teardown. The kernel drops the single-instance lock
     (src/single_instance.py, a flock) when the process ends, so the new one is
     not blocked by it.

At most ``MAX_RESTARTS`` restarts in ``WINDOW_S`` seconds, counted in a tiny file
in the data folder (``audio_restarts.json``, times only), so a Mac whose audio
stays broken is not restarted in a loop: after that the message says to quit
and open Waffler again. On Windows (a different audio system) and wherever the
app is not an installed bundle, nothing is restarted: Windows shows the message,
and a development run only writes a line in the log.

Everything with a side effect is passed in, so the tests restart nothing.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional

try:
    import audio_guard
    import user_messages
    from atomic_json import write_json_atomic
except ImportError:  # imported as src.wedge_recovery
    from src import audio_guard, user_messages
    from src.atomic_json import write_json_atomic

# What was decided.
RESTART, TOAST_ONLY, LOG_ONLY, ALREADY, CLEARED = "restart", "toast", "log", "already", "cleared"

FILE = "audio_restarts.json"
VERSION = 1
# At most this many automatic restarts in any WINDOW_S seconds.
MAX_RESTARTS = 2
WINDOW_S = 600.0
# The most times kept in the file, so a damaged one cannot grow.
MAX_KEPT = 10
# A time later than now by more than this is not a time that was written by
# this computer's clock: it is ignored, so a clock that was wrong once cannot
# hold restarts off for ever.
FUTURE_SLACK_S = 60.0

# How long the wedge is looked at again before anything is done about it, and how
# often. A call that is still not back after the recorder's own bound (3 to 5 s)
# and this much more is not coming back: the deadlock never returns.
CONFIRM_S = 6.0
CONFIRM_POLL_S = 0.25
# The helper waits this long before it opens the app again: the process is
# gone in milliseconds, but Launch Services and coreaudiod need a moment to
# let go of it.
RELAUNCH_DELAY_S = 2.0
# How long a message is given to be drawn before the process ends.
SETTLE_S = 0.8
# A dictation being turned into text is given this long to finish first, looked
# at this often. It needs no audio; ending it would leave a Recovered card to
# be retried by hand.
BUSY_WAIT_S = 25.0
BUSY_POLL_S = 0.5
# Reading the recording and saving it is a read of memory and one file: it is
# bounded anyway, so a disk that does not answer cannot hold the restart.
KEEP_TIMEOUT_S = 6.0
SHOW_TIMEOUT_S = 2.0


def app_bundle(executable) -> Optional[str]:
    """The ``.app`` a frozen Mac executable lives in:
    ``/Applications/Waffler.app/Contents/MacOS/Waffler`` gives
    ``/Applications/Waffler.app``. None for anything else."""
    if not executable or not isinstance(executable, str):
        return None
    parts = PurePosixPath(executable).parts
    for i, part in enumerate(parts):
        if part.endswith(".app") and parts[i + 1:i + 3] == ("Contents", "MacOS") and len(parts) > i + 3:
            return str(PurePosixPath(*parts[:i + 1]))
    return None


def decide(platform: str, frozen: bool, bundle: Optional[str], recent_restarts: int) -> str:
    """``RESTART``, ``TOAST_ONLY`` or ``LOG_ONLY`` for this process."""
    if not frozen:
        return LOG_ONLY
    if platform != "darwin" or not bundle:
        return TOAST_ONLY
    if recent_restarts >= MAX_RESTARTS:
        return TOAST_ONLY
    return RESTART


# ── the restarts so far ──────────────────────────────────────────────────────

class RestartLog:
    """``audio_restarts.json``: the times (seconds since 1970) of the restarts
    made lately, and nothing else:

        {"version": 1, "restarts": [1790000000.0, 1790000300.5]}

    A file that is not what was written (cut short, empty, edited) counts as no
    restarts and is written over. Never raises."""

    def __init__(self, path, clock: Callable[[], float] = time.time):
        self._path = Path(path)
        self._clock = clock

    def recent(self, now: Optional[float] = None) -> List[float]:
        now = self._clock() if now is None else now
        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return []
        raw = data.get("restarts") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return []
        out = []
        for t in raw:
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
                continue
            if now - WINDOW_S < t <= now + FUTURE_SLACK_S:
                out.append(float(t))
        return sorted(out)[-MAX_KEPT:]

    def record(self, now: Optional[float] = None) -> bool:
        now = self._clock() if now is None else now
        try:
            times = (self.recent(now) + [now])[-MAX_KEPT:]
            write_json_atomic(self._path, {"version": VERSION, "restarts": times}, indent=None)
            return True
        except Exception:
            return False


# ── opening the app again ────────────────────────────────────────────────────

# Run by /bin/sh after this process has gone. The arguments follow the script:
#   $1 seconds to wait first   $2 the pid to wait for   $3 the app bundle
#   $4 what opens it           $5 how many tenths of a second to wait for the pid
# Nothing is spliced into the script, so no character in a path can change it.
# The pid is polled (not just waited out): ``open`` asked for while the old
# process still counts as running can be collapsed into "bring it to the
# front" and open nothing (the case app.py restart_app was written for). A
# process that has not gone by then is ended, as the audio deadlock can leave
# one that does not exit.
_RELAUNCH_SCRIPT = (
    'sleep "$1"\n'
    'i=0\n'
    'while kill -0 "$2" 2>/dev/null && [ "$i" -lt "$5" ]; do sleep 0.1; i=$((i+1)); done\n'
    'if kill -0 "$2" 2>/dev/null; then kill -9 "$2" 2>/dev/null; sleep 0.3; fi\n'
    'exec "$4" -n -a "$3"\n'
)


def relaunch_argv(bundle: str, pid: int, delay_s: float = RELAUNCH_DELAY_S,
                  opener: str = "/usr/bin/open", max_polls: int = 30) -> List[str]:
    return ["/bin/sh", "-c", _RELAUNCH_SCRIPT, "waffler-relaunch",
            f"{float(delay_s):g}", str(int(pid)), str(bundle), str(opener), str(int(max_polls))]


def spawn_detached(argv: List[str]) -> None:
    """Start ``argv`` in a session of its own, so it outlives this process."""
    subprocess.Popen(argv, start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def exit_now(code: int = 0) -> None:
    """End this process at once. Not ``sys.exit``: that runs the same audio
    teardown that is stuck."""
    os._exit(code)


# ── the recovery ─────────────────────────────────────────────────────────────

class WedgeRecovery:
    """One per process; ``handle(op)`` is told that audio has wedged (``op``
    names the call, for the log) and acts once, whoever tells it, however often.

    ``take_in_progress()``  a take is being recorded now
    ``busy()``              a dictation is being turned into text
    ``salvage()``           the take so far as WAV bytes: ``b""`` if there is
                            none, None if it cannot be read (AudioRecorder.salvage_wav)
    ``keep(wav)``           save it as a Not sent recording; True if it was
    ``show(heading, body)`` put a message on screen
    ``still_stuck()``       CoreAudio has still not answered (False once the
                            call that was given up on has come back)
    ``on_cleared()``        it did answer after all: forget the wedge
    """

    def __init__(self, *, platform: str, frozen: bool, executable: str, state_path,
                 take_in_progress: Callable[[], bool], busy: Callable[[], bool],
                 salvage: Callable[[], Optional[bytes]], keep: Callable[[bytes], bool],
                 show: Callable[[str, str], None],
                 spawn: Callable[[List[str]], None] = spawn_detached,
                 exit_now: Callable[[int], None] = exit_now,
                 still_stuck: Callable[[], bool] = lambda: True,
                 on_cleared: Callable[[], None] = lambda: None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time,
                 log: Callable[[str], None] = print,
                 pid: Optional[int] = None):
        self._platform = platform
        self._frozen = bool(frozen)
        self._executable = executable
        self._state_path = Path(state_path)
        self._take = take_in_progress
        self._busy = busy
        self._salvage = salvage
        self._keep = keep
        self._show = show
        self._spawn = spawn
        self._exit = exit_now
        self._still_stuck = still_stuck
        self._on_cleared = on_cleared
        self._sleep = sleep
        self._clock = clock
        self._log = log
        self._pid = os.getpid() if pid is None else pid
        self._lock = threading.Lock()
        self._handled = False

    def _say(self, line: str) -> None:
        try:
            self._log(line)
        except Exception:
            pass

    def handle(self, op: str) -> str:
        """Act on a wedge. Returns what was decided (``ALREADY`` if it had
        been told before). Never raises."""
        with self._lock:
            if self._handled:
                return ALREADY
            self._handled = True
        try:
            return self._handle(op)
        except Exception as e:
            self._say(f"[wedge] recovery failed ({type(e).__name__})")
            return TOAST_ONLY

    def _handle(self, op: str) -> str:
        now = self._clock()
        history = RestartLog(self._state_path, self._clock)
        bundle = app_bundle(self._executable) if self._platform == "darwin" else None
        decision = decide(self._platform, self._frozen, bundle, len(history.recent(now)))
        self._say(f"[wedge] audio stopped answering in {op}: {decision}")

        if decision == LOG_ONLY:
            self._say("[wedge] a development run: not restarting, and no message is shown")
            return LOG_ONLY
        if not self._confirmed():
            self._say(f"[wedge] CoreAudio answered again ({op}): it was slow, not stuck")
            try:
                self._on_cleared()
            except Exception:
                pass
            with self._lock:
                self._handled = False            # a later wedge is a new one
            return CLEARED
        if decision == TOAST_ONLY:
            self._toast(False, None)
            return TOAST_ONLY

        kept = self._quiet_then_keep()
        history.record(self._clock())
        self._toast(True, kept)
        self._sleep(SETTLE_S)
        try:
            self._spawn(relaunch_argv(bundle, self._pid))
        except Exception as e:
            # Ending a process that nothing will open again would leave the
            # person with no Waffler at all.
            self._say(f"[wedge] the helper could not be started ({type(e).__name__}): staying open")
            self._toast(False, None)
            return TOAST_ONLY
        self._say("[wedge] restarting Waffler")
        self._exit(0)
        return RESTART

    # ── the steps ──

    def _confirmed(self) -> bool:
        """Still stuck after CONFIRM_S more seconds? Looks first, so a call that
        has already come back costs no wait, and ends the wait as soon as it
        does. A check that raises counts as stuck."""
        waited = 0.0
        while True:
            try:
                if not self._still_stuck():
                    return False
            except Exception:
                pass
            if waited >= CONFIRM_S:
                return True
            self._sleep(CONFIRM_POLL_S)
            waited += CONFIRM_POLL_S

    def _asked(self, fn) -> bool:
        try:
            return bool(fn())
        except Exception:
            return False

    def _quiet_then_keep(self) -> Optional[bool]:
        """Wait (a little) for a dictation that is being turned into text, then
        keep a take that is being recorded. ``None``: there was no take to
        keep; True: it is kept (or there was nothing in it); False: it could
        not be."""
        waited = 0.0
        while waited < BUSY_WAIT_S and not self._asked(self._take) and self._asked(self._busy):
            self._sleep(BUSY_POLL_S)
            waited += BUSY_POLL_S
        if not self._asked(self._take):
            return None
        done, value = audio_guard.run_bounded(self._read_and_keep, KEEP_TIMEOUT_S,
                                              "keep the recording", skip_if_wedged=False)
        if not done or isinstance(value, BaseException):
            self._say("[wedge] the recording in progress could not be saved")
            return False
        return bool(value)

    def _read_and_keep(self) -> bool:
        wav = self._salvage()
        if wav is None:
            self._say("[wedge] the recording in progress could not be read")
            return False
        if not wav:
            return True                      # nothing recorded yet: nothing lost
        saved = bool(self._keep(wav))
        self._say("[wedge] the recording in progress was kept as Not sent" if saved
                  else "[wedge] the recording in progress was not saved")
        return saved

    def _toast(self, restarting: bool, kept: Optional[bool]) -> None:
        heading, body = user_messages.audio_wedged_message(self._platform, restarting, kept)
        audio_guard.run_bounded(lambda: self._show(heading, body), SHOW_TIMEOUT_S,
                                "wedge message", skip_if_wedged=False)
