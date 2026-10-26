"""A press of the hotkey does not depend on the microphone (app.py on_hotkey_press).

What was seen, on a Mac whose audio system had wedged: the hotkey went down, the
log said "Recording started", and no pill ever appeared, because the pill was
shown after ``audio.start()`` returned and ``audio.start()`` never did. The 20
minute limit, which was checked by a loop started after the same call, never
began either, so hands-free takes ran for 670 s and 1,760 s.

Now the pill and the status come first, the limit's own timer is started before
the recorder, and a recorder that fails or does not answer in 12 s undoes the
press and, if it does not answer, tells the wedge recovery. The real methods of
WafflerPipeline are run with fakes (tests/_pipeline_harness.py); the recorder's
``start`` waits on an event nothing sets, which is what the deadlock looks like.
"""
import ast
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import take_limit  # noqa: E402
import wedge_recovery  # noqa: E402
from _pipeline_harness import FakeAudio, make_pipeline, speech_wav, wait_for  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_PIPE = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")


def method_source(name):
    return ast.unparse(next(n for n in _PIPE.body if isinstance(n, ast.FunctionDef) and n.name == name))


@pytest.fixture
def quick(monkeypatch):
    """The start deadline and the timer's tick shortened."""
    monkeypatch.setattr(pw, "WEDGE_DEADLINE_S", 0.4)
    monkeypatch.setattr(take_limit, "TICK_S", 0.02)


def pipeline(tmp_path, **audio):
    p = make_pipeline(tmp_path)
    p.audio = FakeAudio(speech_wav(3.0), **audio)
    p._permissions_block_recording = lambda: False
    return p


def press_on_a_thread(p):
    t = threading.Thread(target=p.on_hotkey_press, daemon=True, name="Press")
    t.start()
    return t


# ── the pill does not wait for the microphone ────────────────────────────────

def test_the_pill_is_up_while_the_recorder_is_still_stuck_starting(tmp_path):
    release = threading.Event()
    p = pipeline(tmp_path, start_hang=release)
    t = press_on_a_thread(p)
    try:
        assert wait_for(lambda: p.audio.starts == 1), "the recorder was asked to start"
        assert wait_for(lambda: "show" in p.overlay.names()), "the pill is on screen although start() has not returned"
        assert p.page.statuses[-1:] == ["listening"], "and the window says Listening"
        assert p.is_recording
        assert t.is_alive(), "start() really is stuck"
    finally:
        release.set()
        t.join(5)


def test_a_pill_that_is_slow_to_show_does_not_delay_the_recorder(tmp_path):
    """The other way round: a stalled overlay (its pipe full, or a restart of
    the child) can take seconds to answer, and the first words of the take are
    lost for each of them if the recorder waits for the pill."""
    slow = threading.Event()
    p = pipeline(tmp_path)
    real = p.overlay.show

    def show(*a, **k):
        slow.wait(10)
        return real(*a, **k)
    p.overlay.show = show
    t = press_on_a_thread(p)
    try:
        assert wait_for(lambda: p.audio.starts == 1, timeout=2), "the recorder started at once"
        assert "show" not in p.overlay.names(), "while the pill was still on its way"
    finally:
        slow.set()
        t.join(5)
    assert wait_for(lambda: "show" in p.overlay.names())


def test_the_pill_is_shown_once_for_a_press(tmp_path):
    p = pipeline(tmp_path)
    p.on_hotkey_press()
    assert wait_for(lambda: "show" in p.overlay.names())
    time.sleep(0.1)
    assert p.overlay.names().count("show") == 1


def test_a_pill_that_would_come_after_the_take_ended_is_not_shown(tmp_path):
    """The key came up before the pill thread got to run: the working pill
    belongs to the dictation now, and a recording pill must not replace it."""
    p = pipeline(tmp_path)
    p.is_recording = False
    p._show_recording_pill(p._current_press_id)
    assert "show" not in p.overlay.names()


