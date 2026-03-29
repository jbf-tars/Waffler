"""
Fn Key + Space Detection for macOS using CGEventTap
Works without crashing - no pynput needed
"""

import threading
import os
from Quartz import (
    CGEventTapCreate,
    CGEventTapEnable,
    CGEventCreateCopy,
    CGEventSetFlags,
    kCGEventFlagsChanged,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventTapOptionDefault,
    kCGHeadInsertEventTap,
    kCGSessionEventTap,
    kCGAnnotatedSessionEventTap,
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


class FnKeyMonitor:
    """Monitors Fn key and Space key using CGEventTap (no pynput crashes)"""

    def __init__(self, on_fn_press, on_fn_release, on_space_press=None):
        self._on_fn_press = on_fn_press
        self._on_fn_release = on_fn_release
        self._on_space_press = on_space_press
        self._fn_pressed = False
        self._suppress_next_space_up = False  # Track if we should suppress Space KeyUp
        self._tap = None
        self._runloop_source = None
        self._runloop = None
        self._lock = threading.Lock()
        self._thread = None

    def _event_callback(self, proxy, event_type, event, refcon):
        """Called for both modifier changes and key presses"""
        try:
            # Re-enable tap if system disabled it (e.g., when popup appears)
            if event_type == kCGEventTapDisabledByTimeout or event_type == kCGEventTapDisabledByUserInput:
                print("[FN_KEY] Event tap disabled by system - re-enabling")
                CGEventTapEnable(self._tap, True)
                return event

            # Check for external keyboard Fn (often mapped to F13-F19)
            # Some external keyboards send Fn as keycode 105 (F13)
            if event_type == kCGEventKeyDown or event_type == kCGEventKeyUp:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                # F13 = 105, F14 = 107, F15 = 113 (common Fn mappings on external keyboards)
                if keycode in [105, 107, 113]:
                    print(f"[FN_KEY] External keyboard Fn detected (keycode {keycode})")
                    with self._lock:
                        if event_type == kCGEventKeyDown and not self._fn_pressed:
                            self._fn_pressed = True
                            threading.Thread(target=self._on_fn_press, daemon=True).start()
                            return None
                        elif event_type == kCGEventKeyUp and self._fn_pressed:
                            self._fn_pressed = False
                            threading.Thread(target=self._on_fn_release, daemon=True).start()
                            return None

            # Check for Fn key flag changes (MacBook keyboards)
            if event_type == kCGEventFlagsChanged:
                # kCGEventFlagMaskSecondaryFn = 0x800000 (bit 23)
                fn_flag = 0x800000
                flags = CGEventGetFlags(event)
                is_fn_pressed = bool(flags & fn_flag)

                # Track state changes and fire callbacks
                with self._lock:
                    if is_fn_pressed and not self._fn_pressed:
                        self._fn_pressed = True
                        threading.Thread(target=self._on_fn_press, daemon=True).start()
                    elif not is_fn_pressed and self._fn_pressed:
                        self._fn_pressed = False
                        threading.Thread(target=self._on_fn_release, daemon=True).start()

                # ALWAYS suppress ALL Fn key flag changes (even rapid presses)
                # This prevents macOS emoji picker/input source selector from triggering
                return None

            # Check for Space key press
            elif event_type == kCGEventKeyDown:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                if keycode == 49:  # Space key = keycode 49
                    with self._lock:
                        fn_state = self._fn_pressed

                    print(f"[FN_KEY] Space pressed, Fn is {'HELD' if fn_state else 'NOT HELD'}")

                    # Trigger app's sticky mode handler
                    if self._on_space_press:
                        threading.Thread(target=self._on_space_press, daemon=True).start()

                    # ALWAYS suppress Space (both Fn+Space and Space alone)
                    # The callback in smart_hotkey.py will re-inject if not used for control
                    self._suppress_next_space_up = True
                    print("[FN_KEY] Suppressing Space KeyDown")
                    return None  # Suppress to prevent typing during sticky mode control

            # Check for Space key release (KeyUp)
            elif event_type == kCGEventKeyUp:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                if keycode == 49:  # Space key = keycode 49
                    with self._lock:
                        if self._suppress_next_space_up:
                            self._suppress_next_space_up = False
                            print("[FN_KEY] Suppressing Space KeyUp")
                            return None  # Suppress the KeyUp event too

        except Exception as e:
            print(f"Event error: {e}")

        # Return the event unchanged
        return event

    def _run_event_tap(self):
        """Run the event tap in its own thread"""
        try:
            # Create event mask for flags changed, key down, and key up events
            event_mask = (1 << kCGEventFlagsChanged) | (1 << kCGEventKeyDown) | (1 << kCGEventKeyUp)

            # Use kCGHIDEventTap (hardware level) for highest priority
            # This intercepts events before system handlers like input source switcher
            tap_location = kCGHIDEventTap

            # Create event tap
            self._tap = CGEventTapCreate(
                tap_location,
                kCGHeadInsertEventTap,
                kCGEventTapOptionDefault,
                event_mask,
                self._event_callback,
                None
            )

            if self._tap is None:
                print("Failed to create event tap - grant Accessibility permission")
                print("  System Settings > Privacy & Security > Accessibility")
                return

            # Create run loop source
            self._runloop_source = CFMachPortCreateRunLoopSource(None, self._tap, 0)

            # Get current run loop and add source
            self._runloop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self._runloop, self._runloop_source, kCFRunLoopCommonModes)

            # Enable the tap
            CGEventTapEnable(self._tap, True)

            # Run the loop (blocks until stopped)
            CFRunLoopRun()

        except Exception as e:
            print(f"Event tap error: {e}")

    def start(self):
        """Start monitoring in background thread"""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run_event_tap,
                daemon=True,
                name="FnKeyMonitorThread"
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

    def is_fn_pressed(self):
        """Check current Fn key state"""
        with self._lock:
            return self._fn_pressed
