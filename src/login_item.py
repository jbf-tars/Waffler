"""Start Waffler when the user signs in to the computer.

Until 3.15 nothing started Waffler after a restart, so the hotkey silently did
nothing until the user remembered to open the app. Setup now switches this on
by default (owner decision D6), and Settings has the off switch. The switch
always reads the real state from the operating system, never a remembered
copy, so it cannot claim to be on when it is not.

Windows: a value named "Waffler" under
    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
holding the installed Waffler.exe followed by --hidden. HKCU needs no admin
rights, which matches the installer (PrivilegesRequired=lowest), and the
installer removes the value on uninstall.

macOS: a LaunchAgent, ~/Library/LaunchAgents/com.waffler.app.login.plist,
which asks launchd to open the app bundle with --hidden at login. launchd
reads the folder at every login, so writing or deleting the file is enough.

Only an installed build can be started at sign-in. A copy run from source,
or a Mac app still running from the downloaded disk image (or from macOS's
quarantine copy), would leave an entry that points at something that goes
away, so those report "not supported" with a reason instead.

Everything platform-specific is passed in, so the tests run on any computer
without touching the real registry or the real LaunchAgents folder.
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path, PurePosixPath

APP_NAME = "Waffler"
HIDDEN_FLAG = "--hidden"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LAUNCH_AGENT_LABEL = "com.waffler.app.login"


def _is_mac(platform: str) -> bool:
    return platform == "darwin"


def _is_windows(platform: str) -> bool:
    return platform.startswith("win")


def mac_bundle_path(executable: str):
    """The .app folder that holds this executable, or None.

    A PyInstaller Mac build runs Waffler.app/Contents/MacOS/Waffler.
    """
    p = PurePosixPath(executable)
    try:
        bundle = p.parents[2]
    except IndexError:
        return None
    if bundle.suffix == ".app" and p.parent.name == "MacOS" and p.parent.parent.name == "Contents":
        return bundle
    return None


def mac_running_from_download(path) -> bool:
    """True when the app runs from the disk image or macOS's quarantine copy.

    Permissions and login items given to that copy stop working once the
    disk image is ejected or the app is moved to Applications.
    """
    s = str(path)
    return s.startswith("/Volumes/") or "/AppTranslocation/" in s


class LoginItem:
    """Read and change whether Waffler starts at sign-in."""

    def __init__(self, platform: str | None = None, executable: str | None = None,
                 frozen: bool | None = None, winreg_mod=None, agents_dir=None):
        self.platform = platform if platform is not None else sys.platform
        self.executable = executable if executable is not None else sys.executable
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
        self._winreg = winreg_mod
        self._agents_dir = Path(agents_dir) if agents_dir is not None else None

    # ── what would be started ────────────────────────────────────────────
    def unsupported_reason(self) -> str:
        """Empty when this computer can start Waffler at sign-in, otherwise
        one plain sentence saying why not."""
        if not (_is_windows(self.platform) or _is_mac(self.platform)):
            return "Starting at sign-in isn't available on this computer."
        if not self.frozen:
            return "Starting at sign-in works in the installed app."
        if _is_mac(self.platform):
            bundle = mac_bundle_path(self.executable)
            if bundle is None:
                return "Starting at sign-in works in the installed app."
            if mac_running_from_download(bundle):
                return "Move Waffler to Applications first, then turn this on."
        return ""

    def supported(self) -> bool:
        return not self.unsupported_reason()

    def command_line(self) -> str:
        """The Windows Run value: the quoted exe, then --hidden."""
        return f'"{self.executable}" {HIDDEN_FLAG}'

    def program_arguments(self) -> list:
        """The macOS LaunchAgent's ProgramArguments."""
        bundle = mac_bundle_path(self.executable)
        return ["/usr/bin/open", "-a", str(bundle), "--args", HIDDEN_FLAG]

    # ── Windows ─────────────────────────────────────────────────────────
    def _reg(self):
        if self._winreg is None:
            import winreg  # noqa: PLC0415 (Windows only)
            self._winreg = winreg
        return self._winreg

    def _win_read(self):
        reg = self._reg()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as k:
                value, _type = reg.QueryValueEx(k, APP_NAME)
                return value
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def _win_write(self, value: str):
        reg = self._reg()
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as k:
            reg.SetValueEx(k, APP_NAME, 0, reg.REG_SZ, value)

    def _win_delete(self):
        reg = self._reg()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as k:
                reg.DeleteValue(k, APP_NAME)
        except FileNotFoundError:
            pass

    # ── macOS ───────────────────────────────────────────────────────────
    def agent_path(self) -> Path:
        base = self._agents_dir or (Path.home() / "Library" / "LaunchAgents")
        return base / f"{LAUNCH_AGENT_LABEL}.plist"

    def _mac_plist(self) -> dict:
        return {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": self.program_arguments(),
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }

    def _mac_read(self):
        p = self.agent_path()
        if not p.exists():
            return None
        try:
            with open(p, "rb") as fh:
                return plistlib.load(fh)
        except Exception:
            # A file we can't read still starts something at login; count it
            # as on, so switching off removes it.
            return {}

    # ── the switch ──────────────────────────────────────────────────────
    def is_enabled(self) -> bool:
        if _is_windows(self.platform):
            try:
                return self._win_read() is not None
            except Exception:
                return False
        if _is_mac(self.platform):
            return self._mac_read() is not None
        return False

    def status(self) -> dict:
        reason = self.unsupported_reason()
        return {"supported": not reason, "enabled": self.is_enabled(), "reason": reason}

    def enable(self) -> dict:
        reason = self.unsupported_reason()
        if reason:
            return {"ok": False, "enabled": self.is_enabled(), "error": reason}
        try:
            if _is_windows(self.platform):
                self._win_write(self.command_line())
            else:
                p = self.agent_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".plist.tmp")
                with open(tmp, "wb") as fh:
                    plistlib.dump(self._mac_plist(), fh)
                os.replace(tmp, p)
        except Exception:
            return {"ok": False, "enabled": self.is_enabled(),
                    "error": "Couldn't turn on starting at sign-in. Try again."}
        return {"ok": True, "enabled": self.is_enabled()}

    def disable(self) -> dict:
        try:
            if _is_windows(self.platform):
                self._win_delete()
            elif _is_mac(self.platform):
                p = self.agent_path()
                if p.exists():
                    p.unlink()
        except Exception:
            return {"ok": False, "enabled": self.is_enabled(),
                    "error": "Couldn't turn off starting at sign-in. Try again."}
        return {"ok": True, "enabled": self.is_enabled()}

    def set(self, on: bool) -> dict:
        return self.enable() if on else self.disable()

    def refresh(self) -> bool:
        """Point an existing entry at this copy of Waffler.

        After an update installs to a different folder the old entry would
        start nothing. Only rewrites an entry that is already there, and only
        when it differs, so it never switches the feature on by itself.
        Returns True when it rewrote the entry.
        """
        if not self.supported() or not self.is_enabled():
            return False
        try:
            if _is_windows(self.platform):
                if self._win_read() != self.command_line():
                    self._win_write(self.command_line())
                    return True
            elif _is_mac(self.platform):
                if self._mac_read() != self._mac_plist():
                    return self.enable()["ok"]
        except Exception:
            return False
        return False
