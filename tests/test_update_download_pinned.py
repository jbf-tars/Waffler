"""An update is downloaded only from Waffler's own releases.

The window's page asks Python to download an update (start_update_download)
and then to run it (install_update_and_restart). Before: any
https://github.com/<anyone>/<anything>/releases/download/... address was
taken, and the SHA-256 it was checked against came from that same repository,
so a page that someone else's script got into could have run any program.
Now the bridge downloads only the installer the update check itself found in
jbf-tars/waffler, the updater refuses every other repository, and the saved
file's name cannot leave the temp folder.

app.py's bridge methods are lifted out of the source and run with a fake
requests module and a fake updater: no network, no keys.
"""
import ast
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import user_messages as um  # noqa: E402
import updater  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))

WAFFLER = "https://github.com/jbf-tars/Waffler/releases/download/v999.0.0/"
RELEASE = {"tag_name": "v999.0.0", "draft": False, "prerelease": False,
           "html_url": "https://github.com/jbf-tars/Waffler/releases/tag/v999.0.0",
           "assets": [{"name": "Waffler-Setup-999.0.0.exe",
                       "browser_download_url": WAFFLER + "Waffler-Setup-999.0.0.exe"},
                      {"name": "Waffler-999.0.0-mac.dmg",
                       "browser_download_url": WAFFLER + "Waffler-999.0.0-mac.dmg"}]}


class _Resp:
    status_code = 200

    def json(self):
        return [RELEASE]


def _bridge(monkeypatch):
    """check_for_updates and start_update_download in one namespace (they
    share its globals, as they share app.py's), with a fake updater."""
    klass = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fns = [n for n in klass.body if isinstance(n, ast.FunctionDef)
           and n.name in ("check_for_updates", "start_update_download")]
    logged, started = [], []
    ns = {"os": os, "_log_to_file": logged.append,
          "update_check_error": um.update_check_error,
          "UPDATE_NO_INSTALLER": um.UPDATE_NO_INSTALLER,
          "UPDATE_DOWNLOAD_FAILED": um.UPDATE_DOWNLOAD_FAILED,
          "DOWNLOAD_PAGE": um.DOWNLOAD_PAGE}
    exec(compile(ast.Module(fns, []), "<app.py>", "exec"), ns)
    monkeypatch.setitem(sys.modules, "requests",
                        types.SimpleNamespace(get=lambda *a, **k: _Resp()))
    fake = types.SimpleNamespace(start_download=started.append,
                                 is_release_asset_url=updater.is_release_asset_url)
    import src
    monkeypatch.setattr(src, "updater", fake, raising=False)
    monkeypatch.setitem(sys.modules, "src.updater", fake)
    return ns["check_for_updates"], ns["start_update_download"], logged, started


def test_the_installer_the_update_check_found_is_the_only_one_downloaded(monkeypatch):
    check, start, logged, started = _bridge(monkeypatch)
    offered = check(object())["download_url"]
    assert offered.startswith(WAFFLER)
    for other in (offered.replace("/jbf-tars/Waffler/", "/someone/Waffler/"),
                  offered.replace("/jbf-tars/Waffler/", "/jbf-tars/other/"),
                  offered + "#\\..\\..\\..\\Startup\\a.bat",
                  "https://objects.githubusercontent.com/x/Waffler-Setup-999.0.0.exe"):
        r = start(object(), other)
        assert r["ok"] is False and r["error"] == um.UPDATE_DOWNLOAD_FAILED, other
    assert started == []
    assert any("refused untrusted" in m for m in logged)
    assert start(object(), offered) == {"ok": True}
    assert started == [offered]


def test_nothing_is_downloaded_before_the_update_check_has_found_it(monkeypatch):
    _check, start, logged, started = _bridge(monkeypatch)
    r = start(object(), WAFFLER + "Waffler-Setup-999.0.0.exe")
    assert r["ok"] is False and r["error"] == um.UPDATE_DOWNLOAD_FAILED
    assert started == []


# ── the updater itself ───────────────────────────────────────────────────────

OTHER = "https://github.com/someone/waffler/releases/download/v999.0.0/Waffler-Setup-999.0.0.exe"


def test_release_assets_are_only_ever_waffler_s_own():
    assert updater.is_release_asset_url(WAFFLER + "Waffler-Setup-999.0.0.exe")
    assert updater.is_release_asset_url(
        "https://github.com/jbf-tars/waffler/releases/download/v1/W.exe")
    assert not updater.is_release_asset_url(OTHER)
    assert updater._parse_release_asset_url(OTHER) is None


def test_no_digest_is_asked_of_another_repository(monkeypatch):
    asked = []
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: asked.append(a))
    assert updater._expected_digest_for_url(OTHER) is None
    assert asked == []


def test_another_repository_is_never_downloaded(monkeypatch):
    updater._reset_state()
    ran = []
    monkeypatch.setattr(updater, "_download_worker", ran.append)
    updater.start_download(OTHER)
    state = updater.get_progress()
    assert ran == []
    assert state["active"] is False and state["error"] == um.UPDATE_DOWNLOAD_FAILED


def test_the_saved_file_stays_in_the_temp_folder(monkeypatch, tmp_path):
    updater._reset_state()
    seen = {}

    def download(url, partial, dest):
        seen["partial"], seen["dest"] = Path(partial), Path(dest)
        raise IOError("stopped by the test")

    monkeypatch.setattr(updater, "_download_with_curl", download)
    monkeypatch.setattr(updater, "_download_with_requests", download)
    monkeypatch.setattr(updater, "_expected_digest_for_url", lambda url: None)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    updater._download_worker(WAFFLER + "Waffler-Setup-999.0.0.exe#\\..\\..\\..\\Startup\\a.bat")
    assert seen["dest"].parent == tmp_path and seen["partial"].parent == tmp_path
    assert seen["dest"].name.endswith("Waffler-Setup-999.0.0.exe")
    assert "\\" not in seen["dest"].name and ".." not in seen["dest"].name
