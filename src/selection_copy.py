"""Read the selected text by copying it, and leave the clipboard as it was.

Accessibility cannot always say what is selected: Chromium and Electron apps
(Chrome, Slack, VS Code), Windows, and anything that hides its text. For a
feature the user asked for (rewrite what I selected) there is one more way:
press the app's own Copy, read what arrived on the clipboard, and put the
user's own clipboard back.

    snapshot every type on the clipboard      (src/pasteboard.py)
      -> send Cmd+C (Ctrl+C on Windows)
      -> wait for the clipboard to change, and for the text to be complete
      -> read the text
      -> put the old contents back, byte for byte, unless someone else wrote

Rules that keep it safe:

* It runs only when the caller says so (``allow_copy_fallback=True``). Ordinary
  dictation never reads the selection, and nothing here runs on a timer.
* If the old clipboard cannot be captured whole (a password manager's copy, a
  clipboard too large or slow to read, one that is not readable) it does not
  run at all: it would change what the user had copied for good.
* If nothing arrives on the clipboard the keystroke copied nothing, so the
  clipboard was never touched and nothing is restored.
* If the user (or another app) copies something while this waits, their copy is
  left alone and the old one is not put back over it.
* One clipboard operation at a time, shared with the paste (``pasteboard``'s
  lock), and a wedged one is not waited for long.
* The text is returned to the caller and never logged here.

The platform pieces (``MacPasteboard`` from pasteboard.py, ``WindowsTextClipboard``
below, the key senders) are handed in, so the sequence is tested without a
clipboard or a keyboard.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import pasteboard
from pasteboard import Snapshot

# Why a read gave nothing (or what it gave), for the caller to turn into a
# plain sentence. "" means it worked.
OK = ""
NOT_RESTORABLE = "clipboard_not_restorable"   # the old clipboard could not be captured whole
BUSY = "clipboard_busy"                       # another clipboard operation held the lock
NO_COPY = "nothing_copied"                    # the keystroke copied nothing (no selection / ignored)
TIMED_OUT = "timed_out"                       # no time left for a round trip
KEYSTROKE_FAILED = "keystroke_failed"
UNREADABLE = "clipboard_unreadable"

POLL_SECONDS = 0.01
# After the clipboard changes, the app may still be writing its data. The
# count has to stay the same for this long before the text is read.
SETTLE_SECONDS = 0.03
# A clipboard that changed but holds no text yet (the app clears it and fills
# it a moment later, or it was an image) is looked at for this long.
EMPTY_GRACE_SECONDS = 0.25
# Waiting for the hotkey's own keys to come up before Cmd+C is sent.
MODIFIER_WAIT_SECONDS = 0.3

# Windows consoles read Ctrl+C as "stop the running program" when nothing is
# selected, so the copy fallback must never be sent into one. (Process names,
# lower case, without .exe.)
NO_COPY_KEY_NAMES = frozenset({
    "cmd", "powershell", "powershell_ise", "pwsh", "conhost", "windowsterminal", "wt",
    "wsl", "wslhost", "bash", "mintty", "alacritty", "wezterm-gui", "kitty", "putty",
    "openconsole", "git-bash", "cygwin-terminal",
})


@dataclass(frozen=True)
class CopyResult:
    """``text`` is what was selected ('' when nothing was read); ``reason`` is
    ``OK`` or why not."""

    text: str = ""
    reason: str = OK

    @property
    def ok(self) -> bool:
        return self.reason == OK and bool(self.text)


def keystroke_allowed(app_name: str, platform: Optional[str] = None) -> bool:
    """May the copy keystroke be sent into this app? Everywhere yes, except a
    Windows console (see ``NO_COPY_KEY_NAMES``)."""
    if (platform or sys.platform) != "win32":
        return True
    name = " ".join(str(app_name or "").split()).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if not name:
        return False        # cannot tell what is in front, so do not risk Ctrl+C
    return name not in NO_COPY_KEY_NAMES


class CopyRoundTrip:
    """The copy-and-restore sequence over an injected clipboard and key sender.

    ``clipboard`` offers ``snapshot()``, ``change_count()``, ``current_text()``
    and ``restore(snapshot, expect_change_count)`` (``pasteboard.MacPasteboard``
    and ``WindowsTextClipboard`` do). ``send_copy()`` posts the copy keystroke
    and may raise. ``flags`` (optional) reads the held modifier keys.
    """

    def __init__(self, clipboard, send_copy: Callable[[], None], *,
                 flags: Optional[Callable[[], int]] = None, lock=None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 lock_wait: float = pasteboard.LOCK_WAIT_SECONDS,
                 log: Callable[[str], None] = lambda _m: None):
        self.pb = clipboard
        self._send_copy = send_copy
        self._flags = flags
        self._lock = lock if lock is not None else pasteboard._PASTE_LOCK
        self._sleep = sleep
        self._clock = clock
        self._lock_wait = lock_wait
        self._log = log

    def read(self, budget: float) -> CopyResult:
        """One round trip, finished within about ``budget`` seconds. Never
        raises; the clipboard is back as it was on every path that touched it."""
        started = self._clock()
        deadline = started + max(0.0, budget)
        if deadline - started <= 0:
            return CopyResult("", TIMED_OUT)
        if not self._lock.acquire(timeout=max(0.0, min(self._lock_wait, budget))):
            self._log("[selection] clipboard busy; not copying")
            return CopyResult("", BUSY)
        try:
            return self._round_trip(deadline)
        except Exception as e:      # noqa: BLE001 - this must not take the caller down
            self._log(f"[selection] copy failed: {type(e).__name__}")
            return CopyResult("", UNREADABLE)
        finally:
            self._lock.release()

    # -- internals -------------------------------------------------------

    def _round_trip(self, deadline: float) -> CopyResult:
        snap = self.pb.snapshot()
        if snap.skipped:
            # Whatever the user has copied cannot be put back, so copying
            # over it is not allowed.
            self._log(f"[selection] not copying: clipboard not restorable ({snap.skipped})")
            return CopyResult("", NOT_RESTORABLE)
        before = self.pb.change_count()
        self._wait_for_modifiers(deadline)
        try:
            self._send_copy()
        except Exception as e:      # noqa: BLE001
            self._log(f"[selection] copy keystroke failed: {type(e).__name__}")
            return CopyResult("", KEYSTROKE_FAILED)

        count = self._wait_for_change(before, deadline)
        if count is None:
            # Nothing was copied, so nothing of ours is on the clipboard.
            return CopyResult("", NO_COPY)
        count, text = self._settle_and_read(count, deadline)
        self._put_back(snap, count)
        if not text:
            return CopyResult("", NO_COPY)
        return CopyResult(text, OK)

    def _wait_for_modifiers(self, deadline: float) -> None:
        if self._flags is None:
            return
        budget = min(MODIFIER_WAIT_SECONDS, max(0.0, deadline - self._clock()))
        pasteboard.wait_for_modifiers_released(self._flags, budget, self._sleep, self._clock)

    def _wait_for_change(self, before: int, deadline: float) -> Optional[int]:
        while True:
            now = self.pb.change_count()
            if now != before:
                return now
            if self._clock() >= deadline:
                return None
            self._sleep(POLL_SECONDS)

    def _settle_and_read(self, count: int, deadline: float):
        """Wait until the clipboard stops changing (an app may clear it and
        write the data a moment later) and holds text, then read it. A
        clipboard with no text in it is given a short grace period for the
        data to arrive before it counts as "nothing copied". Returns the final
        change count and the text ('' when it holds none)."""
        first_change = stable_since = self._clock()
        text = ""
        while True:
            now = self.pb.change_count()
            if now != count:
                count, stable_since = now, self._clock()
            if self._clock() - stable_since >= SETTLE_SECONDS:
                text = self._clipboard_text()
                if text or self._clock() - first_change >= EMPTY_GRACE_SECONDS:
                    break
            if self._clock() >= deadline:
                break
            self._sleep(POLL_SECONDS)
        return count, text

    def _clipboard_text(self) -> str:
        try:
            text = self.pb.current_text()
        except Exception as e:      # noqa: BLE001
            self._log(f"[selection] could not read the copy: {type(e).__name__}")
            return ""
        return text if isinstance(text, str) else ""

    def _put_back(self, snap: Snapshot, count: int) -> None:
        """Give the user's clipboard back, but only over what the copy left
        there: if anything else wrote since, that is theirs now."""
        try:
            restored = self.pb.restore(snap, count)
        except Exception as e:      # noqa: BLE001
            self._log(f"[selection] restore failed: {type(e).__name__}")
            return
        if not restored:
            self._log("[selection] clipboard changed meanwhile; left as it is")


# ── Windows: a text-only clipboard ───────────────────────────────────────────

_CF_TEXT, _CF_OEMTEXT, _CF_UNICODETEXT, _CF_LOCALE = 1, 7, 13, 16
_TEXT_FORMATS = frozenset({_CF_TEXT, _CF_OEMTEXT, _CF_UNICODETEXT, _CF_LOCALE})
WINDOWS_TEXT_TYPE = "text/plain;windows"


def _windows_formats() -> Sequence[int]:
    import ctypes
    user32 = ctypes.windll.user32
    user32.EnumClipboardFormats.restype = ctypes.c_uint
    user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
    if not user32.OpenClipboard(None):
        raise OSError("clipboard is in use")
    try:
        found, fmt = [], 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                return found
            found.append(int(fmt))
    finally:
        user32.CloseClipboard()


def _windows_sequence() -> int:
    import ctypes
    return int(ctypes.windll.user32.GetClipboardSequenceNumber())


class WindowsTextClipboard:
    """The clipboard as the copy fallback needs it on Windows.

    Windows has no cheap whole-clipboard snapshot here, so this one can only
    put back a clipboard that is empty or holds plain text. Anything richer
    (an image, files, HTML from a browser or Office) makes ``snapshot`` say it
    cannot be restored, and the fallback then does not run: the user's copy is
    worth more than reading one selection.
    """

    def __init__(self, *, formats: Callable[[], Sequence[int]] = _windows_formats,
                 sequence: Callable[[], int] = _windows_sequence, pyperclip=None):
        self._formats = formats
        self._sequence = sequence
        self._pyperclip = pyperclip

    def _clip(self):
        if self._pyperclip is None:
            import pyperclip
            self._pyperclip = pyperclip
        return self._pyperclip

    def change_count(self) -> int:
        return int(self._sequence())

    def snapshot(self) -> Snapshot:
        count = self.change_count()
        try:
            formats = list(self._formats())
        except Exception:      # noqa: BLE001
            return Snapshot((), skipped="unreadable", change_count=count)
        if any(f not in _TEXT_FORMATS for f in formats):
            return Snapshot((), skipped="not_text", change_count=count)
        if not formats:
            return Snapshot((), change_count=count)          # empty: restoring empties it again
        text = self._clip().paste()
        if not isinstance(text, str):
            return Snapshot((), skipped="unreadable", change_count=count)
        return Snapshot((((WINDOWS_TEXT_TYPE, text.encode("utf-8")),),), change_count=count)

    def current_text(self) -> str:
        text = self._clip().paste()
        return text if isinstance(text, str) else ""

    def restore(self, snap: Optional[Snapshot], expect_change_count: int) -> bool:
        if snap is None or snap.skipped:
            return False
        if self.change_count() != expect_change_count:
            return False
        text = snap.items[0][0][1].decode("utf-8") if snap.items else ""
        self._clip().copy(text)
        return True


# ── The real senders ─────────────────────────────────────────────────────────

def _windows_ctrl_c() -> None:
    import ctypes
    keyup, vk_control, vk_c = 0x0002, 0x11, 0x43
    user32 = ctypes.windll.user32
    user32.keybd_event(vk_control, 0, 0, 0)
    user32.keybd_event(vk_c, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk_c, 0, keyup, 0)
    user32.keybd_event(vk_control, 0, keyup, 0)


def _mac_cmd_c() -> None:
    import mac_input
    mac_input.send_command_key(mac_input.c_keycode_for_current_layout())


def default_round_trip(log: Callable[[str], None] = lambda _m: None) -> Optional[CopyRoundTrip]:
    """The copy round trip for this computer, or None where there is none."""
    if sys.platform == "darwin":
        import mac_input
        return CopyRoundTrip(pasteboard.MacPasteboard(), _mac_cmd_c,
                             flags=mac_input.modifier_flags, log=log)
    if sys.platform == "win32":
        return CopyRoundTrip(WindowsTextClipboard(), _windows_ctrl_c, log=log)
    return None


__all__ = ["CopyResult", "CopyRoundTrip", "WindowsTextClipboard", "default_round_trip",
           "keystroke_allowed", "NO_COPY_KEY_NAMES", "OK", "NOT_RESTORABLE", "BUSY", "NO_COPY",
           "TIMED_OUT", "KEYSTROKE_FAILED", "UNREADABLE"]
