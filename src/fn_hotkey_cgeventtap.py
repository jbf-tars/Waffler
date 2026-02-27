"""
Fn Key Detection using CGEventTap (Advanced/Experimental)

⚠️  WARNING: This implementation is EXPERIMENTAL and has known issues:
    1. Fn key flags are set even when Fn is NOT pressed (hardware quirk)
    2. CGEventTapCreate leaks memory when called repeatedly
    3. Complex thread/queue management required
    4. May not work reliably on all systems

RECOMMENDED ALTERNATIVE: Use smart_hotkey_f13.py with F13 key instead.

This module is provided for:
- Advanced users who specifically need Fn key
- Research/experimentation
- Understanding macOS event tap internals

References:
- https://developer.apple.com/documentation/coregraphics/cgeventflags/masksecondaryfn
- https://pyobjc.readthedocs.io/en/latest/apinotes/Quartz.html
- https://github.com/pqrs-org/osx-event-observer-examples
"""

import threading
import time
from Quartz import (
    CGEventTapCreate,
    kCGSessionEventTap,
    kCGHeadInsertEventTap,
    kCGEventTapOptionDefault,
    CGEventMaskBit,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventFlagsChanged,
    kCGEventFlagMaskSecondaryFn,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskShift,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskAlternate,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    kCGKeyboardEventKeycode,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopGetCurrent,
    CFRunLoopAddSource,
    CFRunLoopRun,
    CFRunLoopStop,
    kCFRunLoopCommonModes,
)


# macOS keycodes
KEYCODE_SPACE = 49


class FnKeyListener:
    """
    Low-level Fn key listener using CGEventTap.

    This implementation attempts to detect the Fn key by monitoring
    kCGEventFlagMaskSecondaryFn flag in keyboard events.

    Known Issues:
    - Fn flag is set on F1-F12 and arrow keys even when Fn is NOT pressed
    - Some keyboards don't expose Fn key at all
    - Requires Input Monitoring permission
    """

    def __init__(self, on_press, on_release):
        """
        Initialize Fn key listener.

        Args:
            on_press: Callback when Fn key is pressed
            on_release: Callback when Fn key is released
        """
        self._on_press = on_press
        self._on_release = on_release

        self._fn_pressed = False
        self._sticky_mode = False
        self._recording = False

        self._tap = None
        self._run_loop_source = None
        self._run_loop = None
        self._thread = None
        self._stop_requested = False

        # Track other modifier keys to filter out false positives
        self._other_modifiers = 0

    def _event_callback(self, proxy, event_type, event, refcon):
        """
        CGEventTap callback - runs on event tap thread.

        IMPORTANT: Keep this minimal and fast to avoid blocking the event stream.
        Do NOT call back to UI or do heavy processing here.
        """
        try:
            flags = CGEventGetFlags(event)

            # Extract individual flags
            fn_flag = bool(flags & kCGEventFlagMaskSecondaryFn)
            cmd_flag = bool(flags & kCGEventFlagMaskCommand)
            shift_flag = bool(flags & kCGEventFlagMaskShift)
            ctrl_flag = bool(flags & kCGEventFlagMaskControl)
            alt_flag = bool(flags & kCGEventFlagMaskAlternate)

            # Get keycode if this is a key event
            keycode = -1
            if event_type in (kCGEventKeyDown, kCGEventKeyUp):
                keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

            # Filter out false positives:
            # Fn flag is set on F1-F12 (keycodes 122-111, 105-103, 98) and arrows
            # when those keys are pressed, even without Fn being held.
            #
            # Strategy: Only treat as "pure Fn press" if:
            # 1. Fn flag is set
            # 2. No actual key press (flagsChanged event only)
            # 3. OR it's combined with Space (for sticky mode)

            is_pure_fn = fn_flag and event_type == kCGEventFlagsChanged and keycode == -1
            is_fn_space = fn_flag and keycode == KEYCODE_SPACE and event_type == kCGEventKeyDown

            # Handle Fn press
            if is_pure_fn and not self._fn_pressed:
                self._fn_pressed = True
                self._handle_fn_press()

            # Handle Fn + Space (sticky mode toggle)
            elif is_fn_space and self._fn_pressed:
                self._handle_sticky_toggle()

            # Handle Fn release
            elif not fn_flag and self._fn_pressed:
                # But only if no other keys are pressed
                # (to avoid false release when pressing F-keys)
                if event_type == kCGEventFlagsChanged:
                    self._fn_pressed = False
                    self._handle_fn_release()

        except Exception as e:
            print(f"[FnKeyListener] Event callback error: {e}")
            import traceback
            traceback.print_exc()

        # Always pass through the event (don't consume it)
        return event

    def _handle_fn_press(self):
        """Handle Fn key press - runs in event tap thread."""
        if self._sticky_mode and self._recording:
            # Fn pressed while in sticky mode → stop recording
            self._sticky_mode = False
            self._recording = False
            if self._on_release:
                # Fire callback in separate thread
                threading.Thread(
                    target=self._on_release,
                    daemon=True,
                    name="FnRelease"
                ).start()
            print("⏹️  Recording stopped (Fn pressed in sticky mode)")

        elif not self._recording:
            # Start push-to-talk recording
            self._recording = True
            if self._on_press:
                # Fire callback in separate thread
                threading.Thread(
                    target=self._on_press,
                    daemon=True,
                    name="FnPress"
                ).start()
            print("🎤 Recording started (hold Fn)")

    def _handle_fn_release(self):
        """Handle Fn key release - runs in event tap thread."""
        if self._recording and not self._sticky_mode:
            # Stop push-to-talk recording
            self._recording = False
            if self._on_release:
                # Fire callback in separate thread
                threading.Thread(
                    target=self._on_release,
                    daemon=True,
                    name="FnRelease"
                ).start()
            print("⏹️  Recording stopped (Fn released)")

    def _handle_sticky_toggle(self):
        """Handle Fn + Space to toggle sticky mode - runs in event tap thread."""
        if self._recording and not self._sticky_mode:
            self._sticky_mode = True
            print("📌 Sticky mode ON — release Fn and keep talking; press Fn again to stop")

    def _run_event_tap(self):
        """Run the event tap in a dedicated thread."""
        try:
            # Create event mask for key events and flag changes
            event_mask = (
                CGEventMaskBit(kCGEventKeyDown) |
                CGEventMaskBit(kCGEventKeyUp) |
                CGEventMaskBit(kCGEventFlagsChanged)
            )

            # Create the event tap
            # Note: This may leak memory according to PyObjC docs, but it's
            # acceptable since we only create it once per app session.
            self._tap = CGEventTapCreate(
                kCGSessionEventTap,          # Session event tap (works in background)
                kCGHeadInsertEventTap,       # Insert at head of event stream
                kCGEventTapOptionDefault,    # Default options (receive events)
                event_mask,                  # Events we're interested in
                self._event_callback,        # Callback function
                None                         # User info (refcon)
            )

            if not self._tap:
                print("❌ Failed to create event tap. Check Input Monitoring permission:")
                print("   System Preferences > Privacy & Security > Input Monitoring")
                print("   Add and enable Python or your app")
                return

            # Create run loop source and add to current run loop
            self._run_loop_source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._run_loop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(
                self._run_loop,
                self._run_loop_source,
                kCFRunLoopCommonModes
            )

            # Enable the tap
            CGEventTapEnable(self._tap, True)

            print("✅ Fn key listener started (CGEventTap)")
            print("⚠️  Note: This is experimental. Some keyboards may not support Fn detection.")

            # Run the loop (blocks until stopped)
            CFRunLoopRun()

        except Exception as e:
            print(f"[FnKeyListener] Event tap thread error: {e}")
            import traceback
            traceback.print_exc()

    def start(self):
        """Start listening for Fn key events."""
        print("⌨️  Hotkey: Hold Fn to record | Fn + Space = sticky | Fn again = stop")
        print("")
        print("⚠️  EXPERIMENTAL: Fn key detection has known issues:")
        print("   • May not work on all keyboards")
        print("   • Fn flag is set on F1-F12 even when Fn not pressed")
        print("   • Requires Input Monitoring permission")
        print("")
        print("💡 RECOMMENDED: Use F13 key instead (see smart_hotkey_f13.py)")
        print("")

        # Start event tap on dedicated thread
        self._thread = threading.Thread(
            target=self._run_event_tap,
            daemon=True,
            name="FnKeyEventTap"
        )
        self._thread.start()

        # Give it a moment to initialize
        time.sleep(0.5)

    def stop(self):
        """Stop listening for Fn key events."""
        self._stop_requested = True

        if self._tap:
            CGEventTapEnable(self._tap, False)

        if self._run_loop:
            CFRunLoopStop(self._run_loop)

    def join(self):
        """Wait for the event tap thread to finish."""
        if self._thread:
            self._thread.join()


