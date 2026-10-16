"""macOS input plumbing for safe paste: modifier state, the paste keystroke,
the active keyboard layout, and a best-effort Accessibility read of the
focused text field.

Everything here is lazy: no framework is touched at import time, so the module
imports cleanly on Windows and in CI. Functions that need macOS return a safe
"unknown" answer instead of raising when the framework is missing.

Accessibility is read through ctypes rather than PyObjC's ApplicationServices
binding, because that binding is not one of the app's declared dependencies
(and the PyObjC ``AXIsProcessTrusted`` wrapper is known to cache a stale
``False`` in frozen builds, see CHANGELOG 3.14.x).
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

# ── Modifier flags (CGEventFlags) ─────────────────────────────────────────

MOD_SHIFT = 0x00020000
MOD_CONTROL = 0x00040000
MOD_OPTION = 0x00080000
MOD_COMMAND = 0x00100000
MOD_FN = 0x00800000
# Caps Lock, Help and the numeric-pad bit are deliberately not here: they can
# stay set without changing what Cmd+V does.
HOTKEY_MODIFIER_MASK = MOD_SHIFT | MOD_CONTROL | MOD_OPTION | MOD_COMMAND | MOD_FN


def modifier_flags() -> int:
    """Physical modifier keys held right now (0 when none, or off macOS)."""
    if sys.platform != "darwin":
        return 0
    from Quartz import CGEventSourceFlagsState, kCGEventSourceStateHIDSystemState
    return int(CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState))


# ── Keyboard layout → the keycode that types "v" ──────────────────────────

_kTISPropertyUnicodeKeyLayoutData = "kTISPropertyUnicodeKeyLayoutData"
_CMD_MODIFIER_STATE = 1  # (cmdKey >> 8) & 0xFF, what UCKeyTranslate expects


def _carbon():
    """Carbon + CoreFoundation with prototypes set (Text Input Services lives
    in Carbon's HIToolbox)."""
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
    v = ctypes.c_void_p
    cf.CFRelease.argtypes = [v]
    cf.CFDataGetBytePtr.restype = v
    cf.CFDataGetBytePtr.argtypes = [v]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetLength.argtypes = [v]
    carbon.TISCopyCurrentKeyboardLayoutInputSource.restype = v
    carbon.TISCopyCurrentKeyboardLayoutInputSource.argtypes = []
    carbon.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.restype = v
    carbon.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.argtypes = []
    carbon.TISGetInputSourceProperty.restype = v
    carbon.TISGetInputSourceProperty.argtypes = [v, v]
    carbon.LMGetKbdType.restype = ctypes.c_uint8
    carbon.UCKeyTranslate.restype = ctypes.c_int32
    carbon.UCKeyTranslate.argtypes = [
        v, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_uint16),
    ]
    return cf, carbon


def _mapper_from_table(table, kbd_type: int) -> Callable[[int], Optional[str]]:
    """``keycode -> character`` for a keyboard layout's 'uchr' table, typed
    with Command held. (With Command down, Russian/Arabic/Greek layouts map
    the V key to a Latin "v", and "Dvorak - QWERTY Command" keeps QWERTY's
    position, which is exactly what an app sees for Cmd+V.)"""
    _, carbon = _carbon()

    def char_for(keycode: int) -> Optional[str]:
        dead = ctypes.c_uint32(0)
        n = ctypes.c_ulong(0)
        buf = (ctypes.c_uint16 * 4)()
        # action 3 = display (no dead-key side effects); options 1 = no dead keys
        err = carbon.UCKeyTranslate(table, keycode, 3, _CMD_MODIFIER_STATE, kbd_type,
                                    1, ctypes.byref(dead), 4, ctypes.byref(n), buf)
        if err != 0 or not n.value:
            return None
        return "".join(chr(c) for c in buf[:n.value])

    return char_for


def _layout_char_mapper(ascii_capable: bool = False) -> Optional[Callable[[int], Optional[str]]]:
    """Build ``keycode -> character`` for the active layout (or, with
    ``ascii_capable``, the nearest Latin layout). None if unavailable. Text
    Input Services is documented as main-thread-only, so callers go through
    ``v_keycode_for_current_layout``."""
    if sys.platform != "darwin":
        return None
    try:
        cf, carbon = _carbon()
        getter = (carbon.TISCopyCurrentASCIICapableKeyboardLayoutInputSource if ascii_capable
                  else carbon.TISCopyCurrentKeyboardLayoutInputSource)
        prop = ctypes.c_void_p.in_dll(carbon, _kTISPropertyUnicodeKeyLayoutData)
        src = getter()
        if not src:
            return None
        data = carbon.TISGetInputSourceProperty(src, prop)
        if not data:
            cf.CFRelease(src)
            return None
        # Copy the (small) layout table out so the source can be released now.
        size = int(cf.CFDataGetLength(data))
        table = ctypes.create_string_buffer(ctypes.string_at(cf.CFDataGetBytePtr(data), size), size)
        cf.CFRelease(src)
        return _mapper_from_table(table, int(carbon.LMGetKbdType()))
    except Exception:
        return None


