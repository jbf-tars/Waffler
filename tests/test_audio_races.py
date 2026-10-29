"""The recorder's stream lifecycle under concurrent callers (audit M1, M2).

Two classes of race, both in the family of the old CoreAudio crashes:

* A microphone event (a ranked USB microphone plugged in or out, a ranking
  saved) re-opens the idle stream. The check "is anything recording?" and the
  re-open were two steps, so a take that had just been let go of, or a
  Command-mode hold, could have its stream torn down and its audio emptied.
* A stream was torn down after the recorder's own lock was let go, so a hotkey
  press could start a new stream (and restart PortAudio, which frees every
  open stream) in the gap, and the late teardown then switched the NEW
  stream's callback off, or stopped a stream PortAudio had already freed.

Nothing here opens a microphone. ``audio.sd`` is a fake, the locks and the
clocks are scripted with events, and no test sleeps to make a race happen.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import audio as audio_mod  # noqa: E402
from audio import AudioRecorder  # noqa: E402
from test_mic_capture_health import FakeSD, FakeStream, block, feed  # noqa: E402

WAIT = 5.0


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSD()
    monkeypatch.setattr(audio_mod, "sd", fake)
    monkeypatch.setattr(audio_mod, "_HAL_DRAIN_S", 0)
    monkeypatch.setattr(audio_mod, "_POSTROLL_MS", 0)
    fake.live_on_start = True            # a stream that delivers audio at once, so a start() need not wait
    return fake


def taking(sd):
    """A recorder mid-take on a live (fake) stream, with a little audio in it."""
    rec = AudioRecorder()
    rec.start_monitoring()
    rec.start()
    assert rec.is_recording
    feed(rec, 300, 4)
    return rec


def in_thread(fn, name):
    out = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as e:      # noqa: BLE001 - handed to the test
            out["error"] = e
    t = threading.Thread(target=run, daemon=True, name=name)
    t.start()
    return t, out


class StopsOnCue(FakeStream):
    """A stream whose stop() says it has begun and then waits to be let go."""

    def __init__(self, owner, **kw):
        super().__init__(owner, **kw)
        self.stop_begun = threading.Event()
        self.stop_gate = threading.Event()
        self.hold_stop = False

    def stop(self):
        if self.hold_stop:
            self.stop_begun.set()
            self.stop_gate.wait(WAIT)
        super().stop()


def with_cued_streams(sd):
    def make(**kw):
        s = StopsOnCue(sd, **kw)
        sd.streams.append(s)
        return s
    sd.InputStream = make


class ScriptedLock:
    """The process-wide stream lock, except that a named thread is made to wait
    before it may ask for it: the moment in the race where that thread has let
    go of the recorder's own lock and not yet taken this one."""

    def __init__(self, name):
        self._real = threading.Lock()
        self.name = name
        self.waiting = threading.Event()
        self.go = threading.Event()

    def _gate(self):
        if threading.current_thread().name == self.name and not self.go.is_set():
            self.waiting.set()
            self.go.wait(WAIT)

    def acquire(self, blocking=True, timeout=-1):
        self._gate()
        return self._real.acquire(blocking, timeout)

    def release(self):
        self._real.release()

    def __enter__(self):
        self._gate()
        self._real.acquire()
        return self

    def __exit__(self, *exc):
        self._real.release()


# ── M1: the idle re-open never takes a take with it ──────────────────────────

def test_an_idle_rebuild_makes_a_fresh_stream(sd):
    rec = AudioRecorder()
    rec.start_monitoring()
    old = rec._stream
    assert rec.rebuild_if_idle() is True
    assert rec._stream is not old and rec._stream.active and rec._callback_active is True
    assert old.events == ["start", "stop", "close"]


def test_a_rebuild_leaves_a_take_alone(sd):
    rec = taking(sd)
    stream, kept = rec._stream, len(rec._buffer)
    assert rec.rebuild_if_idle() is False
    assert rec._stream is stream and stream.events == ["start"] and len(sd.streams) == 1
    assert rec.is_recording and len(rec._buffer) == kept
    assert len(rec.stop()) > 44


