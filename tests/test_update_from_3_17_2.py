"""Can Waffler 3.17.2 update itself to 4.0.0?

Every existing user reaches 4.0.0 through 3.17.2's own updater, so it is that
code, not 4.0.0's, that must accept what the 4.0.0 release publishes. Its
update check and updater are frozen in tests/fixtures/v3_17_2/ (copied
unchanged from the v3.17.2 tag) and driven here against a stand-in GitHub:
the release lists the assets the two release workflows publish for v4.0.0,
named as the workflows name them, with GitHub's own sha256 digest and
4.0.0's real sizes (a Windows installer of about 105 MB, a Mac disk image of
about 75 MB; 3.17.2's were 36 MB and 24 MB). Nothing touches the network and
nothing is installed or run.
"""
import ast
import hashlib
import importlib.util
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "v3_17_2"
VERSION = "4.0.0"
WIN_SIZE = 105 * 1024 * 1024 + 12345
MAC_SIZE = 75 * 1024 * 1024 + 678
CHUNK = 1024 * 1024


# -- what the 4.0.0 release publishes, read from the workflows themselves ------

def _published_names(version=VERSION):
    mac_wf = (ROOT / ".github" / "workflows" / "macos-release.yml").read_text(encoding="utf-8")
    assert 'VERSION="${GITHUB_REF_NAME#v}"' in mac_wf
    m = re.search(r'DMG_NAME="([^"]+)"', mac_wf)
    dmg = m.group(1).replace("${VERSION}", version)
    assert "files: dist-installer/*.dmg" in mac_wf
    iss = (ROOT / "installer" / "windows" / "Waffler.iss").read_text(encoding="utf-8")
    base = re.search(r"^OutputBaseFilename=(.+)$", iss, re.M).group(1).strip()
    exe = base.replace("{#MyAppVersion}", version) + ".exe"
    win_wf = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
    assert "files: installer/windows/dist-installer/*.exe" in win_wf
    # One asset per platform in the release: the old check takes the first
    # name ending in .dmg / .exe.
    assert win_wf.count("files:") == 1 and mac_wf.count("files:") == 1
    return dmg, exe


def test_the_workflows_publish_the_names_3_17_2_looks_for():
    dmg, exe = _published_names()
    assert dmg == "Waffler-4.0.0-mac.dmg"
    assert exe == "Waffler-Setup-4.0.0.exe"


