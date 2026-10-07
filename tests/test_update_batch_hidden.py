"""The Windows update batch must run with no visible windows.

Updating 3.14.99 to 3.14.100 opened a stream of terminal windows
("find /I "Waffler.exe"", taskkill, ping). The batch was started with
DETACHED_PROCESS | CREATE_NO_WINDOW; Windows ignores CREATE_NO_WINDOW when
DETACHED_PROCESS is present, so cmd.exe had no console and every console
command it ran got a new, visible one.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import updater  # noqa: E402

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def test_batch_flags_hide_every_console():
    assert updater.UPDATE_BATCH_FLAGS & CREATE_NO_WINDOW
    assert not updater.UPDATE_BATCH_FLAGS & DETACHED_PROCESS


def test_batch_is_spawned_with_those_flags(monkeypatch, tmp_path):
    calls = []

    class FakePopen:
        def __init__(self, args, **kw):
            calls.append((args, kw))

    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    monkeypatch.setattr(updater, "_log_windows_signature_status", lambda p: None)

    class Exit(Exception):
        pass

    def fake_exit(code):
        raise Exit()

    monkeypatch.setattr(updater.os, "_exit", fake_exit)
    monkeypatch.setattr(updater, "_pending_dir", lambda: tmp_path)
    try:
        updater._install_windows(tmp_path / "Waffler-Setup-9.9.9.exe")
    except Exit:
        pass
    assert calls, "the update batch was not started"
    assert calls[0][1]["creationflags"] == updater.UPDATE_BATCH_FLAGS
