"""Bounded calls into PortAudio and CoreAudio (src/audio_guard.py).

A live capture from a Mac whose audio system had deadlocked showed one Python
thread stuck in ``AudioOutputUnitStop`` and CoreAudio's own I/O thread stuck on
a lock that thread held. Nothing in the process can free that, and every later
CoreAudio call blocks for ever. ``run_bounded`` is the wall around such a call:
it runs on a daemon thread, the caller waits up to a bound, and a call that does
not come back is abandoned and reported as wedged, so the caller can carry on
(and let go of its locks) instead of hanging.

No test here touches real audio: the stuck call is a function that waits on an
event the test sets (or never sets).
"""
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audio_guard as ag  # noqa: E402


@pytest.fixture(autouse=True)
def clean_guard():
    ag.reset()
    yield
    ag.reset()


def stuck():
    """A call that never comes back until the test lets it."""
    gate = threading.Event()

    def fn():
        gate.wait(30)
        return "late"
    return fn, gate


# ── the helper itself ────────────────────────────────────────────────────────

def test_a_call_that_returns_hands_back_its_value():
    assert ag.run_bounded(lambda: 42, 1.0, "answer") == (True, 42)


def test_a_call_that_raises_hands_back_the_exception_as_done():
    def boom():
        raise OSError("no such device")
    done, value = ag.run_bounded(boom, 1.0, "boom")
    assert done is True and isinstance(value, OSError) and "no such device" in str(value)


def test_a_call_that_does_not_return_is_abandoned_within_the_bound():
    fn, gate = stuck()
    t0 = time.monotonic()
    done, value = ag.run_bounded(fn, 0.15, "AudioOutputUnitStop")
    took = time.monotonic() - t0
    assert done is False and value is ag.WEDGED
    assert 0.14 <= took < 1.0, "it waited for the bound and no longer"
    gate.set()


def test_the_work_runs_on_a_daemon_thread_not_the_callers():
    seen = {}

    def fn():
        t = threading.current_thread()
        seen["daemon"], seen["same"], seen["name"] = t.daemon, t is threading.current_thread(), t.name
        return 1
    ag.run_bounded(fn, 1.0, "which thread")
    assert seen["daemon"] is True
    assert threading.current_thread().name != seen["name"]


# ── what is known about an abandoned call ────────────────────────────────────

def test_an_abandoned_call_is_remembered_by_name_while_it_is_stuck():
    fn, gate = stuck()
    assert ag.wedged_op() is None
    ag.run_bounded(fn, 0.1, "stream.stop")
    assert ag.wedged_op() == "stream.stop"
    gate.set()


def test_a_call_that_was_only_slow_clears_the_mark_when_it_finishes():
    fn, gate = stuck()
    ag.run_bounded(fn, 0.1, "stream.close")
    assert ag.wedged_op() == "stream.close"
    gate.set()
    deadline = time.monotonic() + 2
    while ag.wedged_op() is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ag.wedged_op() is None, "a call that came back late is not a wedge"


def test_while_wedged_the_next_call_is_not_started_at_all():
    """Every later CoreAudio call would block too: starting a thread for each
    one (the device monitor asks every two seconds) would pile up threads for
    ever."""
    fn, gate = stuck()
    ag.run_bounded(fn, 0.1, "stream.stop")
    ran = []
    t0 = time.monotonic()
    done, value = ag.run_bounded(lambda: ran.append(1), 5.0, "query_devices")
    assert (done, value) == (False, ag.SKIPPED)
    assert time.monotonic() - t0 < 0.5, "it did not wait"
    assert ran == [], "and the call was not made"
    assert threading.active_count() < 50
    gate.set()


def test_a_call_can_ask_to_run_even_while_wedged():
    fn, gate = stuck()
    ag.run_bounded(fn, 0.1, "stream.stop")
    assert ag.run_bounded(lambda: "ran", 1.0, "unrelated", skip_if_wedged=False) == (True, "ran")
    gate.set()


def test_the_next_call_runs_once_the_stuck_one_has_finished():
    fn, gate = stuck()
    ag.run_bounded(fn, 0.1, "stream.stop")
    gate.set()
    deadline = time.monotonic() + 2
    while ag.wedged_op() is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ag.run_bounded(lambda: "fine", 1.0, "again") == (True, "fine")


def test_the_streams_of_an_abandoned_stop_are_kept_alive():
    """The callback CoreAudio may still call lives in the stream object: it must
    not be freed while the abandoned call can still reach it."""
    fn, gate = stuck()
    token = object()
    ag.run_bounded(fn, 0.1, "stream.stop", keep=token)
    assert token in ag.kept_alive()
    gate.set()


# ── reporting a wedge ────────────────────────────────────────────────────────

def test_a_wedge_is_logged_as_one_line_naming_the_bound_and_the_call():
    lines = []
    ag.set_logger(lines.append)
    assert ag.report_wedge("stream.stop", 3.0) is True
    assert lines == ["[audio] CoreAudio did not answer within 3s in stream.stop"]


def test_the_bound_is_written_without_a_trailing_zero_or_a_fraction_it_does_not_have():
    lines = []
    ag.set_logger(lines.append)
    ag.report_wedge("create the stream", 5.0)
    assert lines[0].endswith("within 5s in create the stream")


def test_a_second_report_is_not_logged_or_announced_again():
    lines, told = [], []
    ag.set_logger(lines.append)
    ag.set_handler(told.append)
    assert ag.report_wedge("stream.stop", 3.0) is True
    assert ag.report_wedge("query_devices", 3.0) is False
    assert len(lines) == 1 and told == ["stream.stop"]


def test_a_report_can_say_it_will_announce_it_itself():
    """The recorder tells its own owner; the process-wide handler is for calls
    that have no recorder (the device monitor's listing)."""
    told = []
    ag.set_handler(told.append)
    ag.report_wedge("stream.stop", 3.0, notify=False)
    assert told == []


def test_a_handler_that_raises_does_not_break_the_report():
    def bad(op):
        raise RuntimeError("toast failed")
    ag.set_handler(bad)
    assert ag.report_wedge("stream.stop", 3.0) is True


def test_what_was_reported_is_readable():
    assert ag.reported() is None
    ag.report_wedge("stream.stop", 3.0)
    op, at = ag.reported()
    assert op == "stream.stop" and abs(time.time() - at) < 5


def test_a_slow_handler_does_not_hold_up_the_caller():
    gate = threading.Event()
    ag.set_handler(lambda op: gate.wait(5))
    t0 = time.monotonic()
    ag.report_wedge("stream.stop", 3.0)
    assert time.monotonic() - t0 < 0.5
    gate.set()
