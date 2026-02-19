"""
VoiceFlow Smart Hotkey

Right Option (⌥) alone  → Push-to-talk: hold to record, release to stop
Right Option + Space     → Sticky mode: recording locks on (can release Option)
Right Option (again)     → Stop sticky recording
"""

import threading
from pynput import keyboard


class SmartHotkeyListener:

    def __init__(self, on_press, on_release):
        self._on_press = on_press
        self._on_release = on_release

        self._option_held = False   # Right Option currently down
        self._sticky = False        # Locked-on (toggle) mode active
        self._recording = False     # Are we recording right now?
        self._listener = None

    # ── Key events ────────────────────────────────────────────────────

    def _on_key_press(self, key):
        is_right_option = (key == keyboard.Key.alt_r)
        is_space = (key == keyboard.Key.space)

        if is_right_option:
            if self._sticky and self._recording:
                # Already in sticky mode → Right Option stops it
                self._sticky = False
                self._recording = False
                self._option_held = False
                self._fire_release()

            elif not self._recording:
                # Start push-to-talk
                self._option_held = True
                self._recording = True
                self._fire_press()

        elif is_space and self._option_held and self._recording:
            # Space pressed while holding Option → switch to sticky
            self._sticky = True
            print("📌 Sticky mode — release Option and keep talking; press Option again to stop")

    def _on_key_release(self, key):
        is_right_option = (key == keyboard.Key.alt_r)

        if is_right_option:
            self._option_held = False
            if self._recording and not self._sticky:
                # Push-to-talk: release Option → stop
                self._recording = False
                self._fire_release()

    # ── Callbacks (run in a thread to avoid blocking pynput) ─────────

    def _fire_press(self):
        threading.Thread(target=self._on_press, daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._on_release, daemon=True).start()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        print("⌨️  Hotkey: Hold Right ⌥ to record | Right ⌥ + Space = sticky | Right ⌥ again = stop")
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
