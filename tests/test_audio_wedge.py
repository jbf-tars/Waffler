"""The recorder when CoreAudio stops answering (src/audio.py, src/audio_guard.py).

What happened, on a Mac whose audio system deadlocked: every press of the hotkey
after half a minute of quiet took the always-open stream down and made a new
one, with the recorder's locks held, and a call into CoreAudio that never came
back froze the press (no pill), the stop of the take, and then everything that
touched the microphone. Two things are tested here:

* a healthy stream is no longer taken down on a press: a rebuild needs a sign
  that the devices changed (a flag), never just the time since the last press;
* when a call into PortAudio or CoreAudio does not answer, the caller gets
  control back within a bound, the locks are let go, the wedge is flagged and
  announced once, and a stop still hands over what was recorded.

``audio.sd`` is a fake whose calls can be made to wait on an event nothing ever
sets, which is what the deadlock looks like from Python. No microphone is
opened and no real CoreAudio call is made. The bounds are shortened for speed.
"""
import io
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import audio as audio_mod  # noqa: E402
import audio_guard  # noqa: E402
from audio import AudioRecorder  # noqa: E402
from test_mic_capture_health import FakeSD, FakeStream, feed  # noqa: E402

BOUND = 0.25            # every bound, shortened for the tests


class HangStream(FakeStream):
    """A stream whose stop, close and start can wait for ever."""

    def start(self):
        self.owner.maybe_hang("start")
        super().start()

    def stop(self):
        self.owner.calls["stop"] += 1
        self.owner.maybe_hang("stop")
        super().stop()

    def close(self):
        self.owner.calls["close"] += 1
        self.owner.maybe_hang("close")
        super().close()


class WedgeSD(FakeSD):
    """``hang`` names the calls that never return: stop, close, start, create
    (the InputStream itself), terminate, initialize, query."""

    def __init__(self):
        super().__init__()
        self.hang = set()
        self.never = threading.Event()        # nothing sets this during a test
        self.live_on_start = True
        self.calls = {"stop": 0, "close": 0, "terminate": 0, "initialize": 0, "query": 0}

    def maybe_hang(self, op):
        if op in self.hang:
            self.never.wait(30)

    def _terminate(self):
        self.calls["terminate"] += 1
        self.maybe_hang("terminate")
        super()._terminate()

    def _initialize(self):
        self.calls["initialize"] += 1
        self.maybe_hang("initialize")
        super()._initialize()

    def query_devices(self, device=None, kind=None):
        self.calls["query"] += 1
        self.maybe_hang("query")
        return super().query_devices(device, kind)

    def InputStream(self, **kw):
        self.maybe_hang("create")
        s = HangStream(self, **kw)
        if self.next_gate is not None:
            s.start_gate, self.next_gate = self.next_gate, None
        self.streams.append(s)
        return s

    def touched_audio(self):
        return {k: v for k, v in self.calls.items() if v}


@pytest.fixture
def sd(monkeypatch):
    fake = WedgeSD()
    monkeypatch.setattr(audio_mod, "sd", fake)
    monkeypatch.setattr(audio_mod, "_HAL_DRAIN_S", 0)
    monkeypatch.setattr(audio_mod, "_POSTROLL_MS", 0)
    for name in ("_STOP_TIMEOUT_S", "_CLOSE_TIMEOUT_S", "_TERMINATE_TIMEOUT_S",
                 "_INITIALIZE_TIMEOUT_S", "_CREATE_TIMEOUT_S", "_QUERY_TIMEOUT_S"):
        monkeypatch.setattr(audio_mod, name, BOUND)
    monkeypatch.setattr(audio_mod, "_STOP_LOCK_WAIT_S", 1.0)
    monkeypatch.setattr(audio_mod, "_SHUTDOWN_LOCK_WAIT_S", 1.0)
    monkeypatch.setattr(audio_mod, "_SALVAGE_LOCK_WAIT_S", 0.3)
    monkeypatch.setattr(audio_mod, "_recycles_when_idle", lambda: False)    # a Mac, whatever runs the test
    yield fake
    fake.never.set()                           # let the stuck threads finish


