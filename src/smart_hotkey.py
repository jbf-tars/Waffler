"""
Waffler Smart Hotkey

Fn alone                 → Push-to-talk: hold to record, release to stop
Fn + Space               → Sticky mode: recording locks on (can release Fn)
Fn (again)               → Stop sticky recording

Uses CGEventTap for all key detection - no pynput (avoids macOS crashes)
"""

import threading
from fn_key_cgevent import FnKeyMonitor


class SmartHotkeyListener:

    def __init__(self, on_press, on_release):
        self._on_press = on_press
        self._on_release = on_release

        self._fn_held = False       # Fn currently down
        self._sticky = False        # Locked-on (toggle) mode active
        self._recording = False     # Are we recording right now?

        # Use CGEventTap for both Fn and Space detection (no pynput crashes!)
        self._fn_monitor = FnKeyMonitor(
            on_fn_press=self._on_fn_press,
            on_fn_release=self._on_fn_release,
            on_space_press=self._on_space_press
        )

    # ── Key events ────────────────────────────────────────────────────

    def _on_space_press(self):
        """Called when Space key is pressed"""
        print(f"[HOTKEY] Space pressed | fn_held={self._fn_held} recording={self._recording}")
        # Space pressed while holding Fn → switch to sticky mode
        if self._fn_held and self._recording:
            self._sticky = True
            print("📌 Sticky mode — release Fn and keep talking; press Fn again to stop")

    def _on_fn_press(self):
        """Called when Fn key is pressed"""
        print(f"[HOTKEY] Fn pressed | sticky={self._sticky} recording={self._recording} fn_held={self._fn_held}")
        if self._sticky and self._recording:
            # Already in sticky mode → Fn stops it
            print("[HOTKEY] → Stopping sticky mode")
            self._sticky = False
            self._recording = False
            self._fn_held = False
            self._fire_release()

        elif not self._recording:
            # Start push-to-talk
            print("[HOTKEY] → Starting push-to-talk")
            self._fn_held = True
            self._recording = True
            self._fire_press()
        else:
            print(f"[HOTKEY] → No action (already recording, not sticky)")

    def _on_fn_release(self):
        """Called when Fn key is released"""
        print(f"[HOTKEY] Fn released | sticky={self._sticky} recording={self._recording}")
        self._fn_held = False
        if self._recording and not self._sticky:
            # Push-to-talk: release Fn → stop
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
        self._fn_held = False
        self._sticky = False
        self._recording = False
        print("[HOTKEY] → State reset to: sticky=False recording=False fn_held=False")

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        print("⌨️  Hotkey: Hold Fn to record | Fn + Space = sticky | Fn again = stop")
        # Start Fn + Space monitoring (CGEventTap - no pynput!)
        self._fn_monitor.start()

    def stop(self):
        if self._fn_monitor:
            self._fn_monitor.stop()

    def join(self):
        # No listener to join - CGEventTap runs in daemon thread
        pass