def test_both_releases_are_published_as_latest_and_not_as_drafts_or_prereleases():
    for wf in ("macos-release.yml", "windows-release.yml"):
        text = (ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
        assert "make_latest: 'true'" in text
        assert "draft: true" not in text and "prerelease: true" not in text


def test_the_app_says_it_is_4_0_0():
    init = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{VERSION}"' in init


# -- the stand-in GitHub ---------------------------------------------------------

def _body(size):
    full, rest = divmod(size, CHUNK)
    for i in range(full):
        yield bytes([i % 251]) * CHUNK
    if rest:
        yield b"\x07" * rest


def _digest(size):
    h = hashlib.sha256()
    for c in _body(size):
        h.update(c)
    return "sha256:" + h.hexdigest()


class _Resp:
    def __init__(self, status=200, data=None, size=0, headers=None):
        self.status_code = status
        self._data = data
        self._size = size
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        yield from _body(self._size)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGitHub:
    def __init__(self):
        dmg, exe = _published_names()
        base = f"https://github.com/jbf-tars/waffler/releases/download/v{VERSION}/"
        self.assets = {
            dmg: {"name": dmg, "size": MAC_SIZE, "digest": _digest(MAC_SIZE),
                  "browser_download_url": base + dmg},
            exe: {"name": exe, "size": WIN_SIZE, "digest": _digest(WIN_SIZE),
                  "browser_download_url": base + exe},
        }
        self.release = {"tag_name": f"v{VERSION}", "draft": False, "prerelease": False,
                        "html_url": f"https://github.com/jbf-tars/waffler/releases/tag/v{VERSION}",
                        "assets": list(self.assets.values())}
        old = {"tag_name": "v3.17.2", "draft": False, "prerelease": False, "assets": [],
               "html_url": "https://github.com/jbf-tars/waffler/releases/tag/v3.17.2"}
        self.releases = [self.release, old]
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if url == "https://api.github.com/repos/jbf-tars/waffler/releases":
            return _Resp(data=self.releases)
        if url == f"https://api.github.com/repos/jbf-tars/waffler/releases/tags/v{VERSION}":
            return _Resp(data=self.release)
        for a in self.assets.values():
            if url == a["browser_download_url"]:
                return _Resp(size=a["size"], headers={"content-length": str(a["size"])})
        return _Resp(status=404)


@pytest.fixture
def github():
    return FakeGitHub()


@pytest.fixture
def old_updater(github, tmp_path, monkeypatch):
    """3.17.2's src/updater.py, loaded from the frozen copy."""
    spec = importlib.util.spec_from_file_location("_waffler_updater_3_17_2", FIX / "updater.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.requests, "get", github.get)
    monkeypatch.setattr(mod.tempfile, "gettempdir", lambda: str(tmp_path))
    return mod


def test_the_frozen_copy_is_what_3_17_2_shipped():
    try:
        # The file is UTF-8 (an em dash in a comment); text=True alone would
        # decode it with Windows' code page.
        shipped = subprocess.run(["git", "-C", str(ROOT), "show", "v3.17.2:src/updater.py"],
                                 capture_output=True, encoding="utf-8", timeout=20)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    if shipped.returncode != 0:
        pytest.skip("the v3.17.2 tag is not in this checkout")
    assert (FIX / "updater.py").read_text(encoding="utf-8") == shipped.stdout


# -- 3.17.2's update check -------------------------------------------------------

def _old_check(platform_name, monkeypatch, github):
    tree = ast.parse((FIX / "app_update_methods.py").read_text(encoding="utf-8"))
    ns = {"_log_to_file": lambda m: None, "update_check_error": lambda e=None: "error",
          "UPDATE_NO_INSTALLER": "no installer", "DOWNLOAD_PAGE": "page",
          "UPDATE_DOWNLOAD_FAILED": "failed", "os": __import__("os")}
    exec(compile(tree, "app_update_methods.py", "exec"), ns)
    import requests
    import platform
    import src
    monkeypatch.setattr(requests, "get", github.get)
    monkeypatch.setattr(platform, "system", lambda: platform_name)
    monkeypatch.setattr(src, "__version__", "3.17.2")
    return ns


@pytest.mark.parametrize("platform_name,asset", [("Darwin", 0), ("Windows", 1)])
def test_3_17_2_finds_4_0_0_and_its_installer(platform_name, asset, monkeypatch, github):
    ns = _old_check(platform_name, monkeypatch, github)
    r = ns["check_for_updates"](object())
    name = _published_names()[asset]
    assert r["update_available"] is True and r["latest_version"] == VERSION
    assert r["download_url"] == github.assets[name]["browser_download_url"]
    assert r["no_installer"] is False


def test_3_17_2_accepts_the_download_address(monkeypatch, github):
    ns = _old_check("Windows", monkeypatch, github)
    started = []
    import src.updater
    monkeypatch.setattr(src.updater, "start_download", started.append)
    url = github.assets[_published_names()[1]]["browser_download_url"]
    assert ns["start_update_download"](object(), url) == {"ok": True}
    assert started == [url]


# -- 3.17.2's download and checks, at 4.0.0's sizes ------------------------------

def test_windows_3_17_2_downloads_the_105_mb_installer_and_its_digest_matches(old_updater, github, tmp_path):
    name = _published_names()[1]
    url = github.assets[name]["browser_download_url"]
    expected = old_updater._expected_digest_for_url(url)
    assert expected == github.assets[name]["digest"]
    dest = tmp_path / f"waffler-update-1-{name}"
    old_updater._download_with_requests(url, dest.with_suffix(".exe.partial"), dest)
    state = old_updater.get_progress()
    assert state["done"] is True and state["error"] is None
    assert dest.stat().st_size == WIN_SIZE == state["bytes_downloaded"]
    old_updater._verify_artifact_digest(dest, expected)        # raises on any mismatch
    # The version it records before restarting comes from the file name.
    assert re.search(r"(\d+\.\d+\.\d+)", dest.name).group(1) == VERSION


def test_mac_3_17_2_downloads_the_75_mb_disk_image_with_curl(old_updater, github, tmp_path, monkeypatch):
    name = _published_names()[0]
    url = github.assets[name]["browser_download_url"]
    argvs = []

    def fake_run(argv, **kw):
        argvs.append(argv)
        return types.SimpleNamespace(stdout=f"HTTP/2 302\r\nlocation: x\r\n\r\nHTTP/2 200\r\n"
                                            f"content-length: {MAC_SIZE}\r\n", returncode=0)

    class FakePopen:
        def __init__(self, argv, **kw):
            argvs.append(argv)
            out = Path(argv[argv.index("-o") + 1])
            with open(out, "wb") as f:
                for c in _body(MAC_SIZE):
                    f.write(c)
            self.returncode = 0
            self.stderr = None

        def poll(self):
            return 0

    monkeypatch.setattr(old_updater.subprocess, "run", fake_run)
    monkeypatch.setattr(old_updater.subprocess, "Popen", FakePopen)
    dest = tmp_path / f"waffler-update-1-{name}"
    old_updater._download_with_curl(url, dest.with_suffix(".dmg.partial"), dest)
    assert dest.stat().st_size == MAC_SIZE
    old_updater._verify_artifact_digest(dest, github.assets[name]["digest"])
    # No size cap and no overall time limit on the body: only a connect timeout.
    body = argvs[-1]
    assert "--max-filesize" not in body and "--max-time" not in body and "-m" not in body
    assert re.search(r"(\d+\.\d+\.\d+)", dest.name).group(1) == VERSION


def test_3_17_2_has_no_size_cap_and_only_stall_timeouts():
    src = (FIX / "updater.py").read_text(encoding="utf-8")
    assert "max_bytes" not in src and "MAX_SIZE" not in src.upper().replace("MAX_SIZE_", "")
    assert "_STALL_TIMEOUT_S = 45" in src
    assert "timeout=(15, 30)" in src          # connect 15 s, then 30 s between bytes
    # codesign / spctl / hdiutil each get 120 s; a notarized 4.0.0 Waffler.app
    # (157 MB, 405 files) verified in about 1 s on an Apple silicon Mac.
    assert src.count("timeout=120") >= 3


def test_after_the_restart_4_0_0_reports_the_update_as_done(old_updater, tmp_path):
    import updater as new_updater
    old_updater.record_pending_update(VERSION, base_dir=tmp_path)   # what 3.17.2 writes
    r = new_updater.check_pending_update(VERSION, base_dir=tmp_path)  # what 4.0.0 reads
    assert r["ok"] is True and r["expected"] == VERSION


def test_a_digest_that_does_not_match_is_refused_by_3_17_2(old_updater, tmp_path):
    f = tmp_path / "Waffler-Setup-4.0.0.exe"
    f.write_bytes(b"not the installer")
    with pytest.raises(RuntimeError):
        old_updater._verify_artifact_digest(f, _digest(1024))


def test_the_windows_installer_keeps_its_identity_so_the_update_lands_in_place():
    """3.17.2 runs the new installer /VERYSILENT and relaunches its own
    Waffler.exe path: the AppId, folder and exe name must not move."""
    iss = (ROOT / "installer" / "windows" / "Waffler.iss").read_text(encoding="utf-8")
    assert "AppId={{2C9F4E1A-8B3D-4F7C-A2E5-6D0B1C3F8E9A}" in iss
    assert r"DefaultDirName={autopf}\{#MyAppName}" in iss
    assert "PrivilegesRequired=lowest" in iss
    spec = (ROOT / "Waffler_windows.spec").read_text(encoding="utf-8")
    assert "name='Waffler'" in spec or 'name="Waffler"' in spec


def test_the_mac_app_keeps_its_bundle_id_and_minimum_macos():
    """3.17.2 swaps the .app in place; a new bundle id would lose the person's
    macOS permissions, a higher minimum would install an app that cannot open."""
    spec = (ROOT / "Waffler_mac.spec").read_text(encoding="utf-8")
    assert "bundle_identifier='com.waffler.app'" in spec
    assert "'LSMinimumSystemVersion': '14.0.0'" in spec
