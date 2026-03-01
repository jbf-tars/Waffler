"""
Waffler Windows Hotkey — Ctrl+Win Smart Hotkey

Ctrl+Win held      = Push-to-talk (hold to record, release to stop)
Ctrl+Win + Space   = Sticky mode (locks recording on, can release keys)
Ctrl+Win again     = Cancel sticky mode (stops recording)

Uses SetWindowsHookEx(WH_KEYBOARD_LL) for press/release detection.
Only suppresses Win key-up (to prevent Start menu) — never blocks key-down.
Falls back to GetAsyncKeyState polling if the hook fails.

Writes debug output to ~/.waffler/hotkey.log.
"""

import ctypes
import ctypes.wintypes
import time
import threading
from pathlib import Path
from enum import Enum

# ── Win32 constants ───────────────────────────────────────────────────
WH_KEYBOARD_LL = 13
WM_KEYDOWN     = 0x0100
WM_KEYUP       = 0x0101
WM_SYSKEYDOWN  = 0x0104
WM_SYSKEYUP    = 0x0105
WM_QUIT        = 0x0012
VK_LWIN        = 0x5B
VK_RWIN        = 0x5C
VK_CONTROL     = 0x11
VK_LCONTROL    = 0xA2
VK_RCONTROL    = 0xA3
VK_SPACE       = 0x20

# KBDLLHOOKSTRUCT
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      ctypes.wintypes.DWORD),
        ("scanCode",    ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

# Hook procedure function type
HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, ctypes.wintypes.HINSTANCE, ctypes.wintypes.DWORD
]
user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK

user32.CallNextHookEx.argtypes = [
    ctypes.wintypes.HHOOK, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
]
user32.CallNextHookEx.restype = ctypes.c_long

user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype  = ctypes.wintypes.BOOL

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype  = ctypes.c_short

user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD, ctypes.c_uint,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype  = ctypes.wintypes.DWORD


# ── Debug log ─────────────────────────────────────────────────────────
_LOG_FILE = Path.home() / ".waffler" / "hotkey.log"


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


# ── State enum ────────────────────────────────────────────────────────

class _State(Enum):
    IDLE         = "idle"
    PUSH_TO_TALK = "push_to_talk"
    STICKY       = "sticky"


# ── Listener ──────────────────────────────────────────────────────────

