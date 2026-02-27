"""
Fn Key Detection for macOS using CGEventTap
Works without crashing by using CGEventTap instead of NSEvent
"""

import threading
from Quartz import (
    CGEventTapCreate,
    CGEventTapEnable,
    kCGEventFlagsChanged,
    kCGEventTapOptionDefault,
    kCGHeadInsertEventTap,
    kCGSessionEventTap,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    CFRunLoopStop,
    CFMachPortCreateRunLoopSource,
    kCFRunLoopCommonModes,
    CGEventGetFlags,
)


class FnKeyMonitor:
    """Monitors Fn key using CGEventTap (more reliable than NSEvent)"""

    def __init__(self, on_fn_press, on_fn_release):
        self._on_fn_press = on_fn_press
        self._on_fn_release = on_fn_release
        self._fn_pressed = False
        self._tap = None
        self._runloop_source = None
        self._runloop = None
        self._lock = threading.Lock()
        self._thread = None

    def _event_callback(self, proxy, event_type, event, refcon):
        """Called when modifier flags change"""
        try:
            # kCGEventFlagMaskSecondaryFn = 0x800000 (bit 23)
            fn_flag = 0x800000
            flags = CGEventGetFlags(event)
            is_fn_pressed = bool(flags & fn_flag)

            with self._lock:
                if is_fn_pressed and not self._fn_pressed:
                    self._fn_pressed = True
                    threading.Thread(target=self._on_fn_press, daemon=True).start()
                elif not is_fn_pressed and self._fn_pressed:
                    self._fn_pressed = False
                    threading.Thread(target=self._on_fn_release, daemon=True).start()
        except Exception as e:
            print(f"⚠️  Fn event error: {e}")

        # Return the event unchanged
        return event

    def _run_event_tap(self):
        """Run the event tap in its own thread"""
        try:
            # Create event tap
            self._tap = CGEventTapCreate(
                kCGSessionEventTap,  # Session-level tap
                kCGHeadInsertEventTap,  # Insert at head
                kCGEventTapOptionDefault,  # Default options
                1 << kCGEventFlagsChanged,  # Only flags changed events
                self._event_callback,  # Callback
                None  # User info
            )

            if self._tap is None:
                print("⚠️  Failed to create Fn key tap - need Accessibility permission")
                print("   Go to: System Preferences > Security & Privacy > Privacy > Accessibility")
                print("   Add Python or your terminal app to the list")
                return

            # Create run loop source
            self._runloop_source = CFMachPortCreateRunLoopSource(None, self._tap, 0)

            # Get current run loop and add source
            self._runloop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self._runloop, self._runloop_source, kCFRunLoopCommonModes)

            # Enable the tap
            CGEventTapEnable(self._tap, True)

            print("⌨️  Fn key monitoring started (CGEventTap)")

            # Run the loop (blocks until stopped)
            CFRunLoopRun()

        except Exception as e:
            print(f"⚠️  Fn monitor error: {e}")

    def start(self):
        """Start monitoring Fn key in background thread"""
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