class SmartHotkeyListener:
    """
    Fn key-based smart hotkey listener (experimental).

    This is a wrapper around FnKeyListener to maintain compatibility
    with the existing SmartHotkeyListener interface.

    ⚠️  RECOMMENDED: Use smart_hotkey_f13.py instead for production use.
    """

    def __init__(self, on_press, on_release):
        self._listener = FnKeyListener(on_press, on_release)

    def start(self):
        self._listener.start()

    def stop(self):
        self._listener.stop()

    def join(self):
        self._listener.join()


# ── Permission Check ──────────────────────────────────────────────────

def check_input_monitoring_permission():
    """
    Check if Input Monitoring permission is granted.

    Returns:
        bool: True if permission granted, False otherwise
    """
    try:
        # Attempt to create a temporary event tap to check permission
        test_tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            CGEventMaskBit(kCGEventFlagsChanged),
            lambda proxy, type, event, refcon: event,  # Pass-through callback
            None
        )

        if test_tap:
            # Permission granted - clean up
            CGEventTapEnable(test_tap, False)
            return True
        else:
            return False

    except Exception as e:
        print(f"Permission check error: {e}")
        return False


def request_input_monitoring_permission():
    """
    Request Input Monitoring permission (triggers system dialog).

    This will fail if permission is not granted, which triggers macOS
    to show the permission dialog to the user.
    """
    print("Requesting Input Monitoring permission...")
    print("A system dialog should appear. Please grant permission and restart the app.")

    has_permission = check_input_monitoring_permission()

    if has_permission:
        print("✅ Input Monitoring permission granted!")
    else:
        print("❌ Input Monitoring permission required:")
        print("   1. Open System Preferences")
        print("   2. Go to Privacy & Security > Input Monitoring")
        print("   3. Add and enable Python (or your app)")
        print("   4. Restart this app")

    return has_permission


# ── CLI for testing ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    def test_press():
        print("▶️  Recording started!")

    def test_release():
        print("⏹️  Recording stopped!")

    print("Waffler Fn Key Listener Test (CGEventTap)")
    print("=" * 60)

    # Check permission first
    if not check_input_monitoring_permission():
        print("\n⚠️  Input Monitoring permission not granted.")
        request_input_monitoring_permission()
        sys.exit(1)

    listener = FnKeyListener(test_press, test_release)
    listener.start()

    try:
        print("\n✅ Fn key listener active. Test your Fn key!")
        print("Press Ctrl+C to exit.\n")
        listener.join()
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
        listener.stop()
