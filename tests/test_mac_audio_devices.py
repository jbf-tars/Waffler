"""The CoreAudio reader (S12), macOS only for the live call.

The blobs CoreAudio hands back are read by pure functions, tested here with
made-up bytes on every platform. The one live test asks this Mac for its list
of inputs: that is a read of the device list, it opens no device and starts no
stream.
"""
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mac_audio_devices as ca  # noqa: E402

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="CoreAudio is macOS only")


def buffer_list(*channel_counts, pad=True):
    """An AudioBufferList as a 64-bit Mac lays it out."""
    out = struct.pack("=I", len(channel_counts)) + (b"\0" * 4 if pad else b"")
    for ch in channel_counts:
        out += struct.pack("=IIQ", ch, ch * 4096, 0)
    return out


def test_device_ids_are_native_integers():
    assert ca.parse_ids(struct.pack("=3I", 90, 83, 95)) == [90, 83, 95]
    assert ca.parse_ids(b"") == [] and ca.parse_ids(None) == []
    assert ca.parse_ids(b"\x01\x02\x03") == []          # not a whole number of ids


def test_a_device_with_no_input_buffers_has_no_input_channels():
    assert ca.parse_input_channels(buffer_list()) == 0
    assert ca.parse_input_channels(None) == 0 and ca.parse_input_channels(b"") == 0


def test_input_channels_are_counted_across_buffers():
    assert ca.parse_input_channels(buffer_list(1)) == 1
    assert ca.parse_input_channels(buffer_list(2, 2)) == 4


def test_a_truncated_or_lying_buffer_list_cannot_read_past_its_end():
    good = buffer_list(2, 2)
    assert ca.parse_input_channels(good[:-6]) == 2             # the second buffer is cut off
    lying = struct.pack("=I", 5000) + b"\0" * 4 + struct.pack("=IIQ", 1, 4096, 0)
    assert ca.parse_input_channels(lying) == 1                  # claims 5000, holds one
    assert ca.parse_input_channels(b"\x01\x00") == 0


def test_transport_codes_read_as_text():
    assert ca.code_text(struct.unpack(">I", b"bltn")[0]) == "bltn"
    assert ca.code_text(struct.unpack(">I", b"usb ")[0]) == "usb "
    assert ca.code_text(None) is None
    assert ca.code_text(0) is None                              # not four printable characters


def test_not_a_mac_gives_none(monkeypatch):
    monkeypatch.setattr(ca.sys, "platform", "win32")
    assert ca.list_inputs() is None


@mac_only
def test_this_mac_lists_its_inputs_without_opening_anything():
    got = ca.list_inputs()
    if got is None:
        pytest.skip("CoreAudio did not answer (a headless runner)")
    assert isinstance(got, list)
    for d in got:
        assert isinstance(d.name, str) and d.name.strip()
        assert d.channels >= 1 and d.host_api == "Core Audio"
        assert d.index is None                                  # PortAudio's index is not known here
    assert sum(1 for d in got if d.is_default) <= 1
    # Asking again gives the same list (nothing is consumed or left behind).
    assert [d.name for d in ca.list_inputs()] == [d.name for d in got]


@mac_only
def test_asking_many_times_is_stable_and_quick():
    import time
    first = ca.list_inputs()
    if first is None:
        pytest.skip("CoreAudio did not answer (a headless runner)")
    t0 = time.perf_counter()
    for _ in range(200):
        assert ca.list_inputs() is not None
    assert time.perf_counter() - t0 < 5.0       # about a millisecond each; the bound is for a slow runner


# ── a CoreAudio that does not answer ─────────────────────────────────────────
# The device monitor asks for this list every two seconds. On a Mac whose audio
# system had deadlocked, one of these asks sat in AudioObjectGetPropertyDataSize
# for ever; the listing now runs under a bound (src/audio_guard.py) and gives
# None, as it does for a Mac that cannot be asked at all.

class _Stuck:
    """A listing that never returns until the test lets it."""

    def __init__(self):
        import threading
        self.gate = threading.Event()
        self.calls = 0

    def __call__(self, api=None):
        self.calls += 1
        self.gate.wait(30)
        return ["late"]


@pytest.fixture
def mac(monkeypatch):
    import audio_guard
    monkeypatch.setattr(ca.sys, "platform", "darwin")
    monkeypatch.setattr(ca, "_load", lambda: object())
    monkeypatch.setattr(ca, "_LIST_TIMEOUT_S", 0.2)
    audio_guard.reset()
    yield
    audio_guard.reset()


def test_a_listing_that_never_returns_gives_none_within_the_bound(mac, monkeypatch):
    import time
    import audio_guard
    stuck = _Stuck()
    monkeypatch.setattr(ca, "_list_inputs", stuck)
    t0 = time.monotonic()
    assert ca.list_inputs() is None
    assert time.monotonic() - t0 < 1.5
    assert audio_guard.wedged_op() == "coreaudio device list"
    stuck.gate.set()


def test_while_it_is_stuck_the_next_poll_does_not_ask_again(mac, monkeypatch):
    import time
    stuck = _Stuck()
    monkeypatch.setattr(ca, "_list_inputs", stuck)
    ca.list_inputs()
    t0 = time.monotonic()
    for _ in range(10):
        assert ca.list_inputs() is None
    assert time.monotonic() - t0 < 0.5, "the polls did not wait"
    assert stuck.calls == 1, "and no thread was started for them"
    stuck.gate.set()


def test_the_wedge_is_told_to_the_process_handler_once(mac, monkeypatch):
    import time
    import audio_guard
    told = []
    audio_guard.set_handler(told.append)
    stuck = _Stuck()
    monkeypatch.setattr(ca, "_list_inputs", stuck)
    ca.list_inputs()
    ca.list_inputs()
    deadline = time.monotonic() + 2
    while not told and time.monotonic() < deadline:
        time.sleep(0.01)
    assert told == ["coreaudio device list"]
    stuck.gate.set()


def test_a_listing_that_answers_is_returned_as_it_was(mac, monkeypatch):
    monkeypatch.setattr(ca, "_list_inputs", lambda api: ["MacBook Pro Microphone"])
    assert ca.list_inputs() == ["MacBook Pro Microphone"]


def test_a_listing_that_raises_is_none_and_not_a_wedge(mac, monkeypatch):
    import audio_guard

    def boom(api):
        raise OSError("coreaudiod went away")
    monkeypatch.setattr(ca, "_list_inputs", boom)
    assert ca.list_inputs() is None
    assert audio_guard.wedged_op() is None


def test_the_bound_is_a_named_constant_with_room_over_a_healthy_listing():
    assert ca._LIST_TIMEOUT_S >= 1.0, "a listing takes under a millisecond; the bound is for a slow runner"