def test_a_rebuild_in_the_post_roll_of_a_stop_does_not_lose_the_take(sd, monkeypatch):
    """The key is up and stop() is still waiting out the post-roll: the pipeline
    already says it is not recording, but the recorder is, and its audio is
    only copied when stop() ends the wait."""
    rec = taking(sd)
    monkeypatch.setattr(audio_mod, "_POSTROLL_MS", 250)
    in_roll, leave_roll = threading.Event(), threading.Event()
    real_sleep = time.sleep

    class Clock:
        def __getattr__(self, name):
            return getattr(time, name)

        @staticmethod
        def sleep(seconds):
            if seconds == 0.25:
                in_roll.set()
                leave_roll.wait(WAIT)
            else:
                real_sleep(seconds)
    monkeypatch.setattr(audio_mod, "time", Clock())

    stopper, got = in_thread(rec.stop, "stop")
    assert in_roll.wait(WAIT)
    assert rec.rebuild_if_idle() is False, "a stop was waiting out its post-roll"
    assert len(sd.streams) == 1
    leave_roll.set()
    stopper.join(WAIT)
    assert len(got["value"]) > 44, "the take must come out of stop()"


def test_a_rebuild_during_a_cold_start_leaves_the_stream_being_warmed_up(sd, monkeypatch):
    sd.live_on_start = False                     # a stream that is still handing back nothing
    rec = AudioRecorder()
    in_warmup, go_on = threading.Event(), threading.Event()
    real_sleep = time.sleep

    class Clock:
        def __getattr__(self, name):
            return getattr(time, name)

        @staticmethod
        def sleep(seconds):
            if seconds == 0.02:                  # the warm-up loop's wait for live audio
                in_warmup.set()
                go_on.wait(WAIT)
            else:
                real_sleep(seconds)
    monkeypatch.setattr(audio_mod, "time", Clock())

    starter, _ = in_thread(rec.start, "start")
    assert in_warmup.wait(WAIT)
    assert not rec.is_recording and rec._stream is not None
    assert rec.rebuild_if_idle() is False, "a start was warming its stream up"
    assert len(sd.streams) == 1 and sd.streams[0].events == ["start"]
    feed(rec, 300, 2)                            # live audio arrives, so the warm-up ends
    go_on.set()
    starter.join(WAIT)
    assert rec.is_recording


def test_a_rebuild_is_refused_while_the_stream_is_being_reopened(sd):
    rec = taking(sd)
    rec._reopening = True
    assert rec.rebuild_if_idle() is False


def test_the_idle_stream_opening_never_empties_a_take_in_progress(sd):
    rec = taking(sd)
    kept = len(rec._buffer)
    rec._stream = None                           # the stream has gone: the take has audio all the same
    rec.start_monitoring()
    assert rec.is_recording and len(rec._buffer) >= kept and rec._stream is not None
    assert len(rec.stop()) > 44


def test_opening_the_idle_stream_still_starts_from_an_empty_buffer(sd):
    rec = AudioRecorder()
    rec._buffer = [block(5)]
    rec.start_monitoring()
    assert rec._buffer == [] and not rec.is_recording


# ── M2: a stream is torn down while the recorder's lock is held ──────────────

def test_a_late_teardown_of_an_old_stream_does_not_mute_the_new_one(sd):
    rec = AudioRecorder()
    rec.start_monitoring()
    old = rec._stream
    rec._stream = None                           # detached, as the callers do, and not yet torn down
    rec.start_monitoring()                       # a newer stream is made and armed
    new = rec._stream
    rec._teardown_stream(old)                    # ...and only now is the old one stopped and closed
    assert old.events[-2:] == ["stop", "close"]
    assert rec._callback_active is True and new.active
    rec.is_recording = True
    feed(rec, 300, 1)
    assert len(rec._buffer) == 1, "the new stream's audio must still be recorded"


def test_tearing_down_the_stream_that_is_armed_does_switch_the_callback_off(sd):
    rec = AudioRecorder()
    rec.start_monitoring()
    rec.stop_monitoring()
    assert rec._callback_active is False and rec._stream is None


def test_stop_monitoring_keeps_the_recorders_lock_until_the_stream_is_closed(sd, monkeypatch):
    """A press landing between the detach and the teardown used to restart
    PortAudio, which frees every open stream, before the old one was closed."""
    scripted = ScriptedLock("teardown")
    monkeypatch.setattr(audio_mod, "_STREAM_LOCK", scripted)
    rec = AudioRecorder()
    rec.start_monitoring()
    old = rec._stream
    sd.log.clear()

    stopper, _ = in_thread(rec.stop_monitoring, "teardown")
    assert scripted.waiting.wait(WAIT), "stop_monitoring never reached the stream lock"
    starter, _ = in_thread(lambda: rec.start(), "press")
    time.sleep(0.1)
    assert "terminate" not in sd.log and len(sd.streams) == 1, \
        "a press built a stream (and restarted PortAudio) while the old one was still open"
    scripted.go.set()
    stopper.join(WAIT)
    starter.join(WAIT)
    assert old.events == ["start", "stop", "close"]
    assert sd.log.index("close") < sd.log.index("terminate")
    assert rec._callback_active is True and rec._stream is not None and rec._stream.active