def make(sd, **kw):
    """A recorder with a warm stream and some audio in its pre-roll, plus what
    it was told about a wedge."""
    rec = AudioRecorder(**kw)
    rec.told, rec.lines = [], []
    rec.on_wedge = rec.told.append
    rec.log = rec.lines.append
    rec.start_monitoring()
    feed(rec, 300, 10)
    return rec


def wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def within(seconds, fn, *args):
    """``fn(*args)``'s result, and how long it took; the test fails if it took
    longer than ``seconds``."""
    t0 = time.monotonic()
    try:
        out = fn(*args)
    except Exception as e:      # noqa: BLE001 - handed to the test
        out = e
    took = time.monotonic() - t0
    assert took < seconds, f"{getattr(fn, '__name__', fn)} took {took:.2f}s (limit {seconds}s)"
    return out


def locks_free(rec):
    """Neither the recorder's stream lock nor the process-wide one is held."""
    got_stream = rec._stream_lock.acquire(blocking=False)
    got_global = audio_mod._STREAM_LOCK.acquire(blocking=False)
    if got_stream:
        rec._stream_lock.release()
    if got_global:
        audio_mod._STREAM_LOCK.release()
    return got_stream and got_global


def wav_frames(data):
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes()


# ── (a) a healthy stream is left alone ───────────────────────────────────────

def test_a_press_after_a_long_idle_leaves_a_healthy_stream_alone(sd):
    rec = make(sd)
    preroll = list(rec._preroll)
    rec._last_press_time = time.time() - 31          # the old trigger: more than 30 s idle
    sd.log.clear()
    sd.calls.update({k: 0 for k in sd.calls})
    rec.start()
    assert rec.is_recording
    assert sd.log == [], "no stop, close, terminate, initialize or start"
    assert sd.touched_audio() == {}, "nothing was asked of PortAudio"
    assert len(rec._buffer) == len(preroll) and all(a is b for a, b in zip(rec._buffer, preroll)), \
        "the pre-roll was spliced in"


def test_a_system_with_no_live_device_list_still_opens_a_new_stream_after_a_long_idle(sd, monkeypatch):
    """Windows. PortAudio's list of devices, and its idea of the default input, are
    only as new as its last restart, and the device monitor has nothing but that
    list to look at (a Mac's is CoreAudio's own). Without a restart now and then a
    microphone plugged in later is never seen, so there a press after half a
    minute's quiet opens a new stream, as it always did. The deadlock this change
    is about was met on a Mac, in CoreAudio."""
    monkeypatch.setattr(audio_mod, "_recycles_when_idle", lambda: True)
    rec = make(sd)
    rec._last_press_time = time.time() - 29
    sd.log.clear()
    rec.start()
    assert sd.log == [], "29 s is not idle enough"
    rec.stop()
    rec._last_press_time = time.time() - 31
    rec.start()
    assert sd.log == ["stop", "close", "terminate", "initialize", "start"]
    assert any("opening the microphone again" in line and "idle" in line for line in rec.lines)


def test_only_a_system_without_a_live_device_list_recycles_when_idle(monkeypatch):
    monkeypatch.setattr(audio_mod.sys, "platform", "darwin")
    assert audio_mod._recycles_when_idle() is False
    monkeypatch.setattr(audio_mod.sys, "platform", "win32")
    assert audio_mod._recycles_when_idle() is True


def test_a_press_a_day_later_still_does_not_rebuild(sd):
    rec = make(sd)
    for _ in range(3):
        rec._last_press_time = 0.0                   # "idle since 1970"
        sd.log.clear()
        rec.start()
        assert sd.log == []
        rec.stop()


def test_a_stream_that_is_not_running_is_still_opened_cold(sd):
    rec = AudioRecorder()
    rec.start()                                      # nothing was ever opened
    assert sd.log == ["terminate", "initialize", "start"]
    assert rec.is_recording


