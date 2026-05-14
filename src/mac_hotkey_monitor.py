"""
macOS hotkey monitoring via a SINGLE CGEventTap with multiple handlers.

Background — the v3.14.x segfault
---------------------------------
Prior versions of this module (and the now-defunct ``fn_key_cgevent.py``)
each spawned their own ``CGEventTapCreate(kCGHIDEventTap, …)`` on its own
``CFRunLoopRun()`` thread. ``SmartHotkeyListener`` then composed two or three
of them — one for the hotkey (Fn or otherwise), one for Space (sticky-mode
trigger), and one for Esc (sticky-cancel escape hatch).

That produced 2–3 HID-level CGEventTaps living in the same process. The
documented PyObjC footgun is that when two C-level event-tap callbacks fire
near-simultaneously (very common at "paste + key release" the instant a
recording finishes), PyObjC's bridge state races between the two C threads
and macOS sends SIGSEGV. The user's crash log captured exactly this — a
segfault with `<no Python frame>` on the crashing thread, while two other
threads were both alive inside their own ``CFRunLoopRun()``.

The fix in v3.14.13 is to keep ONE event tap per process and dispatch the
incoming events to multiple lightweight handlers inside the single callback.

Public surface
--------------
* ``MacEventTap`` — the single CGEventTap + CFRunLoop. ``add_handler()`` to
  attach a handler; ``start()`` / ``stop()`` for lifecycle.
* ``FnHandler`` — detects Fn key state (laptop Fn-flag + external F13-F19
  fallback), fires press/release callbacks, suppresses Fn-flag changes to
  prevent the emoji picker.
* ``SpaceHandler`` — detects Space key; fires its ``on_press`` always so the
  ``SmartHotkeyListener`` can decide what to do, and only suppresses the
  Space when the bound ``FnHandler`` reports Fn held (this is the existing
  Fn+Space sticky-toggle behaviour preserved exactly).
* ``GenericHotkeyHandler`` — arbitrary key combo (modifiers + regular keys),
  with configurable suppression. Used for non-Fn hotkeys, the Space trigger
  for non-Fn hotkeys, and Esc cancel.

Backward-compatibility shim
---------------------------
``fn_key_cgevent.FnKeyMonitor`` is preserved as a thin wrapper over a
``MacEventTap``+``FnHandler``+``SpaceHandler`` so ``app.py``'s permission
probe (``FnKeyMonitor`` instantiation at startup to trigger the Input
Monitoring TCC prompt) keeps working without edits.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Callable, List, Optional

# v3.14.31 — leading-edge debounce. Real Fn taps last 80 ms or longer;
# anything below this threshold is hardware/OS chatter. Used as
# defense-in-depth alongside the v3.14.33 trailing-edge hold-quiet timer.
# Bumped from 40 ms (v3.14.31) to 60 ms because the chatter the user saw
# at 18:07 included some sub-60-ms doubles slipping through the 40 ms gate.
_FN_DEBOUNCE_S = 0.06

# v3.14.33 — trailing-edge hold-quiet timer. The 18:07 reproduction proved
# the chatter isn't sub-millisecond hardware bounce — it's macOS firing
# flagsChanged events that genuinely flip the Fn bit on/off at 60–250 ms
# intervals during a single physical hold (Touch ID sensor + Globe key on
# M-series Macs both poke modifier state). v3.14.31's leading-edge
# debounce was the wrong instrument for that timescale.
#
# When the OS reports "Fn released", we don't fire on_release immediately.
# Instead we start a hold-quiet timer for this many seconds. If Fn=1 comes
# back inside the window the user is still physically holding (the OS just
# oscillated); we cancel the pending release. Only if the window expires
# undisturbed do we fire the real release.
#
# 0.15 s is short enough that a deliberate quick tap (~100 ms press + 150 ms
# trailing wait = 250 ms total) still feels snappy — and the audio post-roll
# already adds 150 ms of trailing capture so nothing is lost. Long enough to
# absorb the worst gap seen in the 18:07 log (~250 ms peak, ~60–120 ms typical).
_FN_RELEASE_HOLD_S = 0.15

try:
    from log_util import log as _diag_log  # bundled-app path
except ImportError:
    from src.log_util import log as _diag_log  # source-run path

# v3.14.30 — diagnostic logging. Each MacEventTap / handler gets a unique
# instance id so a single line in app.log tells us which listener fired
# the event. Used to disambiguate "one handler fanning out 13 times" from
# "13 separate handlers each firing once" when the 16:22-style chaos
# reproduces. Behaviour is otherwise unchanged.
_TAP_ID_GEN = itertools.count(1)
_HANDLER_ID_GEN = itertools.count(1)


def _next_tap_id() -> str:
    return f"tap{next(_TAP_ID_GEN):02d}"


def _next_handler_id(kind: str) -> str:
    return f"{kind}{next(_HANDLER_ID_GEN):02d}"

from Quartz import (
    CGEventTapCreate,
    CGEventTapEnable,
    kCGEventFlagsChanged,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventTapOptionDefault,
    kCGHeadInsertEventTap,
    kCGHIDEventTap,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    CFRunLoopStop,
    CFMachPortCreateRunLoopSource,
    kCFRunLoopCommonModes,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    kCGKeyboardEventKeycode,
)


# ── Constants ──────────────────────────────────────────────────────────

# Key name → macOS virtual keycode mapping
KEY_TO_KEYCODE = {
    "space": 49,
    "return": 36,
    "enter": 36,
    "tab": 48,
    "delete": 51,
    "escape": 53,
    "esc": 53,

    # External-keyboard Fn often maps to a function key keycode
    "f13": 105, "f14": 107, "f15": 113, "f16": 106,
    "f17": 64, "f18": 79, "f19": 80,

    # Letters
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3,
    "g": 5, "h": 4, "i": 34, "j": 38, "k": 40, "l": 37,
    "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15,
    "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
    "y": 16, "z": 6,
}

# Modifier name → CGEventFlags bitmask
MODIFIER_FLAGS = {
    "cmd": 0x100000, "command": 0x100000,   # kCGEventFlagMaskCommand
    "shift": 0x20000,                        # kCGEventFlagMaskShift
    "option": 0x80000, "alt": 0x80000,      # kCGEventFlagMaskAlternate
    "control": 0x40000, "ctrl": 0x40000,    # kCGEventFlagMaskControl
    "fn": 0x800000,                          # kCGEventFlagMaskSecondaryFn
}

# Fn-flag bit. Exposed as a name for FnHandler.
_FN_FLAG = 0x800000

# Space keycode shortcut (used by SpaceHandler).
_SPACE_KEYCODE = 49

# External-keyboard Fn keycodes (some keyboards send Fn as a function key)
_EXT_FN_KEYCODES = {105, 107, 113}


def _fire(callback: Optional[Callable[[], None]]) -> None:
    """Run a callback in a daemon thread so the event-tap thread keeps
    pumping events even if the callback blocks (paste, transcription, etc).
    """
    if callback is None:
        return
    threading.Thread(target=callback, daemon=True).start()


# ── Handlers ──────────────────────────────────────────────────────────
#
# Each handler exposes a single ``handle(event_type, event) -> bool`` method
# that returns ``True`` if the event should be suppressed (NULL returned to
# the OS) and ``False`` otherwise. Handlers are called serially from inside
# the one event-tap callback — they share a single execution thread so they
# don't need internal locks for their own state.


class FnHandler:
    """Tracks the Fn key.

    Fires ``on_press`` / ``on_release`` on Fn flag changes, and recognises
    external-keyboard Fn (F13/F14/F15 keycodes). Always suppresses Fn-flag
    changes — this is what stops macOS from launching the emoji picker or
    input-source selector on quick Fn taps.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        # ``_pressed`` reflects what we've reported to user callbacks — True
        # after on_press has been fired, False after on_release has been
        # fired. The OS's view of the Fn flag may oscillate during a single
        # physical hold; this field is the *reported* state, not the OS state.
        self._pressed = False
        # v3.14.31 — monotonic timestamp of last accepted state transition,
        # used by the leading-edge debounce to reject phantom press pairs.
        # Initialised to -1.0 (well in the past on every monotonic clock)
        # so the very first press never gets falsely rejected on systems
        # where time.monotonic() starts from a small value.
        self._last_transition: float = -1.0
        # v3.14.33 — trailing-edge hold-quiet machinery.
        #
        # ``_pending_release`` holds the threading.Timer currently waiting
        # for the OS to potentially re-assert Fn before we fire on_release.
        # ``_release_ticket`` is monotonically incremented every time we
        # schedule, cancel, or supersede a release timer. The timer's
        # callback captures the ticket value it was scheduled with and
        # aborts harmlessly if the ticket has moved on — this is a robust
        # cancel-race-proof pattern: ``threading.Timer.cancel()`` only
        # stops a timer that hasn't already entered its callback, so we
        # can't rely on it alone.
        self._pending_release: Optional[threading.Timer] = None
        self._release_ticket: int = 0
        # Locks state mutation against the race between the event-tap
        # thread (running ``handle()``) and the Timer thread (running
        # ``_maybe_fire_release()``).
        self._state_lock = threading.Lock()
        # v3.14.30 diagnostic: unique id so the log can distinguish
        # multiple FnHandler instances if any are alive simultaneously.
        self._id = _next_handler_id("Fn")
        _diag_log(f"[HOTKEY/{self._id}] FnHandler created")

    @property
    def is_pressed(self) -> bool:
        return self._pressed

    # Backward-compat alias for app.py / wizard polling
    @property
    def _fn_pressed(self) -> bool:
        return self._pressed

    def _debounce_ok(self, now: float) -> bool:
        """Return True iff enough time has passed since the last accepted
        press transition. Leading-edge defense — the primary anti-chatter
        mechanism is the trailing-edge hold-quiet timer.
        """
        return (now - self._last_transition) >= _FN_DEBOUNCE_S

    def _schedule_release(self, source_flags: int) -> None:
        """Called from inside the state lock. Start a hold-quiet timer
        that will fire the release callback in ``_FN_RELEASE_HOLD_S``
        seconds unless cancelled by a re-assertion in the meantime.
        """
        # Bump the ticket so any in-flight timer becomes stale, then
        # capture the new value for this scheduling.
        self._release_ticket += 1
        ticket = self._release_ticket
        timer = threading.Timer(
            _FN_RELEASE_HOLD_S, self._maybe_fire_release, args=(ticket,)
        )
        timer.daemon = True
        self._pending_release = timer
        timer.start()
        _diag_log(
            f"[HOTKEY/{self._id}] Fn release pending "
            f"(flagsChanged, flags=0x{source_flags:x}) — "
            f"waiting {_FN_RELEASE_HOLD_S*1000:.0f}ms to confirm"
        )

    def _cancel_release(self, reason: str) -> None:
        """Called from inside the state lock. Cancel any pending release
        timer. The ticket bump ensures that even if the timer's callback
        has already started running, it will see a stale ticket and abort.
        """
        self._release_ticket += 1
        if self._pending_release is not None:
            self._pending_release.cancel()
            self._pending_release = None
            _diag_log(
                f"[HOTKEY/{self._id}] Fn release CANCELLED ({reason}) — "
                f"still held, no release fired"
            )

    def _maybe_fire_release(self, my_ticket: int) -> None:
        """Timer callback. Runs on a ``threading.Timer`` thread.
        Acquires the state lock, validates the ticket, and fires the
        release callback if it hasn't been superseded.

        Note: we deliberately do *not* touch ``_last_transition`` here.
        The debounce gate is press-to-press: a real user re-press 300 ms
        after the previous press event should be accepted even though the
        release was only confirmed 150 ms ago.
        """
        with self._state_lock:
            if my_ticket != self._release_ticket:
                # Superseded by a cancel or a new scheduling — no-op.
                return
            if not self._pressed:
                # Already released somehow (defensive — shouldn't happen).
                self._pending_release = None
                return
            self._pressed = False
            self._pending_release = None
        _diag_log(
            f"[HOTKEY/{self._id}] Fn release CONFIRMED "
            f"(hold-quiet {_FN_RELEASE_HOLD_S*1000:.0f}ms elapsed)"
        )
        _fire(self._on_release)

    def handle(self, event_type, event) -> bool:
        # Laptop Fn — comes through as a modifier flag change.
        # Uses the v3.14.33 hold-quiet machinery to absorb the
        # 60–250 ms OS oscillation documented in the 18:07 log.
        if event_type == kCGEventFlagsChanged:
            flags = CGEventGetFlags(event)
            is_pressed = bool(flags & _FN_FLAG)
            now = time.monotonic()
            fire_press = False

            with self._state_lock:
                if is_pressed and not self._pressed:
                    # Leading edge: released → pressed.
                    if not self._debounce_ok(now):
                        _diag_log(
                            f"[HOTKEY/{self._id}] Fn press REJECTED (debounce: "
                            f"{(now - self._last_transition)*1000:.1f}ms < "
                            f"{_FN_DEBOUNCE_S*1000:.0f}ms)"
                        )
                        return True
                    self._pressed = True
                    self._last_transition = now
                    _diag_log(
                        f"[HOTKEY/{self._id}] Fn press fired "
                        f"(flagsChanged, flags=0x{flags:x}) "
                        f"thread={threading.current_thread().name}"
                    )
                    fire_press = True
                elif is_pressed and self._pressed:
                    # Re-assertion while we're already reporting "pressed".
                    # If a release timer was running this is OS oscillation
                    # mid-hold — cancel it, the user is still holding.
                    if self._pending_release is not None:
                        self._cancel_release(f"Fn re-asserted, flags=0x{flags:x}")
                elif not is_pressed and self._pressed:
                    # Trailing-edge candidate. Don't fire on_release yet —
                    # start the hold-quiet timer. If a timer is already
                    # pending, leave it alone (we'd just be re-arming the
                    # same window).
                    if self._pending_release is None:
                        self._schedule_release(flags)
                # else: not_pressed → not_pressed (no-op).

            if fire_press:
                _fire(self._on_press)
            # Always suppress Fn-flag changes (prevents emoji picker).
            return True

        # External keyboards sometimes send Fn as F13/F14/F15 keycodes.
        # These come through as clean keyDown/keyUp pairs (no oscillation
        # observed in the wild), so the simpler debounce-only path is
        # sufficient — no need for the hold-quiet timer here.
        if event_type in (kCGEventKeyDown, kCGEventKeyUp):
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if keycode in _EXT_FN_KEYCODES:
                now = time.monotonic()
                fire_press = False
                fire_release = False
                with self._state_lock:
                    if event_type == kCGEventKeyDown and not self._pressed:
                        if not self._debounce_ok(now):
                            return True
                        self._pressed = True
                        self._last_transition = now
                        _diag_log(
                            f"[HOTKEY/{self._id}] Fn press fired (keyDown, "
                            f"keycode={keycode}) "
                            f"thread={threading.current_thread().name}"
                        )
                        fire_press = True
                    elif event_type == kCGEventKeyUp and self._pressed:
                        if not self._debounce_ok(now):
                            return True
                        self._pressed = False
                        self._last_transition = now
                        # Cancel any pending laptop-Fn release timer too —
                        # external Fn keyUp is authoritative.
                        self._cancel_release(f"external Fn keyUp, keycode={keycode}")
                        _diag_log(
                            f"[HOTKEY/{self._id}] Fn release fired (keyUp, "
                            f"keycode={keycode}) "
                            f"thread={threading.current_thread().name}"
                        )
                        fire_release = True
                if fire_press:
                    _fire(self._on_press)
                elif fire_release:
                    _fire(self._on_release)
                return True

        return False