def test_a_failed_start_that_beats_the_pill_leaves_no_pill_on_screen(tmp_path):
    """start() failing at once while the pill is still being shown: the take
    comes down first, so the pill thread finds it over and shows nothing, or
    shows and is hidden after, never the other way round."""
    for _ in range(20):
        p = pipeline(tmp_path, start_error=OSError("no input device"))
        p.on_hotkey_press()
        time.sleep(0.02)
        names = p.overlay.names()
        assert names.count("show") <= 1
        if "show" in names:
            assert names.index("hide") > names.index("show")


def test_the_start_cue_still_waits_for_the_recorder(tmp_path):
    """The cue means "the microphone is live": it must not sound while the
    recorder is stuck (it would also be in the recording)."""
    release = threading.Event()
    p = pipeline(tmp_path, start_hang=release)
    t = press_on_a_thread(p)
    assert wait_for(lambda: p.audio.starts == 1)
    time.sleep(0.15)
    assert p.cue_backend.played == [], "no cue yet"
    release.set()
    t.join(5)


def test_the_microphone_check_and_the_level_feed_follow_a_recorder_that_answered(tmp_path):
    p = pipeline(tmp_path)
    started = []
    p._level_loop = lambda: started.append(1)
    seen = []
    p._mic.take_started = seen.append
    p.on_hotkey_press()
    assert wait_for(lambda: started == [1])
    assert seen == [p._current_press_id]


def test_a_press_the_recorder_could_not_start_does_not_feed_a_level_or_a_cue(tmp_path):
    p = pipeline(tmp_path, start_error=OSError("no input device"))
    started = []
    p._level_loop = lambda: started.append(1)
    p.on_hotkey_press()
    time.sleep(0.1)
    assert started == [] and p.cue_backend.played == []


# ── a recorder that fails or does not answer ─────────────────────────────────

def test_a_recorder_that_raises_undoes_the_press(tmp_path):
    p = pipeline(tmp_path, start_error=OSError("no input device"))
    p.on_hotkey_press()
    assert not p.is_recording, "otherwise every later press is 'already recording'"
    assert "hide" in p.overlay.names(), "the pill is taken down again"
    assert p.page.statuses[-1] == "error"
    assert any("did not start" in line for line in p.logged)
    assert p._wedge_recovery.told == [], "a failed start is not a wedge"


def test_a_wedged_recorder_is_not_told_to_the_recovery_a_second_time_or_called_a_missing_microphone(tmp_path):
    """The recorder tells the recovery itself, and the recovery says what is
    happening: the press only comes down."""
    import audio as audio_mod
    p = pipeline(tmp_path, start_error=audio_mod.AudioWedged("the stream did not open"))
    p.on_hotkey_press()
    time.sleep(0.2)
    assert not p.is_recording and "hide" in p.overlay.names()
    assert p._wedge_recovery.told == []
    assert p.overlay.toasts() == [], "no 'We couldn't hear you'"


def test_after_a_failed_start_the_next_press_records(tmp_path):
    p = pipeline(tmp_path, start_error=OSError("no input device"))
    p.on_hotkey_press()
    p.audio.start_error = None
    p.on_hotkey_press()
    assert p.is_recording and p.audio.starts == 2


def test_a_failed_start_puts_the_hotkeys_own_idea_of_the_take_back(tmp_path):
    p = pipeline(tmp_path, start_error=OSError("no input device"))
    resets = []
    p.hotkey_listener.reset_state = lambda: resets.append(1)
    p.on_hotkey_press()
    assert resets == [1], "a hands-free take that never began must not leave the listener recording"


def test_a_failed_start_says_the_microphone_could_not_be_heard(tmp_path):
    p = pipeline(tmp_path, start_error=OSError("no input device"))
    p.on_hotkey_press()
    assert wait_for(lambda: p.overlay.toasts(), timeout=3)
    assert p.overlay.toasts()[0]["heading"] == "We couldn't hear you"


