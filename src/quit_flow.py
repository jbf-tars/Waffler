"""One shutdown for every way of quitting Waffler.

Quitting can start in several places: Cmd-Q, the app menu's Quit, the menu bar
or tray Quit, the Dock's Quit, logging out, and ``tell application "Waffler"
to quit`` from AppleScript. On macOS all but the menu bar item reach AppKit's
``applicationShouldTerminate:``, which pywebview turns into the window's
``closing`` event, the same event the red close button raises. Waffler hides
the window on a close, so it has to tell the two apart: ``quit_reason`` does,
from where the close was asked (``inside_app_terminate``), whether a Cmd-Q
key-down is current, and whether a quit Apple Event is being handled.

``QuitController`` runs the shutdown once, whoever asks, and arms a backstop
first: if the process is still running ``grace_s`` seconds later (a thread
that will not stop, a stuck audio teardown in an exit handler), it is ended
with ``os._exit``.

The overlay subprocess is the same executable as the app (``Waffler
--overlay``), so the system lists two "Waffler" processes under one bundle id
and a quit Apple Event can reach the overlay instead. The overlay passes it to
the app (``overlay_terminate_decision``) rather than exiting on its own and
leaving the app running without a pill.

No AppKit here: the caller supplies the facts, so this runs in tests on any
platform.
"""

import os
import sys
import threading
from typing import Callable, Iterable, Optional, Tuple

# How long after a quit is asked the process may still be running before it
# is ended outright.
QUIT_GRACE_S = 2.0

# Apple Event codes: kCoreEventClass ('aevt') and kAEQuitApplication ('quit').
K_CORE_EVENT_CLASS = int.from_bytes(b"aevt", "big")
K_AE_QUIT_APPLICATION = int.from_bytes(b"quit", "big")

# pywebview's NSApplication delegate method. Its Python frame is on the stack
# while AppKit asks whether the app may terminate, whatever started it.
_TERMINATE_FRAME = "applicationShouldTerminate_"

# How long the overlay leaves a forwarded quit to the app before it exits by
# itself (the app is hung, or never got the message).
OVERLAY_FORWARD_PATIENCE_S = 5.0


def is_quit_apple_event(event_class: Optional[int], event_id: Optional[int]) -> bool:
    """True for the kAEQuitApplication event (AppleScript ``quit``, the Dock's
    Quit, log out and restart)."""
    return event_class == K_CORE_EVENT_CLASS and event_id == K_AE_QUIT_APPLICATION


def inside_app_terminate(frame=None, max_depth: int = 200) -> bool:
    """True when called (at any depth) from ``applicationShouldTerminate_``.

    pywebview fires ``closing`` synchronously from both its window delegate
    (red close button, Cmd-W) and its app delegate (every kind of quit), so
    the caller's stack says which one is asking.
    """
    f = frame if frame is not None else sys._getframe(1)
    depth = 0
    while f is not None and depth < max_depth:
        if f.f_code.co_name == _TERMINATE_FRAME:
            return True
        f = f.f_back
        depth += 1
    return False


def quit_reason(*, in_terminate: bool, cmd_q: bool, quit_event: bool) -> Optional[str]:
    """Why a window close is really a quit, or None for a plain close (hide)."""
    if cmd_q:
        return "Cmd-Q"
    if quit_event:
        return "a quit Apple Event"
    if in_terminate:
        return "the app is terminating"
    return None


def overlay_terminate_decision(*, parent_asked: bool, now: float,
                               last_forward: Optional[float]) -> str:
    """What the overlay does when AppKit asks whether it may terminate.

    ``"terminate"`` when the app asked (its quit command, or its stdin closed),
    or when a quit was already passed to the app over
    ``OVERLAY_FORWARD_PATIENCE_S`` ago and nothing happened; otherwise
    ``"forward"``: tell the app and stay up, so the app does the quitting.
    """
    if parent_asked:
        return "terminate"
    if last_forward is not None and now - last_forward > OVERLAY_FORWARD_PATIENCE_S:
        return "terminate"
    return "forward"


Step = Tuple[str, Callable[[], None]]


class QuitController:
    """Runs the quit steps once and makes sure the process then ends.

    ``steps`` are ``(name, fn)`` pairs run in order; one that raises is logged
    and the rest still run. ``hard_exit`` (``os._exit``) is called with 0 if
    the process is still alive ``grace_s`` seconds after the first ``quit``.
    """

    def __init__(self, steps: Iterable[Step], *,
                 hard_exit: Callable[[int], None] = os._exit,
                 log: Callable[[str], None] = print,
                 grace_s: float = QUIT_GRACE_S,
                 timer=threading.Timer):
        self._steps = list(steps)
        self._hard_exit = hard_exit
        self._log = log
        self._grace_s = float(grace_s)
        self._timer = timer
        self._lock = threading.Lock()
        self._started = False
        self._watchdog = None

    @property
    def quitting(self) -> bool:
        return self._started

    def quit(self, reason: str) -> bool:
        """Start quitting. True the first time, False if already quitting."""
        with self._lock:
            if self._started:
                return False
            self._started = True
        self._safe_log(f"[quit] {reason}: shutting down")
        # Armed before the steps, so a step that hangs cannot keep us alive.
        t = self._timer(self._grace_s, self._backstop)
        t.daemon = True
        t.start()
        self._watchdog = t
        for name, fn in self._steps:
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - every step gets its turn
                self._safe_log(f"[quit] {name} failed: {type(e).__name__}: {e}")
        return True

    def _backstop(self) -> None:
        self._safe_log(f"[quit] still running {self._grace_s:g}s after quit - ending the process")
        self._hard_exit(0)

    def _safe_log(self, msg: str) -> None:
        try:
            self._log(msg)
        except Exception:
            pass
