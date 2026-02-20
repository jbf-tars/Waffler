"""
VoiceFlow Windows Hotkey
Ctrl + Space = toggle recording on/off.

Uses Win32 GetAsyncKeyState polling (30ms) for reliable detection
without interfering with other apps.
"""

import ctypes
import time
import threading

VK_CONTROL = 0x11   # Either Ctrl key
VK_SPACE   = 0x20   # Space bar


def _key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class WindowsHotkeyListener:
    """
    Toggle-mode hotkey: press Ctrl + Space → start recording.
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
            both_down = _key_down(VK_CONTROL) and _key_down(VK_SPACE)

            # Rising edge: combo just became active
            if both_down and not self._combo_was_down:
                if self._recording:
                    print("Stop recording")
                    self._recording = False
                    threading.Thread(target=self._on_release, daemon=True).start()
                else:
                    print("Start recording")
                    self._recording = True
                    threading.Thread(target=self._on_press, daemon=True).start()

            self._combo_was_down = both_down
            time.sleep(0.03)   # 30 ms poll — snappy but not CPU-heavy

    def start(self):
        print("Hotkey ready: Ctrl + Space to start/stop recording")
        self._running = True
        self._poll()

    def stop(self):
        self._running = False

    def join(self):
        pass
