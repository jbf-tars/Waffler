"""The one place that decides whether a toast or a tip is shown.

Waffler's messages grew one site at a time: about twenty places in app.py each
called the overlay when they saw fit, with no shared idea of "is something
already on screen", "has this been said enough", or "should this wait". This
module is that idea. Every message has a kind in the registry below, and the
app asks ``request(kind)`` before it shows one.

What a kind carries
-------------------
``severity``  error, tip or info.
``max_times`` how often it may ever be shown (None: no cap).
``min_gap_s`` the least time between two showings.
``category``  tips of one family (the user can hide a whole family).
``sound``     a cue to play with it, if the person has sounds on.
``hold_s``    how long it holds the one toast slot (the overlay hides a toast
              after about 6 s on a Mac and 9 s on Windows; None means "until
              it is answered or withdrawn").

The rules
---------
Errors always pass the cap. They are never held back for a quiet moment, and
they pre-empt whatever is up. The one thing that can drop an error is being
a duplicate: the same kind again inside ``min_gap_s``, in this run of the app.
(A permission message is not hidden for a minute because the app was restarted
to switch that permission on, so the gap does not carry across runs.)

Tips wait. They are suppressed while recording or processing, before first-run
setup is finished, when the person turned tips off, when capped, inside the
minimum gap, once dismissed twice, when "Don't show again" was pressed, when
their category is hidden, and whenever any other message is up. A dropped tip
is not queued and is not counted.

Info messages (the "still working" offer, a recording-length warning) are not
tips: they ignore the tips switch and the quiet moment, but they keep their
cap and gap.

One slot: a toast hides the one before it, so the governor knows what it put
up and for how long. An error arriving over a tip that has been up for under
``IMPRESSION_MIN_S`` hands the tip its showing back: it was never read.

State
-----
Counts, last-shown times, dismissals and "Don't show again" are kept in
``notifications.json`` in the data folder (``FileStore``), not in
settings.json: they change every time a message appears. The file holds no
text from anyone's dictation. A file that cannot be read is ignored and the
next save writes it afresh.

The clock, the store and the "is this a quiet moment" context are all injected,
so the decision table in tests/test_notifier.py runs without sleeping. Thread
safe: messages are raised from the hotkey thread, the pipeline thread and the
menu callbacks.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

try:
    from atomic_json import write_json_atomic
    from data_paths import data_dir as _data_dir
except ImportError:  # imported as src.notifier
    from src.atomic_json import write_json_atomic
    from src.data_paths import data_dir as _data_dir

ERROR, TIP, INFO = "error", "tip", "info"
SEVERITIES = (ERROR, TIP, INFO)

STATE_FILE = "notifications.json"
STATE_VERSION = 1

# A tip dismissed this many times is not shown again.
DISMISSALS_BEFORE_QUIET = 2
# A tip that a newer message replaced before it had been up this long was not
# read, so it does not use up one of its showings.
IMPRESSION_MIN_S = 3.0
# How long a toast holds the slot unless its kind says otherwise: the overlay
# hides one after 6 s (Mac) or 9 s (Windows).
DEFAULT_HOLD_S = 9.0
# A message that stays until answered still frees the slot after this long, so
# one that is never withdrawn cannot silence every tip for the rest of the run.
STAY_SAFETY_S = 300.0


@dataclass(frozen=True)
class Kind:
    id: str
    severity: str
    max_times: Optional[int] = None
    min_gap_s: float = 0.0
    category: Optional[str] = None
    sound: Optional[str] = None
    hold_s: Optional[float] = DEFAULT_HOLD_S

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r} for {self.id!r}")


_HOUR = 3600.0

# Every message Waffler can put up. Errors that had no limit before have none
# now; the gaps below are the ones the code already kept (the paste-failure
# message once every 30 s, a permission once a minute, a dead key listener
# every ten minutes). Where each is raised: app.py, by its id.
_REGISTRY = (
    # Permissions (src/mac_permissions.py asks at key-down, and while idle).
    Kind("permission_microphone", ERROR, min_gap_s=60, hold_s=12),
    Kind("permission_accessibility", ERROR, min_gap_s=60, hold_s=12),
    Kind("permission_accessibility_idle", ERROR, min_gap_s=math.inf, hold_s=12),
    Kind("permission_keyboard", ERROR, min_gap_s=600, hold_s=12),
    # Pasting.
    Kind("paste_failed", ERROR, min_gap_s=30),
    Kind("paste_timed_out", ERROR),
    Kind("clipboard_copy_failed", ERROR),
    # The microphone and the recording.
    Kind("no_audio", ERROR),
    Kind("mic_reset", ERROR),
    Kind("mic_dropped_out", ERROR),
    Kind("mic_slow_to_stop", ERROR),
    # The minute's warning before the longest take, and the stop at the end
    # (src/take_limit.py). Said once a take: the recording loop's watch gives
    # the warning once, and a newer recording ends an older loop.
    Kind("long_recording", INFO, hold_s=6),
    Kind("recording_limit", INFO),
    # Waiting on a provider.
    Kind("still_working", INFO, hold_s=None),
    Kind("pipeline_stuck", ERROR),
    Kind("not_sent", ERROR),
    Kind("cleanup_skipped", ERROR),
    Kind("limit_reached", ERROR),
    Kind("connection_failed", ERROR),
    Kind("access_denied", ERROR),
    Kind("pipeline_error", ERROR),
    # After a dictation.
    Kind("cancelled_kept", ERROR),
    Kind("history_not_saved", ERROR),
    # Transforms (src/transform_action.py): what became of a rewrite, a
    # refusal while a dictation is running (once every 8 s, so mashing the
    # shortcut does not stack messages), and a rewrite that failed.
    Kind("transform_result", INFO, hold_s=8),
    Kind("transform_busy", INFO, min_gap_s=8, hold_s=6),
    Kind("transform_failed", ERROR, hold_s=7),
    # Command mode (src/command_action.py): what became of a command, a
    # refusal while something else is running (once every 8 s), and a failure.
    Kind("command_result", INFO, hold_s=8),
    Kind("command_busy", INFO, min_gap_s=8, hold_s=6),
    Kind("command_failed", ERROR, hold_s=7),
    # The Paste last and Copy last shortcuts (app.py): they have nothing to
    # show when they work, so a short notice says what they did, or why they
    # did nothing (busy, nothing yet). A gap of 3 s keeps a held key quiet.
    Kind("last_result_notice", INFO, min_gap_s=3, hold_s=3),
    # Tips. The hint stays up 10 s (src/hold_hint.py HINT_SECONDS).
    Kind("hold_hint", TIP, min_gap_s=12 * _HOUR, category="hints", hold_s=10),
)
KINDS: Dict[str, Kind] = {k.id: k for k in _REGISTRY}


@dataclass(frozen=True)
class Context:
    """Is this a quiet moment? The app fills it in; only tips ask."""
    recording: bool = False
    processing: bool = False
    setup_done: bool = True
    tips_enabled: bool = True


@dataclass(frozen=True)
class Decision:
    show: bool
    reason: str = "ok"
    sound: Optional[str] = None

    def __bool__(self):
        return self.show


# Every tip has two buttons. Their action ids carry the tip's kind, so a click
# is understood even if another message has come and gone since (app.py
# _on_toast_action).
TIP_DISMISS, TIP_MUTE = "tip_dismiss", "tip_mute"


def tip_buttons(kind_id: str) -> list:
    """The buttons every tip carries: close it, or "Don't show again"."""
    return [{"label": "Got it", "action": f"{TIP_DISMISS}:{kind_id}", "kind": "primary"},
            {"label": "Don't show again", "action": f"{TIP_MUTE}:{kind_id}", "kind": "secondary"}]


def parse_tip_action(action):
    """``(verb, kind_id)`` for a tip button's action id, else None."""
    if isinstance(action, str):
        verb, sep, kind_id = action.partition(":")
        if sep and verb in (TIP_DISMISS, TIP_MUTE) and kind_id:
            return verb, kind_id
    return None


