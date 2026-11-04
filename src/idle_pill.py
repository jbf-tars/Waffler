"""The pill between dictations, its hover menu, and where the pill sits (S12).

Three pieces, all opt-in or invisible until used, so a Waffler that never
touches them behaves exactly as before:

- The idle pill. ``idle_pill`` in settings.json, a true or a false, off unless
  exactly true (Settings, General, "Show the pill when idle"). On, a small
  quiet pill stays on screen between dictations, on every Space, drawn by the
  overlay process in a window of its own (src/overlay_process.py). The
  recording pill's window, and how and when it is shown, hidden, raised and
  kept on the active Space, are not touched: the idle pill is hidden while
  the recording pill or a toast is up and comes back after.
- The hover menu. Resting the pointer on the idle pill (or clicking it) opens a
  small menu: Paste last, Copy last, Open Waffler, the microphone in use (which
  opens Settings at Microphones) and Hide the pill (which turns the setting
  off). The overlay asks the app for the items when the pointer arrives
  (``pill_hover``), the app answers (``pill_menu``), and a click comes back as
  ``pill_action``. The menu is a non-activating panel like the toasts, so a
  click leaves the app the person is typing in at the front; only Open Waffler
  and the microphone item bring Waffler's window forward, because opening it
  is what they are for.
- The remembered position. The idle pill can be dragged. Where it is dropped
  is kept per screen in ``pill_position.json`` (``PillPositions``), keyed by
  the screen's display id and frame (``screen_key``), and used for the idle
  pill and the recording pill on that screen, kept inside the screen's visible
  frame (``clamp_origin``). A screen that is not there now (unplugged,
  rearranged, a new resolution) has no entry under its key, so the pill sits
  where it always did on it. With nothing saved, the overlay places the
  recording pill exactly as it did before. Settings has "Reset pill position".

Pure: no AppKit, no threads. The overlay process imports the geometry, the
app the setting, the store and the menu.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

try:
    from atomic_json import write_json_atomic
except ImportError:  # imported as src.idle_pill
    from src.atomic_json import write_json_atomic

SETTING = "idle_pill"

# The idle pill's window (points). The capsule drawn inside it is smaller; the
# rest of the window takes the pointer so a small target is easy to hit.
IDLE_W = 46
IDLE_H = 18


def enabled(stored) -> bool:
    """Is the idle pill on in this settings.json? Off unless exactly true."""
    return isinstance(stored, dict) and stored.get(SETTING) is True


def touches(payload) -> bool:
    return isinstance(payload, dict) and SETTING in payload


def settings_problem(payload) -> Optional[str]:
    """A plain sentence when a save names the setting with anything but a true
    or a false, else None."""
    if touches(payload) and not isinstance(payload[SETTING], bool):
        try:
            import user_messages
        except ImportError:  # imported as src.idle_pill
            from src import user_messages
        return user_messages.IDLE_PILL_UNKNOWN
    return None


# ── the menu ────────────────────────────────────────────────────────────────

PASTE_LAST = "paste_last"
COPY_LAST = "copy_last"
OPEN = "open"
MICROPHONE = "microphone"
HIDE = "hide"
ACTIONS = (PASTE_LAST, COPY_LAST, OPEN, MICROPHONE, HIDE)

# The items that bring Waffler's window forward. Every other one leaves the
# app the person is typing in at the front.
OPENS_WINDOW = frozenset({OPEN, MICROPHONE})

# Longest microphone name shown before it is cut with an ellipsis.
MIC_NAME_CHARS = 28


def _labels():
    try:
        import user_messages as um
    except ImportError:  # imported as src.idle_pill
        from src import user_messages as um
    return um


def mic_label(name: Optional[str]) -> str:
    """The microphone line: the name of the one in use, cut to fit."""
    um = _labels()
    name = " ".join(str(name).split()) if isinstance(name, str) else ""
    if not name:
        return um.PILL_MENU_MIC_SYSTEM
    if len(name) > MIC_NAME_CHARS:
        name = name[:MIC_NAME_CHARS - 1].rstrip() + "\u2026"
    return name


def menu_items(*, has_last: bool, mic_name: Optional[str]) -> list:
    """The menu, top to bottom, as the overlay draws it: ``{action, label,
    enabled}``. Paste last and Copy last are greyed while there is nothing to
    paste. Never the text of a transcript: menus show on screen shares."""
    um = _labels()
    return [
        {"action": PASTE_LAST, "label": um.PILL_MENU_PASTE, "enabled": bool(has_last)},
        {"action": COPY_LAST, "label": um.PILL_MENU_COPY, "enabled": bool(has_last)},
        {"action": OPEN, "label": um.PILL_MENU_OPEN, "enabled": True},
        {"action": MICROPHONE, "label": mic_label(mic_name), "enabled": True,
         "caption": um.PILL_MENU_MIC_CAPTION},
        {"action": HIDE, "label": um.PILL_MENU_HIDE, "enabled": True},
    ]


def route(action, *, paste_last: Callable[[], object], copy_last: Callable[[], object],
          open_window: Callable[[], object], open_microphones: Callable[[], object],
          hide_pill: Callable[[], object]) -> Optional[str]:
    """Run the item ``action`` names. Returns the action run, or None for one
    that is not on the menu (ignored). Exactly one of the callables is called."""
    table = {PASTE_LAST: paste_last, COPY_LAST: copy_last, OPEN: open_window,
             MICROPHONE: open_microphones, HIDE: hide_pill}
    fn = table.get(action) if isinstance(action, str) else None
    if fn is None:
        return None
    fn()
    return action


# ── hovering ────────────────────────────────────────────────────────────────

# The pointer must rest on the pill this long before the menu is asked for, so
# a pointer passing over it on the way somewhere else opens nothing.
HOVER_DWELL_S = 0.18
# Once open, the menu stays while the pointer is on the pill or the menu, and
# for this long after it leaves both (crossing the gap between them).
LEAVE_GRACE_S = 0.4
# An answer that has not come in this long is given up; the next rest asks again.
ASK_TIMEOUT_S = 1.5


class HoverMenu:
    """When to ask for the menu and when to close it, from where the pointer
    is. ``tick`` is called about 20 times a second while the idle pill shows,
    with the clock injected."""

    def __init__(self):
        self.rest_since: Optional[float] = None
        self.asked_at: Optional[float] = None
        self.left_at: Optional[float] = None
        self.open = False
        # Just dropped after a drag: the pointer is on the pill, but the menu
        # waits until it has left and come back.
        self.quiet = False

    def tick(self, now: float, *, on_pill: bool, on_menu: bool) -> Optional[str]:
        """"ask" (send ``pill_hover``), "close" (close the menu) or None."""
        if self.open:
            if on_pill or on_menu:
                self.left_at = None
                return None
            if self.left_at is None:
                self.left_at = now
                return None
            if now - self.left_at >= LEAVE_GRACE_S:
                self.closed()
                return "close"
            return None
        if not on_pill:
            self.rest_since = None
            self.quiet = False
            if self.asked_at is not None and now - self.asked_at >= ASK_TIMEOUT_S:
                self.asked_at = None
            return None
        if self.quiet:
            return None
        if self.rest_since is None:
            self.rest_since = now
        if self.asked_at is not None:
            if now - self.asked_at < ASK_TIMEOUT_S:
                return None
            self.asked_at = None
        if now - self.rest_since >= HOVER_DWELL_S:
            self.asked_at = now
            return "ask"
        return None

    def ask_now(self, now: float) -> bool:
        """A click on the pill: ask at once. False if already open or asked."""
        if self.open or (self.asked_at is not None and now - self.asked_at < ASK_TIMEOUT_S):
            return False
        self.asked_at = now
        return True

    def should_open(self, *, on_pill: bool, on_menu: bool) -> bool:
        """The answer came: open it only if it was asked for and the pointer
        is still there (it may have moved on while the app answered)."""
        return self.asked_at is not None and not self.open and (on_pill or on_menu)

    def dropped(self) -> None:
        """A drag ended on the pill: no menu until the pointer leaves it."""
        self.closed()
        self.quiet = True

    def opened(self) -> None:
        self.open = True
        self.asked_at = None
        self.left_at = None

    def closed(self) -> None:
        self.open = False
        self.asked_at = None
        self.left_at = None
        self.rest_since = None


# ── geometry (Cocoa coordinates: origin at the bottom left) ──────────────────

Rect = Tuple[float, float, float, float]   # x, y, width, height


def screen_key(display_id, frame: Rect) -> str:
    """The key a position is kept under: the display's id and its frame, so
    the same display arranged or sized differently is a different screen."""
    x, y, w, h = (int(round(float(v))) for v in frame)
    try:
        did = int(display_id)
    except (TypeError, ValueError):
        did = 0
    return f"{did}@{x},{y},{w}x{h}"


def clamp_origin(x: float, y: float, w: float, h: float, visible: Rect) -> Tuple[float, float]:
    """The origin of a ``w`` by ``h`` window at (x, y), moved the least needed
    to lie inside ``visible`` (the screen less the menu bar and the Dock)."""
    vx, vy, vw, vh = (float(v) for v in visible)
    x = min(max(float(x), vx), vx + max(0.0, vw - w))
    y = min(max(float(y), vy), vy + max(0.0, vh - h))
    return x, y


def origin_at(anchor, w: float, h: float, visible: Rect) -> Tuple[float, float]:
    """The origin of a ``w`` by ``h`` window whose bottom centre is at the
    saved ``anchor`` (``{"cx", "y"}``), kept inside ``visible``."""
    return clamp_origin(float(anchor["cx"]) - w / 2.0, float(anchor["y"]), w, h, visible)


def anchor_of(x: float, y: float, w: float) -> Dict[str, float]:
    """The anchor kept for a window of width ``w`` at (x, y): its bottom centre."""
    return {"cx": round(float(x) + w / 2.0, 1), "y": round(float(y), 1)}


def menu_origin(pill: Rect, w: float, h: float, visible: Rect, gap: float = 6.0) -> Tuple[float, float]:
    """Where the menu goes: centred above the pill, or below it when there is
    no room above, and inside the visible frame either way."""
    px, py, pw, ph = (float(v) for v in pill)
    vx, vy, vw, vh = (float(v) for v in visible)
    x = px + (pw - w) / 2.0
    y = py + ph + gap
    if y + h > vy + vh:
        y = py - gap - h
    return clamp_origin(x, y, w, h, visible)


def toast_origin_inside(x: float, y: float, w: float, h: float, pill_y: float,
                        visible: Rect, gap: float) -> Tuple[float, float]:
    """A toast meant to sit above a pill that was moved near the top of the
    screen: below the pill instead, then kept inside the visible frame. Only
    used while a position is saved; otherwise toasts go where they always did."""
    vx, vy, vw, vh = (float(v) for v in visible)
    if y + h > vy + vh:
        y = float(pill_y) - gap - h
    return clamp_origin(x, y, w, h, visible)


# ── the store ───────────────────────────────────────────────────────────────

POSITIONS_FILE = "pill_position.json"
POSITIONS_VERSION = 1
# Screens remembered; the one moved longest ago is dropped past this.
MAX_SCREENS = 12
_KEY_CHARS = 80
_LIMIT = 1_000_000.0      # no screen is a million points from the origin


def _number(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and abs(v) < _LIMIT else None


def clean_positions(raw) -> Dict[str, Dict[str, float]]:
    """``{screen key: {"cx", "y"}}`` from what the file (or the overlay) holds.
    Anything that cannot be read is left out, so a damaged file reads as fewer
    screens, and an empty or unreadable one as none (the pill where it always
    was)."""
    screens = raw.get("screens") if isinstance(raw, dict) else None
    out: Dict[str, Dict[str, float]] = {}
    if not isinstance(screens, dict):
        return out
    for key, entry in screens.items():
        if not isinstance(key, str) or not (0 < len(key) <= _KEY_CHARS) or not isinstance(entry, dict):
            continue
        cx, y = _number(entry.get("cx")), _number(entry.get("y"))
        if cx is None or y is None:
            continue
        out[key] = {"cx": cx, "y": y}
    return out


class PillPositions:
    """Where the pill was dropped on each screen, in ``POSITIONS_FILE`` in the
    data folder (asked for at each call), or at ``path``."""

    def __init__(self, path=None, clock: Callable[[], float] = time.time):
        self._path = path
        self._clock = clock

    def path(self) -> Path:
        if self._path is not None:
            return Path(self._path)
        try:
            from data_paths import data_dir
        except ImportError:  # imported as src.idle_pill
            from src.data_paths import data_dir
        return data_dir() / POSITIONS_FILE

    def _raw(self) -> dict:
        try:
            raw = json.loads(self.path().read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def load(self) -> Dict[str, Dict[str, float]]:
        """Every screen's saved spot; {} when there is no file or it is damaged."""
        return clean_positions(self._raw())

    def save(self, key, cx, y) -> bool:
        """Keep the spot for one screen. False (nothing written) for a key or
        numbers that cannot be kept."""
        if not isinstance(key, str) or not (0 < len(key) <= _KEY_CHARS):
            return False
        cx, y = _number(cx), _number(y)
        if cx is None or y is None:
            return False
        raw = self._raw()
        screens = raw.get("screens") if isinstance(raw.get("screens"), dict) else {}
        kept = {k: v for k, v in screens.items() if k in clean_positions({"screens": {k: v}})}
        kept[key] = {"cx": round(cx, 1), "y": round(y, 1), "at": round(float(self._clock()), 1)}
        if len(kept) > MAX_SCREENS:
            def when(item):
                at = item[1].get("at")
                return at if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0
            kept = dict(sorted(kept.items(), key=when)[-MAX_SCREENS:])
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, {"version": POSITIONS_VERSION, "screens": kept})
        return True

    def clear(self) -> None:
        """Reset pill position: every screen back to where the pill always sat."""
        try:
            self.path().unlink()
        except FileNotFoundError:
            pass
