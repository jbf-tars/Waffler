"""Clipboard management + auto-paste.

The real work lives in ``pasteboard.py`` (snapshot and restore, the paste
keystroke, the outcome). This class keeps the long-standing entry points so
call sites do not change, and adds ``paste_text``, the one call a dictation
makes to put its words into the app in front.
"""

import pyperclip
import platform

import pasteboard


class ClipboardManager:

    @staticmethod
    def copy(text: str):
        ok = pasteboard.copy_text(text)
        if ok:
            print(f"📋 Copied to clipboard ({len(text)} chars)")
        else:
            print("❌ Clipboard error: could not copy")
        return ok

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
        """Send only the paste keystroke (the clipboard must already hold the
        text). Prefer ``paste_text``, which also restores the user's clipboard
        and reports whether the paste worked."""
        try:
            pasteboard.send_paste_keystroke(window_handle)
            print("📋 Auto-pasted")
        except Exception as e:
            print(f"⚠️  Auto-paste failed: {e}")

    @staticmethod
    def paste_text(text: str, window_handle=None, restore: bool = True,
                   settle_delay=None, ticket=None) -> "pasteboard.PasteOutcome":
        """Put ``text`` into the focused app and report ``pasted`` /
        ``unverified`` / ``failed``. With ``restore`` (macOS) the user's
        previous clipboard, every type of it, is put back afterwards; on
        ``failed`` the text is left on the clipboard instead. ``ticket`` lets
        the caller stop waiting without a late restore (see
        ``pasteboard.PasteTicket``)."""
        return pasteboard.paste_text(text, window_handle=window_handle, restore=restore,
                                     settle_delay=settle_delay, ticket=ticket)
