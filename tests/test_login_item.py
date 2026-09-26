"""Start Waffler at sign-in (src/login_item.py, OB9 and owner decision D6).

Nothing started Waffler after a restart, so the hotkey did nothing until the
user opened the app. These tests use a fake registry and a temporary
LaunchAgents folder, so they run offline on any computer and never touch the
real login items.
"""
import plistlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from login_item import (  # noqa: E402
    APP_NAME, HIDDEN_FLAG, LAUNCH_AGENT_LABEL, RUN_KEY, LoginItem,
    mac_bundle_path, mac_running_from_download,
)

WIN_EXE = r"C:\Users\alex\AppData\Local\Programs\Waffler\Waffler.exe"
MAC_EXE = "/Applications/Waffler.app/Contents/MacOS/Waffler"


class FakeWinreg:
    """Just enough of the winreg module, over a dict of key -> {name: value}."""
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.keys = {}

    class _Handle:
        def __init__(self, store):
            self.store = store

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, sub, reserved=0, access=0):
        if (root, sub) not in self.keys:
            raise FileNotFoundError(sub)
        return self._Handle(self.keys[(root, sub)])

    def CreateKeyEx(self, root, sub, reserved=0, access=0):
        return self._Handle(self.keys.setdefault((root, sub), {}))

    def QueryValueEx(self, h, name):
        if name not in h.store:
            raise FileNotFoundError(name)
        return h.store[name], self.REG_SZ

    def SetValueEx(self, h, name, reserved, typ, value):
        h.store[name] = value

    def DeleteValue(self, h, name):
        if name not in h.store:
            raise FileNotFoundError(name)
        del h.store[name]

    def run_values(self):
        return self.keys.get((self.HKEY_CURRENT_USER, RUN_KEY), {})


def win(reg=None, frozen=True, exe=WIN_EXE):
    return LoginItem(platform="win32", executable=exe, frozen=frozen, winreg_mod=reg or FakeWinreg())


def mac(tmp_path, frozen=True, exe=MAC_EXE):
    return LoginItem(platform="darwin", executable=exe, frozen=frozen, agents_dir=tmp_path)


# ── Windows ──────────────────────────────────────────────────────────────────

def test_windows_on_writes_the_run_value_with_the_hidden_flag():
    reg = FakeWinreg()
    item = win(reg)
    assert item.status() == {"supported": True, "enabled": False, "reason": ""}
    assert item.set(True) == {"ok": True, "enabled": True}
    assert reg.run_values() == {APP_NAME: f'"{WIN_EXE}" {HIDDEN_FLAG}'}
    assert item.is_enabled()


def test_windows_off_removes_the_value_and_only_that_value():
    reg = FakeWinreg()
    reg.keys[(reg.HKEY_CURRENT_USER, RUN_KEY)] = {"OneDrive": "onedrive.exe /background"}
    item = win(reg)
    item.set(True)
    assert item.set(False) == {"ok": True, "enabled": False}
    assert reg.run_values() == {"OneDrive": "onedrive.exe /background"}


def test_windows_off_when_it_was_never_on_is_fine():
    assert win().set(False) == {"ok": True, "enabled": False}


def test_the_switch_reads_the_real_state_not_a_remembered_one():
    """The user can remove the entry in Task Manager; the switch must follow."""
    reg = FakeWinreg()
    item = win(reg)
    item.set(True)
    reg.run_values().clear()
    assert item.status()["enabled"] is False


def test_a_source_run_cannot_add_an_entry():
    reg = FakeWinreg()
    item = win(reg, frozen=False, exe=r"C:\Python311\python.exe")
    status = item.status()
    assert status["supported"] is False and status["reason"]
    result = item.set(True)
    assert result["ok"] is False and result["error"] == status["reason"]
    assert reg.run_values() == {}


def test_refresh_follows_an_install_that_moved_but_never_switches_it_on():
    reg = FakeWinreg()
    assert win(reg).refresh() is False
    assert reg.run_values() == {}
    win(reg, exe=r"C:\Old\Waffler.exe").set(True)
    assert win(reg).refresh() is True
    assert reg.run_values()[APP_NAME] == f'"{WIN_EXE}" {HIDDEN_FLAG}'
    assert win(reg).refresh() is False          # already current


