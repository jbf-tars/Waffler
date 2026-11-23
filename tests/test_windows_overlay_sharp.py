"""Windows: the pills and toasts are drawn sharp on a scaled screen.

The overlay helper (src/overlay_process_windows.py, its own process, Tk)
said nothing to Windows about scaling, so on a screen at 125% or 150% Windows
stretched its windows as a picture: blurry pills. The main window is
unaffected (pywebview declares itself aware). The helper now asks for GDI
scaling before its first window: Windows draws Tk's text and shapes at the
screen's own resolution, and every size and position stays in the 100% units
the helper is written in, so nothing moves and nothing changes at 100%.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
SRC = ROOT / "src" / "overlay_process_windows.py"


def _calls_in_main():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    order = []
    for node in ast.walk(main):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            order.append((node.lineno, name))
    return [name for _line, name in sorted(order)]


def test_it_is_asked_for_before_the_first_window():
    calls = _calls_in_main()
    assert "_sharp_when_scaled" in calls and "Tk" in calls
    assert calls.index("_sharp_when_scaled") < calls.index("Tk"), "after a window exists it is too late"
    assert calls.index("_sharp_when_scaled") < calls.index("Thread"), "first of all"


def test_it_asks_for_gdi_scaling_and_says_whether_windows_took_it(monkeypatch):
    pytest.importorskip("tkinter")              # the helper imports Tk (the Mac's Python has none)
    import overlay_process_windows as ov
    asked = []

    class Fn:
        argtypes = restype = None

        def __call__(self, ctx):
            asked.append(ctx.value)
            return 1

    class User32:
        SetProcessDpiAwarenessContext = Fn()
    import ctypes
    monkeypatch.setattr(ov.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: User32(), raising=False)
    assert ov._sharp_when_scaled() is True and asked == [ctypes.c_void_p(-5).value], "UNAWARE_GDISCALED"
    User32.SetProcessDpiAwarenessContext = lambda ctx: 0      # already set, or an older Windows
    assert ov._sharp_when_scaled() is False


def test_off_windows_it_does_nothing(monkeypatch):
    pytest.importorskip("tkinter")
    import overlay_process_windows as ov
    monkeypatch.setattr(ov.sys, "platform", "darwin")
    assert ov._sharp_when_scaled() is False
