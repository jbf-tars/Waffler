"""The longest take is enforced by a timer of its own (src/take_limit.py TakeTimer).

What happened: on a Mac whose audio system had wedged, two hands-free takes ran
for 670 s and 1,760 s, past the 20 minute limit, and nothing stopped them. The
limit was checked by the recording loop (the thread that feeds the pill its
level), and that loop was only started after the recorder had begun, which it
never did. A limit that depends on the thing that failed is no limit.

The timer is started when the key goes down, before anything that touches the
microphone, and reads nothing but the clock and whether this take is still the
current one. No clock here is real: the tests turn it.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import take_limit as tl  # noqa: E402


class Clock:
    def __init__(self, t=5000.0):
        self.t = t

    def __call__(self):
        return self.t


def rig(**kw):
    clock = Clock()
    seen = []
    timer = tl.TakeTimer(
        tl.TakeWatch(5000.0, clock=clock),
        on_warn=lambda: seen.append("warn"),
        on_stop=lambda: seen.append("stop"),
        autostart=False, **kw)
    return clock, seen, timer


def test_it_warns_once_at_nineteen_minutes_and_stops_once_at_twenty():
    clock, seen, timer = rig()
    for elapsed in (0, 600, 1139, 1140, 1141, 1199, 1200, 1201, 3000):
        clock.t = 5000.0 + elapsed
        timer.tick()
    assert seen == ["warn", "stop"]


def test_a_take_that_ran_for_1760_seconds_is_impossible():
    """The owner's second take. The first look after the limit stops it, however
    long the clock has been away."""
    clock, seen, timer = rig()
    clock.t = 5000.0 + 1760
    timer.tick()
    assert seen == ["stop"], "stopped, and with no warning that would arrive after the end"


def test_it_does_nothing_for_a_take_that_is_no_longer_the_current_one():
    clock = Clock()
    seen = []
    current = [True]
    timer = tl.TakeTimer(tl.TakeWatch(5000.0, clock=clock), on_warn=lambda: seen.append("warn"),
                         on_stop=lambda: seen.append("stop"), is_current=lambda: current[0],
                         autostart=False)
    clock.t = 5000.0 + 600
    timer.tick()
    current[0] = False                      # the key came up, or another take began
    clock.t = 5000.0 + 1300
    timer.tick()
    assert seen == []
    assert timer.finished


def test_it_stops_asking_once_it_has_stopped_the_take():
    clock, seen, timer = rig()
    clock.t = 5000.0 + 1300
    assert timer.tick() == tl.STOP
    assert timer.finished
    assert timer.tick() == tl.NOTHING
    assert seen == ["stop"]


def test_a_warning_that_raises_does_not_stop_the_stop():
    clock = Clock()
    seen = []

    def bad():
        raise RuntimeError("toast failed")
    timer = tl.TakeTimer(tl.TakeWatch(5000.0, clock=clock), on_warn=bad,
                         on_stop=lambda: seen.append("stop"), autostart=False)
    clock.t = 5000.0 + 1150
    timer.tick()
    clock.t = 5000.0 + 1200
    timer.tick()
    assert seen == ["stop"]


def test_a_stop_that_raises_is_not_raised_into_the_thread():
    clock = Clock()

    def bad():
        raise RuntimeError("release failed")
    timer = tl.TakeTimer(tl.TakeWatch(5000.0, clock=clock), on_warn=lambda: None, on_stop=bad,
                         autostart=False)
    clock.t = 5000.0 + 1300
    timer.tick()
    assert timer.finished


def test_cancel_ends_it_without_a_stop():
    clock, seen, timer = rig()
    timer.cancel()
    clock.t = 5000.0 + 1300
    timer.tick()
    assert seen == [] and timer.finished


# ── on a thread of its own ───────────────────────────────────────────────────

def test_on_its_own_thread_it_stops_the_take_while_everything_else_is_stuck():
    """Nothing here is the recorder, the pill or the microphone supervisor: a
    thread that waits for ever beside it changes nothing."""
    clock = Clock()
    stopped = threading.Event()
    stuck = threading.Event()
    threading.Thread(target=lambda: stuck.wait(30), daemon=True).start()
    timer = tl.TakeTimer(tl.TakeWatch(5000.0, clock=clock), on_warn=lambda: None,
                         on_stop=stopped.set, tick_s=0.02)
    try:
        clock.t = 5000.0 + 1200
        assert stopped.wait(2.0), "the limit was not enforced"
    finally:
        timer.cancel()
        stuck.set()


def test_the_thread_is_a_daemon_and_ends_with_the_take():
    clock = Clock()
    current = [True]
    timer = tl.TakeTimer(tl.TakeWatch(5000.0, clock=clock), on_warn=lambda: None, on_stop=lambda: None,
                         is_current=lambda: current[0], tick_s=0.02)
    assert timer.thread.daemon
    current[0] = False
    timer.thread.join(2.0)
    assert not timer.thread.is_alive()


def test_cancel_wakes_the_thread_at_once():
    timer = tl.TakeTimer(tl.TakeWatch(time.time()), on_warn=lambda: None, on_stop=lambda: None,
                         tick_s=30.0)
    t0 = time.monotonic()
    timer.cancel()
    timer.thread.join(2.0)
    assert not timer.thread.is_alive() and time.monotonic() - t0 < 1.0


def test_a_thread_that_cannot_be_started_does_not_break_the_press(monkeypatch):
    def refuse(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", refuse)
    timer = tl.TakeTimer(tl.TakeWatch(time.time()), on_warn=lambda: None, on_stop=lambda: None)
    assert timer.thread is None, "the press goes on; the level loop is not asked to cover for it"
