"""The microphone work as app.py wires it (S12): the real recording loop, key
handlers and toast actions from app.py, run in the pipeline harness with a
real MicSupervisor and a fake recorder. No microphone, no overlay process.

What is checked here is the seams: the loop asks the supervisor once a tick,
a microphone that left ends the take through the one atomic stop the key uses
(so it is turned into text once, even racing the key), the notice comes after
the words are in, the "We couldn't hear you" message is not said twice, and
the new toast actions reach the supervisor.
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

import mic_devices as md  # noqa: E402
import mic_supervisor as ms  # noqa: E402
import mic_watch as mw  # noqa: E402
import take_limit  # noqa: E402
from _pipeline_harness import fast_limits, history, make_pipeline, wait_for  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
HEARD = "Your microphone was disconnected"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=3.0, TRANSCRIBE_DEADLINE_BASE_S=3.0,
                TRANSCRIBE_DEADLINE_MAX_S=3.0, STYLE_DEADLINE_S=3.0)


class Mic:
    """What the fake recorder says about its input; tests change it."""

    def __init__(self):
        self.state = {"recording": True, "reopening": False, "stream_ok": True,
                      "quiet_s": 0.0, "stall_s": 0.0, "device": "Fake Microphone"}
        self.reopened = 0

    def health(self, now=None):
        return dict(self.state)

    def reopen_stream(self):
        self.reopened += 1
        return True


def rig(tmp_path, **kw):
    """A pipeline that is recording press 1, with the real level loop on it."""
    p = make_pipeline(tmp_path, **kw)
    mic = Mic()
    p.audio.health = mic.health
    p.audio.reopen_stream = mic.reopen_stream
    p._level_loop = types.MethodType(p.ns["_level_loop"], p)
    p._take_watch = lambda: take_limit.TakeWatch(time.time())
    p.is_recording = True
    p._mic.take_started(1)
    p.mic = mic
    return p


def start_loop(p):
    t = threading.Thread(target=p._level_loop, daemon=True)
    t.start()
    return t


def finished(p):
    return wait_for(lambda: not p._watchdog.active() and bool(history(p)), timeout=8)


def lost_toasts(p):
    return [t for t in p.overlay.toasts() if t["heading"] == HEARD]


# ── the microphone leaves in the middle of a take ────────────────────────

def test_a_stream_that_stops_ends_the_take_and_it_is_turned_into_text_once(tmp_path):
    p = rig(tmp_path)
    p.mic.state["stream_ok"] = False
    t = start_loop(p)
    assert finished(p)
    t.join(3)
    assert not t.is_alive(), "the loop ends with the take"
    assert p.is_recording is False
    assert p.transcriber.calls == 1 and len(p.clipboard.pastes) == 1
    assert len(history(p)) == 1


def test_the_notice_comes_after_the_words_are_in_and_says_so_plainly(tmp_path):
    p = rig(tmp_path)
    p.mic.state["stream_ok"] = False
    start_loop(p)
    assert finished(p)
    assert wait_for(lambda: len(lost_toasts(p)) == 1)
    toast = lost_toasts(p)[0]
    assert toast["body"] == "Waffler used what it had recorded."
    assert "end_working" in p.overlay.names()
    assert p.overlay.names().index("end_working") < p.overlay.names().index("show_toast")
    assert p.clipboard.pastes, "the words were pasted before the notice"
    time.sleep(0.2)
    assert len(lost_toasts(p)) == 1, "said once"


def test_a_microphone_that_leaves_while_the_key_is_let_go_races_safely(tmp_path):
    """The loop's stop and the key coming up are both on_hotkey_release, which
    is one atomic check: over many tries, a take is turned into text exactly
    once and the person is told once."""
    for n in range(15):
        p = rig(tmp_path / f"t{n}")
        p.mic.state["stream_ok"] = False
        p._mic._watch._stream_bad_since = time.monotonic() - 10      # the stop is due on the next tick
        barrier = threading.Barrier(2)

        def key_up():
            barrier.wait(2)
            p.on_hotkey_release()

        def loop():
            barrier.wait(2)
            p._level_loop()
        a = threading.Thread(target=key_up, daemon=True)
        b = threading.Thread(target=loop, daemon=True)
        a.start()
        b.start()
        a.join(3)
        b.join(3)
        assert finished(p), f"try {n}"
        time.sleep(0.05)
        assert p.transcriber.calls == 1, f"try {n}: processed {p.transcriber.calls} times"
        assert len(p.clipboard.pastes) == 1 and len(history(p)) == 1


def test_a_take_with_nothing_usable_in_it_gets_the_old_message_and_not_the_disconnected_notice(tmp_path):
    """The microphone left before anything was recorded: the audio is digital
    silence, and the message that says so (the dead-stream one) is the one
    shown. "Waffler used what it had recorded" would not be true."""
    from _pipeline_harness import silence_wav
    p = rig(tmp_path, wav=silence_wav(3.0))
    p.mic.state["stream_ok"] = False
    start_loop(p)
    assert wait_for(lambda: any(t["heading"] == "Mic reset" for t in p.overlay.toasts()), timeout=8)
    assert wait_for(lambda: not p._watchdog.active())
    time.sleep(0.3)
    assert lost_toasts(p) == []
    assert p.transcriber.calls == 0 and p.clipboard.pastes == []


def test_a_take_that_ended_before_the_loss_is_seen_gets_no_notice(tmp_path):
    p = rig(tmp_path)
    p.on_hotkey_release()                                   # the key came up first
    assert finished(p)
    p.mic.state["stream_ok"] = False
    t = start_loop(p)
    t.join(3)                                               # the loop sees no recording and ends
    time.sleep(0.2)
    assert lost_toasts(p) == []


def test_a_healthy_take_is_left_alone(tmp_path):
    p = rig(tmp_path)
    t = start_loop(p)
    time.sleep(0.4)
    p.is_recording = False
    t.join(3)
    assert p.mic.reopened == 0 and p.overlay.toasts() == []


# ── the silence watchdog through the loop ────────────────────────────────

def test_five_seconds_of_silence_reopens_the_stream_through_the_loop(tmp_path):
    p = rig(tmp_path)
    p.mic.state.update(quiet_s=5.2, stall_s=0.0)
    t = start_loop(p)
    assert wait_for(lambda: p.mic.reopened == 1)
    time.sleep(0.2)
    assert p.mic.reopened == 1
    p.is_recording = False
    t.join(3)


def test_fifteen_seconds_of_silence_says_so_once_and_the_later_message_stays_quiet(tmp_path):
    p = rig(tmp_path)
    p.mic.state.update(quiet_s=5.2)
    t = start_loop(p)
    assert wait_for(lambda: p.mic.reopened == 1)
    time.sleep(1.7)                                          # the settle after the re-open
    p.mic.state.update(quiet_s=15.5)
    assert wait_for(lambda: any(t_["heading"] == "Waffler can't hear your microphone"
                                for t_ in p.overlay.toasts()), timeout=5)
    toast = [t_ for t_ in p.overlay.toasts() if t_["heading"] == "Waffler can't hear your microphone"][0]
    assert toast["buttons"][0]["action"] == "choose_microphone"
    # The take ends and finds only silence: the message that follows is not said again.
    p.is_recording = False
    t.join(3)
    shown_before = len(p.overlay.toasts())
    p._current_press_id = 1
    p._show_no_audio_toast()
    assert len(p.overlay.toasts()) == shown_before


def test_without_the_early_message_the_old_one_is_still_said(tmp_path, monkeypatch):
    p = rig(tmp_path)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    p._current_press_id = 1
    p._show_no_audio_toast()
    assert [t["heading"] for t in p.overlay.toasts()] == ["We couldn't hear you"]


# ── the key going down starts the check ──────────────────────────────────

def test_the_key_going_down_starts_the_takes_check_with_its_press_id(tmp_path):
    p = make_pipeline(tmp_path)
    seen = []
    p._mic = types.SimpleNamespace(take_started=seen.append, tick=lambda press: None,
                                   announce_lost=lambda press, produced=True: None,
                                   told_cant_hear=lambda press: False)
    p.is_recording = False
    p.on_hotkey_press()
    assert seen == [p._current_press_id]


def test_a_supervisor_that_is_not_there_changes_nothing(tmp_path):
    """The seams are guarded: a pipeline without one (a test's, or a failed
    start) records and finishes exactly as before."""
    p = make_pipeline(tmp_path)
    del p._mic
    p.is_recording = True
    p.on_hotkey_release()
    assert finished(p) and len(p.clipboard.pastes) == 1


# ── toast actions ────────────────────────────────────────────────────────

class Window:
    def __init__(self):
        self.js = []

    def show(self):
        pass

    def evaluate_js(self, code):
        self.js.append(code)


def test_choose_microphone_opens_settings_at_the_microphone(tmp_path):
    p = make_pipeline(tmp_path)
    p.ns["_window"] = win = Window()
    p._on_toast_action("choose_microphone")
    assert len(win.js) == 1 and "waffler_show_microphone" in win.js[0]
    p._on_toast_action("select_mic")
    assert len(win.js) == 2 and "waffler_show_microphone" in win.js[1]


def test_the_use_it_answer_reaches_the_supervisor_off_the_overlays_thread(tmp_path):
    p = make_pipeline(tmp_path)
    got = []
    done = threading.Event()
    where = []

    def answer(action):
        got.append(action)
        where.append(threading.current_thread() is threading.main_thread())
        done.set()
        return True
    p._mic = types.SimpleNamespace(answer=answer)
    p._on_toast_action("mic_use:abc123")
    assert done.wait(3) and got == ["mic_use:abc123"] and where == [False]
    done.clear()
    p._on_toast_action("mic_later:abc123")
    assert done.wait(3) and got[-1] == "mic_later:abc123"
    assert "hide_toast" in p.overlay.names()


# ── the monitor's list, a saved ranking, delete my data ──────────────────

def test_the_monitors_list_reaches_the_supervisor_and_a_new_microphone_is_asked_about(tmp_path):
    p = make_pipeline(tmp_path)
    builtin = md.Device("MacBook Pro Microphone", is_default=True)
    yeti = md.Device("Yeti Stereo Microphone", uid="UY")
    p.on_mic_devices([builtin])
    p.on_mic_devices([builtin, yeti])
    assert wait_for(lambda: any(t["heading"] == "Yeti Stereo Microphone detected"
                                for t in p.overlay.toasts()))
    toast = p.overlay.toasts()[-1]
    assert [b["label"] for b in toast["buttons"]] == ["Use it", "Not now"]


def test_no_prompt_while_recording_and_the_prompt_waits_for_the_take_to_end(tmp_path):
    p = make_pipeline(tmp_path)
    builtin, yeti = md.Device("MacBook Pro Microphone", is_default=True), md.Device("Yeti")
    p.on_mic_devices([builtin])
    p.is_recording = True
    p.on_mic_devices([builtin, yeti])
    time.sleep(0.1)
    assert p.overlay.toasts() == []
    p.is_recording = False
    p.on_mic_devices([builtin, yeti])
    assert wait_for(lambda: len(p.overlay.toasts()) == 1)


def test_tips_off_means_no_prompt(tmp_path):
    p = make_pipeline(tmp_path)
    (p.ns["DATA_DIR"] / "settings.json").write_text('{"tips": false}', encoding="utf-8")
    builtin, yeti = md.Device("MacBook Pro Microphone", is_default=True), md.Device("Yeti")
    p.on_mic_devices([builtin])
    p.on_mic_devices([builtin, yeti])
    time.sleep(0.1)
    assert p.overlay.toasts() == []


def test_a_saved_ranking_makes_the_next_dictation_use_it(tmp_path):
    p = make_pipeline(tmp_path)
    noted = []
    p.audio.note_preference_changed = lambda: noted.append(1)
    p.apply_mic_preferences()
    assert noted == [1]


def test_delete_my_data_forgets_which_microphones_were_asked_about(tmp_path):
    p = make_pipeline(tmp_path)
    builtin, yeti = md.Device("MacBook Pro Microphone", is_default=True), md.Device("Yeti")
    p.on_mic_devices([builtin])
    p.on_mic_devices([builtin, yeti])
    assert wait_for(lambda: len(p.overlay.toasts()) == 1)
    memory = p._mic.h.memory
    assert memory.decision(yeti) == "seen" and memory.path().exists()
    p.forget_notifier()
    assert memory.decision(yeti) is None and not memory.path().exists()


def test_the_hooks_use_the_pipelines_own_things(tmp_path):
    p = make_pipeline(tmp_path)
    h = p._mic.h
    assert h.platform == "win32"                              # the harness fakes Windows
    assert h.health()["device"] == "Fake Microphone" and h.opened_device() == "Fake Microphone"
    assert h.reopen() is True
    p.is_recording = True
    assert h.recording() is True
    h.rebuild_idle()                                          # during a take: nothing is touched
    assert h.ranking() == []
