"""
Fn Key Monitor for macOS
Uses PyObjC NSEvent monitoring to detect Fn key state changes
"""

import threading
from AppKit import NSEvent
from Foundation import NSObject


class FnKeyMonitor(NSObject):
    """Monitors Fn key state using NSEvent global monitoring"""

    def __init__(self, on_fn_press, on_fn_release):
        super().__init__()
        self._on_fn_press = on_fn_press
        self._on_fn_release = on_fn_release
        self._fn_pressed = False
        self._monitor = None
        self._lock = threading.Lock()

    def handler_(self, event):
        """Called on any modifier flag change"""
        # NSEventModifierFlagFunction = 0x800000 (bit 23)
        fn_flag = 0x800000
        is_fn_pressed = bool(event.modifierFlags() & fn_flag)

        with self._lock:
            if is_fn_pressed and not self._fn_pressed:
                self._fn_pressed = True
                threading.Thread(target=self._on_fn_press, daemon=True).start()
            elif not is_fn_pressed and self._fn_pressed:
                self._fn_pressed = False
                threading.Thread(target=self._on_fn_release, daemon=True).start()

    def start(self):
        """Start monitoring Fn key"""
        # NSEventMaskFlagsChanged = 0x1000 (4096)
        mask = 4096
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask,
            self.handler_
        )
        print("⌨️  Fn key monitoring started")

    def stop(self):
        """Stop monitoring"""
        if self._monitor:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None

    def is_fn_pressed(self):
        """Check current Fn key state"""
        with self._lock:
            return self._fn_pressed