def test_a_recorder_that_does_not_answer_in_12_seconds_is_given_up_on(tmp_path, quick):
    release = threading.Event()
    p = pipeline(tmp_path, start_hang=release)
    t0 = time.monotonic()
    t = press_on_a_thread(p)
    t.join(5)
    assert not t.is_alive(), "the press is not held for ever by the recorder"
    assert time.monotonic() - t0 < 3.0
    assert not p.is_recording
    assert "hide" in p.overlay.names()
    assert p.page.statuses[-1] == "error"
    assert wait_for(lambda: p._wedge_recovery.told == ["starting the recording"])
    release.set()


def test_the_start_deadline_is_twelve_seconds():
    assert pw.WEDGE_DEADLINE_S == 12.0
    assert "WEDGE_DEADLINE_S" in method_source("_start_audio")


def test_a_start_that_comes_back_late_does_not_leave_a_take_recording(tmp_path, quick):
    release = threading.Event()
    p = pipeline(tmp_path, start_hang=release)
    t = press_on_a_thread(p)
    t.join(5)
    release.set()                                    # the recorder finally starts
    assert wait_for(lambda: p.audio.stops == 1), "and is stopped again: nobody is recording"


def test_a_start_that_comes_back_late_leaves_a_newer_take_alone(tmp_path, quick):
    release = threading.Event()
    p = pipeline(tmp_path, start_hang=release)
    t = press_on_a_thread(p)
    t.join(5)
    p.audio.start_hang = None                        # the next press starts at once
    p.on_hotkey_press()
    assert p.is_recording
    release.set()
    time.sleep(0.2)
    assert p.audio.stops == 0, "the late start of the old press did not end the new take"
    assert p.is_recording


def test_a_release_during_a_stuck_start_still_processes_the_take(tmp_path):
    """Letting go while the recorder is stuck must not be lost: the dictation
    runs, and its stop returns whatever there is."""
    release = threading.Event()
    p = pipeline(tmp_path, start_hang=release)
    t = press_on_a_thread(p)
    assert wait_for(lambda: p.audio.starts == 1)
    p.on_hotkey_release()
    assert wait_for(lambda: p.audio.stops == 1)
    release.set()
    t.join(5)


# ── the limit's own timer ────────────────────────────────────────────────────

def test_the_timer_is_started_before_the_recorder(tmp_path, quick):
    release = threading.Event()
    p = pipeline(tmp_path, start_hang=release)
    t = press_on_a_thread(p)
    try:
        assert wait_for(lambda: p.audio.starts == 1)
        timer = p._take_timer
        assert timer is not None and timer.thread is not None and timer.thread.is_alive()
    finally:
        release.set()
        t.join(5)


def test_the_limit_stops_a_take_whose_recorder_never_answers(tmp_path, monkeypatch):
    """The owner's take of 1,760 s: the recorder never started, the level loop
    never began, and nothing stopped it."""
    monkeypatch.setattr(take_limit, "TICK_S", 0.02)
    monkeypatch.setattr(pw, "WEDGE_DEADLINE_S", 60.0)        # the recorder is still stuck at the limit
    release = threading.Event()
    p = pipeline(tmp_path, start_hang=release)
    clock = types.SimpleNamespace(t=5000.0)
    p._take_watch = lambda: take_limit.TakeWatch(5000.0, clock=lambda: clock.t)
    t = press_on_a_thread(p)
    try:
        assert wait_for(lambda: p.audio.starts == 1)
        assert p.is_recording
        clock.t = 5000.0 + 1760                              # the take has run past the limit
        assert wait_for(lambda: p.audio.stops == 1, timeout=5), "the take was stopped as the key coming up would"
        assert not p.is_recording
        assert any("auto-stopped at 20 minutes" in line for line in p.logged)
    finally:
        release.set()
        t.join(5)