def test_force_rebuild_keeps_the_recorders_lock_until_the_stream_is_closed(sd, monkeypatch):
    scripted = ScriptedLock("teardown")
    monkeypatch.setattr(audio_mod, "_STREAM_LOCK", scripted)
    rec = AudioRecorder()
    rec.start_monitoring()
    old = rec._stream
    sd.log.clear()

    rebuilder, _ = in_thread(rec.force_rebuild, "teardown")
    assert scripted.waiting.wait(WAIT)
    starter, _ = in_thread(lambda: rec.start(), "press")
    time.sleep(0.1)
    assert "terminate" not in sd.log and len(sd.streams) == 1
    scripted.go.set()
    rebuilder.join(WAIT)
    starter.join(WAIT)
    assert sd.log.index("close") < sd.log.index("terminate")
    assert old.events == ["start", "stop", "close"] and rec._callback_active is True


def test_a_stop_of_the_old_stream_that_takes_long_does_not_let_a_second_stream_open(sd):
    with_cued_streams(sd)
    rec = AudioRecorder()
    rec.start_monitoring()
    old = rec._stream
    old.hold_stop = True
    stopper, _ = in_thread(rec.stop_monitoring, "teardown")
    assert old.stop_begun.wait(WAIT)
    starter, _ = in_thread(lambda: rec.start(), "press")
    time.sleep(0.1)
    assert len(sd.streams) == 1, "a new stream was opened while the old one was still being stopped"
    old.stop_gate.set()
    stopper.join(WAIT)
    starter.join(WAIT)
    assert len(sd.streams) == 2 and rec._callback_active is True


def test_the_tear_down_helpers_do_nothing_for_no_stream(sd):
    rec = AudioRecorder()
    rec.stop_monitoring()
    rec.force_rebuild()
    rec._teardown_stream(None)
    assert rec._stream is None and sd.streams == []


def test_audio_survives_a_teardown_that_raises(sd):
    rec = AudioRecorder()
    rec.start_monitoring()

    def boom():
        raise RuntimeError("PortAudio went away")
    rec._stream.stop = boom
    rec.stop_monitoring()
    assert rec._stream is None
    rec.start_monitoring()
    assert rec._stream is not None and rec._callback_active is True


# ── M1 in the app: what counts as "a take" when the microphone changes ───────

from _pipeline_harness import make_pipeline  # noqa: E402


class _Busy:
    def __init__(self, value=False):
        self.value = value

    def busy(self):
        return self.value

    def active(self):
        return self.value


@pytest.fixture
def app(tmp_path):
    return make_pipeline(tmp_path)


def test_nothing_running_is_not_a_take(app):
    assert app._mic.h.recording() is False


@pytest.mark.parametrize("what", ["pipeline", "recorder", "command", "transform", "processing"])
def test_each_kind_of_take_stops_the_microphone_being_rebuilt(app, what):
    """A key-up, a Command or Transform hold and a take still being turned into
    text each hold on to the microphone; only the pipeline's own flag was asked."""
    if what == "pipeline":
        app.is_recording = True
    elif what == "recorder":
        app.audio.is_recording = True            # the pipeline's flag went down at the key-up
    elif what == "command":
        app._commands = _Busy(True)
    elif what == "transform":
        app._transforms = _Busy(True)
    else:
        app._watchdog = _Busy(True)
    assert app._mic.h.recording() is True
    app._mic.preferences_changed()
    time.sleep(0.1)
    assert app.audio.rebuilds == 0, "the idle stream was rebuilt during a take"


def test_a_check_that_cannot_be_made_counts_as_a_take(app):
    class Broken:
        def busy(self):
            raise RuntimeError("no")
    app._commands = Broken()
    assert app._mic.h.recording() is True


def test_when_idle_a_changed_ranking_rebuilds_through_the_recorders_one_step(app):
    app._mic.preferences_changed()
    deadline = time.time() + WAIT
    while app.audio.rebuilds == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert app.audio.rebuilds == 1
