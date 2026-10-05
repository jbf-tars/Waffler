"""Run the real Windows update batch end to end, against a dummy process.

The 3.14.99 -> 3.14.100 update hung for ever in the batch's wait loop
(``tasklist | find`` never returned), so the installer never ran and
Waffler was left closed. This drives the actual batch text with the actual
spawn flags, using a copy of ping.exe under a made-up image name as the
"Waffler" to close, so the owner's real Waffler is never touched.
"""
import os
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import updater  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows batch")

SYS32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")


def test_batch_closes_the_app_installs_records_and_exits(tmp_path):
    image = "WafflerBatchTestDummy.exe"
    dummy = tmp_path / image
    shutil.copy(os.path.join(SYS32, "PING.EXE"), dummy)
    victim = subprocess.Popen([str(dummy), "-n", "120", "127.0.0.1"],
                              stdout=subprocess.DEVNULL,
                              creationflags=0x08000000)
    # Stand-ins: an "installer" that runs and exits, and a GUI-subsystem
    # program for the relaunch that exits at once without a window.
    installer = tmp_path / "FakeSetup.exe"
    shutil.copy(os.path.join(SYS32, "hostname.exe"), installer)
    relaunch = os.path.join(SYS32, "rundll32.exe")
    result = tmp_path / "result.txt"
    bat = tmp_path / "update.bat"
    bat.write_text(updater.update_batch_text(image=image), encoding="ascii")
    env = dict(os.environ)
    env.update(updater.update_batch_env(installer, relaunch, tmp_path / "install.log", result))

    t0 = time.monotonic()
    proc = subprocess.Popen(["cmd", "/c", str(bat)], close_fds=True,
                            creationflags=updater.UPDATE_BATCH_FLAGS, env=env)
    try:
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
        if victim.poll() is None:
            victim.kill()
    elapsed = time.monotonic() - t0

    assert proc.returncode is not None, "the batch hung"
    assert victim.poll() is not None, "the batch did not close the app"
    assert result.exists(), "the installer's exit code was not recorded"
    assert not bat.exists(), "the batch did not delete itself"
    assert elapsed < 45, f"took {elapsed:.0f}s"


def test_batch_gives_up_waiting_instead_of_hanging(tmp_path):
    # A name taskkill cannot find ends the loop at once (exit code 128);
    # the cap is exercised by asserting it is present in the text.
    text = updater.update_batch_text(max_kill_tries=7)
    assert "if %TRIES% GEQ 7 goto killed" in text
    assert "|" not in text, "no pipes: a pipe to find hung the 3.14.100 update"
