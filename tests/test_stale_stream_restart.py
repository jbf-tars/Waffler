"""A change of microphone in the middle of a take must not leave Waffler on a dead stream.

What happened (a real log, 3.15.1): AirPods connected while a hands-free take was being
recorded. The default input changed, the handler said "recording is in flight, deferring
stream restart" and returned, and nothing ever tried again. The always-open stream stayed
on a microphone that no longer delivered anything ("Dead audio stream", "audio captured:
0 bytes", a pill that did nothing) until the next change, nearly two hours later.

Now a restart that cannot run during a take is tried again on every poll of the device
monitor (every two seconds) until it can.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audio_device_monitor as adm  # noqa: E402
import mic_supervisor as ms  # noqa: E402
from mic_devices import Device  # noqa: E402

BUILTIN = Device("MacBook Pro Microphone", is_default=True)


def monitor(log=None):
    return adm.AudioDeviceMonitor(on_change=lambda o, n: None, log_fn=(log or (lambda m: None)),
                                  reader=lambda: [BUILTIN])


# ── the monitor retries ──────────────────────────────────────────────────────

def test_a_deferred_restart_is_tried_on_every_poll_until_it_works():
    m = monitor()
    answers = iter([False, False, True])
    calls = []
    m.defer_until_done(lambda: calls.append(1) or next(answers))
    for _ in range(6):
        m._tick()
    assert len(calls) == 3, "tried until it worked, then never again"


def test_nothing_deferred_means_nothing_is_called():
    m = monitor()
    for _ in range(3):
        m._tick()          # must not raise with nothing deferred


def test_a_newer_restart_replaces_an_older_one():
    m = monitor()
    old, new = [], []
    m.defer_until_done(lambda: old.append(1) or False)
    m.defer_until_done(lambda: new.append(1) or True)
    m._tick()
    m._tick()
    assert old == [] and new == [1]


def test_a_restart_that_raises_does_not_stop_the_poll_and_is_tried_again():
    m = monitor()
    n = []

    def flaky():
        n.append(1)
        if len(n) < 3:
            raise RuntimeError("the device is busy")
        return True

    m.defer_until_done(flaky)
    for _ in range(5):
        m._tick()
    assert len(n) == 3


def test_a_restart_that_never_works_is_given_up_after_a_while_and_says_so():
    lines = []
    m = monitor(lines.append)
    n = []
    m.defer_until_done(lambda: n.append(1) or False)
    for _ in range(adm.AudioDeviceMonitor.DEFERRED_MAX_POLLS + 20):
        m._tick()
    assert len(n) == adm.AudioDeviceMonitor.DEFERRED_MAX_POLLS
    assert any("giving up" in line for line in lines)


def test_the_retry_runs_even_when_the_device_list_cannot_be_read():
    answers = iter([None, None, None])
    m = adm.AudioDeviceMonitor(on_change=lambda o, n: None, log_fn=lambda m: None,
                               reader=lambda: next(answers))
    done = []
    m.defer_until_done(lambda: done.append(1) or True)
    m._tick()
    assert done == [1]


# ── the handler for a change of the default input ────────────────────────────

class FakeAudio:
    def __init__(self, busy_for=0, raises=0):
        self.busy_for = busy_for
        self.raises = raises
        self.rebuilt = 0

    def rebuild_if_idle(self):
        if self.raises:
            self.raises -= 1
            raise OSError("no such device")
        if self.busy_for:
            self.busy_for -= 1
            return False
        self.rebuilt += 1
        return True


def test_an_idle_recorder_is_restarted_at_once_and_nothing_is_deferred():
    lines, m, audio = [], monitor(), FakeAudio()
    adm.restart_stream_when_idle(audio, m, "MacBook Pro Microphone", lines.append)
    assert audio.rebuilt == 1
    assert any("restarted on 'MacBook Pro Microphone'" in line for line in lines)
    m._tick()
    assert audio.rebuilt == 1, "nothing was left to retry"


def test_a_recorder_in_a_take_is_restarted_as_soon_as_the_take_ends():
    lines, m, audio = [], monitor(), FakeAudio(busy_for=2)
    adm.restart_stream_when_idle(audio, m, "AirPods Pro", lines.append)
    assert audio.rebuilt == 0 and any("when the take is over" in line for line in lines)
    m._tick()          # still recording
    assert audio.rebuilt == 0
    m._tick()          # the take ended
    assert audio.rebuilt == 1
    assert any("restarted on 'AirPods Pro'" in line and "after the take" in line for line in lines)
    m._tick()
    assert audio.rebuilt == 1


def test_a_stream_that_fails_to_open_is_tried_again():
    lines, m, audio = [], monitor(), FakeAudio(raises=2)
    adm.restart_stream_when_idle(audio, m, "MacBook Pro Microphone", lines.append)
    for _ in range(4):
        m._tick()
    assert audio.rebuilt == 1
    assert any("restart failed" in line for line in lines)


def test_no_recorder_yet_does_nothing():
    m = monitor()
    adm.restart_stream_when_idle(None, m, "x", lambda s: None)    # the pipeline is still starting
    m._tick()


# ── the app uses it ──────────────────────────────────────────────────────────

def test_the_app_hands_a_change_to_that_helper_and_does_not_just_return():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def _on_default_input_changed"):src.index("global _device_monitor")]
    assert "restart_stream_when_idle(" in body
    assert "deferring stream restart" not in body, "the log line that used to end in a return"


# ── a ranking change during a take (the supervisor) ──────────────────────────

def hooks(**over):
    base = dict(settings=lambda: {}, recording=lambda: False, busy=lambda: False,
                toast_allowed=lambda k: True, show_toast=lambda *a, **k: None,
                health=lambda: {}, reopen=lambda: True, opened_device=lambda: None,
                stop_take=lambda: None, ranking=lambda: [], promote=lambda e: None,
                note_changed=lambda: None, spawn=lambda fn: fn(),
                memory=types.SimpleNamespace(decision=lambda d: None, remember=lambda *a, **k: None))
    base.update(over)
    return ms.Hooks(**base)


def test_a_ranking_change_during_a_take_asks_for_a_restart_after_it():
    calls = []
    state = {"rec": True}
    h = hooks(recording=lambda: state["rec"], rebuild_idle=lambda: calls.append("idle"),
              rebuild_after_take=lambda: calls.append("after"))
    s = ms.MicSupervisor(h)
    s._refresh()
    assert calls == ["after"], calls
    state["rec"] = False
    s._refresh()
    assert calls == ["after", "idle"], calls


def test_the_pipelines_hook_restarts_after_the_take_through_the_monitor(tmp_path):
    """The real hook the app gives the supervisor (built by the pipeline): while a take is
    recorded it asks the device monitor to retry, and once the take is over the retry runs
    the recorder's rebuild."""
    sys.path.insert(0, str(ROOT / "tests"))
    from _pipeline_harness import make_pipeline
    p = make_pipeline(tmp_path)
    m = monitor()
    p.ns["_device_monitor"] = m
    rebuilt = []
    p.audio.rebuild_if_idle = lambda: rebuilt.append(1) or True
    p.is_recording = True                       # a take is being recorded
    p._mic.h.rebuild_after_take()
    m._tick()
    assert rebuilt == [], "a take is still being recorded"
    p.is_recording = False
    m._tick()
    assert rebuilt == [1]
    m._tick()
    assert rebuilt == [1], "done: not tried again"


def test_the_hook_does_not_raise_before_the_monitor_exists(tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from _pipeline_harness import make_pipeline
    p = make_pipeline(tmp_path)            # _device_monitor is None: the app is still starting
    p.audio.rebuild_if_idle = lambda: False
    p._mic.h.rebuild_after_take()          # must not raise


def test_a_command_or_transform_hold_also_keeps_the_restart_waiting(tmp_path):
    """Not only the dictation's flag: the app's own check of what is using the microphone."""
    sys.path.insert(0, str(ROOT / "tests"))
    from _pipeline_harness import make_pipeline
    p = make_pipeline(tmp_path)
    m = monitor()
    p.ns["_device_monitor"] = m
    rebuilt = []
    p.audio.rebuild_if_idle = lambda: rebuilt.append(1) or True
    p._commands = types.SimpleNamespace(busy=lambda: True)
    p._mic.h.rebuild_after_take()
    m._tick()
    assert rebuilt == []
    p._commands = types.SimpleNamespace(busy=lambda: False)
    m._tick()
    assert rebuilt == [1]
