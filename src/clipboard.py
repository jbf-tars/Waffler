"""Clipboard management + auto-paste"""

import pyperclip
import platform
import time


class ClipboardManager:
    _last_paste_ts = 0.0

    @staticmethod
    def copy(text: str):
        try:
            pyperclip.copy(text)
            print(f"📋 Copied to clipboard ({len(text)} chars)")
            return True
        except Exception as e:
            print(f"❌ Clipboard error: {e}")
            return False

    @staticmethod
    def paste() -> str:
        try:
            return pyperclip.paste()
        except Exception as e:
            print(f"❌ Clipboard error: {e}")
            return ""

    @staticmethod
    def get_focused_window():
        """Capture the currently focused window handle (before overlay takes focus)."""
        if platform.system() == "Windows":
            try:
                import ctypes
                return ctypes.windll.user32.GetForegroundWindow()
            except Exception:
                return None
        return None  # macOS uses a different approach

    @staticmethod
    def auto_paste(window_handle=None):
        """Paste clipboard contents into the previously focused window."""
        if platform.system() == "Windows":
            ClipboardManager._auto_paste_windows(window_handle)
        else:
            ClipboardManager._auto_paste_mac()

    @staticmethod
    def _auto_paste_windows(hwnd=None):
        """Restore focus and paste once (debounced) to avoid duplicate pastes."""
        try:
            import ctypes

            now = time.time()
            # Guard against accidental duplicate trigger bursts
            if now - ClipboardManager._last_paste_ts < 0.8:
                print("⏭️  Skipping duplicate auto-paste")
                return

            WM_PASTE = 0x0302
            KEYEVENTF_KEYUP = 0x0002
            VK_CONTROL = 0x11
            VK_V = 0x56

            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.12)

                # Most reliable path: ask target window to paste directly
                pasted = ctypes.windll.user32.SendMessageW(hwnd, WM_PASTE, 0, 0)
                ClipboardManager._last_paste_ts = now
                print("📋 Auto-pasted via WM_PASTE")
                return pasted

            # Fallback if no known target window
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_V, 0, 0, 0)
            time.sleep(0.04)
            ctypes.windll.user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            ClipboardManager._last_paste_ts = now
            print("📋 Auto-pasted via Ctrl+V")
        except Exception as e:
            print(f"⚠️  Auto-paste failed: {e}")

    @staticmethod
    def _auto_paste_mac():
        """Send Cmd+V on macOS via osascript."""
        try:
            import subprocess
            subprocess.run([
                'osascript', '-e',
                'tell application "System Events" to keystroke "v" using command down'
            ], check=True)
            print("📋 Auto-pasted via Cmd+V")
        except Exception as e:
            print(f"⚠️  Auto-paste failed: {e}")
