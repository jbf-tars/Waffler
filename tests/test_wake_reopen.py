"""Mac: after a sleep, the microphone is opened again before the next press.

Since 3.16.2 a wake flags the always-open stream stale and the next press
opens a new one. That press is then a cold start: the stream is taken down and
made again while the key is held, and the half second kept before the press is
thrown away. On one Mac, in twelve days of 3.16.3 and 3.16.5, 7 of 80 takes
began that way. Each handed back 0.23 s less audio than the key was held,
where a warm press hands back 0.65 s more (0.9 s of the start of the take
gone), and 3 of the 7 did not end in a paste (one came back with 0.3 s of
audio for a 0.6 s press), against 8 of the other 73.

Now, once the Mac has been awake for a few seconds and its keyboard, mouse or
trackpad has been used since it woke, the stream is opened again between
presses, with the same call a change of the system's input already uses. A
wake nobody is at (Power Nap wakes a sleeping Mac every quarter of an hour:
769 of them in those twelve days) opens nothing. Where the time since the last
input cannot be read, and on Windows, nothing changes: the next press opens
the stream, as before.

``audio.sd`` is a fake (tests/test_audio_wedge.py); no microphone is opened and
the real idle time is never read.
"""
import sys
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
BUILTIN = Device(name="MacBook Pro Microphone", uid="BuiltInMicrophoneDevice",
                 transport="built-in", is_default=True)
SETTLE = adm.AudioDeviceMonitor.WAKE_SETTLE_S


class Wall:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """A monitor on a test's clock. ``last_input`` is when the keyboard or the
    mouse was last used (None: it cannot be read)."""

    def __init__(self, with_handler=True):
        self.wall = Wall()
        self.last_input = self.wall.t
        self.awake, self.asked, self.lines = [], [], []
        self.m = adm.AudioDeviceMonitor(
            on_change=lambda o, n: None, log_fn=self.lines.append, reader=lambda: [BUILTIN],
            wall_clock=self.wall, input_idle_s=self.idle)
        if with_handler:
            self.m.on_awake = lambda: self.awake.append(self.wall.t)
        self.m._tick()

    def idle(self):
        self.asked.append(self.wall.t)
        return None if self.last_input is None else self.wall.t - self.last_input

    def tick(self, after=2.0):
        self.wall.t += after
        self.m._tick()

    def sleep(self, seconds=3 * 3600):
        self.tick(seconds)

    def touch(self):
        self.last_input = self.wall.t


def test_nothing_is_opened_until_the_mac_has_slept():
    rig = Rig()
    for _ in range(10):
        rig.touch()
        rig.tick()
    assert rig.awake == [] and rig.asked == [], "awake all along: the idle time is not even read"


