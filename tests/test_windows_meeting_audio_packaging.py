"""Meetings on Windows hear the computer through PyAudioWPatch (WASAPI
loopback, src/meetings/recorder.py SystemSource). The Windows release build
installs requirements_windows.txt, and the library is imported inside
functions, so it must be listed there and named in the Windows spec. Until
21 Nov 2026 neither did, so a meeting on Windows recorded the microphone
only: SystemSource.supported() is False when the import fails.

Files read as text; the real build is not run here.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _names(requirements_file):
    text = (ROOT / requirements_file).read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.strip().startswith("#")]
    return {re.split(r"[<>=;\s\[]", line, maxsplit=1)[0].lower() for line in lines}


def test_the_windows_requirements_install_the_loopback_library():
    assert "pyaudiowpatch" in _names("requirements_windows.txt")


def test_the_windows_spec_names_it_among_the_hidden_imports():
    spec = (ROOT / "Waffler_windows.spec").read_text(encoding="utf-8")
    hidden = spec[spec.index("hiddenimports=["):spec.index("excludes=[")]
    assert "'pyaudiowpatch'," in hidden


def test_the_mac_requirements_leave_it_out():
    # Windows-only wheels: listed for the Mac, `pip install -r requirements.txt` would fail.
    assert "pyaudiowpatch" not in _names("requirements.txt")
