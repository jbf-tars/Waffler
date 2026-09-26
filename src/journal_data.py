"""The Journal's numbers and pages, worked out once per change to history.json.

The window used to ask for the whole history (and rescan it for the
counts) on every dictation, every search keystroke and every Settings
visit: at 3,290 entries that was about half a second each time, and it
grew with every day of use. Now:

- ``HistoryCache`` keeps the parsed file and its counts until the file
  changes (its size or modified time), so repeated calls cost nothing.
- ``page`` returns one page of entries, newest first, optionally only
  those matching a search, so the window draws about 50 cards at a time.
- ``compute_stats`` is the one place the counts are worked out: today, this
  week, this month, all time, and the day streak.

Nothing here touches the network or the window.
"""

from __future__ import annotations

import os
import threading
from datetime import date, timedelta


def _day(ts) -> date | None:
    """The calendar day of a history timestamp ("2026-09-25T16:31:02")."""
    s = str(ts or "")
    if len(s) < 10:
        return None
    try:
        y, m, d = s[:10].split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _words(item: dict) -> int:
    # A Not sent entry holds a note, not the user's words: it adds none.
    if item.get("failed"):
        return 0
    return len((item.get("styled") or item.get("text") or "").split())


def compute_stats(history: list, today: date) -> dict:
    """Counts for the Journal strip and Settings, Usage.

    ``today_count`` counts every entry made today, as it always has; the
    week and month counts leave out Not sent entries, which are not
    dictations yet. The week starts on Monday.

    Streak: consecutive days, ending today, with at least one entry. If
    today has none yet, yesterday anchors it, so a streak doesn't snap to
    0 at midnight before the first dictation of the day.
    """
    week_start = today - timedelta(days=today.weekday())
    out = {
        "today_words": 0, "today_count": 0,
        "week_words": 0, "week_count": 0,
        "month_words": 0, "month_count": 0,
        "total_words": 0, "total_count": 0,
        "entries": 0, "streak_days": 0,
    }
    days = set()
    for h in history:
        if not isinstance(h, dict):
            continue
        out["entries"] += 1
        d = _day(h.get("timestamp"))
        w = _words(h)
        failed = bool(h.get("failed"))
        out["total_words"] += w
        if not failed:
            out["total_count"] += 1
        if d is None:
            continue
        days.add(d)
        if d == today:
            out["today_words"] += w
            out["today_count"] += 1
        if not failed and week_start <= d <= today:
            out["week_words"] += w
            out["week_count"] += 1
        if not failed and d.year == today.year and d.month == today.month:
            out["month_words"] += w
            out["month_count"] += 1
    cursor = today if today in days else today - timedelta(days=1)
    while cursor in days:
        out["streak_days"] += 1
        cursor -= timedelta(days=1)
    return out


def matches(item: dict, query: str) -> bool:
    """The Journal search: the clean text and the transcript, any case."""
    q = str(query or "").strip().lower()
    if not q:
        return True
    hay = ((item.get("styled") or "") + " " + (item.get("text") or "")).lower()
    return q in hay


def page(history: list, limit=None, offset=0, query: str = "") -> list:
    """Entries newest first (history.json is oldest first).

    ``limit`` None means all of them. ``offset`` counts from the newest
    matching entry. With a ``query``, only matching entries are counted.
    """
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = None if limit is None else max(0, int(limit))
    except (TypeError, ValueError):
        limit = None
    out = []
    skipped = 0
    for item in reversed(history):
        if not isinstance(item, dict) or not matches(item, query):
            continue
        if skipped < offset:
            skipped += 1
            continue
        if limit is not None and len(out) >= limit:
            break
        out.append(item)
    return out


class HistoryCache:
    """history.json parsed once, and its stats, until the file changes.

    ``load`` is the function that reads the file (app.py load_history).
    The cache is keyed on the file's size and modified time, so a write
    from anywhere (a dictation, Try again, Delete) is picked up on the next
    call. Callers get the cached list itself: they must not change it.
    """

    def __init__(self, path, load):
        self._path = path
        self._load = load
        self._lock = threading.Lock()
        self._sig = None
        self._items: list = []
        self._stats_key = None
        self._stats: dict | None = None
        self.loads = 0      # how many times the file was read (for tests)

    def _signature(self):
        try:
            st = os.stat(self._path)
            # history.json is replaced whole on every write (write_json_atomic),
            # so the file's identity changes too, not only its time.
            return (st.st_size, st.st_mtime_ns, st.st_ino)
        except OSError:
            return None

    def items(self) -> list:
        with self._lock:
            sig = self._signature()
            if sig is None or sig != self._sig:
                self._items = self._load() if sig is not None else []
                self._sig = sig
                self._stats_key = None
                self.loads += 1
            return self._items

    def stats(self, today: date) -> dict:
        items = self.items()
        with self._lock:
            key = (self._sig, today)
            if self._stats is None or self._stats_key != key:
                self._stats = compute_stats(items, today)
                self._stats_key = key
            return dict(self._stats)
