"""Sound cues in the dictation pipeline: when each one sounds, and that none
of them can hurt a dictation.

The start cue sounds once the microphone is live, the stop cue once the
recording has been captured (not before: it would be in the recording), the
lock tick when Space locks hands-free. All three are off unless the Sounds
setting is on, each tells the recorder to ignore its moments first, and a
cue that cannot play changes nothing. The pure parts are in
tests/test_sounds.py; that the microphone really does not hear one is in
tests/test_cue_never_recorded.py.
"""
import ast
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import sounds as S  # noqa: E402
from _pipeline_harness import history, make_pipeline, run_process, wait_for  # noqa: E402


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    H.fast_limits(monkeypatch)


def sounds_on(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"sounds": True}), encoding="utf-8")


def dictate(p):
    t = run_process(p, 1)
    t.join(10)
    assert not t.is_alive()
    return p


def log_order(p):
    """Record what happens, in order, across the recorder and the speakers."""
    events = []
    real_stop = p.audio.stop
    p.audio.stop = lambda: events.append("stop") or real_stop()
    p.audio.deafen_for = lambda seconds: events.append(("deafen", round(seconds, 3)))
    p.cue_backend.play = lambda name, wav: events.append(("play", name))
    return events


def test_by_default_a_dictation_makes_no_sound_and_silences_nothing(tmp_path):
    p = make_pipeline(tmp_path)
    p = dictate(p)
    assert p.cue_backend.played == []
    assert p.audio.deafened == []
    assert p.clipboard.pastes, "and it was an ordinary dictation"


def test_with_sounds_on_the_stop_cue_comes_after_the_recording_is_captured(tmp_path):
    sounds_on(tmp_path)
    p = make_pipeline(tmp_path)
    events = log_order(p)
    dictate(p)
    assert events[0] == "stop", "the recorder is stopped first"
    window = S.mask_seconds(S.STOP, p.cue_backend.latency_s)
    assert events[1:3] == [("deafen", round(window, 3)), ("play", S.STOP)], \
        "then told to ignore the cue's moments, then the cue"
    assert [e for e in events if isinstance(e, tuple) and e[0] == "play"] == [("play", S.STOP)]
    assert p.clipboard.pastes, "and the words were pasted as usual"


def test_a_newer_press_means_no_stop_cue_over_its_recording(tmp_path):
    sounds_on(tmp_path)
    p = make_pipeline(tmp_path)
    events = log_order(p)
    p._processing_id = 2                  # a second press has started recording
    p.is_recording = True
    run_process(p, 1).join(10)
    assert not [e for e in events if isinstance(e, tuple)], "nothing sounded, nothing silenced"


def test_a_recording_in_progress_is_not_given_a_stop_cue_either(tmp_path):
    sounds_on(tmp_path)
    p = make_pipeline(tmp_path)
    events = log_order(p)
    p.is_recording = True
    dictate(p)
    assert not [e for e in events if isinstance(e, tuple)]


def test_a_cue_that_fails_to_play_does_not_touch_the_dictation(tmp_path):
    sounds_on(tmp_path)
    p = make_pipeline(tmp_path)

    def broken(name, wav):
        raise RuntimeError("no output device: SECRET WORDS")
    p.cue_backend.play = broken
    dictate(p)
    assert p.clipboard.pastes and history(p)[0]["styled"]
    assert p.page.statuses[-1] == "done"
    assert any("cue not played (RuntimeError)" in line for line in p.logged)
    assert not any("SECRET" in line for line in p.logged)
    assert p.overlay.toasts() == [], "and nobody is told about it"


def test_a_recorder_that_cannot_be_told_to_ignore_gets_no_cue(tmp_path):
    sounds_on(tmp_path)
    p = make_pipeline(tmp_path)
    p.audio = type("Plain", (), {"stop": lambda self: H.speech_wav(3.0),
                                 "force_rebuild": lambda self: None})()
    dictate(p)
    assert p.cue_backend.played == []
    assert p.clipboard.pastes


def test_the_lock_tick_sounds_when_hands_free_locks(tmp_path):
    sounds_on(tmp_path)
    p = make_pipeline(tmp_path)
    p._current_press_id = 7
    events = log_order(p)
    p._on_hands_free_lock()
    assert p._hands_free_press_id == 7
    assert events == [("deafen", round(S.mask_seconds(S.LOCK, p.cue_backend.latency_s), 3)),
                      ("play", S.LOCK)]


