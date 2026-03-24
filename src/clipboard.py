"""Clipboard management + auto-paste"""

import pyperclip
import platform
import time


class ClipboardManager:

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
        """Restore focus to previous window and send Ctrl+V."""
        try:
            import ctypes
            KEYEVENTF_KEYUP = 0x0002
            VK_CONTROL = 0x11
            VK_V = 0x56

            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.15)  # let window regain focus

            # Send Ctrl+V via keybd_event
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_V, 0, 0, 0)
            time.sleep(0.05)
            ctypes.windll.user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            print("📋 Auto-pasted via Ctrl+V")
        except Exception as e:
            print(f"⚠️  Auto-paste failed: {e}")

    @staticmethod
    def _auto_paste_mac():
        """Send Cmd+V on macOS via Quartz events (same approach as Windows)."""
        try:
            from Quartz import (
                CGEventCreateKeyboardEvent,
                CGEventPost,
                CGEventSetFlags,
                kCGEventKeyDown,
                kCGEventKeyUp,
                kCGEventFlagMaskCommand,
                kCGHIDEventTap
            )

            # V key code is 9 on macOS
            v_keycode = 9

            # Small delay to let the window get focus
            time.sleep(0.1)

            # Create Cmd+V key down event
            key_down = CGEventCreateKeyboardEvent(None, v_keycode, True)
            CGEventSetFlags(key_down, kCGEventFlagMaskCommand)

            # Create Cmd+V key up event
            key_up = CGEventCreateKeyboardEvent(None, v_keycode, False)
            CGEventSetFlags(key_up, kCGEventFlagMaskCommand)

            # Post events
            CGEventPost(kCGHIDEventTap, key_down)
            time.sleep(0.05)
            CGEventPost(kCGHIDEventTap, key_up)

            print("📋 Auto-pasted via Cmd+V")
        except Exception as e:
            print(f"⚠️  Auto-paste failed: {e}")