class SpaceHandler:
    """Handles Space when the hotkey is Fn.

    The semantics matter: ``on_press`` fires unconditionally so the
    ``SmartHotkeyListener`` can decide whether this Space tap means
    "enable sticky mode" (Fn held + recording) or just a normal space.

    Suppression is conditional on Fn being held — when Fn is held, both the
    Space KeyDown and its matching KeyUp are suppressed (to stop the macOS
    Fn+Space input-source selector). When Fn is NOT held, Space passes
    through normally so the user can type spaces.

    Requires a reference to an ``FnHandler`` to know the live Fn state.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        fn_handler: FnHandler,
    ) -> None:
        self._on_press = on_press
        self._fn_handler = fn_handler
        self._suppress_next_space_up = False

    def handle(self, event_type, event) -> bool:
        if event_type == kCGEventKeyDown:
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if keycode == _SPACE_KEYCODE:
                _fire(self._on_press)
                if self._fn_handler.is_pressed:
                    self._suppress_next_space_up = True
                    return True

        elif event_type == kCGEventKeyUp:
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if keycode == _SPACE_KEYCODE and self._suppress_next_space_up:
                self._suppress_next_space_up = False
                return True

        return False


class GenericHotkeyHandler:
    """Detects an arbitrary modifier+key combination.

    Used for non-Fn hotkeys (e.g. Cmd+Shift+Space, Option+Space), and also
    for the standalone Space-trigger and Esc-cancel handlers — each is just
    a single-key "combo" with suppress=False.
    """

    def __init__(
        self,
        keys: List[str],
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        suppress: bool = True,
    ) -> None:
        self._keys = [k.lower() for k in keys]
        self._on_press = on_press
        self._on_release = on_release
        self._suppress = suppress

        self._modifiers = [k for k in self._keys if k in MODIFIER_FLAGS]
        self._regular_keys = [k for k in self._keys if k in KEY_TO_KEYCODE]

        self._pressed_modifiers: set = set()
        self._pressed_regular: set = set()
        self._active = False
        # v3.14.30 diagnostic: unique id + remember the keys for logging.
        self._id = _next_handler_id("Gen")
        _diag_log(
            f"[HOTKEY/{self._id}] GenericHotkeyHandler created keys={self._keys} "
            f"suppress={self._suppress}"
        )

    @property
    def is_active(self) -> bool:
        return self._active

    # Backward-compat alias for app.py / wizard polling
    @property
    def _hotkey_active(self) -> bool:
        return self._active

    def _check_state(self) -> bool:
        mods_ok = all(m in self._pressed_modifiers for m in self._modifiers)
        regs_ok = (
            all(k in self._pressed_regular for k in self._regular_keys)
            if self._regular_keys else True
        )
        return mods_ok and regs_ok

    def handle(self, event_type, event) -> bool:
        suppress = False

        if event_type == kCGEventFlagsChanged:
            flags = CGEventGetFlags(event)
            prev = self._pressed_modifiers.copy()
            self._pressed_modifiers.clear()
            for mod_name, mod_flag in MODIFIER_FLAGS.items():
                if mod_name in self._modifiers and (flags & mod_flag):
                    self._pressed_modifiers.add(mod_name)
            modifier_changed = prev != self._pressed_modifiers

            was_active = self._active
            is_active = self._check_state()
            if is_active and not was_active:
                self._active = True
                _diag_log(
                    f"[HOTKEY/{self._id}] press fired (flagsChanged, keys={self._keys}) "
                    f"thread={threading.current_thread().name}"
                )
                _fire(self._on_press)
            elif not is_active and was_active:
                self._active = False
                _diag_log(
                    f"[HOTKEY/{self._id}] release fired (flagsChanged, keys={self._keys}) "
                    f"thread={threading.current_thread().name}"
                )
                _fire(self._on_release)

            # Surgical suppression: only when OUR modifiers changed.
            # (Avoids breaking Cmd+C etc when our hotkey doesn't use Cmd.)
            if modifier_changed and self._suppress:
                suppress = True

        elif event_type in (kCGEventKeyDown, kCGEventKeyUp):
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

            key_name = None
            for name, code in KEY_TO_KEYCODE.items():
                if code == keycode and name in self._regular_keys:
                    key_name = name
                    break

            if key_name:
                if event_type == kCGEventKeyDown:
                    self._pressed_regular.add(key_name)
                else:
                    self._pressed_regular.discard(key_name)

                was_active = self._active
                is_active = self._check_state()
                if is_active and not was_active:
                    self._active = True
                    _diag_log(
                        f"[HOTKEY/{self._id}] press fired (keyDown, "
                        f"key={key_name}, keys={self._keys}) "
                        f"thread={threading.current_thread().name}"
                    )
                    _fire(self._on_press)
                    if self._suppress:
                        suppress = True
                elif not is_active and was_active:
                    self._active = False
                    _diag_log(
                        f"[HOTKEY/{self._id}] release fired (keyUp, "
                        f"key={key_name}, keys={self._keys}) "
                        f"thread={threading.current_thread().name}"
                    )
                    _fire(self._on_release)
                    if self._suppress:
                        suppress = True
                elif self._active and self._suppress:
                    # Active combo — suppress all component keys
                    suppress = True

        return suppress


# ── The single event tap ──────────────────────────────────────────────


class MacEventTap:
    """One HID-level CGEventTap that dispatches events to multiple handlers.

    All handlers share a single ``CFRunLoopRun()`` on a single thread, which
    is the entire point of this refactor — having more than one HID-level
    tap in one process triggers a PyObjC race that segfaults macOS.

    Handlers are added via ``add_handler()`` before ``start()`` is called.
    Each handler returns ``True`` from ``handle()`` to mean "swallow this
    event" — if ANY handler says suppress, the event is suppressed.
    """

    def __init__(self) -> None:
        self._handlers: List = []
        self._tap = None
        self._runloop_source = None
        self._runloop = None
        self._thread: Optional[threading.Thread] = None
        # Lock guards the handler list — add_handler / start happen in main
        # thread, callback iterates in event-tap thread.
        self._lock = threading.Lock()
        # v3.14.30 diagnostic: unique id so the log can tell us if two
        # MacEventTaps are alive at once (which is the v3.14.13 bug the
        # consolidated design was meant to make impossible — observability
        # confirms it stays impossible in practice).
        self._id = _next_tap_id()
        _diag_log(f"[HOTKEY/{self._id}] MacEventTap created")

    def add_handler(self, handler) -> None:
        """Attach a handler. Must be called before ``start()``."""
        with self._lock:
            self._handlers.append(handler)

    def _event_callback(self, proxy, event_type, event, refcon):
        """The ONE callback. Dispatches to every handler, ORs their
        suppression decisions, and returns the event (or None to suppress).
        """
        # System occasionally disables the tap (timeout, user input event):
        # just re-enable and pass the event through.
        if event_type in (
            kCGEventTapDisabledByTimeout,
            kCGEventTapDisabledByUserInput,
        ):
            print("[HOTKEY] Event tap disabled by system — re-enabling")
            CGEventTapEnable(self._tap, True)
            return event

        suppress = False
        try:
            with self._lock:
                handlers = list(self._handlers)
            # Important: iterate ALL handlers even if one says suppress, so
            # they all see the event (otherwise the SpaceHandler wouldn't
            # see Space events when the FnHandler is also looking at them).
            for handler in handlers:
                if handler.handle(event_type, event):
                    suppress = True
        except Exception as e:
            print(f"[HOTKEY] Event error: {e}")

        return None if suppress else event

    def _run(self) -> None:
        try:
            event_mask = (
                (1 << kCGEventFlagsChanged)
                | (1 << kCGEventKeyDown)
                | (1 << kCGEventKeyUp)
            )

            self._tap = CGEventTapCreate(
                kCGHIDEventTap,
                kCGHeadInsertEventTap,
                kCGEventTapOptionDefault,
                event_mask,
                self._event_callback,
                None,
            )

            if self._tap is None:
                print(
                    "[HOTKEY] Failed to create event tap — grant Accessibility "
                    "and Input Monitoring in System Settings → Privacy & Security"
                )
                return

            self._runloop_source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._runloop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self._runloop, self._runloop_source, kCFRunLoopCommonModes)
            CGEventTapEnable(self._tap, True)

            _diag_log(
                f"[HOTKEY/{self._id}] CGEventTap started "
                f"with {len(self._handlers)} handler(s), "
                f"thread={threading.current_thread().name}"
            )
            CFRunLoopRun()  # blocks until CFRunLoopStop
            _diag_log(f"[HOTKEY/{self._id}] CFRunLoopRun returned (tap thread exiting)")

        except Exception as e:
            _diag_log(f"[HOTKEY/{self._id}] Event tap error: {e}")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            _diag_log(f"[HOTKEY/{self._id}] start() called but thread already alive — no-op")
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"MacEventTapThread-{self._id}"
        )
        self._thread.start()

    def stop(self) -> None:
        """Tear down the run loop and disable the tap. Cleanup must happen
        in the right order: ``CFRunLoopStop`` releases the run-loop thread,
        and ``CGEventTapEnable(False)`` makes sure no further callbacks fire
        before we drop the tap reference.
        """
        _diag_log(f"[HOTKEY/{self._id}] stop() called")
        if self._runloop is not None:
            try:
                CFRunLoopStop(self._runloop)
            except Exception:
                pass
        if self._tap is not None:
            try:
                CGEventTapEnable(self._tap, False)
            except Exception:
                pass
        self._tap = None
        self._runloop_source = None
        self._runloop = None
