"""What tells the recorder that the devices changed (src/audio_device_monitor.py,
src/mic_supervisor.py), now that a press no longer opens a new stream just
because half a minute has passed.

Until then a press after 30 s idle opened a new stream "to pick up any mic
change". The mic change is now noticed where it happens and flagged
(``AudioRecorder.note_devices_changed``), and the next press opens one stream,
once. Three things say so, and nothing else does:

* the system's default input changed (the device monitor, as before, plus the
  flag, so a restart that could not run during a take is still done);
* the microphone the stream is on is no longer in the list of inputs;
* the Mac woke up (the monitor's poll arrived much later than it was due),
  which is when a stream most often goes dead.

A device that comes or goes and is not the one in use changes nothing: a phone's
microphone or a meeting app's driver appearing every few minutes must not make
every press a cold start.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import audio_device_monitor as adm  # noqa: E402
import mic_supervisor as ms  # noqa: E402
from mic_devices import Device  # noqa: E402
from test_mic_supervisor import AIRPODS, BUILTIN, TEAMS, YETI, World  # noqa: E402

IPHONE = Device("James's iPhone Microphone", host_api="Core Audio", transport="cont")


def supervisor(world):
    hooks = world.hooks()
    world.flagged = []
    hooks.note_devices_changed = lambda: world.flagged.append(world.opened)
    return ms.MicSupervisor(hooks)


# ── the microphone in use leaves ─────────────────────────────────────────────

def test_the_microphone_the_stream_is_on_leaving_flags_a_change(tmp_path):
    w = World(tmp_path)
    w.opened = "Yeti Stereo Microphone"
    s = supervisor(w)
    s.on_devices([BUILTIN, YETI])
    s.on_devices([BUILTIN])                      # the Yeti was unplugged
    assert w.flagged == ["Yeti Stereo Microphone"]


def test_the_flag_is_raised_once_for_one_departure(tmp_path):
    w = World(tmp_path)
    w.opened = "Yeti Stereo Microphone"
    s = supervisor(w)
    s.on_devices([BUILTIN, YETI])
    for _ in range(5):
        s.on_devices([BUILTIN])
    assert len(w.flagged) == 1


def test_another_device_coming_and_going_changes_nothing(tmp_path):
    w = World(tmp_path)                          # the stream is on the built-in microphone
    s = supervisor(w)
    s.on_devices([BUILTIN])
    for devices in ([BUILTIN, IPHONE], [BUILTIN], [BUILTIN, TEAMS], [BUILTIN, TEAMS, AIRPODS], [BUILTIN]):
        s.on_devices(devices)
    assert w.flagged == [], "a phone, a meeting driver and AirPods came and went: the stream is fine"


def test_the_first_look_at_the_list_flags_nothing(tmp_path):
    w = World(tmp_path)
    s = supervisor(w)
    s.on_devices([YETI])                         # the built-in one is not even listed yet
    assert w.flagged == []


def test_a_stream_whose_microphone_is_not_known_flags_nothing(tmp_path):
    w = World(tmp_path)
    w.opened = None
    s = supervisor(w)
    s.on_devices([BUILTIN])
    s.on_devices([])
    assert w.flagged == []


def test_a_hook_that_raises_never_reaches_the_monitor(tmp_path):
    w = World(tmp_path)
    s = supervisor(w)
    s.h.note_devices_changed = lambda: (_ for _ in ()).throw(RuntimeError("no"))
    s.on_devices([BUILTIN])
    s.on_devices([])                             # must not raise


# ── the default input changes ────────────────────────────────────────────────

class FakeAudio:
    def __init__(self, busy_for=0):
        self.flags = 0
        self.rebuilt = 0
        self.busy_for = busy_for

    def note_devices_changed(self):
        self.flags += 1

    def rebuild_if_idle(self):
        if self.busy_for:
            self.busy_for -= 1
            return False
        self.rebuilt += 1
        return True


def monitor():
    return adm.AudioDeviceMonitor(on_change=lambda o, n: None, log_fn=lambda m: None,
                                  reader=lambda: [BUILTIN])


def test_a_change_of_default_input_flags_the_recorder_before_it_tries_to_restart(tmp_path):
    audio = FakeAudio()
    adm.restart_stream_when_idle(audio, monitor(), "AirPods Pro", lambda m: None)
    assert audio.flags == 1 and audio.rebuilt == 1


def test_a_change_during_a_take_stays_flagged_for_the_next_press(tmp_path):
    audio = FakeAudio(busy_for=100)              # the take outlasts every retry
    adm.restart_stream_when_idle(audio, monitor(), "AirPods Pro", lambda m: None)
    assert audio.flags == 1 and audio.rebuilt == 0


def test_a_recorder_without_the_flag_is_still_restarted():
    class Old:
        rebuilt = 0

        def rebuild_if_idle(self):
            self.rebuilt += 1
            return True
    audio = Old()
    adm.restart_stream_when_idle(audio, monitor(), "AirPods Pro", lambda m: None)
    assert audio.rebuilt == 1


# ── the Mac wakes ────────────────────────────────────────────────────────────

class Wall:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def wake_monitor(wall, woke):
    return adm.AudioDeviceMonitor(on_change=lambda o, n: None, log_fn=lambda m: None,
                                  reader=lambda: [BUILTIN], on_wake=lambda: woke.append(wall.t),
                                  wall_clock=wall)


def test_a_poll_that_comes_far_too_late_means_the_mac_slept():
    wall, woke = Wall(), []
    m = wake_monitor(wall, woke)
    m._tick()
    wall.t += 2.0
    m._tick()
    assert woke == []
    wall.t += 3 * 3600                           # the lid was shut for three hours
    m._tick()
    assert len(woke) == 1


def test_the_first_poll_is_never_a_wake():
    wall, woke = Wall(), []
    m = wake_monitor(wall, woke)
    m._tick()
    assert woke == []


def test_a_slow_poll_is_not_a_wake():
    """A CoreAudio that took its whole bound (3 s) to answer is slow, not asleep."""
    wall, woke = Wall(), []
    m = wake_monitor(wall, woke)
    m._tick()
    wall.t += 2.0 + 3.0
    m._tick()
    assert woke == []
    assert adm.AudioDeviceMonitor.WAKE_GAP_S >= 10.0, "well over a poll and a bounded CoreAudio call"


def test_a_wake_is_told_once_per_sleep():
    wall, woke = Wall(), []
    m = wake_monitor(wall, woke)
    m._tick()
    wall.t += 600
    m._tick()
    wall.t += 2
    m._tick()
    wall.t += 2
    m._tick()
    assert len(woke) == 1


def test_a_wake_handler_that_raises_does_not_stop_the_poll():
    wall = Wall()

    def boom():
        raise RuntimeError("no")
    m = adm.AudioDeviceMonitor(on_change=lambda o, n: None, log_fn=lambda m: None,
                               reader=lambda: [BUILTIN], on_wake=boom, wall_clock=wall)
    m._tick()
    wall.t += 600
    m._tick()                                    # must not raise
    wall.t += 2
    m._tick()


def test_no_wake_handler_is_fine():
    wall = Wall()
    m = adm.AudioDeviceMonitor(on_change=lambda o, n: None, log_fn=lambda m: None,
                               reader=lambda: [BUILTIN], wall_clock=wall)
    m._tick()
    wall.t += 600
    m._tick()


# ── what the app does with them ──────────────────────────────────────────────

def test_the_app_wires_the_signals_to_the_recorder():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "note_devices_changed=lambda: self.audio.note_devices_changed()" in src
    assert "_device_monitor.on_wake = _on_wake" in src and "_pipeline.audio.note_devices_changed()" in src
