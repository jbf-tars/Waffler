"""No terminal window may flash on Windows.

Clicking Update on Windows flashed a terminal window for a second or two
(3.17.0 to 3.17.1, 12 November 2026): just before installing, the updater
runs PowerShell to log the installer's Authenticode status, and started it
without CREATE_NO_WINDOW. Waffler is a windowed program with no console, so
Windows gave PowerShell a new, visible one. The update helper itself was
already started hidden (UPDATE_BATCH_FLAGS).

These tests pin that call, check on a real Windows that the flag leaves the
child with no console window at all, and keep an inventory of every program
Waffler starts, so a new console program cannot be added without the flag.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import updater  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


# -- the call that flashed -------------------------------------------------------

class _Done:
    def __init__(self, stdout="NotSigned"):
        self.stdout, self.stderr, self.returncode = stdout, "", 0


def _run_signature_check(monkeypatch, platform):
    calls = []

    def fake_run(args, **kw):
        calls.append((args, kw))
        return _Done()

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(updater.shutil, "which", lambda name: "C:/Windows/powershell.exe")
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    updater._log_windows_signature_status(Path("Waffler-Setup-9.9.9.exe"))
    assert len(calls) == 1, "the signature check should start PowerShell once"
    return calls[0]


def test_the_signature_check_starts_powershell_with_no_window(monkeypatch):
    args, kw = _run_signature_check(monkeypatch, "win32")
    assert args[0].endswith("powershell.exe")
    assert kw.get("creationflags", 0) & CREATE_NO_WINDOW, "PowerShell would open a visible terminal"
    # DETACHED_PROCESS makes Windows ignore CREATE_NO_WINDOW (the 3.14.100 bug).
    assert not kw["creationflags"] & DETACHED_PROCESS
    assert kw.get("capture_output") is True


def test_no_flag_is_passed_off_windows(monkeypatch):
    # subprocess raises on a Mac if creationflags is anything but 0.
    _args, kw = _run_signature_check(monkeypatch, "darwin")
    assert kw.get("creationflags", 0) == 0


def test_the_flags_are_the_windows_no_window_flag(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert updater._no_window_flags() == CREATE_NO_WINDOW
    monkeypatch.setattr(sys, "platform", "darwin")
    assert updater._no_window_flags() == 0


@pytest.mark.skipif(sys.platform != "win32", reason="checks a real Windows console")
def test_on_windows_a_child_started_this_way_has_no_console_window():
    """The child asks Windows for its console window: none means nothing can
    appear on screen, whatever the default terminal is."""
    child = [sys.executable, "-c", "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"]
    done = subprocess.run(child, capture_output=True, text=True, timeout=60,
                          creationflags=updater._no_window_flags())
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "0"


# -- every program Waffler starts --------------------------------------------------

# Launches that pass no creationflags, and why that is fine. Each is either a
# program that only runs on a Mac (or Linux), or a windowed program on
# Windows, which never gets a console. Anything else must start hidden.
NOT_A_CONSOLE_ON_WINDOWS = {
    ("src/languages.py", "_locale_names", "defaults"): "Mac only (reads AppleLocale)",
    ("app.py", "download_logs", "open"): "Mac only",
    ("app.py", "download_logs", "explorer"): "File Explorer is a windowed program",
    ("app.py", "reveal_unsent", "explorer"): "File Explorer is a windowed program",
    ("app.py", "reveal_unsent", "open"): "Mac only",
    ("app.py", "reveal_unsent", "xdg-open"): "Linux only",
    ("app.py", "open_url", "/usr/bin/open"): "Mac only",
    ("app.py", "open_url", "os.startfile"): "hands the link to the browser",
    ("app.py", "open_practice_editor", "/usr/bin/open"): "Mac only",
    ("app.py", "open_practice_editor", "notepad.exe"): "Notepad is a windowed program",
    ("app.py", "open_accessibility_settings", "open"): "Mac only",
    ("app.py", "open_input_monitoring_settings", "open"): "Mac only",
    ("app.py", "restart_app", "/bin/sh"): "Mac only",
    ("app.py", "restart_app", "sys.executable"): "restarts Waffler itself, a windowed program",
    ("app.py", "_disable_input_source_shortcut", "defaults"): "Mac only",
    ("src/app_detection.py", "_get_active_app_macos", "osascript"): "Mac only",
    ("src/history_export.py", "reveal_in_folder", "open"): "Mac only",
    ("src/history_export.py", "reveal_in_folder", "explorer"): "File Explorer is a windowed program",
    ("src/updater.py", "_download_with_curl", "/usr/bin/curl"): "Mac only",
    ("src/updater.py", "_verify_macos_app_signature", "cmd"): "Mac only (codesign, spctl)",
    ("src/updater.py", "_hdiutil_attach", "/usr/bin/hdiutil"): "Mac only",
    ("src/updater.py", "_hdiutil_detach", "/usr/bin/hdiutil"): "Mac only",
    ("src/updater.py", "_install_macos", "/bin/bash"): "Mac only",
    ("src/wedge_recovery.py", "spawn_detached", "argv"): "Mac only (the relaunch script)",
}

_LAUNCHERS = {("subprocess", n) for n in ("Popen", "run", "call", "check_call", "check_output")}
_LAUNCHERS |= {("os", n) for n in ("system", "popen", "startfile")}


def _program(call):
    """What a launch starts, as written: the first word of the command."""
    if (call.func.value.id, call.func.attr) == ("os", "startfile"):
        return "os.startfile"
    if not call.args:
        return "?"
    a = call.args[0]
    if isinstance(a, ast.BinOp) and isinstance(a.left, ast.List) and a.left.elts:
        a = a.left                       # [sys.executable] + sys.argv
    if isinstance(a, (ast.List, ast.Tuple)) and a.elts:
        a = a.elts[0]
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value.split()[0]
    if isinstance(a, ast.JoinedStr):     # f'explorer /select,"{path}"'
        head = next((v.value for v in a.values if isinstance(v, ast.Constant)), "")
        return head.split()[0] if head.split() else "?"
    return ast.unparse(a)


def _launches():
    files = [ROOT / "app.py"] + sorted((ROOT / "src").glob("*.py"))
    out = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def walk(node, func, func_src):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, child.name, ast.get_source_segment(path.read_text(encoding="utf-8"), child) or "")
                    continue
                if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                        and isinstance(child.func.value, ast.Name)
                        and (child.func.value.id, child.func.attr) in _LAUNCHERS):
                    names = [k.arg for k in child.keywords]
                    hidden = "creationflags" in names or (None in names and "creationflags" in func_src)
                    out.append((rel, func, _program(child), hidden, child.lineno))
                walk(child, func, func_src)

        walk(tree, "<module>", "")
    return out


def test_every_program_started_without_a_window_flag_is_known():
    unknown = [f"{rel}:{line} in {func}() starts {prog!r}"
               for rel, func, prog, hidden, line in _launches()
               if not hidden and (rel, func, prog) not in NOT_A_CONSOLE_ON_WINDOWS]
    assert not unknown, (
        "These start a program with no creationflags. On Windows a console program "
        "started this way opens a visible terminal window. Pass creationflags="
        "updater._no_window_flags() (or CREATE_NO_WINDOW on Windows), or, if it only "
        "runs on a Mac or starts a windowed program, add it to NOT_A_CONSOLE_ON_WINDOWS "
        "with the reason:\n  " + "\n  ".join(unknown))


def test_the_known_list_has_no_stale_entries():
    present = {(rel, func, prog) for rel, func, prog, _h, _l in _launches()}
    stale = sorted(set(NOT_A_CONSOLE_ON_WINDOWS) - present)
    assert not stale, f"no longer in the code, remove from NOT_A_CONSOLE_ON_WINDOWS: {stale}"


def test_the_windows_console_launches_are_all_hidden():
    """The ones that do start console programs on Windows."""
    hidden = {(rel, func) for rel, func, _p, h, _l in _launches() if h}
    for site in [("src/updater.py", "_log_windows_signature_status"),   # PowerShell
                 ("src/updater.py", "_install_windows"),                # the update helper
                 ("src/vpn_detect.py", "_run"),                          # route, netsh
                 ("src/overlay.py", "_start_process_locked")]:           # the overlay
        assert site in hidden, site