def test_the_warning_comes_from_the_timer_once(tmp_path, monkeypatch):
    monkeypatch.setattr(take_limit, "TICK_S", 0.02)
    p = pipeline(tmp_path)
    clock = types.SimpleNamespace(t=5000.0)
    p._take_watch = lambda: take_limit.TakeWatch(5000.0, clock=lambda: clock.t)
    p.on_hotkey_press()
    clock.t = 5000.0 + 1140
    assert wait_for(lambda: len(p.overlay.toasts()) == 1)
    time.sleep(0.15)
    clock.t = 5000.0 + 1160
    time.sleep(0.15)
    toasts = p.overlay.toasts()
    assert len(toasts) == 1 and toasts[0]["heading"] == "Long recording"
    assert p.is_recording, "a warning stops nothing"
    p.on_hotkey_release()


def test_a_take_that_ended_normally_is_not_stopped_again(tmp_path, monkeypatch):
    monkeypatch.setattr(take_limit, "TICK_S", 0.02)
    p = pipeline(tmp_path)
    clock = types.SimpleNamespace(t=5000.0)
    p._take_watch = lambda: take_limit.TakeWatch(5000.0, clock=lambda: clock.t)
    p.on_hotkey_press()
    p.on_hotkey_release()
    assert wait_for(lambda: p.audio.stops == 1)
    clock.t = 5000.0 + 1300
    time.sleep(0.15)
    assert p.audio.stops == 1
    assert not any("auto-stopped" in line for line in p.logged)


def test_a_newer_take_replaces_the_older_ones_timer(tmp_path, monkeypatch):
    monkeypatch.setattr(take_limit, "TICK_S", 0.02)
    p = pipeline(tmp_path)
    p.on_hotkey_press()
    first = p._take_timer
    p.on_hotkey_release()
    p.on_hotkey_press()
    assert p._take_timer is not first
    assert wait_for(lambda: first.finished)


def test_the_level_loop_no_longer_decides_the_limit():
    loop = method_source("_level_loop")
    assert "_take_limit." not in loop and "_take_watch" not in loop
    assert "_arm_take_timer" in method_source("on_hotkey_press")


def test_the_timer_and_the_pill_are_started_in_the_press_before_the_recorder_is_asked():
    press = method_source("on_hotkey_press")
    assert press.index("self._arm_take_timer(") < press.index("self._start_audio(")
    assert press.index("self._show_recording_pill") < press.index("self._start_audio(")
    assert "self.overlay.show()" in method_source("_show_recording_pill")


# ── telling the wedge recovery ───────────────────────────────────────────────

def test_the_wedge_goes_to_the_recovery(tmp_path):
    p = pipeline(tmp_path)
    p._on_audio_wedge("stream.stop")
    assert p._wedge_recovery.told == ["stream.stop"]


def test_a_recovery_that_raises_is_not_raised_into_the_audio_thread(tmp_path):
    p = pipeline(tmp_path)

    def boom(op):
        raise RuntimeError("no")
    p._wedge_recovery.handle = boom
    p._on_audio_wedge("stream.stop")
    assert any("recovery" in line.lower() for line in p.logged)


def test_a_recorder_that_does_not_stop_in_time_is_a_wedge(tmp_path, monkeypatch):
    from _pipeline_harness import Hang, run_process
    H.fast_limits(monkeypatch, PREPARE_DEADLINE_S=0.4)
    hang = Hang(30)
    p = pipeline(tmp_path)
    p.audio = FakeAudio(speech_wav(3.0), hang=hang)
    worker = run_process(p)
    worker.join(3)
    assert wait_for(lambda: p._wedge_recovery.told == ["stopping the recording"])
    hang.release.set()


def test_a_recorder_that_stops_normally_is_not_a_wedge(tmp_path):
    from _pipeline_harness import run_process
    p = pipeline(tmp_path)
    p.is_recording = True
    run_process(p).join(10)
    assert p._wedge_recovery.told == []


# ── the recovery's hooks, as the app builds them ─────────────────────────────