def test_the_person_coming_back_after_a_sleep_is_told_once():
    rig = Rig()
    rig.sleep()
    rig.tick()
    rig.touch()                                   # the password, a move of the mouse
    for _ in range(int(SETTLE // 2) + 2):
        rig.tick()
    assert len(rig.awake) == 1
    for _ in range(5):
        rig.touch()
        rig.tick()
    assert len(rig.awake) == 1


def test_not_before_the_mac_has_been_awake_for_the_settle_time():
    rig = Rig()
    rig.sleep()
    woke_at = rig.wall.t
    rig.touch()
    while rig.wall.t + 2.0 - woke_at < SETTLE:
        rig.tick()
        assert rig.awake == []
    rig.touch()
    rig.tick()
    assert len(rig.awake) == 1
    assert SETTLE >= 4.0, "CoreAudio is busiest as the Mac wakes"


def test_a_wake_with_nobody_there_opens_nothing():
    """Power Nap: the Mac wakes for half a minute with nobody at it."""
    rig = Rig()
    rig.sleep()
    for _ in range(15):
        rig.tick()
    assert rig.awake == []


def test_a_night_of_dark_wakes_opens_nothing_and_the_morning_opens_once():
    rig = Rig()
    for _ in range(30):
        rig.sleep(900)
        for _ in range(8):
            rig.tick()
    assert rig.awake == []
    rig.sleep(900)                                # the lid is opened
    for _ in range(4):
        rig.tick()
    rig.touch()
    rig.tick()
    assert len(rig.awake) == 1


def test_input_from_before_the_sleep_does_not_count():
    """The key that was pressed just before the lid was shut: the idle time a
    Mac reports does not count the hours asleep."""
    rig = Rig()
    rig.touch()
    rig.wall.t += 3 * 3600
    rig.last_input = rig.wall.t - 1.0             # "1 s ago", by a clock that stopped in sleep
    rig.m._tick()
    for _ in range(10):
        rig.tick()                                # idle grows with the time awake: never under it
    assert rig.awake == []


def test_where_the_idle_time_cannot_be_read_nothing_changes():
    rig = Rig()
    rig.last_input = None
    rig.sleep()
    for _ in range(10):
        rig.tick()
    assert rig.awake == []
    assert len(rig.asked) == 1, "asked once for that wake, then left to the next press"


def test_a_reader_that_raises_is_as_good_as_none():
    rig = Rig()

    def boom():
        raise OSError("no window server")
    rig.m._input_idle_s = boom
    rig.sleep()
    for _ in range(10):
        rig.tick()                                # must not raise
    assert rig.awake == []


def test_without_a_handler_the_idle_time_is_never_read():
    rig = Rig(with_handler=False)
    rig.sleep()
    for _ in range(10):
        rig.touch()
        rig.tick()
    assert rig.asked == []


def test_a_handler_that_raises_does_not_stop_the_poll_and_is_not_called_again():
    rig = Rig()
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("no")
    rig.m.on_awake = boom
    rig.sleep()
    for _ in range(8):
        rig.touch()
        rig.tick()
    assert calls == [1]
    assert any("on_awake failed" in line for line in rig.lines)


def test_the_real_reader_answers_none_off_a_mac(monkeypatch):
    monkeypatch.setattr(adm.sys, "platform", "win32")
    assert adm.seconds_since_input() is None


# ── the recorder ─────────────────────────────────────────────────────────────

@pytest.fixture
def sd(monkeypatch):
    fake = WedgeSD()
    monkeypatch.setattr(audio_mod, "sd", fake)
    monkeypatch.setattr(audio_mod, "_HAL_DRAIN_S", 0)
    monkeypatch.setattr(audio_mod, "_POSTROLL_MS", 0)
    monkeypatch.setattr(audio_mod, "_recycles_when_idle", lambda: False)     # a Mac
    yield fake
    fake.never.set()


def make(sd):
    rec = AudioRecorder()
    rec.lines = []
    rec.log = rec.lines.append
    rec.start_monitoring()
    feed(rec, 300, 10)
    sd.log.clear()
    return rec


def test_a_stale_stream_is_opened_again_and_the_press_then_keeps_its_preroll(sd):
    rec = make(sd)
    rec.note_devices_changed()                    # what a wake does
    assert rec.reopen_if_stale() is True
    assert sd.log == REOPEN
    assert any("between presses" in line and "the audio devices changed" in line for line in rec.lines)
    feed(rec, 300, 10)                            # the new stream fills its pre-roll
    preroll = list(rec._preroll)
    sd.log.clear()
    rec.start()
    assert rec.is_recording
    assert sd.log == [], "the press opened nothing"
    assert len(rec._buffer) == len(preroll) and all(a is b for a, b in zip(rec._buffer, preroll))
    assert not any("opening the microphone again" in line for line in rec.lines)


def test_a_stream_that_is_current_is_left_alone(sd):
    rec = make(sd)
    assert rec.reopen_if_stale() is False
    assert sd.log == [] and rec.lines == []


def test_once_is_enough(sd):
    rec = make(sd)
    rec.note_devices_changed()
    assert rec.reopen_if_stale() is True
    sd.log.clear()
    assert rec.reopen_if_stale() is False
    assert sd.log == []


def test_never_while_something_is_using_the_microphone(sd):
    rec = make(sd)
    rec.note_devices_changed()
    assert rec.reopen_if_stale(busy=lambda: True) is False

    def unsure():
        raise RuntimeError("not sure")
    assert rec.reopen_if_stale(busy=unsure) is False
    assert sd.log == []
    assert rec._stale_reason() is not None, "still flagged: the next press opens it"


def test_never_during_a_take(sd):
    rec = make(sd)
    rec.start()
    rec.note_devices_changed()                    # the Mac "woke" in the middle of a take
    sd.log.clear()
    assert rec.reopen_if_stale() is False
    assert sd.log == [] and rec.is_recording


def test_no_stream_or_a_wedged_recorder_opens_nothing(sd):
    rec = make(sd)
    rec.note_devices_changed()
    rec._wedged = True
    assert rec.reopen_if_stale() is False
    rec._wedged = False
    rec.stop_monitoring()
    sd.log.clear()
    assert rec.reopen_if_stale() is False
    assert sd.log == []


def test_a_reopen_that_fails_says_so_and_leaves_the_press_to_open_it(sd, monkeypatch):
    rec = make(sd)
    rec.note_devices_changed()
    real = rec._create_stream
    monkeypatch.setattr(rec, "_create_stream", lambda: (_ for _ in ()).throw(OSError("no device")))
    assert rec.reopen_if_stale() is False
    assert any("could not open the microphone again" in line and "OSError" in line for line in rec.lines)
    assert rec.reopen_if_stale() is False, "not tried again every two seconds"
    monkeypatch.setattr(rec, "_create_stream", real)
    rec.start()
    assert rec.is_recording


# ── the app ──────────────────────────────────────────────────────────────────

def test_the_app_opens_the_stream_again_when_the_person_is_back():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def _on_awake"):src.index("_device_monitor.on_awake = _on_awake")]
    assert "reopen_if_stale(" in body
    assert "_update_would_interrupt(_pipeline)" in body, "never while anything is using the microphone"
    wake = src[src.index("def _on_wake() -> None:"):src.index("_device_monitor.on_wake = _on_wake")]
    assert "note_devices_changed()" in wake, "the flag stays: a press before then still opens it"


# ── is the microphone delivering audio? (for src/wedge_recovery.py) ──────────

def test_a_stream_that_has_just_sent_a_chunk_is_delivering(sd):
    rec = make(sd)
    assert rec.is_delivering() is True


def test_a_stream_that_has_sent_nothing_for_a_while_is_not(sd, monkeypatch):
    rec = make(sd)
    rec._last_chunk_at -= audio_mod._DELIVERING_WITHIN_S + 0.1
    assert rec.is_delivering() is False
    feed(rec, 0, 1)                                # exact zeros are still a chunk: the I/O thread runs
    assert rec.is_delivering() is True


def test_no_stream_is_not_delivering_and_no_stream_is_touched(sd):
    rec = make(sd)
    rec.stop_monitoring()
    fresh = AudioRecorder()
    sd.log.clear()
    before = dict(sd.calls)
    assert rec.is_delivering() is False
    assert fresh.is_delivering() is False
    assert sd.log == [] and sd.calls == before
