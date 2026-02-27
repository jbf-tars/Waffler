"""
Fn Key Monitor for macOS
Uses PyObjC NSEvent monitoring to detect Fn key state changes
"""

import threading
from AppKit import NSEvent


class FnKeyMonitor:
    """Monitors Fn key state using NSEvent global monitoring"""

    def __init__(self, on_fn_press, on_fn_release):
        self._on_fn_press = on_fn_press
        self._on_fn_release = on_fn_release
        self._fn_pressed = False
        self._monitor = None
        self._lock = threading.Lock()

    def _handle_event(self, event):
        """Called on any modifier flag change"""
        try:
            # NSEventModifierFlagFunction = 0x800000 (bit 23)
            fn_flag = 0x800000
            is_fn_pressed = bool(event.modifierFlags() & fn_flag)

            with self._lock:
                if is_fn_pressed and not self._fn_pressed:
                    self._fn_pressed = True
                    # Run callback in background thread
                    threading.Thread(target=self._on_fn_press, daemon=True).start()
                elif not is_fn_pressed and self._fn_pressed:
                    self._fn_pressed = False
                    # Run callback in background thread
                    threading.Thread(target=self._on_fn_release, daemon=True).start()
        except Exception as e:
            print(f"⚠️  Fn monitor error: {e}")

    def start(self):
        """Start monitoring Fn key"""
        try:
            # NSEventMaskFlagsChanged = 4096
            mask = 4096
            # Use addLocalMonitorForEventsMatchingMask instead of global
            # This avoids dispatch queue issues
            self._monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask,
                self._create_handler()
            )
            print("⌨️  Fn key monitoring started (local monitor)")
        except Exception as e:
            print(f"⚠️  Could not start Fn monitoring: {e}")
            print("   Falling back to Right Option key")

    def _create_handler(self):
        """Create handler that returns the event"""
        def handler(event):
            self._handle_event(event)
            return event  # Must return event for local monitor
        return handler

    def stop(self):
        """Stop monitoring"""
        if self._monitor:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None

    def is_fn_pressed(self):
        """Check current Fn key state"""
        with self._lock:
            return self._fn_pressed