def built(tmp_path, **audio):
    p = pipeline(tmp_path, **audio)
    return p, p._make_wedge_recovery()


def test_the_recovery_is_built_for_this_platform_and_this_executable(tmp_path, monkeypatch):
    p, rec = built(tmp_path)
    assert isinstance(rec, wedge_recovery.WedgeRecovery)
    assert rec._state_path == tmp_path / wedge_recovery.FILE
    assert rec._frozen is bool(getattr(sys, "frozen", False))
    assert rec._executable == sys.executable


def test_what_was_being_recorded_is_kept_as_not_sent(tmp_path):
    p, rec = built(tmp_path)
    assert rec._keep(speech_wav(3.0)) is True
    [wav] = list((tmp_path / "unsent").glob("*.wav"))
    assert wav.stat().st_size > 44
    [entry] = H.history(p)
    assert entry["failed"] is True and entry["not_sent_reason"] == "stuck"
    assert entry["will_retry"] is True, "it is sent again by itself when Waffler is back"


def test_a_take_with_no_speech_in_it_is_not_kept_as_a_card(tmp_path):
    from test_processing_watchdog import _silent_wav
    p, rec = built(tmp_path)
    assert rec._keep(_silent_wav(2.0)) is True, "nothing was lost"
    assert not (tmp_path / "unsent").exists() and H.history(p) == []


def test_a_take_in_progress_means_either_flag(tmp_path):
    p, rec = built(tmp_path)
    assert rec._take() is False
    p.is_recording = True
    assert rec._take() is True
    p.is_recording = False
    p.audio.is_recording = True
    assert rec._take() is True


def test_busy_means_a_dictation_is_being_turned_into_text(tmp_path):
    p, rec = built(tmp_path)
    assert rec._busy() is False
    run = p._watchdog.begin(5)
    try:
        assert rec._busy() is True
    finally:
        p._watchdog.end(run, pw.DONE)


def test_the_salvage_reads_the_recorders_buffer(tmp_path):
    p, rec = built(tmp_path)
    p.audio.salvage = b"RIFFbuffer"
    assert rec._salvage() == b"RIFFbuffer"
    p.audio.salvage = None
    assert rec._salvage() is None


def test_the_message_goes_through_the_notifier_and_the_pill(tmp_path):
    p, rec = built(tmp_path)
    rec._show("Waffler is restarting", "macOS's audio system stopped answering, so Waffler is restarting itself. "
                                      "Anything you were recording is kept under Not sent.")
    [toast] = p.overlay.toasts()
    assert toast["heading"] == "Waffler is restarting" and toast["style"] == "warn"
    assert "—" not in toast["body"]
    assert toast["timeout"] >= 8, "it stays up if Waffler cannot restart itself"


def test_a_message_the_notifier_holds_back_is_not_shown(tmp_path):
    """The notifier decides, like for every other message (the restart itself does not
    wait on it: see tests/test_wedge_recovery.py)."""
    p, rec = built(tmp_path)
    p._toast_allowed = lambda kind: False
    rec._show("a", "b")
    assert p.overlay.toasts() == []


def test_the_logs_name_the_call_and_never_the_words(tmp_path):
    p, rec = built(tmp_path)
    rec._keep(speech_wav(3.0))
    joined = " ".join(p.logged)
    assert "ok so ship it on monday" not in joined


def test_a_wedge_is_only_confirmed_while_the_recorder_is_still_stuck(tmp_path):
    p, rec = built(tmp_path)
    p.audio.stuck = True
    assert rec._still_stuck() is True
    p.audio.stuck = False
    assert rec._still_stuck() is False


def test_a_false_alarm_clears_the_recorders_and_the_guards_memory_of_it(tmp_path):
    import audio_guard
    p, rec = built(tmp_path)
    audio_guard.report_wedge("stream.stop", 3.0)
    rec._on_cleared()
    assert p.audio.cleared == 1
    assert audio_guard.reported() is None, "a later wedge is logged and told again"
