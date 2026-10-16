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
