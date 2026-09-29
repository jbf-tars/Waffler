"""Small start-up and packaging fixes (audit item QW13).

* The window is painted in the active theme's background, not always dark.
* "System" has a light branch, so it is not dark on a light OS.
* The single-instance lock is the first thing main() does.
* UPX is off in both builds.
* The installer removes uninstallers orphaned by older installs.

(The .env precedence fix has its own file, tests/test_env_precedence.py.)
app.py cannot be imported here (pywebview, audio hardware), so its order and
bridge method are read from the source, as in tests/test_usage_pricing.py.
"""
import ast
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import theme  # noqa: E402

# The theme colours live in ui/tokens.css since 3.15.
CSS = (ROOT / "ui" / "tokens.css").read_text(encoding="utf-8")
APP_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


# ── window background ────────────────────────────────────────────────────────

def _css_block(selector_start):
    i = CSS.index(selector_start)
    return CSS[i:CSS.index("}", i)]


@pytest.mark.parametrize("name, selector", [
    ("cream", ":root,\nbody {"),
    ("dark", 'body[data-theme="dark"] {'),
])
def test_window_colours_match_the_stylesheet(name, selector):
    block = _css_block(selector)
    bg = re.search(r"--bg:\s*(#[0-9A-Fa-f]{6})", block).group(1)
    assert theme.BACKGROUNDS[name].lower() == bg.lower()


@pytest.mark.parametrize("stored, os_dark, expected", [
    ("cream", True, "#FDFCFC"),
    ("dark", False, "#0C0A09"),
    ("auto", False, "#FDFCFC"),
    ("auto", True, "#0C0A09"),
    ("auto", None, "#FDFCFC"),
    (None, None, "#FDFCFC"),
    ("purple", True, "#FDFCFC"),
])
def test_window_background_follows_the_theme(stored, os_dark, expected):
    assert theme.window_background(stored, os_dark) == expected


def test_os_preference_never_raises():
    assert theme.os_prefers_dark() in (True, False, None)


def _main():
    return next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef) and n.name == "main")


def test_the_window_is_not_created_dark_regardless():
    call = next(n for n in ast.walk(_main()) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "webview.create_window")
    bg = next(k.value for k in call.keywords if k.arg == "background_color")
    assert not isinstance(bg, ast.Constant), ast.unparse(bg)
    assert "window_background" in ast.unparse(_main())


def _set_theme_ns():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "set_theme")
    ns = {"_log_to_file": lambda m: None}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    return ns["set_theme"]


def test_set_theme_saves_a_known_theme():
    saved = {}
    api = types.SimpleNamespace(_load_settings_file=lambda: {"language": "en"},
                                _save_settings_file=saved.update)
    assert _set_theme_ns()(api, "auto") == {"ok": True}
    assert saved == {"language": "en", "theme": "auto"}


def test_set_theme_refuses_an_unknown_theme():
    api = types.SimpleNamespace(_load_settings_file=lambda: {},
                                _save_settings_file=lambda d: pytest.fail("saved"))
    result = _set_theme_ns()(api, "neon")
    assert result["ok"] is False and result["error"]


# ── System theme ─────────────────────────────────────────────────────────────

def test_system_theme_has_a_light_branch():
    # The light values apply to every body, System included, so a light OS
    # never falls through to night colours.
    light = _css_block(":root,\nbody {")
    assert re.search(r"--bg:\s*#FDFCFC", light)
    # System's night values only apply inside the dark-OS media query.
    media = CSS.index("@media (prefers-color-scheme: dark)")
    assert CSS.index('body[data-theme="auto"]') > media
    assert CSS.count('body[data-theme="auto"]') == 1


def test_the_ui_resolves_system_to_cream_or_dark():
    js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    assert "prefers-color-scheme: dark" in js
    assert "data-theme-pref" in js
    assert "set_theme" in js


# ── single-instance lock first ───────────────────────────────────────────────

def _first_line(pred):
    return min(n.lineno for n in ast.walk(_main()) if pred(n))


def _calls(name):
    return lambda n: isinstance(n, ast.Call) and ast.unparse(n.func).endswith(name)


def test_the_lock_is_taken_before_any_other_start_up_work():
    lock = _first_line(_calls("_acquire_lock"))
    banner = _first_line(lambda n: isinstance(n, ast.Constant) and isinstance(n.value, str)
                         and "=== Waffler starting ===" in n.value)
    assert lock < banner
    assert lock < _first_line(_calls("check_pending_update"))
    assert lock < _first_line(_calls("_is_vpn_active"))
    assert lock < _first_line(_calls("Config"))


def test_there_is_one_lock_call():
    assert sum(1 for n in ast.walk(_main()) if _calls("_acquire_lock")(n)) == 1


# ── packaging ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec", ["Waffler_windows.spec", "Waffler_mac.spec"])
def test_upx_is_off(spec):
    tree = ast.parse((ROOT / spec).read_text(encoding="utf-8"))
    upx = [k.value for n in ast.walk(tree) if isinstance(n, ast.Call)
           for k in n.keywords if k.arg == "upx"]
    assert len(upx) == 2
    assert all(isinstance(v, ast.Constant) and v.value is False for v in upx)


def test_installer_removes_stale_uninstallers_but_keeps_its_own():
    iss = (ROOT / "installer" / "windows" / "Waffler.iss").read_text(encoding="utf-8")
    code = iss[iss.index("[Code]"):]
    assert "CurStep = ssPostInstall" in code
    assert "DeleteStaleUninstallers" in code
    assert "{uninstallexe}" in code
    assert "CompareText(Base, Current) <> 0" in code
    assert "unins???.*" in code