def resolve_keycode(char_for_keycode: Optional[Callable[[int], Optional[str]]], target: str,
                    preferred: int, default: Optional[int]) -> Optional[int]:
    """The hardware keycode whose character (with Command held) is ``target``.

    ``char_for_keycode`` maps a keycode to the character it types under the
    active layout. ``preferred`` is where QWERTY keeps the key (and where
    layouts that keep QWERTY positions for Command shortcuts keep it too): it
    wins when more than one key makes the letter. When a layout offers no such
    letter (or the mapper is missing or fails) ``default`` is returned.
    """
    if char_for_keycode is None:
        return default
    found = []
    try:
        for code in range(128):
            ch = char_for_keycode(code)
            if ch and ch.lower() == target:
                found.append(code)
    except Exception:
        return default
    if not found:
        return default
    return preferred if preferred in found else found[0]


def resolve_v_keycode(char_for_keycode: Optional[Callable[[int], Optional[str]]],
                      default: Optional[int] = 9) -> Optional[int]:
    """The hardware keycode that types "v" with Command held (the paste key).

    QWERTY gives 9; Dvorak gives 47; layouts that keep QWERTY positions for
    Command shortcuts give 9 again. When a layout offers no Latin "v" (or the
    mapper is missing or fails) ``default`` is returned. Keycode 9 wins when
    more than one key makes a "v".
    """
    return resolve_keycode(char_for_keycode, "v", 9, default)


def _resolve_char_on_main_thread(target: str, preferred: int) -> Optional[int]:
    mapper = _layout_char_mapper()
    code = resolve_keycode(mapper, target, preferred, None) if mapper else None
    if code is None:
        # Non-Latin layouts (Russian, Arabic, Greek...) keep Latin shortcuts.
        mapper = _layout_char_mapper(ascii_capable=True)
        code = resolve_keycode(mapper, target, preferred, None) if mapper else None
    return code


def _resolve_on_main_thread() -> Optional[int]:
    return _resolve_char_on_main_thread("v", 9)


_cached_keycodes: dict = {}


def keycode_for_current_layout(target: str, preferred: int, default: int,
                               timeout: float = 0.15) -> int:
    """The hardware keycode that types ``target`` (a letter) with Command held,
    under the active keyboard layout.

    Text Input Services must be called on the main thread, so from a worker
    thread this hops to the main run loop and waits up to ``timeout`` s; if the
    main thread is busy (or no run loop is running) the last good answer for
    that letter, then ``default`` (QWERTY's), is used. Never raises.
    """
    if sys.platform != "darwin":
        return default
    code = None
    try:
        if threading.current_thread() is threading.main_thread():
            code = _resolve_char_on_main_thread(target, preferred)
        else:
            from PyObjCTools import AppHelper
            done = threading.Event()
            box = []

            def work():
                try:
                    box.append(_resolve_char_on_main_thread(target, preferred))
                finally:
                    done.set()

            AppHelper.callAfter(work)
            if done.wait(timeout) and box:
                code = box[0]
    except Exception:
        code = None
    if code is not None:
        _cached_keycodes[target] = int(code)
        return _cached_keycodes[target]
    return _cached_keycodes.get(target, default)


def v_keycode_for_current_layout(default: int = 9, timeout: float = 0.15) -> int:
    """The keycode for Cmd+V under the active layout (see
    ``keycode_for_current_layout``). QWERTY's 9 when it cannot be told."""
    return keycode_for_current_layout("v", 9, default, timeout)


def c_keycode_for_current_layout(default: int = 8, timeout: float = 0.15) -> int:
    """The keycode for Cmd+C under the active layout (QWERTY's 8 when it
    cannot be told). Used by the copy fallback that reads a selection."""
    return keycode_for_current_layout("c", 8, default, timeout)


# ── Sending Cmd+<key> ─────────────────────────────────────────────────────

