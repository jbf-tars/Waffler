"""The check on the microphone while a take is being recorded (S12).

A recording that has gone wrong used to be found out only after the key came
up: the finished audio was measured (app.py ``_process``), found to be digital
silence, and the person was told to try again. By then they had spoken for
however long they spoke. This watches the take as it happens, from the
recorder's own account of its input (``AudioRecorder.health``), so the
common failures are met while there is still something to do about them:

  - the input delivers nothing, or only blocks of exact zeros, for
    ``REOPEN_AFTER_S`` (5 s): the stream is opened again, once in a take.
    A stream that went dead after sleep, or a USB device that glitched, often
    comes back on a new one.
  - still nothing at ``NOTIFY_AFTER_S`` (15 s): say so, with a button to
    choose another microphone. Once in a take. If sound comes back the
    message is withdrawn.
  - the microphone left (the stream stopped, or nothing arrives and the
    system no longer lists the device): ``LOST``. The take is ended and
    turned into text with what there is.

A microphone with a noise gate (Windows Voice Focus, a headset's own DSP) sends
exact zeros whenever nobody speaks, which is the same fact as a dead stream.
The recorder notes when a stream has sent sound after a long run of zeros
(``health["gated"]``), and from then on a pause on it is a pause: no re-open
(it would cost a third of a second of someone's first word) and no message.
A stream that is dead and always was is never flagged, so it is still caught.

One mechanism with the checks that already exist, not a second: the
definition of "dead" is the one ``quality.mic_dropout_signal`` and the
post-hoc dead-stream check use (a run of exact zeros, never a loudness
threshold: 3.15.1 lifts quiet speech, and a quiet voice is not silence), the
rebuild is ``AudioRecorder``'s own stream teardown, and the finished take is
still measured afterwards as the safety net. What this adds is only that the
same fact is noticed seconds in rather than after.

Pure and clock-injected. ``check`` is given the health dict and returns at
most one event; the caller does what the event says and, for a re-open, tells
the watch when it is finished (``reopen_finished``), so the 15 s message is
never raised in the moments a re-open is still working.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

# Digital silence (or no callbacks) this long: open the stream again, once.
REOPEN_AFTER_S = 5.0
# Still silent this long: tell the person. Once.
NOTIFY_AFTER_S = 15.0
# After a re-open, this long for sound to come back before the message.
SETTLE_AFTER_REOPEN_S = 1.5
# Sound this recent after the message means it has come back.
RECOVER_QUIET_S = 1.0
# Nothing has arrived for this long and the system no longer lists the device.
LOST_STALL_S = 0.75
# The stream is not running for this long (not counting a re-open).
STREAM_GONE_S = 0.6

REOPEN, NOTIFY, LOST, RECOVERED = "reopen", "notify", "lost", "recovered"


class MicWatch:
    """The state of one take's check. A new one for each take, so each take
    has its own single re-open and its own single message."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.reopen_started = False
        self.reopen_done_at: Optional[float] = None
        self.notified = False
        self.recovered = False
        self.lost = False
        self._stream_bad_since: Optional[float] = None

    def reopen_finished(self) -> None:
        """The re-open the watch asked for has ended, well or not."""
        self.reopen_done_at = self._clock()

    def check(self, health: dict, device_present: Optional[bool] = None) -> Optional[str]:
        """What to do now, if anything: ``REOPEN``, ``NOTIFY``, ``LOST`` or
        ``RECOVERED``. ``device_present`` is whether the system still lists the
        microphone the stream is on: True, False, or None when it cannot say
        (then only the stream itself can say the microphone is gone)."""
        if self.lost or not isinstance(health, dict) or not health.get("recording"):
            return None
        if health.get("reopening"):
            self._stream_bad_since = None
            return None
        now = self._clock()

        # The microphone left. A stream that has stopped running says so
        # itself; one that has gone quiet is "gone" only when the system also
        # no longer lists the device (a list that cannot be read is not proof).
        if not health.get("stream_ok", True):
            if self._stream_bad_since is None:
                self._stream_bad_since = now
            if now - self._stream_bad_since >= STREAM_GONE_S:
                self.lost = True
                return LOST
        else:
            self._stream_bad_since = None
        if device_present is False and health.get("stall_s", 0.0) >= LOST_STALL_S:
            self.lost = True
            return LOST

        quiet = float(health.get("quiet_s", 0.0))
        if self.notified and not self.recovered and quiet < RECOVER_QUIET_S:
            self.recovered = True
            return RECOVERED
        # A noise-gated microphone sends exact zeros between words, so a pause
        # is not a dead stream (the recorder has seen it send sound after a
        # long run of zeros). The finished take is still measured afterwards.
        if health.get("gated"):
            return None
        if quiet >= REOPEN_AFTER_S and not self.reopen_started:
            self.reopen_started = True
            return REOPEN
        if (quiet >= NOTIFY_AFTER_S and not self.notified and self.reopen_started
                and self.reopen_done_at is not None
                and now - self.reopen_done_at >= SETTLE_AFTER_REOPEN_S):
            self.notified = True
            return NOTIFY
        return None