def test_the_lock_is_remembered_even_with_sounds_off(tmp_path):
    """The hint needs to know a take locked, whether or not it has a sound."""
    p = make_pipeline(tmp_path)
    p._current_press_id = 4
    p._on_hands_free_lock()
    assert p._hands_free_press_id == 4 and p.cue_backend.played == []


def test_turning_sounds_off_in_settings_applies_to_the_next_cue(tmp_path):
    sounds_on(tmp_path)
    p = make_pipeline(tmp_path)
    events = log_order(p)
    p._play_cue(S.STOP)
    assert ("play", S.STOP) in events
    (tmp_path / "settings.json").write_text(json.dumps({"sounds": False}), encoding="utf-8")
    events.clear()
    p._play_cue(S.STOP)
    assert events == []


# ── in app.py ────────────────────────────────────────────────────────────

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_PIPE = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")


def _method(name):
    return next(n for n in _PIPE.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_start_cue_follows_audio_start_and_comes_before_the_pill():
    src = ast.unparse(_method("on_hotkey_press"))
    assert src.index("self.audio.start()") < src.index("self._play_cue(_sound_cues.START)") \
        < src.index("self.overlay.show()")


def test_the_stop_cue_is_after_the_recorder_returned_and_before_anything_slow():
    src = ast.unparse(_method("_process"))
    stopped = src.index("audio_bytes = _stopped.value")
    cue = src.index("self._play_cue(_sound_cues.STOP)")
    assert stopped < cue < src.index("_speech_seconds(audio_bytes)")
    assert "not _is_superseded() and (not self.is_recording)" in src


def test_both_listeners_are_given_the_lock_callback_wherever_they_are_made():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert src.count("on_lock=self._on_hands_free_lock") == 2
    assert src.count("on_lock=_pipeline._on_hands_free_lock") == 2


def test_the_listeners_call_it_when_space_locks():
    for name, needle in (("smart_hotkey.py", "self._fire_lock()"), ("windows_hotkey.py", "self._fire_lock()")):
        src = (ROOT / "src" / name).read_text(encoding="utf-8")
        assert "on_lock=None" in src and needle in src, name
    mac = (ROOT / "src" / "smart_hotkey.py").read_text(encoding="utf-8")
    assert mac.index('self._fire_visual_feedback("sticky_on")') < mac.index("self._fire_lock()")


def _lift_methods(path, cls, names, **ns):
    """Methods of a listener class, lifted so no event tap or hook is made."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    nodes = [n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    ns.setdefault("threading", threading)
    exec(compile(ast.Module(nodes, []), str(path), "exec"), ns)
    return ns


def test_the_mac_listener_calls_on_lock_once_when_space_locks_and_not_on_unlock():
    ns = _lift_methods(ROOT / "src" / "smart_hotkey.py", "SmartHotkeyListener",
                       ("_on_space_press", "_fire_lock"))
    calls, done = [], threading.Event()
    ln = type("L", (), {})()
    ln._hotkey_held, ln._recording, ln._sticky = True, True, False
    ln._on_lock_cb = lambda: (calls.append("lock"), done.set())
    ln._fire_visual_feedback = lambda e: None
    ln._fire_release = lambda: None
    ln._fire_lock = ns["_fire_lock"].__get__(ln)
    ns["_on_space_press"].__get__(ln)()
    assert done.wait(2) and calls == ["lock"] and ln._sticky is True
    ns["_on_space_press"].__get__(ln)()          # Space again: unlock, not another lock
    assert calls == ["lock"] and ln._sticky is False


def test_a_listener_with_no_callback_locks_quietly():
    ns = _lift_methods(ROOT / "src" / "smart_hotkey.py", "SmartHotkeyListener", ("_fire_lock",))
    ln = type("L", (), {})()
    ln._on_lock_cb = None
    ns["_fire_lock"].__get__(ln)()               # nothing to call, nothing raised


def test_the_windows_listener_calls_on_lock_when_it_enters_hands_free():
    ns = _lift_methods(ROOT / "src" / "windows_hotkey.py", "WindowsHotkeyListener",
                       ("_enter_sticky", "_fire_lock", "_safe_callback"),
                       _State=type("S", (), {"STICKY": "sticky"}), _log=lambda m: None)
    calls, done = [], threading.Event()
    ln = type("L", (), {})()
    ln._on_lock = lambda: (calls.append("lock"), done.set())
    ln._safe_callback = ns["_safe_callback"]
    ln._fire_lock = ns["_fire_lock"].__get__(ln)
    ns["_enter_sticky"].__get__(ln)()
    assert done.wait(2) and calls == ["lock"] and ln._state == "sticky"
