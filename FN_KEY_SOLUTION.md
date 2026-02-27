# Fn Key Detection on macOS - Solution Analysis & Implementation

## Problem Summary

The current implementation in `app.py` (lines 1602-1620) uses `NSEvent.addGlobalMonitorForEventsMatchingMask_handler_()` which causes dispatch queue crashes:

```python
def _request_input_monitoring_permission():
    from AppKit import NSEvent
    mask = 4096  # NSEventMaskFlagsChanged
    test_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
        mask,
        lambda event: None
    )
```

**Crash Details** (from `/Users/james/Library/Logs/DiagnosticReports/Python-2026-02-27-203645.ips`):
- Exception: `EXC_BREAKPOINT` (SIGTRAP)
- Fault: `_dispatch_assert_queue_fail` at offset 120
- Root cause: NSEvent callbacks execute on wrong dispatch queue, causing assertion failure

## Why Fn Key Detection is Problematic

Based on research:

1. **Hardware Limitation**: The Fn key is a form of meta-modifier that alters scancodes at the hardware level. The OS has no direct notion of the Fn key, so it cannot normally be remapped in software ([Wikipedia](https://en.wikipedia.org/wiki/Fn_key))

2. **Flag Quirk**: Events from function keys (F1-F12) and arrow keys have the Fn modifier flag set even when Fn is NOT pressed ([skhd issue #10](https://github.com/koekeishiya/skhd/issues/10))

3. **PyObjC Issue**: `CGEventTapCreate` leaks memory when called repeatedly, and should be called sparingly ([PyObjC Quartz docs](https://pyobjc.readthedocs.io/en/latest/apinotes/Quartz.html))

4. **CGEvent Limitation**: Posting a CGEvent with fn key enabled doesn't always work - the flag is present but not applied ([FB9093710](https://github.com/feedback-assistant/reports/issues/524))

5. **Pynput Limitation**: Known issue where function keys like F13 don't work properly on macOS ([pynput issue #439](https://github.com/moses-palmer/pynput/issues/439))

## Recommended Solutions

### Option 1: Use F13-F20 Keys (RECOMMENDED)

**Pros:**
- Reliable detection with existing pynput library
- No dispatch queue issues
- Unused on Mac keyboards (no conflicts)
- Easy to implement

**Cons:**
- Not available on all keyboards
- Requires external/mechanical keyboard for built-in MacBooks

**Implementation:**

```python
# In src/smart_hotkey.py
from pynput import keyboard

class SmartHotkeyListener:
    def __init__(self, on_press, on_release):
        self._on_press = on_press
        self._on_release = on_release
        self._f13_held = False
        self._sticky = False
        self._recording = False
        self._listener = None

    def _on_key_press(self, key):
        # F13 for push-to-talk, F13+Space for sticky mode
        is_f13 = (key == keyboard.Key.f13)
        is_space = (key == keyboard.Key.space)

        if is_f13:
            if self._sticky and self._recording:
                # Stop sticky recording
                self._sticky = False
                self._recording = False
                self._f13_held = False
                self._fire_release()
            elif not self._recording:
                # Start push-to-talk
                self._f13_held = True
                self._recording = True
                self._fire_press()

        elif is_space and self._f13_held and self._recording:
            # Enable sticky mode
            self._sticky = True
            print("📌 Sticky mode — release F13 and keep talking; press F13 again to stop")

    def _on_key_release(self, key):
        is_f13 = (key == keyboard.Key.f13)

        if is_f13:
            self._f13_held = False
            if self._recording and not self._sticky:
                # Stop push-to-talk
                self._recording = False
                self._fire_release()

    def _fire_press(self):
        threading.Thread(target=self._on_press, daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._on_release, daemon=True).start()

    def start(self):
        print("⌨️  Hotkey: Hold F13 to record | F13 + Space = sticky | F13 again = stop")
        print("💡 Tip: Use Karabiner-Elements to remap Caps Lock → F13 for easy access")
        self._listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def join(self):
        if self._listener:
            self._listener.join()
```

**User Setup Instructions:**
1. Install [Karabiner-Elements](https://karabiner-elements.pqrs.org/)
2. Remap Caps Lock → F13 (most ergonomic placement)
3. Or use F13 key if keyboard has it (extended keyboards)

### Option 2: Right Command Key

**Pros:**
- Available on all Mac keyboards
- Rarely used key (good candidate)
- Works reliably with pynput
- No permission issues

**Cons:**
- Some users may use it for other shortcuts
- Not as unique as F13

**Implementation:**

```python
# In src/smart_hotkey.py - similar pattern
is_right_cmd = (key == keyboard.Key.cmd_r)
```

### Option 3: CGEventTap with Proper Queue Handling (Advanced)

**Pros:**
- Can detect Fn key directly
- Low-level control

**Cons:**
- Complex implementation
- Memory leak issues with PyObjC
- Fn flag quirks (set even when not pressed)
- Requires careful thread/queue management

**Implementation:**

```python
# In src/fn_hotkey_cgeventtap.py
import threading
from Quartz import (
    CGEventTapCreate,
    kCGSessionEventTap,
    kCGHeadInsertEventTap,
    kCGEventTapOptionDefault,
    CGEventMaskBit,
    kCGEventFlagsChanged,
    kCGEventFlagMaskSecondaryFn,
    CGEventGetFlags,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopGetCurrent,
    CFRunLoopAddSource,
    CFRunLoopRun,
    CFRunLoopStop,
    kCFRunLoopCommonModes,
)
from AppKit import NSObject
import objc

class FnKeyListener(NSObject):
    """
    CGEventTap-based Fn key listener that properly handles dispatch queues.
    """

    def initWithCallbacks_(self, callbacks):
        self = objc.super(FnKeyListener, self).init()
        if self is None:
            return None

        self.on_press = callbacks.get('on_press')
        self.on_release = callbacks.get('on_release')
        self._fn_pressed = False
        self._tap = None
        self._run_loop_source = None
        self._run_loop = None
        self._stop_requested = False

        return self

    def eventTapCallback_type_event_refcon_(self, proxy, event_type, event, refcon):
        """
        Event tap callback - runs on event tap thread.
        IMPORTANT: Keep this minimal to avoid dispatch queue issues.
        """
        try:
            flags = CGEventGetFlags(event)
            fn_is_down = bool(flags & kCGEventFlagMaskSecondaryFn)

            # Detect state changes
            if fn_is_down and not self._fn_pressed:
                self._fn_pressed = True
                if self.on_press:
                    # Fire callback in separate thread to avoid blocking event tap
                    threading.Thread(target=self.on_press, daemon=True).start()

            elif not fn_is_down and self._fn_pressed:
                self._fn_pressed = False
                if self.on_release:
                    threading.Thread(target=self.on_release, daemon=True).start()

        except Exception as e:
            print(f"[FnKeyListener] Event callback error: {e}")

        # Always pass through the event
        return event

    def start(self):
        """Start the event tap on a dedicated thread."""
        def run_event_tap():
            # Create event mask for flag changes
            event_mask = CGEventMaskBit(kCGEventFlagsChanged)

            # Create the event tap
            self._tap = CGEventTapCreate(
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                kCGEventTapOptionDefault,
                event_mask,
                self.eventTapCallback_type_event_refcon_,
                None
            )

            if not self._tap:
                print("[FnKeyListener] Failed to create event tap. Check Input Monitoring permission.")
                return

            # Create run loop source
            self._run_loop_source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._run_loop = CFRunLoopGetCurrent()

            # Add to run loop
            CFRunLoopAddSource(self._run_loop, self._run_loop_source, kCFRunLoopCommonModes)

            # Enable the tap
            CGEventTapEnable(self._tap, True)

            print("⌨️  Fn key listener started")

            # Run the loop
            CFRunLoopRun()

        # Start on dedicated thread
        self._thread = threading.Thread(target=run_event_tap, daemon=True, name="FnKeyEventTap")
        self._thread.start()

    def stop(self):
        """Stop the event tap."""
        if self._tap:
            CGEventTapEnable(self._tap, False)

        if self._run_loop:
            CFRunLoopStop(self._run_loop)

    def join(self):
        """Wait for the event tap thread to finish."""
        if hasattr(self, '_thread'):
            self._thread.join()


class SmartHotkeyListener:
    """
    Fn key-based hotkey listener using CGEventTap.
    Properly handles dispatch queues to avoid crashes.
    """

    def __init__(self, on_press, on_release):
        self._on_press = on_press
        self._on_release = on_release
        self._fn_held = False
        self._sticky = False
        self._recording = False
        self._space_listener = None
        self._fn_listener = None

    def _handle_fn_press(self):
        if self._sticky and self._recording:
            # Stop sticky mode
            self._sticky = False
            self._recording = False
            self._fn_held = False
            self._fire_release()
        elif not self._recording:
            # Start push-to-talk
            self._fn_held = True
            self._recording = True
            self._fire_press()

    def _handle_fn_release(self):
        self._fn_held = False
        if self._recording and not self._sticky:
            # Stop push-to-talk
            self._recording = False
            self._fire_release()

    def _handle_space_press(self, key):
        if key == keyboard.Key.space and self._fn_held and self._recording:
            # Enable sticky mode
            self._sticky = True
            print("📌 Sticky mode — release Fn and keep talking; press Fn again to stop")

    def _fire_press(self):
        threading.Thread(target=self._on_press, daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._on_release, daemon=True).start()

    def start(self):
        # Start Fn key listener with CGEventTap
        self._fn_listener = FnKeyListener.alloc().initWithCallbacks_({
            'on_press': self._handle_fn_press,
            'on_release': self._handle_fn_release,
        })
        self._fn_listener.start()

        # Start pynput listener for Space key (fallback)
        from pynput import keyboard
        self._space_listener = keyboard.Listener(on_press=self._handle_space_press)
        self._space_listener.start()

        print("⌨️  Hotkey: Hold Fn to record | Fn + Space = sticky | Fn again = stop")

    def stop(self):
        if self._fn_listener:
            self._fn_listener.stop()
        if self._space_listener:
            self._space_listener.stop()

    def join(self):
        if self._fn_listener:
            self._fn_listener.join()
        if self._space_listener:
            self._space_listener.join()
```

**Note**: This approach is complex and may still have issues due to the Fn key's hardware-level behavior.

## Final Recommendation

**Use Option 1 (F13 key) with Karabiner-Elements remapping Caps Lock → F13.**

### Why?
1. ✅ Most reliable (no dispatch queue issues)
2. ✅ Ergonomic (Caps Lock is well-positioned)
3. ✅ No macOS permission complexity
4. ✅ Works with existing pynput library
5. ✅ Caps Lock is rarely used by most users
6. ✅ Similar to how professionals use push-to-talk (Discord, Teamspeak, etc.)

### User Experience
- **Default**: "Hold F13 to record"
- **Recommended Setup**: "Install Karabiner-Elements and remap Caps Lock → F13"
- **Alternative**: Use Right Command key if user prefers

### Implementation Steps

1. Update `smart_hotkey.py` to use F13 instead of Right Option
2. Add setup instructions to README
3. Provide one-click Karabiner-Elements configuration file
4. Offer Right Command as fallback option

## How Competitors Handle This

Research shows:
- **Raycast**: Uses customizable hotkeys, default is Cmd+Space
- **CleanShot X**: Integrates with Raycast, uses custom hotkeys
- **Push-to-Talk apps**: Often use Right Command key ([MacWhisper example](https://manual.raycast.com/hotkey))

None rely exclusively on Fn key due to its limitations.

## References

- [CGEventTap Documentation](https://github.com/usagimaru/EventTapper)
- [CGEvent Dispatch Queue Issues (2026)](https://danielraffel.me/til/2026/02/19/cgevent-taps-and-code-signing-the-silent-disable-race/)
- [NSEvent vs CGEventTap Discussion](https://github.com/keepassxreboot/keepassxc/issues/3393)
- [macOS Event Observation Presentation](https://docs.google.com/presentation/d/1nEaiPUduh1vjks0rDVRTcJaEULbSWWh1tVdG2HF_XSU/htmlpresent)
- [Pynput Function Key Issues](https://github.com/moses-palmer/pynput/issues/439)
- [F13-F16 Key Usage on macOS](https://forums.macrumors.com/threads/the-f-keys-f13-f14-f15-f16.328997/)
- [Fn Key Modifier Flag Documentation](https://developer.apple.com/documentation/coregraphics/cgeventflags/masksecondaryfn)
- [Karabiner-Elements Features](https://karabiner-elements.pqrs.org/docs/getting-started/features/)
- [PyObjC Quartz API Notes](https://pyobjc.readthedocs.io/en/latest/apinotes/Quartz.html)
- [CGEventTap Example (Objective-C)](https://github.com/pqrs-org/osx-event-observer-examples/blob/main/cgeventtap-example/src/CGEventTapExample.m)
