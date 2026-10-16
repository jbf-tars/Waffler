"""The recorder's side of the microphone work (S12): capture health, opening
the stream again in the middle of a take, and choosing the microphone at the
moment a stream is made.

No microphone is opened: ``audio.sd`` is replaced by a fake whose streams
record what is done to them. The stream-lifecycle rules in audio.py (stop,
drain, close, and the lock around it) are checked in the order they happen.
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audio as audio_mod  # noqa: E402
from audio import AudioRecorder  # noqa: E402


class FakeStream:
    def __init__(self, owner, **kw):
        self.owner, self.kw = owner, kw
        self.active = False
        self.events = []
        self.callback = kw.get("callback")
        self.start_gate = None            # a threading.Event the start waits on, if set

    def start(self):
        if self.start_gate is not None:
            self.start_gate.wait(5)
        if self.owner.fail_start:
            raise RuntimeError("no such device")
        self.active = True
        self.events.append("start")
        self.owner.log.append("start")
        if self.owner.live_on_start and self.callback is not None:
            # A real stream starts delivering audio; the recorder waits for it.
            self.callback(np.full((1024, 1), 300, dtype=np.int16), 1024, None, None)

    def stop(self):
        self.active = False
        self.events.append("stop")
        self.owner.log.append("stop")

    def close(self):
        self.events.append("close")
        self.owner.log.append("close")


class FakeSD:
    """The parts of sounddevice that audio.py calls."""

    def __init__(self, devices=("MacBook Pro Microphone", "AirPods Pro", "Yeti"), default=0):
        self.devices = [{"name": n, "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0}
                        for n in devices]
        self.default_index = default
        self.streams = []
        self.log = []
        self.fail_start = False
        self.next_gate = None
        self.live_on_start = False

        class _Default:
            hostapi = 0
        self.default = _Default()

    def _terminate(self):
        self.log.append("terminate")

    def _initialize(self):
        self.log.append("initialize")

    def query_devices(self, device=None, kind=None):
        if kind == "input":
            return dict(self.devices[self.default_index], index=self.default_index)
        if device is None:
            return list(self.devices)
        return self.devices[device]

    def query_hostapis(self, index=None):
        return ({"name": "Core Audio"},)

    def InputStream(self, **kw):
        s = FakeStream(self, **kw)
        if self.next_gate is not None:
            s.start_gate, self.next_gate = self.next_gate, None
        self.streams.append(s)
        return s


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSD()
    monkeypatch.setattr(audio_mod, "sd", fake)
    monkeypatch.setattr(audio_mod, "_HAL_DRAIN_S", 0)
    monkeypatch.setattr(audio_mod, "_POSTROLL_MS", 0)
    return fake


def block(value, n=1024):
    return np.full((n, 1), value, dtype=np.int16)


def feed(rec, value, count=1):
    for _ in range(count):
        rec._callback(block(value), 1024, None, None)


def recording(sd, **kw):
    """A recorder with a live (fake) stream, mid-take."""
    rec = AudioRecorder(**kw)
    rec.start_monitoring()
    rec._last_press_time = time.time()
    rec.start()
    assert rec.is_recording
    return rec


# ── capture health ───────────────────────────────────────────────────────

def test_a_chunk_with_any_sample_but_zero_is_a_sign_of_life(sd):
    rec = recording(sd)
    time.sleep(0.06)            # well over Windows' 15.6 ms clock step: a 20 ms sleep can measure as 16
    feed(rec, 0, 3)
    h = rec.health()
    assert h["quiet_s"] >= 0.03 and h["stall_s"] < h["quiet_s"]       # chunks arrive, all zeros
    feed(rec, 1)                                                      # the faintest sound there is
    assert rec.health()["quiet_s"] < 0.03


def test_a_quiet_voice_is_not_silence_whatever_its_level(sd):
    """The check is for exact zeros, not for loudness: 3.15.1 lifts quiet
    speech, and a mic at the floor of its noise is still alive."""
    rec = recording(sd)
    for value in (1, 2, 3):
        time.sleep(0.01)
        feed(rec, value)
        assert rec.health()["quiet_s"] < 0.01


def test_no_callbacks_at_all_shows_as_a_stall_and_as_quiet(sd):
    rec = recording(sd)
    now = time.monotonic() + 7.0
    h = rec.health(now)
    assert h["stall_s"] >= 6.9 and h["quiet_s"] >= 6.9 and h["stream_ok"] is True


def test_the_time_before_the_take_is_not_counted(sd):
    rec = AudioRecorder()
    rec.start_monitoring()
    rec._last_signal_at = rec._last_chunk_at = time.monotonic() - 100.0     # a long quiet before
    rec._last_press_time = time.time()
    rec.start()
    h = rec.health()
    assert h["quiet_s"] < 1.0 and h["stall_s"] < 1.0


def test_a_cue_blanked_out_of_the_audio_does_not_look_like_a_dead_microphone(sd):
    rec = recording(sd)
    now = [1000.0]
    rec._clock = lambda: now[0]
    rec._deaf = ((999.0, 1001.0),)           # a sound cue is playing: this chunk is blanked
    time.sleep(0.01)
    feed(rec, 500)
    assert rec.health()["quiet_s"] < 0.01
    # ...and what was recorded is the blank.
    assert not rec._buffer[-1].any()


def test_a_stream_that_is_not_running_is_reported(sd):
    rec = recording(sd)
    assert rec.health()["stream_ok"] is True
    rec._stream.active = False
    assert rec.health()["stream_ok"] is False
    rec._stream = None
    assert rec.health()["stream_ok"] is False


def test_health_never_raises_on_a_stream_that_is_gone(sd):
    rec = recording(sd)

    class Gone:
        @property
        def active(self):
            raise RuntimeError("PortAudio stream closed")
    rec._stream = Gone()
    assert rec.health()["stream_ok"] is False


def test_a_stream_that_sends_sound_after_a_long_run_of_zeros_is_a_noise_gate(sd):
    """Exact zeros between words are what a noise-gated microphone sends. Once
    it is seen to send sound after half a second of them, a silence on it is a
    pause."""
    rec = recording(sd)
    assert rec.health()["gated"] is False
    feed(rec, 0)
    assert rec._zero_since is not None
    rec._zero_since = time.monotonic() - 0.6          # half a second of zeros has gone by
    feed(rec, 5)
    assert rec.health()["gated"] is True and rec._zero_since is None


def test_a_blink_of_zeros_is_not_a_gate(sd):
    rec = recording(sd)
    feed(rec, 0)
    rec._zero_since = time.monotonic() - 0.1
    feed(rec, 5)
    assert rec.health()["gated"] is False


def test_a_stream_that_is_dead_from_the_start_is_never_taken_for_a_gate(sd):
    rec = recording(sd)
    feed(rec, 0, 50)
    rec._zero_since = time.monotonic() - 30.0
    feed(rec, 0, 5)
    assert rec.health()["gated"] is False


def test_a_new_stream_is_judged_afresh(sd):
    rec = recording(sd)
    feed(rec, 0)
    rec._zero_since = time.monotonic() - 1.0
    feed(rec, 5)
    assert rec.health()["gated"] is True
    rec.reopen_stream()
    h = rec.health()
    assert h["gated"] is False and rec._zero_since is None


def test_a_cue_blanked_out_of_the_audio_is_not_a_run_of_zeros(sd):
    rec = recording(sd)
    now = [1000.0]
    rec._clock = lambda: now[0]
    rec._deaf = ((999.0, 1001.0),)
    feed(rec, 500)
    assert rec._zero_since is None                      # the device sent sound; the cue's blank is ours


def test_the_device_name_is_known_once_a_stream_is_open(sd):
    rec = recording(sd)
    assert rec.opened_device() == "MacBook Pro Microphone" == rec.health()["device"]


# ── choosing the microphone when a stream is made ────────────────────────

def opened_on(sd):
    return sd.streams[-1].kw["device"]


def test_with_nothing_chosen_the_default_is_used(sd):
    AudioRecorder().start_monitoring()
    assert opened_on(sd) is None


def test_the_bluetooth_avoidance_is_kept_when_the_default_is_a_headset(sd):
    sd.default_index = 1                                     # AirPods Pro is the default input
    rec = AudioRecorder(device_resolver=lambda: None)
    rec.start_monitoring()
    assert opened_on(sd) == 0                                # the built-in microphone instead
    assert rec.opened_device() == "MacBook Pro Microphone"


def test_the_resolver_is_asked_after_portaudio_is_started_again(sd):
    seen = []

    def resolver():
        seen.append(list(sd.log))
        return 2
    rec = AudioRecorder(device_resolver=resolver)
    rec.start_monitoring()
    assert seen == [["terminate", "initialize"]]
    assert opened_on(sd) == 2 and rec.opened_device() == "Yeti"


def test_a_ranked_choice_wins_over_the_bluetooth_avoidance_as_a_pick_always_has(sd):
    sd.default_index = 1
    rec = AudioRecorder(device_resolver=lambda: 1)           # ranked: the AirPods, on purpose
    rec.start_monitoring()
    assert opened_on(sd) == 1


def test_an_index_pinned_by_the_caller_outranks_the_resolver(sd):
    rec = AudioRecorder(device_index=2, device_resolver=lambda: 0)      # the setup practice
    rec.start_monitoring()
    assert opened_on(sd) == 2


def test_a_resolver_that_fails_means_follow_the_system(sd):
    def broken():
        raise RuntimeError("settings unreadable")
    rec = AudioRecorder(device_resolver=broken)
    rec.start_monitoring()
    assert opened_on(sd) is None


def test_a_resolved_index_that_is_not_an_input_falls_back_to_the_default(sd):
    sd.devices.append({"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0})
    rec = AudioRecorder(device_resolver=lambda: 3)
    rec.start_monitoring()
    assert opened_on(sd) is None


# ── a change of preference is picked up by the next start ────────────────

def test_a_preference_change_makes_the_next_start_open_a_new_stream(sd):
    sd.live_on_start = True
    picks = iter([0, 2])
    rec = AudioRecorder(device_resolver=lambda: next(picks))
    rec.start_monitoring()
    rec._last_press_time = time.time()
    first = rec._stream
    rec.start()                                             # warm: the same stream
    assert rec._stream is first
    rec.is_recording = False
    rec.note_preference_changed()
    assert rec.input_level()["current"] is False            # the meter says it is not the current one
    rec._last_press_time = time.time()
    rec.start()
    assert rec._stream is not first and opened_on(sd) == 2
    assert first.events == ["start", "stop", "close"]       # the old one was torn down in order
    assert rec.input_level()["current"] is True


def test_set_device_is_picked_up_by_the_next_start_too(sd):
    sd.live_on_start = True
    rec = AudioRecorder()
    rec.start_monitoring()
    first = rec._stream
    rec.set_device(2)
    rec._last_press_time = time.time()
    rec.start()
    assert rec._stream is not first and opened_on(sd) == 2


# ── opening the stream again in the middle of a take ─────────────────────

def test_a_reopen_keeps_the_take_and_the_old_stream_is_torn_down_in_order(sd):
    rec = recording(sd)
    feed(rec, 300, 4)
    kept = len(rec._buffer)
    old = rec._stream
    assert rec.reopen_stream() is True
    assert old.events == ["start", "stop", "close"]
    assert rec._stream is not old and rec._stream.active
    assert rec.is_recording and len(rec._buffer) == kept
    assert rec._callback_active is True
    assert rec._stream.kw["callback"] == rec._callback_bound       # the one stable bound method
    # And the new stream's audio goes into the same take.
    feed(rec, 400, 2)
    assert len(rec._buffer) == kept + 2


def test_a_reopen_starts_portaudio_again_inside_the_lock_like_any_new_stream(sd):
    rec = recording(sd)
    sd.log.clear()
    rec.reopen_stream()
    assert sd.log == ["stop", "close", "terminate", "initialize", "start"]


def test_a_reopen_asks_which_microphone_again(sd):
    picks = iter([0, 2])
    rec = AudioRecorder(device_resolver=lambda: next(picks))
    rec.start_monitoring()
    rec._last_press_time = time.time()
    rec.start()
    rec.reopen_stream()
    assert opened_on(sd) == 2 and rec.opened_device() == "Yeti"


def test_nothing_is_reopened_when_there_is_no_take(sd):
    rec = AudioRecorder()
    rec.start_monitoring()
    stream = rec._stream
    assert rec.reopen_stream() is False
    assert rec._stream is stream and stream.events == ["start"]


def test_a_reopen_that_cannot_make_a_stream_keeps_the_take_and_says_so(sd):
    rec = recording(sd)
    feed(rec, 300, 3)
    sd.fail_start = True
    assert rec.reopen_stream() is False
    assert rec._reopening is False and rec.is_recording
    # The take can still be ended with what it has.
    wav = rec.stop()
    assert len(wav) > 44


def test_stop_during_a_reopen_waits_its_turn_and_returns_what_there_is(sd):
    rec = recording(sd)
    feed(rec, 300, 5)
    gate = threading.Event()
    sd.next_gate = gate                                      # the new stream's start() waits
    done = threading.Event()
    result = {}

    def reopen():
        result["reopened"] = rec.reopen_stream()
        done.set()
    t = threading.Thread(target=reopen, daemon=True)
    t.start()
    deadline = time.time() + 5
    while not rec._reopening and time.time() < deadline:
        time.sleep(0.005)
    assert rec._reopening
    assert rec.health()["reopening"] is True
    got = {}

    def stopper():
        got["wav"] = rec.stop()
    s = threading.Thread(target=stopper, daemon=True)
    s.start()
    time.sleep(0.1)
    assert s.is_alive(), "stop() must wait for the lock the reopen holds"
    gate.set()
    t.join(5)
    s.join(5)
    assert done.is_set() and not s.is_alive()
    assert result["reopened"] is True
    assert len(got["wav"]) > 44 and rec.is_recording is False
    assert rec.stop() == b""                  # a second stop is harmless


def test_two_reopens_at_once_do_not_overlap(sd):
    rec = recording(sd)
    rec._reopening = True
    assert rec.reopen_stream() is False         # one is already running
    rec._reopening = False


# ── PortAudio's list, taken under the stream lock ────────────────────────

def test_the_snapshot_reads_portaudio_under_the_lock(sd):
    infos, hostapis, default_index, primary = audio_mod.snapshot_devices()
    assert [d["name"] for d in infos][0] == "MacBook Pro Microphone"
    assert hostapis == [{"name": "Core Audio"}] and default_index == 0 and primary == 0


def test_the_snapshot_does_not_wait_forever_for_a_stream_being_made(sd):
    audio_mod._STREAM_LOCK.acquire()
    try:
        t0 = time.monotonic()
        assert audio_mod.snapshot_devices(timeout=0.2) is None
        assert time.monotonic() - t0 < 2.0
    finally:
        audio_mod._STREAM_LOCK.release()


def test_the_snapshot_gives_nothing_when_portaudio_fails(sd, monkeypatch):
    monkeypatch.setattr(sd, "query_devices", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert audio_mod.snapshot_devices() is None
    assert audio_mod._STREAM_LOCK.acquire(timeout=1.0), "the lock must be released after a failure"
    audio_mod._STREAM_LOCK.release()
