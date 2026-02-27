"""
Waffler Smart Hotkey

Fn alone                 → Push-to-talk: hold to record, release to stop
Fn + Space               → Sticky mode: recording locks on (can release Fn)
Fn (again)               → Stop sticky recording
"""

import threading
from pynput import keyboard
from src.fn_key_monitor import FnKeyMonitor


class SmartHotkeyListener:

    def __init__(self, on_press, on_release):
        self._on_press = on_press
        self._on_release = on_release

        self._fn_held = False       # Fn currently down
        self._sticky = False        # Locked-on (toggle) mode active
        self._recording = False     # Are we recording right now?

        # Add Fn key monitor
        self._fn_monitor = FnKeyMonitor(
            self._on_fn_press,
            self._on_fn_release
        )
        self._listener = None       # Still need pynput for Space key

    # ── Key events ────────────────────────────────────────────────────

    def _on_key_press(self, key):
        is_space = (key == keyboard.Key.space)

        # Space pressed while holding Fn → switch to sticky mode
        if is_space and self._fn_held and self._recording:
            self._sticky = True
            print("📌 Sticky mode — release Fn and keep talking; press Fn again to stop")

    def _on_fn_press(self):
        """Called when Fn key is pressed"""
        if self._sticky and self._recording:
            # Already in sticky mode → Fn stops it
            self._sticky = False
            self._recording = False
            self._fn_held = False
            self._fire_release()

        elif not self._recording:
            # Start push-to-talk
            self._fn_held = True
            self._recording = True
            self._fire_press()

    def _on_fn_release(self):
        """Called when Fn key is released"""
        self._fn_held = False
        if self._recording and not self._sticky:
            # Push-to-talk: release Fn → stop
            self._recording = False
            self._fire_release()

    # ── Callbacks (run in a thread to avoid blocking pynput) ─────────

    def _fire_press(self):
        threading.Thread(target=self._on_press, daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._on_release, daemon=True).start()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        print("⌨️  Hotkey: Hold Fn to record | Fn + Space = sticky | Fn again = stop")

        # Start Fn key monitoring (via NSEvent)
        self._fn_monitor.start()

        # Start pynput listener (only for Space key now)
        self._listener = keyboard.Listener(
            on_press=self._on_key_press,
            # No on_release needed
        )
        self._listener.start()

    def stop(self):
        if self._fn_monitor:
            self._fn_monitor.stop()
        if self._listener:
            self._listener.stop()

    def join(self):
        if self._listener:
            self._listener.join()
