"""Tiny "at most once every N seconds, per key" gate.

Used for toasts that must not nag (failed paste, missing permission). The
notifier (roadmap S3) replaces this with one governor for every toast; until
then this keeps the rule in one tested place instead of ad-hoc timestamps in
app.py.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict


class RateLimiter:
    """``allow(key)`` is True at most once per ``min_gap`` seconds for each key.

    The clock is injectable so tests never sleep. Thread-safe: toasts can be
    raised from the hotkey thread, the pipeline thread and the menu callbacks.
    """

    def __init__(self, min_gap: float, clock: Callable[[], float] = time.monotonic):
        self.min_gap = float(min_gap)
        self._clock = clock
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: str = "") -> bool:
        """Return True and start the gap if ``key`` may fire now."""
        now = self._clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and (now - last) < self.min_gap:
                return False
            self._last[key] = now
            return True

    def reset(self, key: str = None) -> None:
        """Forget one key (or all keys when ``key`` is None)."""
        with self._lock:
            if key is None:
                self._last.clear()
            else:
                self._last.pop(key, None)
