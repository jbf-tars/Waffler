"""
Waffler Windows Hotkey
Ctrl + Space = toggle recording on/off.

Primary:  RegisterHotKey  (system-level, most reliable)
Fallback: GetAsyncKeyState polling (if RegisterHotKey fails, e.g. IME conflict)

Writes debug output to ~/.waffler/hotkey.log so issues are visible
even when the app runs as a GUI exe with no console.
"""

import ctypes
import ctypes.wintypes
import time
import threading
from pathlib import Path

# ── Win32 constants ───────────────────────────────────────────────────
MOD_CONTROL  = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE     = 0x20
VK_CONTROL   = 0x11
WM_HOTKEY    = 0x0312
WM_QUIT      = 0x0012
HOTKEY_ID    = 1

# ── Properly typed Win32 calls ────────────────────────────────────────
user32  = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype  = ctypes.c_short

user32.RegisterHotKey.argtypes = [
    ctypes.wintypes.HWND,   # hWnd
    ctypes.c_int,           # id
    ctypes.c_uint,          # fsModifiers
    ctypes.c_uint,          # vk
]
user32.RegisterHotKey.restype = ctypes.wintypes.BOOL

user32.UnregisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype  = ctypes.wintypes.BOOL

user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD,  # idThread
    ctypes.c_uint,          # Msg
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype  = ctypes.wintypes.DWORD

# ── Debug log ─────────────────────────────────────────────────────────
_LOG_FILE = Path.home() / ".waffler-hosted" / "hotkey.log"


def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"{ts}  {msg}"
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def _key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# ── Listener ──────────────────────────────────────────────────────────

class WindowsHotkeyListener:
    """
    Toggle-mode hotkey: press Ctrl + Space → start recording.
    Press again → stop recording.

    Tries RegisterHotKey first (official Windows global-hotkey API).
    Falls back to GetAsyncKeyState polling if registration fails
    (common when an IME or another app already owns Ctrl+Space).
    """

    def __init__(self, on_press, on_release):
        self._on_press   = on_press
        self._on_release = on_release
        self._recording  = False
        self._running    = False
        self._thread_id  = None

    # ── Public API ────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread_id = kernel32.GetCurrentThreadId()
        _log("WindowsHotkeyListener.start()")

        # Attempt RegisterHotKey (most reliable when it succeeds)
        registered = user32.RegisterHotKey(
            None, HOTKEY_ID, MOD_CONTROL | MOD_NOREPEAT, VK_SPACE
        )

        if registered:
            _log("Hotkey registered (RegisterHotKey): Ctrl + Space")
            try:
                self._message_loop()
            finally:
                user32.UnregisterHotKey(None, HOTKEY_ID)
                _log("Hotkey unregistered")
        else:
            err = ctypes.GetLastError()
            _log(f"RegisterHotKey failed (error {err}), using polling fallback")
            self._poll_fallback()

    def stop(self):
        self._running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def join(self):
        pass

    # ── RegisterHotKey message loop ───────────────────────────────────

    def _message_loop(self):
        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self._toggle()

    # ── GetAsyncKeyState fallback ─────────────────────────────────────

    def _poll_fallback(self):
        _log("Polling fallback active: Ctrl + Space (30ms interval)")
        combo_was_down = False
        while self._running:
            try:
                both_down = _key_down(VK_CONTROL) and _key_down(VK_SPACE)
                if both_down and not combo_was_down:
                    self._toggle()
                combo_was_down = both_down
            except Exception as e:
                _log(f"Poll error: {e}")
            time.sleep(0.03)

    # ── Toggle logic ──────────────────────────────────────────────────

    def _toggle(self):
        if self._recording:
            _log("Toggle → STOP recording")
            self._recording = False
            threading.Thread(target=self._safe_callback,
                             args=(self._on_release,), daemon=True).start()
        else:
            _log("Toggle → START recording")
            self._recording = True
            threading.Thread(target=self._safe_callback,
                             args=(self._on_press,), daemon=True).start()

    @staticmethod
    def _safe_callback(fn):
        try:
            fn()
        except Exception as e:
            _log(f"Hotkey callback error: {e}")