# ── (b) a flagged change of devices does rebuild, once ───────────────────────

def test_a_press_after_a_flagged_device_change_rebuilds_exactly_once(sd):
    rec = make(sd)
    rec.note_devices_changed()
    sd.log.clear()
    rec.start()
    assert sd.log == ["stop", "close", "terminate", "initialize", "start"]
    assert rec.is_recording
    rec.stop()
    sd.log.clear()
    rec.start()                                      # the next press: the flag was used up
    assert sd.log == []


def test_a_change_flagged_during_a_take_is_acted_on_at_the_next_idle_moment(sd):
    rec = make(sd)
    rec.start()
    rec.note_devices_changed()
    assert rec.rebuild_if_idle() is False            # a take is under way: nothing is torn down
    sd.log.clear()
    rec.stop()
    assert rec.rebuild_if_idle() is True             # the retry the device monitor makes
    assert sd.log == ["stop", "close", "terminate", "initialize", "start"]
    sd.log.clear()
    rec.start()
    assert sd.log == [], "and the press after it finds a stream that is current"


def test_a_change_flagged_while_the_stream_was_being_made_still_counts(sd):
    """The flag is a version, like the preference's: a change that arrives while
    a stream is being opened is newer than that stream."""
    rec = make(sd)
    rec.note_devices_changed()
    rec.rebuild_if_idle()
    sd.log.clear()
    rec.start()
    assert sd.log == []
    rec.stop()
    rec.note_devices_changed()
    rec.start()
    assert sd.log.count("terminate") == 1


def test_the_reason_for_a_rebuild_is_logged(sd):
    rec = make(sd)
    rec.note_devices_changed()
    rec.start()
    assert any("opening the microphone again" in line and "devices changed" in line
               for line in rec.lines)


# ── (c) a stop that never returns, during a rebuild ──────────────────────────

def test_a_blocked_stream_stop_gives_control_back_within_the_bound(sd):
    rec = make(sd)
    rec.note_devices_changed()
    sd.hang.add("stop")
    result = within(2.0, rec.start)
    assert isinstance(result, audio_mod.AudioWedged), "the stream could not be rebuilt, and start says so"
    assert rec._wedged is True
    assert rec.wedge_info()[0] == "stream.stop"


def test_the_locks_are_let_go_when_the_stop_does_not_return(sd):
    rec = make(sd)
    rec.note_devices_changed()
    sd.hang.add("stop")
    within(2.0, rec.start)
    assert locks_free(rec)


def test_a_stop_and_a_shutdown_alongside_a_stuck_rebuild_return_promptly(sd):
    rec = make(sd)
    rec.note_devices_changed()
    sd.hang.add("stop")
    rebuild = threading.Thread(target=lambda: _quiet(rec.start), daemon=True)
    rebuild.start()
    assert wait_for(lambda: sd.calls["stop"] == 1)       # the rebuild is inside the stuck call
    out = {}
    stopper = threading.Thread(target=lambda: out.update(wav=rec.stop()), daemon=True)
    shutter = threading.Thread(target=rec.shutdown, daemon=True)
    t0 = time.monotonic()
    stopper.start()
    shutter.start()
    stopper.join(3)
    shutter.join(3)
    assert not stopper.is_alive() and not shutter.is_alive()
    assert time.monotonic() - t0 < 2.5
    rebuild.join(3)
    assert not rebuild.is_alive()


def _quiet(fn, *a):
    try:
        return fn(*a)
    except Exception as e:      # noqa: BLE001 - the tests look at the recorder, not the error
        return e