def tips_enabled(settings) -> bool:
    """The "Show tips" setting: on unless it is explicitly False."""
    if not isinstance(settings, dict):
        return True
    return settings.get("tips") is not False


# ── state ────────────────────────────────────────────────────────────────

class _Record:
    __slots__ = ("shown", "last_shown", "dismissed", "muted")

    def __init__(self, shown=0, last_shown=None, dismissed=0, muted=False):
        self.shown = shown
        self.last_shown = last_shown
        self.dismissed = dismissed
        self.muted = muted

    def as_dict(self):
        return {"shown": self.shown, "last_shown": self.last_shown,
                "dismissed": self.dismissed, "muted": self.muted}

    def copy(self):
        return _Record(self.shown, self.last_shown, self.dismissed, self.muted)


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _time(value) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _record_from(raw) -> _Record:
    if not isinstance(raw, dict):
        return _Record()
    return _Record(_count(raw.get("shown")), _time(raw.get("last_shown")),
                   _count(raw.get("dismissed")), raw.get("muted") is True)


class MemoryStore:
    """State kept in memory only: for tests, and for a checker that has no
    data folder."""

    def __init__(self, data=None):
        self.data = data

    def load(self):
        return self.data

    def save(self, data) -> None:
        self.data = json.loads(json.dumps(data))

    def clear(self) -> None:
        self.data = None


