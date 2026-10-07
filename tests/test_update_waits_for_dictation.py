"""Restart to update waits for a dictation instead of losing it.

"Restart to update" exited at once (os._exit), and on Windows the update
helper then force-closes every Waffler. Nothing checked whether a dictation
was being recorded or cleaned up, or a Not sent recording was being sent,
so any of those was lost. The bridge now answers "busy" with a plain
sentence, and the window tries again by itself.

Offline. app.py cannot be imported here, so the two functions are lifted
from its source; the updater is a fake that records whether it was asked to
install.
"""
import ast
import os
import re
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import user_messages as um  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


def _lift(pipeline, monkeypatch, installed):
    api = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    method = next(n for n in api.body if isinstance(n, ast.FunctionDef)
                  and n.name == "install_update_and_restart")
    helper = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_update_would_interrupt")
    logged = []
    ns = {"os": os, "_pipeline": pipeline, "_log_to_file": logged.append,
          "UPDATE_INSTALL_FAILED": um.UPDATE_INSTALL_FAILED,
          "UPDATE_WAITING": um.UPDATE_WAITING, "DOWNLOAD_PAGE": um.DOWNLOAD_PAGE}
    exec(compile(ast.Module([helper, method], []), "<app.py>", "exec"), ns)
    fake = types.SimpleNamespace(
        get_progress=lambda: {"path": "C:/t/Waffler-Setup-3.15.0.exe"},
        install_and_restart=lambda p: installed.append(p))
    import src
    monkeypatch.setattr(src, "updater", fake, raising=False)
    monkeypatch.setitem(sys.modules, "src.updater", fake)
    return (lambda: ns["install_update_and_restart"](None, "C:/t/Waffler-Setup-3.15.0.exe")), logged


class FakeWatchdog:
    def __init__(self, runs=()):
        self.runs = list(runs)

    def active(self):
        return list(self.runs)


def pipeline(recording=False, runs=(), drain=False, unsent=False):
    p = types.SimpleNamespace(is_recording=recording, _watchdog=FakeWatchdog(runs),
                              _drain_lock=threading.Lock(), _unsent_lock=threading.Lock())
    if drain:
        p._drain_lock.acquire()
    if unsent:
        p._unsent_lock.acquire()
    return p


@pytest.mark.parametrize("busy", [
    dict(recording=True), dict(runs=["run"]), dict(drain=True), dict(unsent=True),
])
def test_a_dictation_in_progress_makes_the_restart_wait(monkeypatch, busy):
    installed = []
    install, logged = _lift(pipeline(**busy), monkeypatch, installed)
    r = install()
    assert r == {"ok": False, "busy": True, "error": um.UPDATE_WAITING}
    assert installed == [], "the installer must not run while a dictation is in progress"
    assert any(m.startswith("[update] install waits:") for m in logged)


def test_with_nothing_in_progress_the_install_goes_ahead(monkeypatch):
    installed = []
    install, _ = _lift(pipeline(), monkeypatch, installed)
    assert install() == {"ok": True}
    assert installed == ["C:/t/Waffler-Setup-3.15.0.exe"]


def test_before_the_pipeline_exists_the_install_goes_ahead(monkeypatch):
    installed = []
    install, _ = _lift(None, monkeypatch, installed)
    assert install() == {"ok": True} and installed


def test_the_waiting_message_is_plain():
    msg = um.UPDATE_WAITING
    assert msg.startswith("Finishing your dictation first.")
    assert chr(0x2014) not in msg and msg.endswith(".")


def test_the_window_tries_again_while_busy_and_stops_when_closed():
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    body = app[app.index("async function installDownloadedUpdate"):]
    body = body[:body.index("\n}\n")]
    assert "r.busy" in body and "setTimeout(" in body and "WL.INSTALL_RETRY_MS" in body
    close = app[app.index("function closeUpdateModal"):]
    close = close[:close.index("\n}\n")]
    assert "clearTimeout(_installRetryTimer)" in close
    logic = (ROOT / "ui" / "logic.js").read_text(encoding="utf-8")
    assert re.search(r"const INSTALL_RETRY_MS = \d+;", logic)
