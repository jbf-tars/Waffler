"""A Mac update replaces the Waffler that is running, wherever it is.

The updater always installed to /Applications/Waffler.app. Run from
~/Applications (or as a renamed copy), an update added a second copy and
opened that one, or failed for a user who cannot write to /Applications.
Offline: only the path decision is checked.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import updater  # noqa: E402


@pytest.mark.parametrize("exe, want", [
    ("/Applications/Waffler.app/Contents/MacOS/Waffler", "/Applications/Waffler.app"),
    ("/Users/alex/Applications/Waffler.app/Contents/MacOS/Waffler",
     "/Users/alex/Applications/Waffler.app"),
    ("/Applications/Waffler 2.app/Contents/MacOS/Waffler", "/Applications/Waffler 2.app"),
    # Still on the disk image or in macOS's quarantine copy: a first install.
    ("/Volumes/Waffler/Waffler.app/Contents/MacOS/Waffler", "/Applications/Waffler.app"),
    ("/private/var/folders/x/AppTranslocation/ABC/d/Waffler.app/Contents/MacOS/Waffler",
     "/Applications/Waffler.app"),
    # Not a bundle at all (run from source).
    ("/usr/local/bin/python3", "/Applications/Waffler.app"),
])
def test_the_update_replaces_the_running_copy(exe, want):
    assert updater.mac_install_target(exe).as_posix() == want


def test_the_installer_stages_beside_the_target_not_always_in_applications():
    src = (Path(__file__).resolve().parent.parent / "src" / "updater.py").read_text(encoding="utf-8")
    body = src[src.index("def _install_macos"):]
    end = body.find("\ndef ", 1)
    body = body if end < 0 else body[:end]
    assert "installed = mac_install_target(sys.executable)" in body
    assert "apps_dir = installed.parent" in body
    assert 'installed = apps_dir / "Waffler.app"' not in body
