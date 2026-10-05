"""The Windows tray icon must exist, or the window must not hide.

Installed Windows builds from 3.14.84 to 3.14.100 had no tray icon: the
PyInstaller spec left icon.ico out, so the tray code gave up ("icon.ico not
found for tray icon" at every start). Closing the window still hid it, and a
start at sign-in began hidden, so Waffler could only be ended in Task Manager.

Offline. app.py cannot be imported here (pywebview, audio), so its start-up
order is read from the source, as in tests/test_startup_config.py.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import tray_state  # noqa: E402

APP_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SRC)


def _spec_datas():
    tree = ast.parse((ROOT / "Waffler_windows.spec").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Analysis":
            for kw in node.keywords:
                if kw.arg == "datas":
                    return [tuple(ast.literal_eval(e)) for e in kw.value.elts]
    raise AssertionError("no datas in Waffler_windows.spec")


def test_the_windows_build_bundles_icon_ico():
    assert ("icon.ico", ".") in _spec_datas()
    assert (ROOT / "icon.ico").is_file()


def test_the_png_the_fallback_uses_is_bundled_with_the_window():
    assert ("ui", "ui") in _spec_datas()
    assert (ROOT / "ui" / "logo-icon.png").is_file()


def test_the_first_icon_that_exists_wins(tmp_path):
    a, b = tmp_path / "a.ico", tmp_path / "b.ico"
    b.write_bytes(b"x")
    assert tray_state.find_app_icon([a, b], tmp_path / "none.png", tmp_path / "built.ico") == b


def test_an_icon_is_built_from_the_png_when_icon_ico_is_missing(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    built = tmp_path / "out" / "app-icon.ico"
    got = tray_state.find_app_icon([tmp_path / "missing.ico"],
                                   ROOT / "ui" / "logo-icon.png", built)
    assert got == built and built.is_file()
    assert Image.open(built).format == "ICO"
    # Built once: the next start uses the file already there.
    stamp = built.stat().st_mtime_ns
    assert tray_state.find_app_icon([], tmp_path / "gone.png", built) == built
    assert built.stat().st_mtime_ns == stamp


def test_no_icon_at_all_is_none_not_an_error(tmp_path):
    assert tray_state.find_app_icon([tmp_path / "x.ico"], tmp_path / "y.png",
                                    tmp_path / "z.ico") is None


def _main_src():
    fn = next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    return ast.get_source_segment(APP_SRC, fn)


def test_windows_makes_the_tray_before_deciding_to_start_hidden():
    src = _main_src()
    made = src.index("_windows_tray_up = _create_windows_tray_icon()")
    decided = src.index("start_hidden = (")
    assert made < decided
    block = src[decided:src.index("\n\n", decided)]
    assert "can_hide" in block
    assert 'can_hide = _windows_tray_up or _platform.system() == "Darwin"' in src


def test_closing_hides_the_window_only_when_the_tray_is_up():
    src = _main_src()
    assert 'elif _platform.system() == "Windows" and _windows_tray_up:' in src
    # The old unconditional hook, and the tray made later on a thread, are gone.
    assert "target=_create_tray_icon" not in src
    windows_branch = src[src.index('elif _platform.system() == "Windows" and _windows_tray_up:'):]
    windows_branch = windows_branch[:windows_branch.index("\n\n")]
    assert "window.events.closing += _on_window_closing" in windows_branch


def test_the_tray_maker_says_whether_it_worked():
    fn = next(n for n in APP_TREE.body
              if isinstance(n, ast.FunctionDef) and n.name == "_create_windows_tray_icon")
    nested = {id(r) for inner in ast.walk(fn)
              if isinstance(inner, ast.FunctionDef) and inner is not fn
              for r in ast.walk(inner)}
    returns = [n.value for n in ast.walk(fn)
               if isinstance(n, ast.Return) and id(n) not in nested]
    values = {getattr(v, "value", "other") for v in returns}
    assert values == {True, False}, "every exit says True (icon up) or False"