class WindowsHotkeyListener:
    """
    Smart hotkey: Ctrl+Win push-to-talk with sticky mode.

    Hold Ctrl+Win → start recording (push-to-talk).
    Release either key → stop recording.
    Press Space while holding Ctrl+Win → sticky mode (recording locked on).
    Press Ctrl+Win again in sticky mode → cancel recording.
    """

    def __init__(self, on_press, on_release):
        self._on_press   = on_press
        self._on_release = on_release
        self._state      = _State.IDLE
        self._running    = False
        self._hook       = None
        self._thread_id  = None

        # Key state tracking
        self._ctrl_held = False
        self._win_held  = False
        self._suppress_win_up = False  # suppress Win key-up after our combo
        self._busy = False  # True while processing transcription

        # Must prevent garbage collection of the callback
        self._hook_proc = HOOKPROC(self._ll_keyboard_proc)

    # ── Public API ────────────────────────────────────────────────────

    def start(self):
        """Install the hook and run a message loop (blocks)."""
        self._running   = True
        self._thread_id = kernel32.GetCurrentThreadId()
        _log("WindowsHotkeyListener.start() — Ctrl+Win smart hotkey")

        # Only allow one keyboard hook system-wide (prevents duplicate
        # overlays when both Waffler.exe and python app.py are running)
        self._hook_mutex = ctypes.windll.kernel32.CreateMutexW(
            None, True, "WafflerHotkeyHook"
        )
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            _log("Another Waffler hotkey hook is active — skipping")
            return

        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._hook_proc, None, 0
        )
        if not self._hook:
            err = ctypes.GetLastError()
            _log(f"SetWindowsHookExW failed (error {err}), using polling fallback")
            self._poll_fallback()
            return

        _log("Low-level keyboard hook installed")
        try:
            self._message_loop()
        finally:
            user32.UnhookWindowsHookEx(self._hook)
            _log("Hook removed")

    def stop(self):
        self._running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def join(self):
        pass

    # ── Low-level keyboard hook procedure ─────────────────────────────

    def _ll_keyboard_proc(self, nCode, wParam, lParam):
        """Called by Windows for every keyboard event system-wide."""
        if nCode >= 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up   = wParam in (WM_KEYUP, WM_SYSKEYUP)

            # ── Track Ctrl state ──
            if vk in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL):
                if is_down and not self._ctrl_held:
                    self._ctrl_held = True
                    self._check_combo_press()
                elif is_up:
                    self._ctrl_held = False
                    self._check_release()

            # ── Track Win state ──
            elif vk in (VK_LWIN, VK_RWIN):
                if is_down and not self._win_held:
                    self._win_held = True
                    self._check_combo_press()
                    # Suppress Win key-down when Ctrl is held (our combo)
                    if self._ctrl_held:
                        self._suppress_win_up = True
                        return 1  # block — don't pass to Windows
                elif is_down and self._win_held:
                    # Auto-repeat of Win while held — suppress if in our combo
                    if self._suppress_win_up:
                        return 1
                elif is_up:
                    self._win_held = False
                    self._check_release()
                    # Suppress the Win key-up to prevent Start menu
                    if self._suppress_win_up:
                        self._suppress_win_up = False
                        return 1  # block — prevents Start menu

            # ── Track Space (sticky mode trigger) ──
            elif vk == VK_SPACE and is_down:
                if self._state == _State.PUSH_TO_TALK:
                    self._enter_sticky()

        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    # ── State machine transitions ─────────────────────────────────────

    def set_busy(self, busy: bool):
        """Call with True while transcription is processing, False when done."""
        self._busy = busy
        _log(f"Busy = {busy}")

    def _check_combo_press(self):
        """Called when either Ctrl or Win is pressed."""
        if not (self._ctrl_held and self._win_held):
            return

        # Block new recordings while transcription is still processing
        if self._busy and self._state == _State.IDLE:
            _log("Ctrl+Win ignored — still processing")
            return

        if self._state == _State.IDLE:
            self._state = _State.PUSH_TO_TALK
            _log("Ctrl+Win → PUSH_TO_TALK, start recording")
            self._fire_press()

        elif self._state == _State.STICKY:
            self._state = _State.IDLE
            _log("Ctrl+Win → cancel STICKY, stop recording")
            self._fire_release()

    def _check_release(self):
        """Called when either Ctrl or Win is released."""
        if self._state == _State.PUSH_TO_TALK:
            if not (self._ctrl_held and self._win_held):
                self._state = _State.IDLE
                _log("Ctrl/Win released → stop PUSH_TO_TALK")
                self._fire_release()

    def _enter_sticky(self):
        """Space pressed during push-to-talk → switch to sticky."""
        self._state = _State.STICKY
        _log("Space → STICKY mode (recording locked)")

    # ── Callback helpers ──────────────────────────────────────────────

    def _fire_press(self):
        threading.Thread(target=self._safe_callback,
                         args=(self._on_press,), daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._safe_callback,
                         args=(self._on_release,), daemon=True).start()

    @staticmethod
    def _safe_callback(fn):
        try:
            fn()
        except Exception as e:
            _log(f"Hotkey callback error: {e}")

    # ── Message loop (keeps the hook alive) ───────────────────────────

    def _message_loop(self):
        """Pump Windows messages to keep the low-level hook alive."""
        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # ── Polling fallback (if hook installation fails) ─────────────────

    def _poll_fallback(self):
        """Fallback: GetAsyncKeyState polling with same state machine."""
        _log("Polling fallback active: Ctrl+Win (30ms interval)")
        while self._running:
            try:
                ctrl = _key_down(VK_CONTROL)
                win  = _key_down(VK_LWIN) or _key_down(VK_RWIN)
                space = _key_down(VK_SPACE)

                if self._state == _State.IDLE:
                    if ctrl and win:
                        self._state = _State.PUSH_TO_TALK
                        _log("[poll] Ctrl+Win → PUSH_TO_TALK")
                        self._fire_press()

                elif self._state == _State.PUSH_TO_TALK:
                    if space:
                        self._enter_sticky()
                    elif not (ctrl and win):
                        self._state = _State.IDLE
                        _log("[poll] released → stop PUSH_TO_TALK")
                        self._fire_release()

                elif self._state == _State.STICKY:
                    if ctrl and win:
                        self._state = _State.IDLE
                        _log("[poll] Ctrl+Win → cancel STICKY")
                        self._fire_release()
                        time.sleep(0.3)  # debounce

            except Exception as e:
                _log(f"Poll error: {e}")
            time.sleep(0.03)
