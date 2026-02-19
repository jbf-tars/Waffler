"""
VoiceFlow Windows Hotkey
Right Ctrl + Right Alt (AltGr) pressed together = toggle recording.

UK keyboard note: AltGr generates a synthetic Left Ctrl press from Windows.
We detect Right Ctrl (0xA3) + Right Alt/AltGr (0xA5) specifically,
so AltGr alone or other combos don't accidentally trigger.
"""

import ctypes
import time
import threading

VK_RCONTROL = 0xA3   # Right Ctrl
VK_RMENU    = 0xA5   # Right Alt / AltGr


def _key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class WindowsHotkeyListener:
    """
    Toggle-mode hotkey: press Right Ctrl + Right Alt → start recording.
    Press again → stop recording.
    """

    def __init__(self, on_press, on_release):
        self._on_press    = on_press
        self._on_release  = on_release
        self._recording   = False
        self._running     = False
        self._combo_was_down = False   # both keys were held last tick

    def _poll(self):
        while self._running:
            both_down = _key_down(VK_RCONTROL) and _key_down(VK_RMENU)

            # Rising edge: combo just became active
            if both_down and not self._combo_was_down:
                if self._recording:
                    print("⏹  Stopping recording…")
                    self._recording = False
                    threading.Thread(target=self._on_release, daemon=True).start()
                else:
                    print("🎤 Starting recording…")
                    self._recording = True
                    threading.Thread(target=self._on_press, daemon=True).start()

            self._combo_was_down = both_down
            time.sleep(0.03)   # 30 ms poll — snappy but not CPU-heavy

    def start(self):
        print("⌨️  Hotkey ready: press Right Ctrl + Right Alt to start/stop recording")
        self._running = True
        self._poll()

    def stop(self):
        self._running = False

    def join(self):
        pass
