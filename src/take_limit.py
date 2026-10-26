"""How long one take may be, and the minute's warning before it ends.

One take is at most 20 minutes (it was 12). At 19 minutes Waffler says, once,
that a minute is left; at 20 it stops the recording itself and turns what was
said into text exactly as if the person had let go of the key, so nothing is
lost.

Why 20 and not more: speech to text takes the whole recording in one piece up
to 18 MB (a bit over 9 minutes at the 32 KB a second Waffler records at), and
cuts anything bigger at a pause into pieces of about two minutes
(transcribe_whisper._split_audio_on_silence). Splitting by length used to cut
words off (3.14.71 to 3.14.97), so there is still no chunking while recording
and no cut by time: only a recording too big for one upload is split, at
silence, after it ends. 20 minutes is a 38 MB recording, ten pieces.

The constants here are the one place the limit is written; the recording loop,
the messages and the tests all read them. The clock is injected, so the
warning and the stop are tested without waiting.

Who enforces it
---------------
``TakeTimer`` does, on a thread of its own, started when the key goes down and
before anything that touches the microphone. The limit used to be checked by the
loop that feeds the pill its level, which was started only after the recorder
had begun: on a Mac whose audio system had wedged the recorder never began, and
two hands-free takes ran for 670 s and 1,760 s with nothing to stop them. The
timer reads the clock and whether this is still the current take, and nothing
else, so it holds whatever the recorder, the pill or the microphone is doing.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

# The longest one take can be, and how long before it the warning comes.
MAX_TAKE_S = 20 * 60
WARN_BEFORE_S = 60
WARN_AT_S = MAX_TAKE_S - WARN_BEFORE_S

# How often the timer looks at the clock. The limit is enforced to within this.
TICK_S = 1.0

# The recording is 16 kHz, 16-bit, mono: 32,000 bytes a second. Used for the
# size of a take at the limit (the splitter's gate is in bytes).
BYTES_PER_SECOND = 32000

NOTHING, WARN, STOP = "nothing", "warn", "stop"


def minutes(seconds: float = MAX_TAKE_S) -> int:
    """The limit in whole minutes (20), for the sentences that name it."""
    return int(seconds // 60)


class TakeWatch:
    """What to do about the length of one take, asked once a tick.

    ``check()`` answers ``WARN`` once (the first time 19 minutes have gone),
    ``STOP`` once (the first time 20 have), and ``NOTHING`` otherwise. A take
    whose clock jumps past the limit (the computer slept) is stopped without a
    warning that would arrive after the end. ``started`` is the clock's time
    when the recording began; ``clock`` defaults to wall time, as the
    recording's start was stamped.
    """

    def __init__(self, started: float, clock: Callable[[], float] = time.time,
                 max_s: float = MAX_TAKE_S, warn_before_s: float = WARN_BEFORE_S):
        self._started = float(started)
        self._clock = clock
        self._max = float(max_s)
        self._warn_at = float(max_s) - float(warn_before_s)
        self._warned = False
        self._stopped = False

    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started)

    def check(self) -> str:
        if self._stopped:
            return NOTHING
        elapsed = self.elapsed()
        if elapsed >= self._max:
            self._stopped = True
            return STOP
        if elapsed >= self._warn_at and not self._warned:
            self._warned = True
            return WARN
        return NOTHING


class TakeTimer:
    """Enforces the limit for one take, asked every ``tick_s`` seconds on a
    daemon thread (``tick()`` is public so a test can turn the clock and call
    it). Calls ``on_warn()`` once at the warning and ``on_stop()`` once at the
    limit, then finishes; finishes quietly when ``is_current()`` says the take
    is over (the key came up, another take began) or ``cancel()`` is called.
    A callback that raises is logged nowhere and stops nothing: the next one
    still comes.
    """

    def __init__(self, watch: TakeWatch, on_warn: Callable[[], None], on_stop: Callable[[], None],
                 is_current: Callable[[], bool] = lambda: True, tick_s: Optional[float] = None,
                 autostart: bool = True):
        self._watch = watch
        self._on_warn = on_warn
        self._on_stop = on_stop
        self._is_current = is_current
        self._tick_s = float(TICK_S if tick_s is None else tick_s)
        self._cancel = threading.Event()
        self._finished = False
        self.thread: Optional[threading.Thread] = None
        if autostart:
            self.start()

    @property
    def finished(self) -> bool:
        return self._finished

    def start(self) -> None:
        try:
            self.thread = threading.Thread(target=self._run, daemon=True, name="TakeLimit")
            self.thread.start()
        except RuntimeError:
            # Out of threads: the press carries on without a limit rather than
            # failing; the process is in worse trouble than this.
            self.thread = None

    def cancel(self) -> None:
        self._cancel.set()

    def tick(self) -> str:
        """One look at the take. Returns what it did: ``WARN``, ``STOP`` or
        ``NOTHING``."""
        if self._finished:
            return NOTHING
        if self._cancel.is_set() or not self._safe(self._is_current, True):
            self._finished = True
            return NOTHING
        step = self._watch.check()
        if step == WARN:
            self._safe(self._on_warn)
        elif step == STOP:
            self._finished = True
            self._safe(self._on_stop)
        return step

    @staticmethod
    def _safe(fn, default=None):
        try:
            result = fn()
            return default if result is None and default is not None else result
        except Exception:
            return default

    def _run(self) -> None:
        try:
            while not self._cancel.wait(self._tick_s):
                self.tick()
                if self._finished:
                    return
        finally:
            self._finished = True