def send_command_key(keycode: int, hold: float = 0.02) -> None:
    """Post Cmd+<keycode> down then up to the HID event tap. Raises on failure
    so the caller can report it (the old code swallowed errors silently)."""
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )
    down = CGEventCreateKeyboardEvent(None, keycode, True)
    up = CGEventCreateKeyboardEvent(None, keycode, False)
    if down is None or up is None:
        raise RuntimeError("could not create keyboard events")
    # Flags are set to Command only, so a still-held Fn/Shift from the hotkey
    # cannot turn the shortcut into something else.
    CGEventSetFlags(down, kCGEventFlagMaskCommand)
    CGEventSetFlags(up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(hold)
    CGEventPost(kCGHIDEventTap, up)


# ── Accessibility: what is in the focused text field? ─────────────────────

TEXT = "text"                # editable text we can read: fingerprint is set
OPAQUE = "opaque"            # something has focus but hides its text (Electron, terminals...)
NONE = "none"                # Accessibility works and nothing is focused
NOT_EDITABLE = "not_editable"  # focused element is positively not a text field
BLOCKED = "blocked"          # Accessibility itself is unavailable (not trusted / error)


@dataclass(frozen=True)
class FocusState:
    kind: str
    fingerprint: Optional[Tuple[int, int, int, int]] = None  # (length, hash, sel_loc, sel_len)
    role: str = ""


_NON_EDITABLE_ROLES = frozenset({
    "AXButton", "AXCheckBox", "AXRadioButton", "AXMenuItem", "AXMenuBarItem",
    "AXMenu", "AXMenuButton", "AXPopUpButton", "AXImage", "AXSlider", "AXDockItem",
})

_SECURE_SUBROLE = "AXSecureTextField"

_AX_SUCCESS = 0
_AX_API_DISABLED = -25211
_AX_NO_VALUE = -25212
_kAXValueTypeCFRange = 4
_kCFStringEncodingUTF8 = 0x08000100


class _CFRange(ctypes.Structure):
    _fields_ = [("location", ctypes.c_long), ("length", ctypes.c_long)]


class _AXLibs:
    """Lazily loaded CoreFoundation + ApplicationServices with prototypes."""

    def __init__(self):
        v = ctypes.c_void_p
        self.cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        cf, ax = self.cf, self.ax
        cf.CFRelease.argtypes = [v]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFGetTypeID.argtypes = [v]
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetLength.argtypes = [v]
        cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_ubyte
        cf.CFStringGetCString.argtypes = [v, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = v
        cf.CFStringCreateWithCString.argtypes = [v, ctypes.c_char_p, ctypes.c_uint32]
        ax.AXUIElementCreateSystemWide.restype = v
        ax.AXUIElementCreateSystemWide.argtypes = []
        ax.AXUIElementCreateApplication.restype = v
        ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
        ax.AXUIElementSetMessagingTimeout.restype = ctypes.c_int32
        ax.AXUIElementSetMessagingTimeout.argtypes = [v, ctypes.c_float]
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
        ax.AXUIElementCopyAttributeValue.argtypes = [v, v, ctypes.POINTER(v)]
        ax.AXValueGetType.restype = ctypes.c_uint32
        ax.AXValueGetType.argtypes = [v]
        ax.AXValueGetValue.restype = ctypes.c_ubyte
        ax.AXValueGetValue.argtypes = [v, ctypes.c_uint32, v]
        ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        ax.AXIsProcessTrustedWithOptions.argtypes = [v]
        self._attrs = {}
        self._sysw = None

    def trusted(self) -> bool:
        """Accessibility trust, re-queried live (no prompt)."""
        return bool(self.ax.AXIsProcessTrustedWithOptions(None))

    def system_wide(self, timeout: float):
        # Note: Accessibility applies a timeout set on the system-wide element
        # to every AX request this process makes, not just ours.
        if self._sysw is None:
            self._sysw = self.ax.AXUIElementCreateSystemWide()
            self.ax.AXUIElementSetMessagingTimeout(self._sysw, timeout)
        return self._sysw

    def application(self, pid: int, timeout: float):
        """Retained application element for ``pid`` (caller releases)."""
        el = self.ax.AXUIElementCreateApplication(pid)
        self.ax.AXUIElementSetMessagingTimeout(el, timeout)
        return el

    def release_element(self, element) -> None:
        if element:
            self.cf.CFRelease(element)

    def attr(self, name: str):
        s = self._attrs.get(name)
        if s is None:
            s = self.cf.CFStringCreateWithCString(None, name.encode(), _kCFStringEncodingUTF8)
            self._attrs[name] = s  # tiny constants, kept for the process lifetime
        return s

    def copy_attr(self, element, name: str):
        """(error, retained CFTypeRef or None). Caller releases the value."""
        out = ctypes.c_void_p()
        err = int(self.ax.AXUIElementCopyAttributeValue(element, self.attr(name), ctypes.byref(out)))
        if err != _AX_SUCCESS or not out.value:
            return err, None
        return err, out

    def release(self, ref):
        if ref is not None and getattr(ref, "value", ref):
            self.cf.CFRelease(ref.value if hasattr(ref, "value") else ref)

    def is_string(self, ref) -> bool:
        return ref is not None and self.cf.CFGetTypeID(ref.value) == self.cf.CFStringGetTypeID()

    def string_length(self, ref) -> int:
        return int(self.cf.CFStringGetLength(ref.value))

    def to_str(self, ref) -> str:
        n = self.string_length(ref)
        size = int(self.cf.CFStringGetMaximumSizeForEncoding(n, _kCFStringEncodingUTF8)) + 1
        buf = ctypes.create_string_buffer(size)
        if not self.cf.CFStringGetCString(ref.value, buf, size, _kCFStringEncodingUTF8):
            return ""
        return buf.value.decode("utf-8", errors="replace")

    def to_range(self, ref) -> Optional[Tuple[int, int]]:
        if ref is None or self.ax.AXValueGetType(ref.value) != _kAXValueTypeCFRange:
            return None
        rng = _CFRange()
        if not self.ax.AXValueGetValue(ref.value, _kAXValueTypeCFRange, ctypes.byref(rng)):
            return None
        return int(rng.location), int(rng.length)


class AXFocusReader:
    """Reads "what is in the focused text field" through Accessibility.

    ``read()`` never raises and is bounded by an Accessibility messaging
    timeout, so a hung app cannot stall a paste. Anything it cannot tell comes
    back as ``OPAQUE`` (an unverified paste, not a failed one).
    """

    def __init__(self, timeout: float = 0.25, max_value_chars: int = 1_000_000):
        self.timeout = timeout
        self.max_value_chars = max_value_chars
        self._libs = None

    def read(self) -> FocusState:
        if sys.platform != "darwin":
            return FocusState(BLOCKED)
        try:
            return self._read()
        except Exception:
            return FocusState(OPAQUE)

    def warm(self) -> None:
        """Load the Accessibility frameworks now (the first load costs ~35 ms)."""
        if sys.platform == "darwin":
            self._lib()

    # -- internals -------------------------------------------------------

    def _lib(self) -> _AXLibs:
        if self._libs is None:
            self._libs = _AXLibs()
        return self._libs

    def _focused_element(self, L: _AXLibs):
        """``(FocusState or None, retained element or None)``.

        Careful with "nothing focused": a process that is *not* trusted for
        Accessibility gets NoValue/CannotComplete back from these calls too, so
        NONE is only reported when we are trusted AND the app has no focused
        element AND no focused window. Anything murkier is OPAQUE (unverified).
        """
        if not L.trusted():
            return FocusState(BLOCKED), None
        err, el = L.copy_attr(L.system_wide(self.timeout), "AXFocusedUIElement")
        if el is not None:
            return None, el
        if err == _AX_API_DISABLED:
            return FocusState(BLOCKED), None
        # System-wide lookup can fail for apps that only answer per-app queries.
        pid = self._frontmost_pid()
        if not pid:
            return FocusState(OPAQUE), None
        app = L.application(pid, self.timeout)
        try:
            err2, el = L.copy_attr(app, "AXFocusedUIElement")
            if el is not None:
                return None, el
            if err2 == _AX_API_DISABLED:
                return FocusState(BLOCKED), None
            if err2 == _AX_NO_VALUE:
                err_win, win = L.copy_attr(app, "AXFocusedWindow")
                if win is not None:
                    L.release(win)  # a window has focus but no element: can't tell
                    return FocusState(OPAQUE), None
                if err_win == _AX_NO_VALUE:
                    return FocusState(NONE), None
            return FocusState(OPAQUE), None
        finally:
            L.release_element(app)

    @staticmethod
    def _frontmost_pid() -> int:
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return int(app.processIdentifier()) if app is not None else 0
        except Exception:
            return 0

    def _read(self) -> FocusState:
        L = self._lib()
        state, el = self._focused_element(L)
        if state is not None:
            return state
        try:
            return self._describe(L, el)
        finally:
            L.release(el)

    def _describe(self, L: _AXLibs, el) -> FocusState:
        """Classify one focused element (does not release ``el``)."""
        refs = []
        try:
            _, role_ref = L.copy_attr(el, "AXRole")
            refs.append(role_ref)
            role = L.to_str(role_ref) if L.is_string(role_ref) else ""
            if role in _NON_EDITABLE_ROLES:
                return FocusState(NOT_EDITABLE, role=role)
            # A password field: its contents are never read, only pasted into.
            _, sub = L.copy_attr(el, "AXSubrole")
            refs.append(sub)
            if L.is_string(sub) and L.to_str(sub) == _SECURE_SUBROLE:
                return FocusState(OPAQUE, role=role)
            _, val = L.copy_attr(el, "AXValue")
            refs.append(val)
            if not L.is_string(val):
                return FocusState(OPAQUE, role=role)
            length = L.string_length(val)
            if length > self.max_value_chars:
                return FocusState(OPAQUE, role=role)
            text = L.to_str(val)
            _, sel = L.copy_attr(el, "AXSelectedTextRange")
            refs.append(sel)
            loc, ln = L.to_range(sel) or (-1, -1)
            return FocusState(TEXT, (length, hash(text), loc, ln), role)
        finally:
            for r in refs:
                L.release(r)
