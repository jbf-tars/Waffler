"""The device monitor (S12): it now carries the list of inputs to the
supervisor, and reads the default input from the same list. One poll at a
time is driven by hand (``_tick``); no thread runs and nothing is opened.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audio_device_monitor as adm  # noqa: E402
import mic_devices as md  # noqa: E402
from mic_devices import Device  # noqa: E402


def mon(devices_by_poll, **kw):
    """A monitor whose list of inputs is read from ``devices_by_poll`` (one
    item per poll; None means the reader could not say)."""
    polls = iter(devices_by_poll)
    changes, seen, log = [], [], []
    m = adm.AudioDeviceMonitor(on_change=lambda o, n: changes.append((o, n)), log_fn=log.append,
                               on_devices=seen.append, reader=lambda: next(polls), **kw)
    return m, changes, seen, log


BUILTIN = Device("MacBook Pro Microphone", is_default=True)
YETI = Device("Yeti")


def test_the_first_poll_is_a_baseline_and_changes_nothing():
    m, changes, seen, log = mon([[BUILTIN]])
    m._tick()
    assert changes == [] and len(seen) == 1
    assert any("baseline default input = 'MacBook Pro Microphone'" in line for line in log)


def test_the_supervisor_gets_the_list_every_poll():
    m, _, seen, _ = mon([[BUILTIN], [BUILTIN, YETI], [BUILTIN, YETI]])
    for _ in range(3):
        m._tick()
    assert [[d.name for d in s] for s in seen] == [
        ["MacBook Pro Microphone"], ["MacBook Pro Microphone", "Yeti"], ["MacBook Pro Microphone", "Yeti"]]


def test_a_new_default_is_a_change_as_before_and_read_from_the_same_list():
    m, changes, _, _ = mon([[BUILTIN, YETI], [Device("MacBook Pro Microphone"), Device("Yeti", is_default=True)]])
    m._tick()
    m._tick()
    assert changes == [("MacBook Pro Microphone", "Yeti")]


def test_a_poll_that_cannot_say_is_skipped_and_never_empties_the_list():
    m, changes, seen, _ = mon([[BUILTIN, YETI], None, [], [BUILTIN, YETI]])
    for _ in range(4):
        m._tick()
    assert len(seen) == 2 and changes == []


def test_a_listener_that_fails_does_not_stop_the_poll():
    m, _, _, log = mon([[BUILTIN], [BUILTIN]])
    m.on_devices = lambda devices: (_ for _ in ()).throw(RuntimeError("supervisor"))
    m._tick()
    m._tick()
    assert sum("on_devices failed" in line for line in log) == 2


def test_a_reader_that_raises_does_not_stop_the_poll():
    log = []
    m = adm.AudioDeviceMonitor(on_change=lambda *a: None, log_fn=log.append,
                               reader=lambda: (_ for _ in ()).throw(OSError("x")))
    m._tick()
    assert any("device list not read" in line for line in log)


def test_old_callers_still_work_with_two_arguments():
    m = adm.AudioDeviceMonitor(lambda o, n: None, print)
    assert m.on_devices is None


def test_one_source_is_used_for_the_run(monkeypatch):
    """Once the system's own list has answered, a poll where it does not is
    skipped: falling back to PortAudio's older list would make every
    microphone it lacks look new when the system answers again."""
    answers = iter([[BUILTIN, YETI], None, [BUILTIN, YETI]])
    monkeypatch.setattr(md, "live_inputs", lambda: next(answers))
    monkeypatch.setattr(md, "pa_inputs", lambda: [Device("Cached Only")])
    seen = []
    m = adm.AudioDeviceMonitor(on_change=lambda *a: None, log_fn=lambda *_: None, on_devices=seen.append)
    for _ in range(3):
        m._tick()
    assert [[d.name for d in s] for s in seen] == [["MacBook Pro Microphone", "Yeti"]] * 2


def test_without_a_live_list_portaudios_is_used(monkeypatch):
    monkeypatch.setattr(md, "live_inputs", lambda: None)
    monkeypatch.setattr(md, "pa_inputs", lambda: [BUILTIN])
    seen = []
    m = adm.AudioDeviceMonitor(on_change=lambda *a: None, log_fn=lambda *_: None, on_devices=seen.append)
    m._tick()
    assert m._source == "portaudio" and len(seen) == 1


def test_a_monitor_given_its_list_never_asks_the_machine_for_its_default(monkeypatch):
    """On a CI runner the machine's real default input is not the test's. A poll that
    cannot say (None) or says nothing ([]) must not fall back to the real one."""
    m, changes, seen, _ = mon([[BUILTIN, YETI], None, [], [BUILTIN, YETI]])

    def real_query():
        raise AssertionError("asked the machine for its real default input")
    monkeypatch.setattr(m, "_current_default_name", real_query)
    for _ in range(4):
        m._tick()
    assert changes == []
