"""The clipboard must never be scanned for secrets on a timer.

Windows Defender began deleting Waffler.exe on 2026-09-22, classifying the
running process as Behavior:Win32/CredentialAccess.A!ml (severity 5). The
binary was verified byte-identical to the release our own CI built, so the
verdict was wrong — but the behaviour behind it was real: the setup wizard
polled ``peek_clipboard_key`` every 1200 ms, and that call reads the
clipboard and regex-matches it against API-key shapes (``sk-``, ``gsk_``,
``csk-``). About 50 clipboard secret-scans a minute, for a key that arrives
once, in an unsigned binary that also installs a low-level keyboard hook.

The pickup now runs on window focus instead — the alt-tab back from the
provider's website, which was the only moment the poll ever caught anything.
These tests keep it that way: a future edit that reintroduces a timer would
re-earn the detection and, more importantly, put standing clipboard
surveillance back into a voice-dictation app.
"""
import os
import re

_ROOT = os.path.join(os.path.dirname(__file__), "..")
APP_JS = os.path.join(_ROOT, "ui", "app.js")


def _source():
    with open(APP_JS, "r", encoding="utf-8") as fh:
        return fh.read()


def test_clipboard_key_check_is_not_inside_a_timer():
    """No setInterval/setTimeout body may reach the clipboard key API."""
    src = _source()
    offenders = []
    for m in re.finditer(r"set(?:Interval|Timeout)\s*\(", src):
        # Scan the callback that follows, up to a generous bound.
        window = src[m.end():m.end() + 1200]
        if "peek_clipboard_key" in window or "_wizCheckClipboardOnce" in window:
            line = src[: m.start()].count("\n") + 1
            offenders.append(line)
    assert not offenders, (
        "clipboard key check reachable from a timer at ui/app.js line(s) "
        f"{offenders} - it must be triggered by a user event, not polled"
    )


def test_the_focus_trigger_is_wired():
    src = _source()
    assert "_wizCheckClipboardOnce" in src
    assert re.search(r"addEventListener\(\s*['\"]focus['\"]", src), \
        "the on-focus clipboard pickup is gone; setup would no longer collect a copied key"


def test_the_listener_is_removed_again():
    """A listener that outlives step 3 is a clipboard read on every focus."""
    src = _source()
    assert re.search(r"removeEventListener\(\s*['\"]focus['\"]", src), \
        "the focus listener is never removed"


def test_the_check_is_debounced():
    """Without a debounce a focus flap degenerates back into a poll."""
    src = _source()
    assert "_wizClipLastCheck" in src


def test_only_one_python_side_clipboard_reader_exists():
    """Keep the clipboard read surface to the single audited entry point."""
    with open(os.path.join(_ROOT, "app.py"), "r", encoding="utf-8") as fh:
        app_py = fh.read()
    assert app_py.count("ClipboardManager.paste()") == 1, (
        "a new clipboard reader appeared in app.py - every read is part of "
        "the credential-access surface and needs justifying"
    )
