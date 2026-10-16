"""Double-tap to go hands-free: tap the dictation hotkey twice, quickly, and
the take locks on, as if Space had been tapped while holding it.

Off by default. Settings, Shortcuts has the switch (``double_tap_handsfree`` in
settings.json, a true or a false). Off, neither listener (src/smart_hotkey.py on
a Mac, src/windows_hotkey.py on Windows) asks this module anything.

What it decides, and what it leaves alone
-----------------------------------------
Nothing is held back. A tap of the hotkey does what it has always done: the
recording starts at once, and letting go stops it, drops it if it caught no
speech (3.14.91: a quick "Yes" is still written down) and, once in a while,
says "Don't tap, hold". Waiting for a second tap before stopping would delay
exactly the quick answers 3.14.91 stopped throwing away, so the first tap is
not waited on.

The second tap is what changes. If a press ends within TAP_MAX_S of the
key going down (a tap), and the one before it was also a tap that ended no
more than WINDOW_S earlier, then at that second release the listener does not
stop the take: it keeps it recording and locks it on, the same state Space
gives (the same lock cue, the same hands-free pill, Esc cancels, and a press
of the hotkey ends it). A second press that is held, however soon it comes,
is the ordinary push-to-talk it always was: tap then hold is not a double-tap.

Chatter: two edges closer together than BOUNCE_S are one switch bouncing, not
two taps, and a press that close after a release does not end a hands-free
take. The Mac's Fn key already absorbs chatter below 150 ms before the
listener hears of it (src/mac_hotkey_monitor.py), and says its release that
much late; ``release_lag_s`` is how late, so a tap is measured as the finger
made it.

Pure: the clock is injected, no key is read and no thread is started.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

SETTING = "double_tap_handsfree"

# The second tap's key-down must come within this long of the first tap's
# key-up. Tunable.
WINDOW_S = 0.35
# A press is a tap when the key was down for no longer than this. Tunable: a
# deliberate double-tap is two presses of about a tenth of a second.
TAP_MAX_S = 0.30
# Two edges this close are one bouncing switch, not two taps.
BOUNCE_S = 0.04


def enabled(stored) -> bool:
    """Is the setting on in this settings.json? Off unless it is exactly
    true: anything missing, unreadable or of another kind is off."""
    return isinstance(stored, dict) and stored.get(SETTING) is True


def touches(payload) -> bool:
    return isinstance(payload, dict) and SETTING in payload


def settings_problem(payload) -> Optional[str]:
    """A plain sentence when ``payload`` (what a save is called with) names
    the setting with anything but a true or a false, else None."""
    if touches(payload) and not isinstance(payload[SETTING], bool):
        try:
            import user_messages
        except ImportError:  # imported as src.double_tap
            from src import user_messages
        return user_messages.DOUBLE_TAP_UNKNOWN
    return None


class DoubleTapGate:
    """Remembers the last tap, and says whether a press that has just ended
    was the second of two.

    The listener calls ``press()`` when a key-down starts a take, and
    ``release()`` when the key comes up while that take is a plain hold;
    ``release()`` answers True when the take should be locked on instead of
    stopped. ``reset()`` forgets everything (Esc, a take ended by the app, a
    hands-free take locked another way). ``is_bounce()`` is for the press that
    would end a hands-free take: True when it is only the last release
    bouncing. All of it is behind one lock: the Mac's callbacks each run on a
    thread of their own.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, *,
                 window_s: float = WINDOW_S, tap_max_s: float = TAP_MAX_S,
                 bounce_s: float = BOUNCE_S, release_lag_s: float = 0.0):
        self._clock = clock
        self._window = float(window_s)
        self._tap_max = float(tap_max_s)
        self._bounce = float(bounce_s)
        self._lag = max(0.0, float(release_lag_s))
        self._lock = threading.Lock()
        self._pressed_at: Optional[float] = None       # a take's key is down since
        self._tap_up_at: Optional[float] = None         # the last tap came up at (physical)
        self._last_up_at: Optional[float] = None        # the last release of any kind (physical)

    def press(self) -> None:
        with self._lock:
            self._pressed_at = self._clock()

    def release(self) -> bool:
        """The key of a plain hold has come up. True when this makes two taps,
        and the take should lock on rather than stop."""
        with self._lock:
            now = self._clock()
            up = now - self._lag                       # when the finger left the key
            pressed, self._pressed_at = self._pressed_at, None
            if pressed is None:
                return False
            self._last_up_at = up
            if up - pressed > self._tap_max:           # held: push to talk, nothing remembered
                self._tap_up_at = None
                return False
            first, self._tap_up_at = self._tap_up_at, up
            if first is None:
                return False
            gap = pressed - first
            if self._bounce <= gap <= self._window:
                self._tap_up_at = None                 # the two are used up
                return True
            return False

    def is_bounce(self) -> bool:
        """Is a press now only the last release bouncing?"""
        with self._lock:
            if self._last_up_at is None:
                return False
            return 0.0 <= self._clock() - self._last_up_at < self._bounce

    def reset(self) -> None:
        with self._lock:
            self._pressed_at = self._tap_up_at = self._last_up_at = None
