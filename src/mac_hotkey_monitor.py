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

import threading
from typing import Callable, List, Optional

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
        self._pressed = False

    @property
    def is_pressed(self) -> bool:
        return self._pressed

    # Backward-compat alias for app.py / wizard polling
    @property
    def _fn_pressed(self) -> bool:
        return self._pressed

    def handle(self, event_type, event) -> bool:
        # Laptop Fn — comes through as a modifier flag change
        if event_type == kCGEventFlagsChanged:
            flags = CGEventGetFlags(event)
            is_pressed = bool(flags & _FN_FLAG)
            if is_pressed and not self._pressed:
                self._pressed = True
                _fire(self._on_press)
            elif not is_pressed and self._pressed:
                self._pressed = False
                _fire(self._on_release)
            # Always suppress Fn-flag changes (prevents emoji picker)
            return True

        # External keyboards sometimes send Fn as F13/F14/F15 keycodes
        if event_type in (kCGEventKeyDown, kCGEventKeyUp):
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if keycode in _EXT_FN_KEYCODES:
                if event_type == kCGEventKeyDown and not self._pressed:
                    self._pressed = True
                    _fire(self._on_press)
                elif event_type == kCGEventKeyUp and self._pressed:
                    self._pressed = False
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
                _fire(self._on_press)
            elif not is_active and was_active:
                self._active = False
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
                    _fire(self._on_press)
                    if self._suppress:
                        suppress = True
                elif not is_active and was_active:
                    self._active = False
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

            print(f"[HOTKEY] Single CGEventTap started with {len(self._handlers)} handler(s)")
            CFRunLoopRun()  # blocks until CFRunLoopStop

        except Exception as e:
            print(f"[HOTKEY] Event tap error: {e}")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="MacEventTapThread"
        )
        self._thread.start()

    def stop(self) -> None:
        """Tear down the run loop and disable the tap. Cleanup must happen
        in the right order: ``CFRunLoopStop`` releases the run-loop thread,
        and ``CGEventTapEnable(False)`` makes sure no further callbacks fire
        before we drop the tap reference.
        """
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