def test_the_wedge_is_announced_once_and_logged_once(sd):
    rec = make(sd)
    rec.note_devices_changed()
    sd.hang.add("stop")
    within(2.0, _quiet, rec.start)
    assert wait_for(lambda: rec.told == ["stream.stop"])
    for _ in range(3):                                   # later calls hit the same wall
        within(2.0, _quiet, rec.start)
        within(2.0, _quiet, rec.rebuild_if_idle)
    time.sleep(0.1)
    assert rec.told == ["stream.stop"], "told once"
    said = [line for line in rec.lines if "CoreAudio did not answer" in line]
    assert said == [f"[audio] CoreAudio did not answer within {BOUND:g}s in stream.stop"]


def test_the_stream_whose_stop_did_not_return_is_kept_alive(sd):
    """CoreAudio may still call into it: it must not be freed."""
    rec = make(sd)
    stream = rec._stream
    rec.note_devices_changed()
    sd.hang.add("stop")
    within(2.0, _quiet, rec.start)
    assert stream in audio_guard.kept_alive()
    assert "close" not in sd.log, "and it is not closed under the stuck stop"


def test_a_wedged_recorder_does_not_start_more_threads_for_each_call(sd):
    rec = make(sd)
    rec.note_devices_changed()
    sd.hang.add("stop")
    within(2.0, _quiet, rec.start)
    before = threading.active_count()
    for _ in range(20):
        within(1.0, _quiet, rec.start)
    assert threading.active_count() <= before + 1
    assert sd.calls["stop"] == 1, "the stuck call was not made again"


# ── (d) a PortAudio restart that never returns ───────────────────────────────

def test_a_blocked_terminate_gives_control_back_and_flags_the_wedge(sd):
    rec = AudioRecorder()
    rec.told, rec.lines = [], []
    rec.on_wedge, rec.log = rec.told.append, rec.lines.append
    sd.hang.add("terminate")
    result = within(2.0, _quiet, rec.start)
    assert isinstance(result, audio_mod.AudioWedged)
    assert rec._wedged and rec.wedge_info()[0] == "sd._terminate"
    assert locks_free(rec)
    assert wait_for(lambda: rec.told == ["sd._terminate"])
    assert rec._stream is None and rec._callback_active is False
    assert not rec.is_recording


def test_a_blocked_initialize_is_treated_the_same(sd):
    rec = AudioRecorder()
    rec.told = []
    rec.on_wedge = rec.told.append
    sd.hang.add("initialize")
    result = within(2.0, _quiet, rec.start_monitoring)
    assert isinstance(result, audio_mod.AudioWedged)
    assert wait_for(lambda: rec.told == ["sd._initialize"])
    assert locks_free(rec)


def test_a_stream_that_never_opens_is_treated_the_same(sd):
    rec = AudioRecorder()
    rec.told = []
    rec.on_wedge = rec.told.append
    sd.hang.add("create")
    result = within(2.0, _quiet, rec.start)
    assert isinstance(result, audio_mod.AudioWedged)
    assert wait_for(lambda: rec.told == ["create the stream"])
    assert rec._stream is None and rec._callback_active is False


def test_a_stream_that_never_starts_is_treated_the_same(sd):
    rec = AudioRecorder()
    rec.told = []
    rec.on_wedge = rec.told.append
    sd.hang.add("start")
    result = within(2.0, _quiet, rec.start)
    assert isinstance(result, audio_mod.AudioWedged)
    assert wait_for(lambda: rec.told == ["create the stream"])


def test_a_close_that_never_returns_is_treated_the_same(sd):
    rec = make(sd)
    rec.note_devices_changed()
    sd.hang.add("close")
    result = within(2.0, _quiet, rec.start)
    assert isinstance(result, audio_mod.AudioWedged)
    assert rec.wedge_info()[0] == "stream.close"
    assert locks_free(rec)


def test_a_device_query_that_never_returns_gives_up_and_the_stream_is_not_made(sd):
    rec = AudioRecorder()
    sd.hang.add("query")
    result = within(2.0, _quiet, rec.start)
    assert isinstance(result, audio_mod.AudioWedged)
    assert rec.wedge_info()[0] == "choose the microphone"
    assert locks_free(rec)