class FileStore:
    """State in ``notifications.json`` in the data folder. The folder is
    asked for on every call (data_paths.data_dir), so a test that changes it
    is honoured. A path, or a function that gives one, can be passed
    instead."""

    def __init__(self, path=None):
        self._path = path

    def path(self) -> Path:
        if self._path is None:
            return _data_dir() / STATE_FILE
        return Path(self._path() if callable(self._path) else self._path)

    def load(self):
        """The stored value, or None when there is none or it cannot be read."""
        try:
            return json.loads(self.path().read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None

    def save(self, data) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, data, indent=1)

    def clear(self) -> None:
        try:
            self.path().unlink()
        except FileNotFoundError:
            pass


# ── the decision ─────────────────────────────────────────────────────────

class _Slot:
    __slots__ = ("kind", "shown_at", "expires_at", "before")

    def __init__(self, kind, shown_at, expires_at, before):
        self.kind = kind
        self.shown_at = shown_at
        self.expires_at = expires_at
        self.before = before        # the record as it was before this showing


def evaluate(kind: Kind, record: _Record, ctx: Optional[Context], now: float, *,
             session_start: float, slot: Optional[_Slot], category_muted: bool) -> str:
    """Why ``kind`` may not be shown now, or "" when it may. The whole rule
    table, with no side effects."""
    if kind.severity == ERROR:
        # An error passes the cap and every quiet rule. Only a duplicate in
        # this run is dropped.
        if kind.min_gap_s > 0 and record.last_shown is not None \
                and record.last_shown >= session_start:
            if 0 <= now - record.last_shown < kind.min_gap_s:
                return "gap"
        return ""
    if kind.severity == INFO:
        if record.muted:
            return "muted"
        if kind.max_times is not None and record.shown >= kind.max_times:
            return "capped"
        if kind.min_gap_s > 0 and record.last_shown is not None \
                and 0 <= now - record.last_shown < kind.min_gap_s:
            return "gap"
        return ""
    # A tip.
    if ctx is None:
        return "context"
    if not ctx.tips_enabled:
        return "tips_off"
    if ctx.recording:
        return "recording"
    if ctx.processing:
        return "processing"
    if not ctx.setup_done:
        return "setup"
    if slot is not None:
        return "slot_busy"
    if record.muted:
        return "muted"
    if category_muted:
        return "category_muted"
    if record.dismissed >= DISMISSALS_BEFORE_QUIET:
        return "dismissed"
    if kind.max_times is not None and record.shown >= kind.max_times:
        return "capped"
    # A clock set back makes the elapsed time negative; that counts as "long
    # enough", so a wrong date cannot hide a tip for a year.
    if kind.min_gap_s > 0 and record.last_shown is not None \
            and 0 <= now - record.last_shown < kind.min_gap_s:
        return "gap"
    return ""


