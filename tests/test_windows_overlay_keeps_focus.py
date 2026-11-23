"""The Windows overlay helper must never hold the foreground.

Measured on a Windows 11 PC in November 2026: Tk, building the helper's
withdrawn dictation window when its main loop started, made that invisible
window the foreground window whenever the helper was allowed to take it. Keys
typed in the app the person was using then went nowhere until the dictation
pill was next shown and hidden. The message cards before the pills did the
same. main() now notes the foreground window before Tk starts, builds the
window marked no-activate, and hands the foreground back if Tk took it
(_keep_foreground). These fakes check that rule on any platform; the
measurement itself is in docs/WINDOWS-TEST-REPORT.md.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pytest  # noqa: E402

pytest.importorskip("tkinter")           # a Python built without Tk (some Macs) cannot load the helper
import overlay_process_windows as ow  # noqa: E402

WIN = ROOT / "src" / "overlay_process_windows.py"


def _calls_in_main():
    tree = ast.parse(WIN.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    found = [(node.lineno, node.col_offset, ast.unparse(node.func))
             for node in ast.walk(main) if isinstance(node, ast.Call)]
    return [name for _, _, name in sorted(found)]


def test_main_notes_the_foreground_before_tk_and_hands_it_back_before_the_loop():
    calls = _calls_in_main()
    assert calls.index("_foreground_window") < calls.index("tk.Tk")
    assert calls.index("tk.Tk") < calls.index("_root.withdraw")       # hidden straight away
    assert calls.index("_keep_foreground") < calls.index("_root.mainloop")


def _run(monkeypatch, foreground, before, mine=1234):
    handed_back = []
    monkeypatch.setattr(ow, "_root", object())
    monkeypatch.setattr(ow, "_no_activate", lambda win: True)
    monkeypatch.setattr(ow, "_top_hwnd", lambda win: mine)
    monkeypatch.setattr(ow, "_foreground_api",
                        lambda: ((lambda: foreground), (lambda hwnd: handed_back.append(hwnd) or True)))
    monkeypatch.setattr(ow.sys, "platform", "win32")
    ow._keep_foreground(before)
    return handed_back


def test_the_foreground_goes_back_when_the_hidden_window_took_it(monkeypatch):
    assert _run(monkeypatch, foreground=1234, before=999) == [999]


def test_nothing_is_moved_when_the_person_kept_the_foreground(monkeypatch):
    assert _run(monkeypatch, foreground=777, before=999) == []


def test_nothing_is_moved_when_the_window_before_is_unknown(monkeypatch):
    assert _run(monkeypatch, foreground=1234, before=None) == []


def test_off_windows_it_does_nothing(monkeypatch):
    def must_not_be_called():
        raise AssertionError("the Windows calls were made off Windows")

    monkeypatch.setattr(ow.sys, "platform", "darwin")
    monkeypatch.setattr(ow, "_root", object())
    monkeypatch.setattr(ow, "_foreground_api", must_not_be_called)
    ow._keep_foreground(999)
    assert ow._foreground_window() is None
