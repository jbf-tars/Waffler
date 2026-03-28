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
            # Check for Fn key flag changes
            if event_type == kCGEventFlagsChanged:
                # kCGEventFlagMaskSecondaryFn = 0x800000 (bit 23)
                fn_flag = 0x800000
                flags = CGEventGetFlags(event)
                is_fn_pressed = bool(flags & fn_flag)

                with self._lock:
                    if is_fn_pressed and not self._fn_pressed:
                        self._fn_pressed = True
                        threading.Thread(target=self._on_fn_press, daemon=True).start()
                        # Try stripping Fn flag instead of suppressing completely
                        # This might prevent the language switcher popup
                        try:
                            modified_event = CGEventCreateCopy(event)
                            new_flags = flags & ~fn_flag  # Remove Fn flag
                            CGEventSetFlags(modified_event, new_flags)
                            return modified_event
                        except:
                            # Fallback to suppression if flag manipulation fails
                            return None
                    elif not is_fn_pressed and self._fn_pressed:
                        self._fn_pressed = False
                        threading.Thread(target=self._on_fn_release, daemon=True).start()
                        # Strip Fn flag on release too
                        try:
                            modified_event = CGEventCreateCopy(event)
                            new_flags = flags & ~fn_flag
                            CGEventSetFlags(modified_event, new_flags)
                            return modified_event
                        except:
                            return None

            # Check for Space key press
            elif event_type == kCGEventKeyDown:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                if keycode == 49:  # Space key = keycode 49
                    # Trigger app's sticky mode handler
                    if self._on_space_press:
                        threading.Thread(target=self._on_space_press, daemon=True).start()

                    # Suppress event if Fn is held to prevent input source selector
                    with self._lock:
                        if self._fn_pressed:
                            self._suppress_next_space_up = True
                            return None  # Suppress Fn+Space system shortcut

            # Check for Space key release (KeyUp)
            elif event_type == kCGEventKeyUp:
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                if keycode == 49:  # Space key = keycode 49
                    with self._lock:
                        if self._suppress_next_space_up:
                            self._suppress_next_space_up = False
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

            # Try kCGAnnotatedSessionEventTap first (higher priority, might bypass system popups)
            # Falls back to kCGSessionEventTap if not available
            try:
                tap_location = kCGAnnotatedSessionEventTap
            except NameError:
                tap_location = kCGSessionEventTap

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
