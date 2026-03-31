"""
Waffler Smart Hotkey

Supports ANY hotkey combination (Fn, Cmd+Shift+Space, Option+Space, etc.)

Press once       → Start recording (push-to-talk: hold to record)
Release          → Stop recording
Double-tap       → Sticky mode (hands-free recording)
Press again      → Stop sticky mode

Uses CGEventTap for all key detection - no pynput (avoids macOS crashes)
"""

import threading
from mac_hotkey_monitor import MacHotkeyMonitor


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
        self._last_press_time = 0   # For double-tap detection

        # Use generic MacHotkeyMonitor for any key combination
        self._monitor = MacHotkeyMonitor(
            keys=self._keys,
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release
        )

        print(f"[HOTKEY] SmartHotkeyListener initialized with keys: {self._keys}")

    # ── Key events ────────────────────────────────────────────────────

    def _on_hotkey_press(self):
        """Called when hotkey combination is pressed"""
        import time
        current_time = time.time()

        print(f"[HOTKEY] Hotkey pressed | sticky={self._sticky} recording={self._recording}")

        # Check for double-tap (press within 0.5s of last press)
        if not self._sticky and self._recording and (current_time - self._last_press_time) < 0.5:
            # Double-tap → enable sticky mode
            self._sticky = True
            self._hotkey_held = False  # Allow release without stopping
            print("📌 Sticky mode — hands-free recording; press hotkey again to stop")
        elif self._sticky and self._recording:
            # Already in sticky mode → stop
            print("[HOTKEY] → Stopping sticky mode")
            self._sticky = False
            self._recording = False
            self._fire_release()
        elif not self._recording:
            # First press → start push-to-talk
            print("[HOTKEY] → Starting push-to-talk")
            self._hotkey_held = True
            self._recording = True
            self._fire_press()

        self._last_press_time = current_time

    def _on_hotkey_release(self):
        """Called when hotkey combination is released"""
        print(f"[HOTKEY] Hotkey released | sticky={self._sticky} recording={self._recording}")
        self._hotkey_held = False

        if self._recording and not self._sticky:
            # Push-to-talk: release hotkey → stop
            print("[HOTKEY] → Stopping push-to-talk")
            self._recording = False
            self._fire_release()

    # ── Callbacks (run in a thread to avoid blocking) ─────────

    def _fire_press(self):
        threading.Thread(target=self._on_press, daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._on_release, daemon=True).start()

    # ── State Management ──────────────────────────────────────────

    def reset_state(self):
        """Reset internal state - call when recording is stopped externally (manual stop button)"""
        print(f"[HOTKEY] reset_state() called | was: sticky={self._sticky} recording={self._recording}")
        self._hotkey_held = False
        self._sticky = False
        self._recording = False
        self._last_press_time = 0
        print("[HOTKEY] → State reset to: sticky=False recording=False hotkey_held=False")

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        hotkey_display = " + ".join(self._keys)
        print(f"⌨️  Hotkey: Hold {hotkey_display} to record | Double-tap = sticky | Press again = stop")
        self._monitor.start()

    def stop(self):
        if self._monitor:
            self._monitor.stop()

    def join(self):
        # No listener to join - CGEventTap runs in daemon thread
        pass
