"""The recorder and the ranking together (S12): a stream is opened on the
highest-ranked microphone that is plugged in, using the real resolver and the
real recorder over a fake PortAudio. The choice is made while the recorder
holds its stream lock, so PortAudio must be read without asking for that lock
again: asking would wait out its timeout and ignore the ranking.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import audio as audio_mod  # noqa: E402
import mic_devices as md  # noqa: E402
import mic_ranking as mr  # noqa: E402
from audio import AudioRecorder  # noqa: E402
from test_mic_capture_health import FakeSD  # noqa: E402


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSD(devices=("MacBook Pro Microphone", "USB Headset", "Yeti"))
    monkeypatch.setattr(audio_mod, "sd", fake)
    monkeypatch.setattr(audio_mod, "_HAL_DRAIN_S", 0)
    monkeypatch.setattr(md, "live_inputs", lambda: None)
    return fake


def recorder(settings, legacy=None):
    return AudioRecorder(device_resolver=mr.open_resolver(lambda: settings, lambda: legacy))


def test_the_real_resolver_opens_the_ranked_microphone_without_waiting_on_the_lock(sd):
    rec = recorder({"mic_ranking": [{"name": "Scarlett"}, {"name": "Yeti"}]})
    t0 = time.monotonic()
    rec.start_monitoring()
    assert time.monotonic() - t0 < 0.5, "the choice waited on a lock the recorder already holds"
    assert sd.streams[-1].kw["device"] == 2 and rec.opened_device() == "Yeti"
    assert audio_mod._STREAM_LOCK.acquire(timeout=1.0)
    audio_mod._STREAM_LOCK.release()


def test_with_nothing_ranked_it_follows_the_system(sd):
    rec = recorder({"mic_ranking": []})
    rec.start_monitoring()
    assert sd.streams[-1].kw["device"] is None and rec.opened_device() == "MacBook Pro Microphone"


def test_the_index_is_looked_up_afresh_for_every_stream(sd):
    rec = recorder({"mic_ranking": [{"name": "Yeti"}]})
    rec.start_monitoring()
    assert sd.streams[-1].kw["device"] == 2
    rec.stop_monitoring()
    sd.devices.insert(1, {"name": "New Interface", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0})
    rec.start_monitoring()                       # Yeti is device 3 now
    assert sd.streams[-1].kw["device"] == 3 and rec.opened_device() == "Yeti"


def test_the_old_saved_index_is_used_until_a_ranking_exists(sd):
    rec = recorder({}, legacy=1)
    rec.start_monitoring()
    assert sd.streams[-1].kw["device"] == 1 and rec.opened_device() == "USB Headset"


def test_a_bluetooth_default_is_still_avoided_when_nothing_is_ranked(sd):
    sd.devices[1]["name"] = "AirPods Pro"
    sd.default_index = 1
    rec = recorder({})
    rec.start_monitoring()
    assert sd.streams[-1].kw["device"] == 0 and rec.opened_device() == "MacBook Pro Microphone"


def test_the_system_list_decides_who_is_here_on_a_mac(sd, monkeypatch):
    monkeypatch.setattr(md, "live_inputs", lambda: [md.Device("Yeti", uid="U1", host_api="Core Audio")])
    rec = recorder({"mic_ranking": [{"name": "Studio Mic", "id": "uid:U1"}, {"name": "USB Headset"}]})
    rec.start_monitoring()
    assert sd.streams[-1].kw["device"] == 2          # renamed, but the same device
