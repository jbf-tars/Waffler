"""Windows: the stream is opened again while idle, never at the press.

PortAudio's list of devices on Windows is only as new as its last restart, and
there is no other list to read (src/mic_devices.py ``live_inputs`` answers None
there), so a microphone plugged in later is only found when the stream is
opened again. That used to happen at the press after 30 s of quiet: the stream
was taken down, the half second of pre-roll that holds the first words was
thrown away, and the take waited for a new stream. On the owner's Windows PC it
happened on most dictations. Now the device monitor's poll opens the stream
again in the background once it has been idle that long, and the press finds a
warm stream with its pre-roll. A Mac is told of device changes by CoreAudio and
does none of this.

``audio.sd`` is a fake (tests/test_audio_wedge.py); no microphone is opened.
The recorder's clock is a test's, so "an hour later" takes no time.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import audio as audio_mod  # noqa: E402
import audio_device_monitor as adm  # noqa: E402
from audio import AudioRecorder  # noqa: E402
from mic_devices import Device  # noqa: E402
from test_audio_wedge import WedgeSD  # noqa: E402
from test_mic_capture_health import feed  # noqa: E402

REOPEN = ["stop", "close", "terminate", "initialize", "start"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def sd(monkeypatch):
    fake = WedgeSD()
    monkeypatch.setattr(audio_mod, "sd", fake)
    monkeypatch.setattr(audio_mod, "_HAL_DRAIN_S", 0)
    monkeypatch.setattr(audio_mod, "_POSTROLL_MS", 0)
    monkeypatch.setattr(audio_mod, "_recycles_when_idle", lambda: True)     # Windows
    yield fake
    fake.never.set()


def make(sd, clock):
    """A recorder on ``clock`` with a warm stream and a full pre-roll."""
    rec = AudioRecorder()
    rec._clock = clock
    rec.lines = []
    rec.log = rec.lines.append
    rec.start_monitoring()
    feed(rec, 300, 10)
    sd.log.clear()
    return rec


def recycled(rec):
    return [line for line in rec.lines if "while idle" in line]


# ── the press ────────────────────────────────────────────────────────────────

def test_a_press_after_a_long_idle_keeps_the_preroll_and_opens_nothing(sd):
    clock = Clock()
    rec = make(sd, clock)
    preroll = list(rec._preroll)
    clock.t += 3600                                    # an hour of quiet
    rec.start()
    assert rec.is_recording
    assert sd.log == [], "the stream was not taken down at the press"
    assert len(rec._buffer) == len(preroll) and all(a is b for a, b in zip(rec._buffer, preroll)), \
        "the pre-roll, with the first words, was spliced in"
    assert not any("opening the microphone again" in line for line in rec.lines)


def test_the_press_still_opens_a_new_stream_for_a_flagged_device_change(sd):
    clock = Clock()
    rec = make(sd, clock)
    rec.note_devices_changed()
    rec.start()
    assert sd.log == REOPEN
    assert any("the audio devices changed" in line for line in rec.lines)


def test_the_first_press_logs_no_idle_time(sd):
    rec = AudioRecorder()
    rec.lines = []
    rec.log = rec.lines.append
    rec.start_monitoring()
    rec.start()
    assert not any("idle for" in line for line in rec.lines)


def test_idle_is_counted_from_when_the_recorder_began_not_from_1970(sd):
    clock = Clock()
    rec = make(sd, clock)              # no press yet
    clock.t += 31
    assert rec.recycle_when_idle()
    line, = recycled(rec)
    assert "31s" in line, line


# ── the background recycle ───────────────────────────────────────────────────

def test_an_idle_stream_is_opened_again_once_it_has_been_idle_long_enough(sd):
    clock = Clock()
    rec = make(sd, clock)
    clock.t += 29
    assert rec.recycle_when_idle() is False
    assert sd.log == [] and recycled(rec) == []
    clock.t += 2
    assert rec.recycle_when_idle() is True
    assert sd.log == REOPEN
    assert len(recycled(rec)) == 1


def test_one_line_per_recycle_not_per_poll(sd):
    clock = Clock()
    rec = make(sd, clock)
    clock.t += 31
    rec.recycle_when_idle()
    for _ in range(14):                # the next 28 s of polls
        clock.t += 2
        assert rec.recycle_when_idle() is False
    assert sd.log == REOPEN and len(recycled(rec)) == 1
    clock.t += 4                       # 32 s after the last one
    assert rec.recycle_when_idle() is True
    assert len(recycled(rec)) == 2


def test_the_press_after_a_recycle_finds_a_warm_stream_and_its_preroll(sd):
    clock = Clock()
    rec = make(sd, clock)
    clock.t += 31
    rec.recycle_when_idle()
    feed(rec, 400, 8)
    preroll = list(rec._preroll)
    sd.log.clear()
    rec.start()
    assert sd.log == []
    assert len(rec._buffer) == len(preroll) and all(a is b for a, b in zip(rec._buffer, preroll))


def test_never_during_a_take(sd):
    clock = Clock()
    rec = make(sd, clock)
    rec.start()
    sd.log.clear()
    clock.t += 120                     # a two-minute take
    assert rec.recycle_when_idle() is False
    assert sd.log == [] and rec.is_recording
    rec.stop()
    clock.t += 10                      # idle is counted from the end of the take
    assert rec.recycle_when_idle() is False
    clock.t += 25
    assert rec.recycle_when_idle() is True


def test_never_while_a_start_is_warming_up_or_the_stream_is_being_reopened(sd):
    clock = Clock()
    rec = make(sd, clock)
    clock.t += 60
    rec._starting = 1
    assert rec.recycle_when_idle() is False
    rec._starting = 0
    rec._reopening = True
    assert rec.recycle_when_idle() is False
    assert sd.log == []


def test_never_while_the_app_is_busy_a_hold_a_paste_or_a_take_being_sent(sd):
    clock = Clock()
    rec = make(sd, clock)
    clock.t += 60
    assert rec.recycle_when_idle(busy=lambda: True) is False
    assert sd.log == [] and recycled(rec) == []
    assert rec.recycle_when_idle(busy=lambda: False) is True


def test_a_busy_check_that_fails_counts_as_busy(sd):
    clock = Clock()
    rec = make(sd, clock)
    clock.t += 60

    def boom():
        raise RuntimeError("pipeline")
    assert rec.recycle_when_idle(busy=boom) is False
    assert sd.log == []


def test_no_stream_is_opened_where_there_was_none(sd):
    """Nothing open (the pipeline is still starting, or a dead stream was
    dropped for the next press to replace): the recycle opens nothing."""
    clock = Clock()
    rec = AudioRecorder()
    rec._clock = clock
    clock.t += 60
    assert rec.recycle_when_idle() is False
    assert sd.log == []


def test_a_wedged_recorder_is_left_alone(sd):
    clock = Clock()
    rec = make(sd, clock)
    rec._wedged = True
    clock.t += 60
    assert rec.recycle_when_idle() is False
    assert sd.log == []


def test_a_recycle_that_fails_is_logged_once_and_not_tried_on_every_poll(sd):
    clock = Clock()
    rec = make(sd, clock)
    sd.fail_start = True
    clock.t += 31
    assert rec.recycle_when_idle() is False
    failed = [line for line in rec.lines if "could not open the microphone again" in line]
    assert len(failed) == 1
    sd.log.clear()
    clock.t += 2
    assert rec.recycle_when_idle() is False
    assert sd.log == []
    assert len([line for line in rec.lines if "could not open the microphone again" in line]) == 1


def test_a_recycle_also_clears_a_flagged_device_change(sd):
    """Windows waking from sleep flags the stream stale; a recycle while idle
    is a new stream, so the press after it need not open another."""
    clock = Clock()
    rec = make(sd, clock)
    rec.note_devices_changed()
    clock.t += 31
    assert rec.recycle_when_idle()
    sd.log.clear()
    rec.start()
    assert sd.log == []


# ── a Mac ────────────────────────────────────────────────────────────────────

def test_a_mac_never_recycles_while_idle(sd, monkeypatch):
    monkeypatch.setattr(audio_mod, "_recycles_when_idle", lambda: False)
    clock = Clock()
    rec = make(sd, clock)
    clock.t += 86400
    assert rec.recycle_when_idle() is False
    rec.start()
    assert sd.log == [] and rec.lines == []


# ── the poll that drives it ──────────────────────────────────────────────────

def test_the_monitor_calls_its_tick_hook_on_every_poll_before_reading_the_list():
    order = []
    m = adm.AudioDeviceMonitor(on_change=lambda *a: None, log_fn=lambda line: None,
                               reader=lambda: order.append("read") or [Device("Yeti", is_default=True)])
    m.on_tick = lambda: order.append("tick")
    m._tick()
    m._tick()
    assert order == ["tick", "read", "tick", "read"]


def test_a_tick_hook_that_raises_does_not_stop_the_poll():
    log, seen = [], []
    m = adm.AudioDeviceMonitor(on_change=lambda *a: None, log_fn=log.append, on_devices=seen.append,
                               reader=lambda: [Device("Yeti", is_default=True)])
    m.on_tick = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    m._tick()
    assert len(seen) == 1 and any("on_tick failed" in line for line in log)


def test_the_app_drives_the_recycle_from_the_poll_and_asks_whether_anything_is_busy():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_device_monitor.on_tick = " in src
    start = src.index("def _on_poll_tick")
    body = src[start:src.index("_device_monitor.on_tick = ", start)]
    assert "recycle_when_idle(" in body and "_update_would_interrupt(" in body


def test_what_counts_as_busy_includes_a_hold_and_a_paste():
    """``_update_would_interrupt`` is the busy check the recycle is given."""
    import ast
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_update_would_interrupt")
    ns: dict = {}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    check = ns["_update_would_interrupt"]

    class Busy:
        def __init__(self, value):
            self.value = value

        def busy(self):
            return self.value

        def active(self):
            return False

    class Clip:
        def __init__(self, pasting):
            self.pasting = pasting

        def paste_in_progress(self):
            return self.pasting

    class Pipeline:
        is_recording = False
        _watchdog = Busy(False)
        _transforms = Busy(False)
        _commands = Busy(False)
        clipboard = Clip(False)

    p = Pipeline()
    assert check(p) == ""
    p._transforms = Busy(True)
    assert check(p)
    p._transforms = Busy(False)
    p._commands = Busy(True)
    assert check(p)
    p._commands = Busy(False)
    p.clipboard = Clip(True)
    assert check(p)
    p.clipboard = Clip(False)
    p.is_recording = True
    assert check(p)


def test_the_real_clock_is_used_outside_tests():
    rec = AudioRecorder()
    assert rec._clock is time.monotonic