# ── macOS ────────────────────────────────────────────────────────────────────

def test_mac_on_writes_a_launch_agent_that_opens_the_bundle_hidden(tmp_path):
    item = mac(tmp_path)
    assert item.set(True) == {"ok": True, "enabled": True}
    path = tmp_path / f"{LAUNCH_AGENT_LABEL}.plist"
    with open(path, "rb") as fh:
        plist = plistlib.load(fh)
    assert plist["Label"] == LAUNCH_AGENT_LABEL
    assert plist["RunAtLoad"] is True
    # -g: open without activating, or the Dock-reopen handler shows the window.
    assert plist["ProgramArguments"] == ["/usr/bin/open", "-g", "-a", "/Applications/Waffler.app",
                                         "--args", HIDDEN_FLAG]


def test_mac_off_deletes_the_agent(tmp_path):
    item = mac(tmp_path)
    item.set(True)
    assert item.set(False) == {"ok": True, "enabled": False}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("exe", [
    "/Volumes/Waffler/Waffler.app/Contents/MacOS/Waffler",
    "/private/var/folders/x/AppTranslocation/1234/d/Waffler.app/Contents/MacOS/Waffler",
])
def test_mac_refuses_while_running_from_the_download(tmp_path, exe):
    item = mac(tmp_path, exe=exe)
    assert item.status()["supported"] is False
    assert "Applications" in item.status()["reason"]
    assert item.set(True)["ok"] is False
    assert list(tmp_path.iterdir()) == []


def test_mac_bundle_and_download_detection():
    assert str(mac_bundle_path(MAC_EXE)) == "/Applications/Waffler.app"
    assert mac_bundle_path("/usr/local/bin/python3") is None
    assert mac_running_from_download("/Volumes/Waffler/Waffler.app")
    assert not mac_running_from_download("/Applications/Waffler.app")


def test_other_systems_say_so_plainly():
    item = LoginItem(platform="linux", executable="/usr/bin/waffler", frozen=True)
    assert item.status() == {"supported": False, "enabled": False,
                             "reason": "Starting at sign-in isn't available on this computer."}


def test_every_message_is_plain_and_has_no_em_dash():
    reasons = [win(frozen=False).unsupported_reason(),
               LoginItem(platform="linux", executable="x", frozen=True).unsupported_reason(),
               LoginItem(platform="darwin", executable="/Volumes/W/Waffler.app/Contents/MacOS/Waffler",
                         frozen=True).unsupported_reason()]
    for r in reasons:
        assert r and "\u2014" not in r and "registry" not in r.lower() and "launchagent" not in r.lower()


# ── the installer removes the Windows entry on uninstall ─────────────────────

def test_the_uninstaller_removes_the_run_value():
    iss = (Path(__file__).resolve().parent.parent / "installer" / "windows" / "Waffler.iss").read_text(encoding="utf-8")
    reg_lines = [l for l in iss.splitlines() if l.startswith("Root: HKCU") and "CurrentVersion\\Run" in l]
    assert reg_lines, "no [Registry] entry for the Run value, so uninstalling would leave it behind"
    line = reg_lines[0]
    assert 'ValueName: "Waffler"' in line and "uninsdeletevalue" in line
    # The app owns the switch (one place only): the installer never writes the value.
    assert "ValueType: none" in line


# ── app.py: an activation while starting hidden does not show the window ────

def _app_reopen_helper():
    import ast
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "app.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_reopen_should_show"]
    assert nodes, "_reopen_should_show not found in app.py"
    ns = {}
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    return ns["_reopen_should_show"]


def test_mac_activation_during_a_hidden_start_keeps_the_window_hidden():
    show = _app_reopen_helper()
    # Started at sign-in at t=100 with a 5 s grace: launch activation ignored.
    assert show(True, 101.0, 105.0) is False
    # A Dock click afterwards brings the window back, as before.
    assert show(True, 106.0, 105.0) is True
    # A normal start has no grace, and a visible window is never re-shown.
    assert show(True, 1.0, 0.0) is True
    assert show(False, 106.0, 105.0) is False
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    assert "_reopen_should_show(_window_hidden, time.monotonic(), _hidden_start_until)" in src
    assert "_hidden_start_until = time.monotonic() + _HIDDEN_START_GRACE_S" in src