def test_snapshot_devices_gives_up_within_the_bound_and_says_not_now(sd):
    sd.hang.add("query")
    assert within(2.0, audio_mod.snapshot_devices) is None
    assert audio_guard.wedged_op() == "query_devices"
    assert audio_mod._STREAM_LOCK.acquire(blocking=False), "the lock is not held by a stuck query"
    audio_mod._STREAM_LOCK.release()
    sd.calls["query"] = 0
    assert within(0.5, audio_mod.snapshot_devices) is None, "the next poll does not wait or ask again"
    assert sd.calls["query"] == 0


def test_a_slow_but_answering_call_is_not_a_wedge(sd, monkeypatch):
    rec = make(sd)
    monkeypatch.setattr(audio_mod, "_STOP_TIMEOUT_S", 2.0)
    rec.note_devices_changed()
    real = HangStream.stop

    def slow(self):
        time.sleep(0.1)
        real(self)
    monkeypatch.setattr(HangStream, "stop", slow)
    rec.start()
    assert rec.is_recording and rec._wedged is False and rec.told == []


# ── (e) a stop with the lock held by something stuck ─────────────────────────

def hold_the_stream_lock(rec):
    """A start that never returns: another thread takes the recorder's stream
    lock and keeps it."""
    held, release = threading.Event(), threading.Event()

    def stuck():
        with rec._stream_lock:
            held.set()
            release.wait(30)
    t = threading.Thread(target=stuck, daemon=True, name="StuckStart")
    t.start()
    assert held.wait(2)
    return release


def test_a_stop_with_the_lock_held_by_a_stuck_start_still_returns_the_audio(sd):
    rec = make(sd)
    rec._take_started_at = time.monotonic()
    rec.is_recording = True
    rec._buffer = list(rec._preroll)
    feed(rec, 500, 20)
    frames = sum(len(c) for c in rec._buffer)
    release = hold_the_stream_lock(rec)
    try:
        data = within(3.0, rec.stop)
    finally:
        release.set()
    assert isinstance(data, bytes) and data[:4] == b"RIFF"
    assert wav_frames(data) == frames, "everything recorded so far, from the buffer alone"
    assert rec._wedged is True
    assert wait_for(lambda: len(rec.told) == 1)
    assert "stop" in rec.told[0] or "stream lock" in rec.told[0]


def test_a_stop_that_waited_out_the_lock_does_not_wait_twice(sd):
    rec = make(sd)
    rec.is_recording = True
    rec._buffer = list(rec._preroll)
    release = hold_the_stream_lock(rec)
    try:
        t0 = time.monotonic()
        rec.stop()
        took = time.monotonic() - t0
    finally:
        release.set()
    assert took < 2.0, f"one wait for the lock (limit 1.0 s here), not two: {took:.2f}s"


def test_a_stop_in_a_healthy_recorder_is_unchanged(sd):
    rec = make(sd)
    rec.start()
    feed(rec, 500, 5)
    data = rec.stop()
    assert wav_frames(data) > 5 * 1024
    assert rec._wedged is False and rec.told == []
    assert not rec.is_recording


# ── the buffer, read without the stream ──────────────────────────────────────

def test_the_buffer_can_be_read_without_the_stream_or_its_lock(sd):
    rec = make(sd)
    rec.start()
    feed(rec, 500, 4)
    release = hold_the_stream_lock(rec)
    try:
        data = within(1.0, rec.salvage_wav)
    finally:
        release.set()
    assert data[:4] == b"RIFF"
    assert rec.is_recording, "reading it does not end the take"
    assert len(rec._buffer) > 0, "or empty it"


def test_a_buffer_that_cannot_be_read_is_reported_as_unreadable(sd):
    rec = make(sd)
    rec.start()
    held, release = threading.Event(), threading.Event()

    def stuck():
        with rec._lock:
            held.set()
            release.wait(30)
    threading.Thread(target=stuck, daemon=True).start()
    assert held.wait(2)
    try:
        assert within(2.0, rec.salvage_wav) is None
    finally:
        release.set()


