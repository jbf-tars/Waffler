"""Finding, removing and putting back Journal entries.

app.py does the file work under its history lock; this holds the rules, so
they can be tested without a window.

Finding an entry. Entries saved with an ``id`` (see src/history_ids.py) are found by it.
Older ones have none and are found by their time and words together
(``timestamp`` and ``text``); two takes in the same second with the same words
are the same to this, and the newest is the one that is found.

Undo. Deleting an entry (or redoing a take) takes effect at once, on disk. The
shelf keeps what was removed, in memory only, for a short while so the window's
"Undo" can put it back: the entry, and the recording kept with it (so
"Redo this take" still works afterwards). Nothing on the shelf is ever
written to disk, and it is emptied after ``WINDOW_S`` seconds or when the app
quits.
"""

from __future__ import annotations

import secrets
import threading
import time

try:
    from history_ids import parse as _parse_id
except ImportError:  # imported as src.history_ops
    from src.history_ids import parse as _parse_id

# The window shows "Undo" for 10 seconds; this is a little longer so a click
# at the last moment still works.
WINDOW_S = 15.0
SHELF_LIMIT = 8                    # entries kept at once
SHELF_AUDIO_LIMIT = 64 * 1024 * 1024    # bytes of recordings kept at once

DELETED = "deleted"
REPLACED = "replaced"


def find(history, item_id=None, timestamp="", text="") -> int:
    """Index of the entry, or -1. By ``item_id`` when one is given (and then
    only by it: an id that is not there is not found), else by ``timestamp``
    and ``text`` together. Never matches a Not sent entry by words (it has
    none): those have their own delete."""
    wanted = _parse_id(item_id)
    if wanted is not None:
        for i in range(len(history) - 1, -1, -1):
            h = history[i]
            if isinstance(h, dict) and h.get("id") == wanted:
                return i
        return -1
    if item_id not in (None, "") or not (isinstance(timestamp, str) and timestamp):
        return -1
    text = text if isinstance(text, str) else ""
    for i in range(len(history) - 1, -1, -1):
        h = history[i]
        if (isinstance(h, dict) and not h.get("failed")
                and str(h.get("timestamp", "")) == timestamp
                and (h.get("text") or "") == text):
            return i
    return -1


def insert_in_order(history: list, entry: dict) -> int:
    """Put ``entry`` back where its time says: after the newest entry that is
    not later than it. Returns the index. (history.json is oldest first.)"""
    when = str(entry.get("timestamp", ""))
    i = len(history)
    while i > 0:
        h = history[i - 1]
        if isinstance(h, dict) and str(h.get("timestamp", "")) <= when:
            break
        i -= 1
    history.insert(i, entry)
    return i


class UndoShelf:
    """What can still be undone: removed entries, held in memory."""

    def __init__(self, window_s: float = WINDOW_S, limit: int = SHELF_LIMIT,
                 audio_limit: int = SHELF_AUDIO_LIMIT, clock=time.monotonic,
                 timer=True):
        self.window_s = window_s
        self.limit = limit
        self.audio_limit = audio_limit
        self._clock = clock
        self._timer = timer
        self._lock = threading.Lock()
        self._records: dict = {}        # token -> record, oldest first

    def _sweep_locked(self):
        now = self._clock()
        for token in [t for t, r in self._records.items() if r["expires"] <= now]:
            del self._records[token]

    def sweep(self):
        with self._lock:
            self._sweep_locked()

    def put(self, kind: str, entry: dict, *, audio=None, replaced_by=None) -> str:
        """Keep ``entry`` (what was removed, or what a redo replaced) and
        return the token that undoes it. ``audio`` is (name, bytes, mtime) of
        the recording that went with it, or None. ``replaced_by`` is, for a
        redo, the entry that took its place."""
        token = secrets.token_urlsafe(9)
        with self._lock:
            self._sweep_locked()
            kept_audio = audio
            held = sum(len(r["audio"][1]) for r in self._records.values() if r["audio"])
            if kept_audio and held + len(kept_audio[1]) > self.audio_limit:
                kept_audio = None       # too much to hold: the entry is kept, not the audio
            self._records[token] = {"kind": kind, "entry": entry, "audio": kept_audio,
                                    "now": replaced_by, "expires": self._clock() + self.window_s}
            while len(self._records) > self.limit:
                del self._records[next(iter(self._records))]
        if self._timer:
            t = threading.Timer(self.window_s + 0.5, self.sweep)
            t.daemon = True
            t.start()
        return token

    def take(self, token):
        """The record for ``token`` (and forget it), or None when it is
        unknown, already used or too old."""
        with self._lock:
            self._sweep_locked()
            return self._records.pop(str(token or ""), None)

    def __len__(self):
        with self._lock:
            self._sweep_locked()
            return len(self._records)

    def clear(self):
        with self._lock:
            self._records.clear()


# The one shelf the window's Undo uses (app.py). Delete all my data empties it
# (src/privacy_data.py): a deleted dictation must not be recoverable from here.
SHELF = UndoShelf()
