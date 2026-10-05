"""The Windows update survives a user name outside ASCII, and a failed check
is not reported as a failed install.

cmd.exe reads a batch file in the console's OEM code page, but the update
batch was written as UTF-8 with the installer, log, result and Waffler paths
in its text. Every one of them sits under C:\\Users\\<name>, so a name like
Seán or Zoë garbled them: Waffler was force-closed, the installer never ran
and nothing relaunched. A "%" in a path was expanded as a variable. The paths
now travel in the batch's environment, which is Unicode, and the batch text
is plain ASCII.

install_and_restart also wrote pending_update.json before checking the
download's SHA-256, so a download that failed the check (and never ran) was
reported at the next start as "Update to vX did NOT apply".

Offline. The end-to-end run uses stand-in programs copied from System32, so
the owner's Waffler is never touched.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import updater  # noqa: E402

SYS32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
NAMES = ("WAFFLER_INSTALLER", "WAFFLER_EXE", "WAFFLER_LOG", "WAFFLER_RESULT")


def test_the_batch_text_is_ascii_and_holds_no_path():
    text = updater.update_batch_text()
    text.encode("ascii")                       # raises if not
    for name in NAMES:
        assert f'"%{name}%"' in text
    assert "Users" not in text and ":\\" not in text


def test_the_environment_carries_each_path_exactly():
    env = updater.update_batch_env(r"C:\Users\Seán\AppData\Local\Temp\Waffler-Setup-3.15.0.exe",
                                   r"C:\Users\Zoë\AppData\Local\Programs\Waffler\Waffler.exe",
                                   r"C:\Users\Seán\100%\install.log", r"D:\r.txt")
    assert set(env) == set(NAMES)
    assert env["WAFFLER_INSTALLER"].endswith(r"Seán\AppData\Local\Temp\Waffler-Setup-3.15.0.exe")
    assert env["WAFFLER_EXE"].startswith(r"C:\Users\Zoë")
    assert env["WAFFLER_LOG"] == r"C:\Users\Seán\100%\install.log"


def test_install_windows_starts_the_batch_with_the_paths_in_its_environment(monkeypatch, tmp_path):
    calls = []

    class FakePopen:
        def __init__(self, args, **kw):
            calls.append((args, kw))

    class Exit(Exception):
        pass

    def fake_exit(code):
        raise Exit()

    temp = tmp_path / "Seán"
    temp.mkdir()
    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    monkeypatch.setattr(updater, "_log_windows_signature_status", lambda p: None)
    monkeypatch.setattr(updater.os, "_exit", fake_exit)
    monkeypatch.setattr(updater, "_pending_dir", lambda: tmp_path)
    setup = temp / "Waffler-Setup-9.9.9.exe"
    with pytest.raises(Exit):
        updater._install_windows(setup)
    [(args, kw)] = calls
    env = kw["env"]
    assert env["WAFFLER_INSTALLER"] == str(setup)
    assert env["WAFFLER_EXE"] == sys.executable
    assert env["WAFFLER_RESULT"] == str(tmp_path / updater.PENDING_RESULT_NAME)
    assert env["WAFFLER_LOG"].startswith(str(temp))
    assert "PATH" in {k.upper() for k in env}, "the rest of the environment is kept"
    bat = Path(args[-1])
    bat.read_bytes().decode("ascii")
    assert "Seán" not in bat.read_text(encoding="ascii", errors="replace")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch")
def test_the_real_batch_runs_an_installer_in_a_folder_named_sean_zoe_100(tmp_path):
    folder = tmp_path / "Seán Zoë 100%"
    folder.mkdir()
    installer = folder / "Waffler-Setup.exe"
    shutil.copy(os.path.join(SYS32, "hostname.exe"), installer)
    relaunch = folder / "Wäffler.exe"
    shutil.copy(os.path.join(SYS32, "rundll32.exe"), relaunch)
    result = folder / "result.txt"
    bat = tmp_path / "update.bat"
    bat.write_text(updater.update_batch_text(image="WafflerUnicodeTestNoSuch.exe"),
                   encoding="ascii")
    env = dict(os.environ)
    env.update(updater.update_batch_env(installer, relaunch, folder / "install.log", result))
    proc = subprocess.Popen(["cmd", "/c", str(bat)], close_fds=True,
                            creationflags=updater.UPDATE_BATCH_FLAGS, env=env)
    try:
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert result.exists(), "the result path was garbled"
    rc = result.read_text(encoding="ascii", errors="replace").strip()
    # 9009 is cmd's "is not recognized": the installer path was garbled.
    assert rc and rc != "9009", rc
    assert not bat.exists()


# ── the pending-update marker ────────────────────────────────────────────────

def test_a_download_that_fails_the_check_leaves_no_pending_update(monkeypatch, tmp_path):
    setup = tmp_path / "Waffler-Setup-3.15.0.exe"
    setup.write_bytes(b"not the release")
    monkeypatch.setattr(updater, "_pending_dir", lambda base_dir=None: tmp_path)
    monkeypatch.setitem(updater._state, "expected_digest", "sha256:" + "0" * 64)
    monkeypatch.setitem(updater._state, "source_url", None)
    ran = []
    monkeypatch.setattr(updater, "_install_windows", lambda p: ran.append(p))
    monkeypatch.setattr(updater, "_install_macos", lambda p: ran.append(p))
    with pytest.raises(Exception):
        updater.install_and_restart(str(setup))
    assert not ran
    assert not (tmp_path / updater.PENDING_MARKER_NAME).exists()
    assert updater.check_pending_update("3.14.100", base_dir=tmp_path) is None


def test_a_verified_download_records_the_pending_update_before_installing(monkeypatch, tmp_path):
    import hashlib
    setup = tmp_path / "Waffler-Setup-3.15.0.exe"
    setup.write_bytes(b"the release")
    digest = "sha256:" + hashlib.sha256(b"the release").hexdigest()
    monkeypatch.setattr(updater, "_pending_dir", lambda base_dir=None: tmp_path)
    monkeypatch.setitem(updater._state, "expected_digest", digest)
    seen = []

    def install(p):
        seen.append((tmp_path / updater.PENDING_MARKER_NAME).exists())

    monkeypatch.setattr(updater, "_install_windows", install)
    monkeypatch.setattr(updater, "_install_macos", install)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    updater.install_and_restart(str(setup))
    assert seen == [True]