def test_an_empty_take_reads_as_nothing_recorded_not_as_unreadable(sd):
    rec = AudioRecorder()
    assert rec.salvage_wav() == b""


# ── shutdown ─────────────────────────────────────────────────────────────────

def test_shutdown_returns_within_a_moment_when_the_stop_never_returns(sd):
    rec = make(sd)
    sd.hang.add("stop")
    within(2.0, rec.shutdown)


def test_shutdown_returns_when_the_lock_is_held_by_something_stuck(sd):
    rec = make(sd)
    release = hold_the_stream_lock(rec)
    try:
        within(3.0, rec.shutdown)
    finally:
        release.set()


# ── the other places that ask PortAudio ──────────────────────────────────────

def test_the_press_path_of_a_warm_stream_never_queries_devices(sd):
    rec = make(sd)
    sd.calls["query"] = 0
    rec.start()
    rec.stop()
    rec.start()
    assert sd.calls["query"] == 0


# ── is it still wedged, and clearing a wedge that was only slow ──────────────

def test_a_recorder_with_a_stuck_call_is_still_stuck_until_the_call_returns(sd):
    rec = make(sd)
    assert rec.is_stuck() is False
    rec.note_devices_changed()
    sd.hang.add("stop")
    within(2.0, _quiet, rec.start)
    assert rec.is_stuck() is True
    sd.never.set()                                 # CoreAudio answers after all
    assert wait_for(lambda: rec.is_stuck() is False)


def test_a_recorder_stuck_on_its_lock_is_still_stuck_until_the_lock_is_let_go(sd):
    rec = make(sd)
    rec.is_recording = True
    release = hold_the_stream_lock(rec)
    within(3.0, rec.stop)
    assert rec._wedged is True and rec.is_stuck() is True
    release.set()
    assert wait_for(lambda: rec.is_stuck() is False)


def test_a_healthy_recorder_is_not_stuck(sd):
    rec = make(sd)
    rec.start()
    rec.stop()
    assert rec.is_stuck() is False


def test_clearing_a_wedge_lets_the_next_one_be_logged_and_told_again(sd):
    rec = make(sd)
    rec.note_devices_changed()
    sd.hang.add("stop")
    within(2.0, _quiet, rec.start)
    assert wait_for(lambda: len(rec.told) == 1)
    sd.never.set()                                 # it was only slow
    assert wait_for(lambda: audio_guard.wedged_op() is None)
    rec.clear_wedge()
    assert rec._wedged is False and rec.wedge_info() is None
    sd.never.clear()
    rec.start()                                    # works again
    assert rec.is_recording
    rec.stop()
    rec.note_devices_changed()
    within(2.0, _quiet, rec.start)
    assert wait_for(lambda: len(rec.told) == 2), "a second wedge is a new one"
    assert len([line for line in rec.lines if "did not answer" in line]) == 2


# ── a stream that opens too late is not left running ─────────────────────────

def test_a_stream_that_opens_after_the_recorder_gave_up_is_stopped_and_closed(sd):
    """The call was only slow. Nobody owns the stream it opens; left running it
    would feed the same callback a second copy of the audio."""
    rec = AudioRecorder()
    sd.hang.add("create")
    assert isinstance(within(2.0, _quiet, rec.start), audio_mod.AudioWedged)
    sd.hang.discard("create")
    sd.never.set()                                  # the slow call finishes now
    assert wait_for(lambda: len(sd.streams) == 1)
    late = sd.streams[0]
    assert wait_for(lambda: late.events == ["start", "stop", "close"]), late.events
    assert late.active is False
    assert rec._stream is None and rec._callback_active is False


def test_a_stream_that_opens_in_time_is_used(sd):
    rec = AudioRecorder()
    rec.start()
    assert len(sd.streams) == 1 and sd.streams[0].events == ["start"]
    assert rec._stream is sd.streams[0] and rec._armed is sd.streams[0]
