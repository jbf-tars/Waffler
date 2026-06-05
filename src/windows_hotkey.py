"""
Waffler Windows Hotkey — Configurable Smart Hotkey

Default: Win+Ctrl held = Push-to-talk (hold to record, release to stop)
         Win+Ctrl + Space = Sticky mode (locks recording on, can release keys)
         Win+Ctrl again   = Cancel sticky mode (stops recording)

Users can rebind the hotkey combo via Settings. Space always triggers sticky mode.

Uses SetWindowsHookEx(WH_KEYBOARD_LL) for press/release detection.
Suppresses Win/Alt key-up to prevent OS side effects when part of the combo.
Falls back to GetAsyncKeyState polling if the hook fails.

Writes debug output to ~/.waffler-hosted/hotkey.log.
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
VK_SPACE       = 0x20
VK_ESCAPE      = 0x1B  # v3.14.37 — Esc-cancel hotkey

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


# ── Key identifier → VK code mapping ────────────────────────────────
KEY_TO_VK = {
    "win":   [0x5B, 0x5C],          # VK_LWIN, VK_RWIN
    "ctrl":  [0x11, 0xA2, 0xA3],    # VK_CONTROL, VK_LCONTROL, VK_RCONTROL
    "alt":   [0x12, 0xA4, 0xA5],    # VK_MENU, VK_LMENU, VK_RMENU
    "shift": [0x10, 0xA0, 0xA1],    # VK_SHIFT, VK_LSHIFT, VK_RSHIFT
}

# Non-modifier function keys (F1-F24)
for _i in range(1, 25):
    KEY_TO_VK[f"f{_i}"] = [0x70 + _i - 1]
# Letter keys a-z
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEY_TO_VK[_c] = [ord(_c.upper())]
# Digit keys 0-9
for _d in "0123456789":
    KEY_TO_VK[_d] = [ord(_d)]

MODIFIER_KEYS = {"win", "ctrl", "alt", "shift"}
DEFAULT_HOTKEY = ["win", "ctrl"]

# Build reverse lookup: VK code → key string ID
_VK_TO_KEY = {}
for _kid, _vks in KEY_TO_VK.items():
    for _vk in _vks:
        _VK_TO_KEY[_vk] = _kid


def _vk_to_key_id(vk):
    """Reverse lookup: VK code → key string ID, or None."""
    return _VK_TO_KEY.get(vk)


def hotkey_display(keys):
    """Format key list as display string, e.g. ['win', 'ctrl'] → 'Win + Ctrl'."""
    return " + ".join(k.capitalize() if k in MODIFIER_KEYS else k.upper() for k in keys)


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


# ── State enum ────────────────────────────────────────────────────────

class _State(Enum):
    IDLE         = "idle"
    PUSH_TO_TALK = "push_to_talk"
    STICKY       = "sticky"


# ── Listener ──────────────────────────────────────────────────────────

class WindowsHotkeyListener:
    """
    Configurable smart hotkey with push-to-talk and sticky mode.

    Hold configured keys → start recording (push-to-talk).
    Release any key → stop recording.
    Press Space while holding → sticky mode (recording locked on).
    Press configured keys again in sticky mode → cancel recording.

    Default combo: Win+Ctrl. Users can rebind via Settings.
    """

    def __init__(self, on_press, on_release, on_cancel=None, keys=None):
        self._on_press   = on_press
        self._on_release = on_release
        # v3.14.37 — Esc cancels an in-progress recording without firing
        # on_release (which would transcribe + paste). Same pipeline as
        # clicking X on the overlay. Optional so wizard test-listeners
        # that don't want cancellation can just leave it as None.
        self._on_cancel  = on_cancel
        self._state      = _State.IDLE
        self._running    = False
        self._hook       = None
        self._thread_id  = None

        # Configurable keys
        self._keys = keys or DEFAULT_HOTKEY
        self._key_states = {k: False for k in self._keys}
        self._suppress_vks = set()  # VK codes to suppress on key-up
        self._busy = False  # True while processing transcription

        # Must prevent garbage collection of the callback
        self._hook_proc = HOOKPROC(self._ll_keyboard_proc)

    # ── Public API ────────────────────────────────────────────────────

    def start(self):
        """Install the hook and run a message loop (blocks)."""
        self._running   = True
        self._thread_id = kernel32.GetCurrentThreadId()
        _log(f"WindowsHotkeyListener.start() — {hotkey_display(self._keys)} smart hotkey")

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

    @property
    def is_combo_active(self):
        """Return True when all configured hotkey keys are held."""
        return self._all_keys_held()

    def set_busy(self, busy: bool):
        """Call with True while transcription is processing, False when done."""
        self._busy = busy
        _log(f"Busy = {busy}")

    # ── Low-level keyboard hook procedure ─────────────────────────────

    def _all_keys_held(self):
        """Return True when all configured keys are currently held."""
        return all(self._key_states.values())

    def _ll_keyboard_proc(self, nCode, wParam, lParam):
        """Called by Windows for every keyboard event system-wide."""
        if nCode >= 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode

            # Ignore injected/synthetic keystrokes (e.g. from auto-paste Ctrl+V)
            LLKHF_INJECTED = 0x10
            if kb.flags & LLKHF_INJECTED:
                return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

            is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up   = wParam in (WM_KEYUP, WM_SYSKEYUP)

            key_id = _vk_to_key_id(vk)

            # ── Track configured key state ──
            if key_id and key_id in self._key_states:
                if is_down and not self._key_states[key_id]:
                    self._key_states[key_id] = True
                    was_idle = self._state == _State.IDLE
                    self._check_combo_press()
                    # Suppress Win/Alt key-down only when it triggered recording
                    if key_id in ("win", "alt") and self._state != _State.IDLE and was_idle:
                        self._suppress_vks.add(vk)
                        return 1  # block — don't pass to OS
                    # Also suppress if already in our combo state
                    if key_id in ("win", "alt") and vk in self._suppress_vks:
                        return 1
                elif is_down and self._key_states[key_id]:
                    # Auto-repeat — suppress if we're suppressing this key
                    if vk in self._suppress_vks:
                        return 1
                elif is_up:
                    self._key_states[key_id] = False
                    self._check_release()
                    # Suppress key-up to prevent OS side effects (Start menu, Alt menu)
                    if vk in self._suppress_vks:
                        self._suppress_vks.discard(vk)
                        return 1

            # ── Track Space (sticky mode trigger) ──
            elif vk == VK_SPACE and is_down:
                if self._state == _State.PUSH_TO_TALK:
                    self._enter_sticky()

            # ── Esc → cancel an active recording (v3.14.37) ──
            # Only fires when actively recording; outside that, Esc passes
            # through normally so dialogs / vim / file pickers still work.
            elif vk == VK_ESCAPE and is_down and self._state != _State.IDLE:
                _log(f"Esc → CANCEL recording (was {self._state.value})")
                self._state = _State.IDLE
                self._fire_cancel()
                self._reset_key_states()  # clean stale modifier flags

        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    # ── State machine transitions ─────────────────────────────────────

    def _reset_key_states(self):
        """Reset all key states by polling actual hardware state."""
        for key_id in self._key_states:
            vk_list = KEY_TO_VK.get(key_id, [])
            self._key_states[key_id] = any(_key_down(vk) for vk in vk_list)
        self._suppress_vks.clear()

    def _check_combo_press(self):
        """Called when any configured key is pressed."""
        if not self._all_keys_held():
            return

        # Block new recordings while transcription is still processing
        if self._busy and self._state == _State.IDLE:
            _log("Combo ignored — still processing")
            return

        if self._state == _State.IDLE:
            self._state = _State.PUSH_TO_TALK
            _log(f"{hotkey_display(self._keys)} → PUSH_TO_TALK, start recording")
            self._fire_press()

        elif self._state == _State.STICKY:
            self._state = _State.IDLE
            _log(f"{hotkey_display(self._keys)} → cancel STICKY, stop recording")
            self._fire_release()

    def _check_release(self):
        """Called when any configured key is released."""
        if self._state == _State.PUSH_TO_TALK:
            if not self._all_keys_held():
                self._state = _State.IDLE
                _log("Key released → stop PUSH_TO_TALK")
                self._fire_release()
                # Reset states by polling hardware to avoid stuck keys
                self._reset_key_states()

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

    def _fire_cancel(self):
        """Esc-cancel — invoke the pipeline's cancel handler. Skipped when
        the constructor wasn't given an `on_cancel` callback (e.g. for
        the wizard's test listeners)."""
        if self._on_cancel is None:
            return
        threading.Thread(target=self._safe_callback,
                         args=(self._on_cancel,), daemon=True).start()

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
        """Fallback: GetAsyncKeyState polling with configurable keys."""
        _log(f"Polling fallback active: {hotkey_display(self._keys)} (30ms interval)")
        while self._running:
            try:
                all_held = all(
                    any(_key_down(vk) for vk in KEY_TO_VK.get(k, []))
                    for k in self._keys
                )
                space = _key_down(VK_SPACE)
                esc = _key_down(VK_ESCAPE)

                # v3.14.37 — Esc cancels any active recording. Same as the
                # hook path: discards audio, no transcription, no paste.
                if esc and self._state != _State.IDLE:
                    _log(f"[poll] Esc → CANCEL recording (was {self._state.value})")
                    self._state = _State.IDLE
                    self._fire_cancel()
                    time.sleep(0.3)  # debounce so a held Esc doesn't re-fire
                    continue

                if self._state == _State.IDLE:
                    if all_held:
                        self._state = _State.PUSH_TO_TALK
                        _log(f"[poll] {hotkey_display(self._keys)} → PUSH_TO_TALK")
                        self._fire_press()

                elif self._state == _State.PUSH_TO_TALK:
                    if space:
                        self._enter_sticky()
                    elif not all_held:
                        self._state = _State.IDLE
                        _log("[poll] released → stop PUSH_TO_TALK")
                        self._fire_release()

                elif self._state == _State.STICKY:
                    if all_held:
                        self._state = _State.IDLE
                        _log(f"[poll] {hotkey_display(self._keys)} → cancel STICKY")
                        self._fire_release()
                        time.sleep(0.3)  # debounce

            except Exception as e:
                _log(f"Poll error: {e}")
            time.sleep(0.03)
