"""Two Windows guards from the audit (M5, M6), tested from any computer.

* M5: Command mode and Transforms read a selection on Windows by sending Ctrl+C
  to the app in front. In a terminal that is "stop the running program" when
  nothing is selected, and the terminals inside VS Code, JetBrains IDEs and
  other programs were not on the list of apps that are never sent it.
* M6: when Windows would not let Waffler open the program that owns the window
  in front (an elevated or protected one), the window's TITLE was used as the
  app's name, and the name is stored in the Journal. The README promises the
  title is never stored.

Every Win32 call is replaced by a stand-in; no key is sent.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import app_detection as ad  # noqa: E402
import selection_copy as sc  # noqa: E402
import text_context as tc  # noqa: E402
from test_command_action import Rig  # noqa: E402
from test_text_context import FakeTrip, reader  # noqa: E402


# ── M6: the title is never the name ──────────────────────────────────────────

class FakeWin32:
    """One foreground window: its title, its class, and the program that owns it."""

    def __init__(self, title="", path="", cls="Notepad", hwnd=7, pid=4242):
        self.title, self.path, self.cls, self.hwnd, self.pid = title, path, cls, hwnd, pid
        self.title_reads = 0

    def foreground_window(self):
        return self.hwnd

    def window_pid(self, hwnd):
        return self.pid

    def window_class(self, hwnd):
        return self.cls

    def process_image_path(self, pid):
        return self.path

    def window_title(self, hwnd):            # a title is never asked for
        self.title_reads += 1
        return self.title


def test_the_program_that_owns_the_window_is_the_app_name():
    api = FakeWin32("Payroll - Notepad", r"C:\Windows\System32\notepad.exe")
    assert ad._get_active_app_windows(api)["name"] == "notepad"
    assert api.title_reads == 0


def test_a_program_that_cannot_be_opened_is_unknown_not_its_window_title():
    api = FakeWin32("Payroll-2027 - Microsoft Visual Studio (Administrator)", "")
    got = ad._get_active_app_windows(api)
    assert got["name"] == "Unknown" and "Payroll" not in str(got)


def test_a_window_with_no_title_still_has_a_program():
    assert ad._get_active_app_windows(FakeWin32("", r"D:\apps\Some App.exe"))["name"] == "Some App"


def test_no_window_in_front_is_unknown():
    assert ad._get_active_app_windows(FakeWin32(hwnd=0))["name"] == "Unknown"


def test_a_win32_error_is_unknown_and_says_nothing_of_the_window(capsys):
    class Broken(FakeWin32):
        def process_image_path(self, pid):
            raise OSError("access denied: Payroll-2027 - Notepad")
    assert ad._get_active_app_windows(Broken("Payroll-2027 - Notepad"))["name"] == "Unknown"
    assert "Payroll" not in capsys.readouterr().out


def test_the_frontmost_name_the_journal_stores_is_empty_when_the_program_cannot_be_read(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ad, "_Win32", lambda: FakeWin32("passwords.txt - Notepad", ""))
    assert ad.frontmost_app_name() == ""
    monkeypatch.setattr(ad, "_Win32", lambda: FakeWin32("passwords.txt - Notepad", r"C:\Windows\notepad.exe"))
    assert ad.frontmost_app_name() == "notepad"


def test_the_window_class_is_known_on_windows_and_empty_elsewhere(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ad, "_Win32", lambda: FakeWin32(cls="CASCADIA_HOSTING_WINDOW_CLASS"))
    assert ad.frontmost_window_class() == "CASCADIA_HOSTING_WINDOW_CLASS"
    monkeypatch.setattr(ad, "_Win32", lambda: FakeWin32(hwnd=0))
    assert ad.frontmost_window_class() == ""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert ad.frontmost_window_class() == ""


def test_the_process_is_opened_with_the_limited_right_that_works_across_integrity_levels():
    assert ad.PROCESS_QUERY_LIMITED_INFORMATION == 0x1000
    src = (ROOT / "src" / "app_detection.py").read_text(encoding="utf-8")
    assert "PROCESS_VM_READ" not in src and "window_title[:50]" not in src and "GetWindowTextW" not in src


def test_a_windows_front_app_carries_no_title(monkeypatch):
    monkeypatch.setattr(ad, "frontmost_app_name", lambda: "")
    monkeypatch.setattr(ad, "frontmost_window_class", lambda: "")
    assert tc._windows_front() is None
    monkeypatch.setattr(ad, "frontmost_app_name", lambda: "notepad")
    monkeypatch.setattr(ad, "frontmost_window_class", lambda: "Notepad")
    front = tc._windows_front()
    assert (front.name, front.window_class) == ("notepad", "Notepad")


# ── M5: no Ctrl+C into a terminal ────────────────────────────────────────────

IDE_AND_TERMINAL_HOSTS = [
    "Code", "Code - Insiders", "Cursor", "Windsurf", "VSCodium", "idea64", "pycharm64", "webstorm64",
    "phpstorm64", "clion64", "goland64", "rider64", "rubymine64", "datagrip64", "studio64", "devenv",
    "Hyper", "Tabby", "MobaXterm", "ConEmu64", "ConEmu", "Cmder", "Termius", "SecureCRT", "XShell",
    "FluentTerminal", "Warp", "ubuntu", "wsl",
]


@pytest.mark.parametrize("name", IDE_AND_TERMINAL_HOSTS)
def test_an_ide_or_terminal_program_is_never_sent_ctrl_c(name):
    assert sc.keystroke_allowed(name, "win32") is False
    assert sc.keystroke_allowed(name + ".exe", "win32") is False


@pytest.mark.parametrize("cls", ["ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty",
                                 "VirtualConsoleClass", "PuTTY", "Alacritty", "org.wezfurlong.wezterm"])
def test_a_terminal_window_is_never_sent_ctrl_c_whatever_its_program_is_called(cls):
    assert sc.keystroke_allowed("some-new-terminal", "win32", cls) is False
    assert sc.keystroke_allowed("some-new-terminal", "win32", cls.lower()) is False


@pytest.mark.parametrize("name", ["notepad", "chrome", "OUTLOOK.EXE", "slack", "WINWORD", "firefox", "Notion"])
def test_other_windows_apps_may_still_be_sent_ctrl_c(name):
    assert sc.keystroke_allowed(name, "win32") is True
    assert sc.keystroke_allowed(name, "win32", "Chrome_WidgetWin_1") is True


def test_a_mac_is_unaffected_by_the_windows_lists():
    assert sc.keystroke_allowed("Code", "darwin", "ConsoleWindowClass") is True


def test_the_reader_refuses_a_terminal_by_its_window_class_too():
    trip = FakeTrip()
    front = tc.Front("", "newterm", 0, "CASCADIA_HOSTING_WINDOW_CLASS")
    got = reader(front, None, trip, platform="win32").read_selection(True)
    assert got == tc.SelectionResult("", "none", tc.NOT_ALLOWED_HERE) and trip.budgets == []


def test_the_reader_refuses_vs_code_on_windows_and_reads_other_apps():
    trip = FakeTrip(sc.CopyResult("from Word", sc.OK))
    assert reader(tc.Front("", "Code", 0), None, trip, platform="win32").read_selection(True).reason == \
        tc.NOT_ALLOWED_HERE
    assert trip.budgets == []
    assert reader(tc.Front("", "WINWORD", 0, "OpusApp"), None, trip, platform="win32") \
        .read_selection(True).as_tuple() == ("from Word", "copy")


def test_a_front_app_made_before_the_class_existed_still_works():
    assert tc.Front("", "x", 1).window_class == ""
    assert tc.Front("a", "b", 2) == tc.Front("a", "b", 2, "")


# ── M5: Command mode never sends its second Ctrl+C on Windows ────────────────

def windows_rig():
    rig = Rig()
    rig.runner.h.platform = "win32"
    return rig


def test_a_draft_with_nothing_selected_does_not_copy_a_second_time_on_windows():
    rig = windows_rig()
    rig.ax_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NOT_READABLE)         # Windows has no Accessibility
    rig.copy_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)       # the first Ctrl+C found nothing
    rig.spoken = "write a reply saying I will be late"
    rig.chat_text = "ACTION: draft\nI will be a little late."
    rig.go()
    assert rig.reads.count(True) == 1, "one Ctrl+C to read the selection"
    assert rig.rereads == [], "and not a second one to look again: it stops a program in a terminal"
    assert rig.pastes and rig.pastes[0]["text"] == "I will be a little late."


def test_a_mac_still_looks_again_before_pasting_a_draft_over_nothing():
    rig = Rig()
    rig.ax_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NOT_READABLE)
    rig.copy_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.reread = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.spoken = "write a reply saying I will be late"
    rig.chat_text = "ACTION: draft\nI will be a little late."
    rig.go()
    assert tc.METHOD_COPY in rig.rereads


# ── the Win32 plumbing itself, run against a fake library ────────────────────

class FakeDll:
    """What ``ctypes.WinDLL(name)`` gives: functions with prototypes that can be
    set, behaving as the real ones do for one foreground window."""

    def __init__(self, world, name):
        self.w, self.name = world, name

        class Fn:
            def __init__(self, fn):
                self._fn = fn
                self.argtypes = None
                self.restype = None

            def __call__(self, *a):
                return self._fn(*a)
        w = world
        if name == "user32":
            self.GetForegroundWindow = Fn(lambda: w["hwnd"])
            self.GetWindowThreadProcessId = Fn(lambda hwnd, ref: (setattr(ref._obj, "value", w["pid"]), 5)[1])
            self.GetClassNameW = Fn(lambda hwnd, buf, n: (setattr(buf, "value", w["class"]), 1)[1])
        else:
            self.OpenProcess = Fn(lambda access, inherit, pid: w["handle"] if w["can_open"] else 0)
            self.QueryFullProcessImageNameW = Fn(
                lambda h, flags, buf, size: (setattr(buf, "value", w["path"]), 1)[1] if w["path"] else 0)
            self.CloseHandle = Fn(lambda h: w["closed"].append(h))
            self.opened_with = []


def test_the_real_calls_read_a_window_its_program_and_its_class():
    world = {"hwnd": 0x20A4, "pid": 4242, "class": "Notepad", "handle": 0x44, "can_open": True,
             "path": r"C:\Windows\System32\notepad.exe", "closed": []}
    api = ad._Win32(loader=lambda name: FakeDll(world, name))
    assert api.foreground_window() == 0x20A4
    assert api.window_pid(0x20A4) == 4242
    assert api.window_class(0x20A4) == "Notepad"
    assert api.process_image_path(4242) == r"C:\Windows\System32\notepad.exe"
    assert world["closed"] == [0x44], "the process handle is closed"
    assert ad._get_active_app_windows(api)["name"] == "notepad"


def test_the_real_calls_say_nothing_when_the_process_cannot_be_opened():
    world = {"hwnd": 9, "pid": 77, "class": "x", "handle": 0, "can_open": False, "path": "", "closed": []}
    api = ad._Win32(loader=lambda name: FakeDll(world, name))
    assert api.process_image_path(77) == ""
    assert ad._get_active_app_windows(api)["name"] == "Unknown"
    assert world["closed"] == []


def test_the_handle_is_closed_even_when_the_name_cannot_be_read():
    world = {"hwnd": 9, "pid": 77, "class": "x", "handle": 0x55, "can_open": True, "path": "", "closed": []}
    api = ad._Win32(loader=lambda name: FakeDll(world, name))
    assert api.process_image_path(77) == "" and world["closed"] == [0x55]


def test_the_libraries_are_the_lookups_own_not_the_shared_windll():
    src = (ROOT / "src" / "app_detection.py").read_text(encoding="utf-8")
    assert "ctypes.windll" not in src and "WinDLL" in src
