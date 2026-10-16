"""The closed-lid hint (S12, item 6): when it applies, and that the read of
the lid is safe. The read is an in-process IOKit call (no subprocess), so
there is nothing that can hang; the one live test runs it on a Mac.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import clamshell as cs  # noqa: E402

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="IOKit is macOS only")


@pytest.mark.parametrize("lid, device, platform, want", [
    (True, "MacBook Pro Microphone", "darwin", True),
    (True, "Built-in Microphone", "darwin", True),
    (True, "Yeti Stereo Microphone", "darwin", False),       # another microphone: nothing to say
    (True, "AirPods Pro", "darwin", False),
    (False, "MacBook Pro Microphone", "darwin", False),      # lid open
    (None, "MacBook Pro Microphone", "darwin", False),       # a Mac without a lid, or unreadable
    (True, None, "darwin", False),                           # no stream yet: no name
    (True, "", "darwin", False),
    (True, "Microphone (Realtek High Definition Audio)", "win32", False),
    (True, "MacBook Pro Microphone", "win32", False),         # not a Mac
])
def test_when_the_hint_applies(lid, device, platform, want):
    assert cs.hint_due(lid, device, platform) is want


def test_not_a_mac_never_reads_anything(monkeypatch):
    monkeypatch.setattr(cs.sys, "platform", "win32")
    assert cs.lid_closed() is None


def test_a_mac_that_cannot_load_iokit_gives_none(monkeypatch):
    monkeypatch.setattr(cs.sys, "platform", "darwin")
    monkeypatch.setattr(cs, "_api", False)
    assert cs.lid_closed() is None


@mac_only
def test_the_lid_reads_as_open_closed_or_not_there_and_never_raises():
    results = {cs.lid_closed() for _ in range(300)}
    assert len(results) == 1 and results.pop() in (True, False, None)


@mac_only
def test_the_read_takes_microseconds_not_a_process():
    import time
    t0 = time.perf_counter()
    for _ in range(300):
        cs.lid_closed()
    assert (time.perf_counter() - t0) / 300 < 0.01
