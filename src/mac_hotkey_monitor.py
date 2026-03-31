"""
Generic Hotkey Monitor for macOS using CGEventTap
Supports ANY key combination (Cmd+Shift+Space, Option+Space, Fn, etc.)
"""

import threading
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


# Key name to keycode mapping
KEY_TO_KEYCODE = {
    # Special keys
    "space": 49,
    "return": 36,
    "enter": 36,
    "tab": 48,
    "delete": 51,
    "escape": 53,
    "esc": 53,

    # Function keys (external keyboard Fn often maps here)
    "f13": 105,
    "f14": 107,
    "f15": 113,
    "f16": 106,
    "f17": 64,
    "f18": 79,
    "f19": 80,

    # Letters
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3,
    "g": 5, "h": 4, "i": 34, "j": 38, "k": 40, "l": 37,
    "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15,
    "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
    "y": 16, "z": 6,
}

# Modifier flag masks
MODIFIER_FLAGS = {
    "cmd": 0x100000,      # kCGEventFlagMaskCommand
    "command": 0x100000,
    "shift": 0x20000,     # kCGEventFlagMaskShift
    "option": 0x80000,    # kCGEventFlagMaskAlternate
    "alt": 0x80000,
    "control": 0x40000,   # kCGEventFlagMaskControl
    "ctrl": 0x40000,
    "fn": 0x800000,       # kCGEventFlagMaskSecondaryFn
}


class MacHotkeyMonitor:
    """Monitors any key combination on macOS using CGEventTap"""

    def __init__(self, keys, on_press, on_release):
        """
        Initialize monitor for specific key combination.

        Args:
            keys: List of key names, e.g., ["cmd", "shift", "space"] or ["fn"]
            on_press: Callback when hotkey is pressed
            on_release: Callback when hotkey is released
        """
        self._keys = [k.lower() for k in keys]
        self._on_press = on_press
        self._on_release = on_release

        # Separate modifiers from regular keys
        self._modifiers = [k for k in self._keys if k in MODIFIER_FLAGS]
        self._regular_keys = [k for k in self._keys if k in KEY_TO_KEYCODE]

        # Track currently pressed keys
        self._pressed_modifiers = set()
        self._pressed_regular = set()
        self._hotkey_active = False

        self._tap = None
        self._runloop_source = None
        self._runloop = None
        self._lock = threading.Lock()
        self._thread = None

        print(f"[HOTKEY] MacHotkeyMonitor initialized for: {self._keys}")
        print(f"[HOTKEY]   Modifiers: {self._modifiers}")
        print(f"[HOTKEY]   Regular keys: {self._regular_keys}")

    def _check_hotkey_state(self):
        """Check if current pressed keys match hotkey combination"""
        # All modifiers must be pressed
        modifiers_match = all(mod in self._pressed_modifiers for mod in self._modifiers)

        # All regular keys must be pressed (or none if modifier-only hotkey)
        if self._regular_keys:
            regular_match = all(key in self._pressed_regular for key in self._regular_keys)
        else:
            regular_match = True

        return modifiers_match and regular_match

    def _event_callback(self, proxy, event_type, event, refcon):
        """Called for keyboard events"""
        try:
            # Re-enable tap if system disabled it
            if event_type == kCGEventTapDisabledByTimeout or event_type == kCGEventTapDisabledByUserInput:
                print("[HOTKEY] Event tap disabled by system - re-enabling")
                CGEventTapEnable(self._tap, True)
                return event

            # Handle modifier flag changes
            if event_type == kCGEventFlagsChanged:
                flags = CGEventGetFlags(event)

                with self._lock:
                    # Update which modifiers are pressed
                    self._pressed_modifiers.clear()
                    for mod_name, mod_flag in MODIFIER_FLAGS.items():
                        if mod_name in self._modifiers and (flags & mod_flag):
                            self._pressed_modifiers.add(mod_name)

                    # Check if hotkey state changed
                    was_active = self._hotkey_active
                    is_active = self._check_hotkey_state()

                    if is_active and not was_active:
                        self._hotkey_active = True
                        threading.Thread(target=self._on_press, daemon=True).start()
                        print(f"[HOTKEY] Activated: {self._keys}")
                    elif not is_active and was_active:
                        self._hotkey_active = False
                        threading.Thread(target=self._on_release, daemon=True).start()
                        print(f"[HOTKEY] Deactivated: {self._keys}")

            # Handle regular key presses
            elif event_type == kCGEventKeyDown or event_type == kCGEventKeyUp:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

                with self._lock:
                    # Find which key name matches this keycode
                    key_name = None
                    for name, code in KEY_TO_KEYCODE.items():
                        if code == keycode and name in self._regular_keys:
                            key_name = name
                            break

                    if key_name:
                        if event_type == kCGEventKeyDown:
                            self._pressed_regular.add(key_name)
                            print(f"[HOTKEY] Key down: {key_name} (code {keycode})")
                        else:
                            self._pressed_regular.discard(key_name)
                            print(f"[HOTKEY] Key up: {key_name} (code {keycode})")

                        # Check if hotkey state changed
                        was_active = self._hotkey_active
                        is_active = self._check_hotkey_state()

                        if is_active and not was_active:
                            self._hotkey_active = True
                            threading.Thread(target=self._on_press, daemon=True).start()
                            print(f"[HOTKEY] Activated: {self._keys}")
                            # Suppress the key event to prevent system handling
                            return None
                        elif not is_active and was_active:
                            self._hotkey_active = False
                            threading.Thread(target=self._on_release, daemon=True).start()
                            print(f"[HOTKEY] Deactivated: {self._keys}")
                            # Suppress the key event
                            return None

                        # If hotkey is active, suppress all component keys
                        if self._hotkey_active:
                            return None

        except Exception as e:
            print(f"[HOTKEY] Event error: {e}")

        return event

    def _run_event_tap(self):
        """Run the event tap in its own thread"""
        try:
            # Create event mask for all keyboard events
            event_mask = (1 << kCGEventFlagsChanged) | (1 << kCGEventKeyDown) | (1 << kCGEventKeyUp)

            # Use HID-level tap for highest priority
            self._tap = CGEventTapCreate(
                kCGHIDEventTap,
                kCGHeadInsertEventTap,
                kCGEventTapOptionDefault,
                event_mask,
                self._event_callback,
                None
            )

            if self._tap is None:
                print("[HOTKEY] Failed to create event tap - grant Accessibility permission")
                return

            # Create run loop source
            self._runloop_source = CFMachPortCreateRunLoopSource(None, self._tap, 0)

            # Get current run loop and add source
            self._runloop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self._runloop, self._runloop_source, kCFRunLoopCommonModes)

            # Enable the tap
            CGEventTapEnable(self._tap, True)

            print("[HOTKEY] Event tap started")

            # Run the loop (blocks until stopped)
            CFRunLoopRun()

        except Exception as e:
            print(f"[HOTKEY] Event tap error: {e}")

    def start(self):
        """Start monitoring in background thread"""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run_event_tap,
                daemon=True,
                name="MacHotkeyMonitorThread"
            )
            self._thread.start()

    def stop(self):
        """Stop monitoring"""
        if self._runloop:
            CFRunLoopStop(self._runloop)
        if self._tap:
            CGEventTapEnable(self._tap, False)
        self._tap = None
        self._runloop_source = None
        self._runloop = None
