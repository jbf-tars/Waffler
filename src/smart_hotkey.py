"""
Waffler Smart Hotkey

Supports ANY hotkey combination (Fn, Cmd+Shift+Space, Option+Space, etc.)

Hold hotkey     → Start recording (push-to-talk)
Release         → Stop recording
Press Space while holding → Sticky mode (hands-free recording)
Press hotkey again → Stop sticky mode

Uses CGEventTap for all key detection - no pynput (avoids macOS crashes)
"""

import threading
from mac_hotkey_monitor import MacHotkeyMonitor
from fn_key_cgevent import FnKeyMonitor


class SmartHotkeyListener:

    def __init__(self, on_press, on_release, keys=None):
        """
        Initialize hotkey listener.

        Args:
            on_press: Callback when recording should start
            on_release: Callback when recording should stop
            keys: List of key names (default: ["fn"])
                 Examples: ["fn"], ["cmd", "shift", "space"], ["option", "space"]
        """
        self._on_press = on_press
        self._on_release = on_release
        self._keys = keys or ["fn"]

        self._hotkey_held = False   # Hotkey currently down
        self._sticky = False        # Locked-on (toggle) mode active
        self._recording = False     # Are we recording right now?

        # Special case: Fn key needs FnKeyMonitor (proper Fn+Space suppression)
        if self._keys == ["fn"]:
            print("[HOTKEY] Using FnKeyMonitor for Fn key (proper Fn+Space suppression)")
            self._monitor = FnKeyMonitor(
                on_fn_press=self._on_hotkey_press,
                on_fn_release=self._on_hotkey_release,
                on_space_press=self._on_space_press
            )
            self._space_monitor = None  # FnKeyMonitor handles space internally
        else:
            # Other hotkeys use MacHotkeyMonitor
            print(f"[HOTKEY] Using MacHotkeyMonitor for keys: {self._keys}")
            self._monitor = MacHotkeyMonitor(
                keys=self._keys,
                on_press=self._on_hotkey_press,
                on_release=self._on_hotkey_release,
                suppress=True
            )

            # Monitor for Space key to trigger sticky mode (DON'T suppress - let space pass through!)
            self._space_monitor = MacHotkeyMonitor(
                keys=["space"],
                on_press=self._on_space_press,
                on_release=lambda: None,  # Don't care about space release
                suppress=False  # CRITICAL: Don't block spacebar!
            )

        print(f"[HOTKEY] SmartHotkeyListener initialized with keys: {self._keys}")

    # ── Key events ────────────────────────────────────────────────────

    def _on_hotkey_press(self):
        """Called when hotkey combination is pressed"""
        print(f"[HOTKEY] Hotkey pressed | sticky={self._sticky} recording={self._recording}")

        if not self._recording:
            # First press → start push-to-talk
            print("[HOTKEY] → Starting push-to-talk")
            self._hotkey_held = True
            self._recording = True
            self._fire_press()
        # Note: Sticky mode is now cancelled via Fn+Space, not Fn tap alone

    def _on_hotkey_release(self):
        """Called when hotkey combination is released"""
        print(f"[HOTKEY] Hotkey released | sticky={self._sticky} recording={self._recording}")
        self._hotkey_held = False

        if self._recording and not self._sticky:
            # Push-to-talk: release hotkey → stop
            print("[HOTKEY] → Stopping push-to-talk")
            self._recording = False
            self._fire_release()

    def _on_space_press(self):
        """Called when Space key is pressed - toggles sticky mode"""
        print(f"[HOTKEY] Space pressed | hotkey_held={self._hotkey_held} recording={self._recording} sticky={self._sticky}")

        if self._hotkey_held and self._recording and not self._sticky:
            # Fn+Space while recording → enable sticky mode
            self._sticky = True
            print("📌 Fn+Space → Sticky mode ON! Press Space alone to stop.")
        elif self._sticky and self._recording and not self._hotkey_held:
            # Space alone (Fn NOT held) while in sticky mode → cancel
            # This works on ALL Macs (Space events always reach the app)
            self._sticky = False
            self._recording = False
            print("🛑 Space → Sticky mode OFF")
            self._fire_release()

    # ── Callbacks (run in a thread to avoid blocking) ─────────

    def _fire_press(self):
        threading.Thread(target=self._on_press, daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._on_release, daemon=True).start()

    def _dismiss_emoji_keyboard(self):
        """Send Escape to dismiss emoji picker if macOS opened it on Fn tap.

        On some Macs (macOS 26.3.1 / M3 Max), the Fn/globe emoji picker is triggered
        at IOKit/HID level, below CGEventTap. CGEventTap suppression can't prevent it.
        This sends Escape to dismiss the picker if it appeared.
        """
        import time
        try:
            from Quartz import CGEventPost, CGEventCreateKeyboardEvent, kCGHIDEventTap
            time.sleep(0.05)  # Brief delay for emoji picker to appear
            esc_down = CGEventCreateKeyboardEvent(None, 53, True)   # keycode 53 = Escape
            esc_up = CGEventCreateKeyboardEvent(None, 53, False)
            CGEventPost(kCGHIDEventTap, esc_down)
            CGEventPost(kCGHIDEventTap, esc_up)
            print("[HOTKEY] Sent Escape to dismiss emoji picker (if it appeared)")
        except Exception as e:
            print(f"[HOTKEY] Failed to dismiss emoji picker: {e}")

    # ── State Management ──────────────────────────────────────────

    def reset_state(self):
        """Reset internal state - call when recording is stopped externally (manual stop button)"""
        print(f"[HOTKEY] reset_state() called | was: sticky={self._sticky} recording={self._recording}")
        self._hotkey_held = False
        self._sticky = False
        self._recording = False
        print("[HOTKEY] → State reset to: sticky=False recording=False hotkey_held=False")

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        hotkey_display = " + ".join(self._keys)
        print(f"⌨️  Hotkey: Hold {hotkey_display} to record | Press Space while holding = sticky | Press hotkey again = stop")
        self._monitor.start()
        if self._space_monitor:
            self._space_monitor.start()

    def stop(self):
        if self._monitor:
            self._monitor.stop()
        if self._space_monitor:
            self._space_monitor.stop()

    def join(self):
        # No listener to join - CGEventTap runs in daemon thread
        pass