class Notifier:
    def __init__(self, store=None, clock: Callable[[], float] = time.time,
                 context: Optional[Callable[[], Context]] = None,
                 kinds: Optional[Dict[str, Kind]] = None,
                 log: Optional[Callable[[str], None]] = None):
        self._store = store if store is not None else MemoryStore()
        self._clock = clock
        self._context = context
        self._kinds = kinds if kinds is not None else KINDS
        self._log = log
        self._lock = threading.RLock()
        self._session_start = clock()
        self._records: Optional[Dict[str, _Record]] = None
        self._muted_categories = set()
        self._slot: Optional[_Slot] = None

    # ── asking ──────────────────────────────────────────────────────────

    def request(self, kind_id: str) -> Decision:
        """May this message be shown now? If so it is counted and takes the
        slot, so the caller shows it straight away."""
        kind = self._kinds.get(kind_id)
        if kind is None:
            # A typo must not silence a message. It is shown, and said.
            self._say(f"[notifier] unregistered message kind {kind_id!r}; shown anyway")
            return Decision(True, "unregistered")
        ctx = None
        if kind.severity == TIP:
            ctx = self._read_context()
        with self._lock:
            records = self._loaded()
            now = self._clock()
            slot = self._live_slot(now)
            record = records.get(kind.id) or _Record()
            reason = evaluate(
                kind, record, ctx, now, session_start=self._session_start, slot=slot,
                category_muted=bool(kind.category and kind.category in self._muted_categories))
            if reason:
                return Decision(False, reason)
            if slot is not None:
                self._hand_back(slot, now)
            before = record.copy()
            record = records.setdefault(kind.id, record)
            record.shown += 1
            record.last_shown = now
            hold = kind.hold_s if kind.hold_s is not None else STAY_SAFETY_S
            self._slot = _Slot(kind, now, now + hold, before)
            self._save()
            return Decision(True, "ok", kind.sound)

    def claim(self, kind_id: str) -> bool:
        """``request`` as a yes or no."""
        return self.request(kind_id).show

    # ── what happened to a message ──────────────────────────────────────

    def release(self, kind_id: Optional[str] = None) -> None:
        """The message is gone (hidden, answered, withdrawn). With a kind,
        only if that kind is the one up."""
        with self._lock:
            if self._slot is not None and (kind_id is None or self._slot.kind.id == kind_id):
                self._slot = None

    def dismiss(self, kind_id: str) -> None:
        """The person closed it with its Got it / Dismiss button."""
        with self._lock:
            self._loaded().setdefault(kind_id, _Record()).dismissed += 1
            self.release(kind_id)
            self._save()

    def mute(self, kind_id: str) -> None:
        """"Don't show again"."""
        with self._lock:
            self._loaded().setdefault(kind_id, _Record()).muted = True
            self.release(kind_id)
            self._save()

    def mute_category(self, category: str) -> None:
        with self._lock:
            self._loaded()
            self._muted_categories.add(category)
            self._save()

    def reset_gap(self, kind_id: str) -> None:
        """Forget when it was last shown (the count stays). For a message
        that is said once until the problem has cleared."""
        with self._lock:
            rec = self._loaded().get(kind_id)
            if rec is not None and rec.last_shown is not None:
                rec.last_shown = None
                self._save()

    def forget(self) -> None:
        """Delete my data: every count, every hidden tip, the file."""
        with self._lock:
            self._records = {}
            self._muted_categories = set()
            self._slot = None
            try:
                self._store.clear()
            except Exception as e:
                self._say(f"[notifier] could not delete its state ({type(e).__name__})")

    def snapshot(self) -> Dict[str, dict]:
        """The counters, by kind, as plain data (for tests and for the log)."""
        with self._lock:
            return {k: r.as_dict() for k, r in self._loaded().items()}

    # ── inside ──────────────────────────────────────────────────────────

    def _read_context(self) -> Optional[Context]:
        if self._context is None:
            return Context()
        try:
            ctx = self._context()
            return ctx if isinstance(ctx, Context) else None
        except Exception as e:
            self._say(f"[notifier] could not read the context ({type(e).__name__})")
            return None

    def _live_slot(self, now: float) -> Optional[_Slot]:
        slot = self._slot
        if slot is not None and now >= slot.expires_at:
            self._slot = slot = None
        return slot

    def _hand_back(self, slot: _Slot, now: float) -> None:
        """A message is replacing ``slot``'s. A tip that was gone before it
        could be read gets its showing back."""
        if slot.kind.severity == TIP and now - slot.shown_at < IMPRESSION_MIN_S:
            rec = self._loaded().get(slot.kind.id)
            if rec is not None:
                rec.shown = slot.before.shown
                rec.last_shown = slot.before.last_shown

    def _loaded(self) -> Dict[str, _Record]:
        if self._records is None:
            self._records = {}
            try:
                raw = self._store.load()
            except Exception:
                raw = None
            if isinstance(raw, dict):
                kinds = raw.get("kinds")
                if isinstance(kinds, dict):
                    for kid, rec in kinds.items():
                        if isinstance(kid, str):
                            self._records[kid] = _record_from(rec)
                cats = raw.get("muted_categories")
                if isinstance(cats, list):
                    self._muted_categories = {c for c in cats if isinstance(c, str)}
        return self._records

    def _save(self) -> None:
        data = {"version": STATE_VERSION,
                "kinds": {k: r.as_dict() for k, r in (self._records or {}).items()},
                "muted_categories": sorted(self._muted_categories)}
        try:
            self._store.save(data)
        except Exception as e:
            # Never the message: only what kind of failure it was.
            self._say(f"[notifier] could not save its state ({type(e).__name__})")

    def _say(self, line: str) -> None:
        if self._log is not None:
            try:
                self._log(line)
            except Exception:
                pass
