"""Small microphone fixes from the audit (L6, L10): a PortAudio restart that
fails once, a sound cue that leaks, a preference saved while a stream is being
made, the device monitor's query, and the last microphone being unplugged.

No microphone is opened: ``audio.sd`` is a fake.
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import audio as audio_mod  # noqa: E402
import audio_device_monitor as adm  # noqa: E402
import mic_devices as md  # noqa: E402
import mic_ranking as mr  # noqa: E402
from audio import AudioRecorder  # noqa: E402
from test_mic_capture_health import FakeSD, block, feed  # noqa: E402


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSD()
    monkeypatch.setattr(audio_mod, "sd", fake)
    monkeypatch.setattr(audio_mod, "_HAL_DRAIN_S", 0)
    monkeypatch.setattr(audio_mod, "_POSTROLL_MS", 0)
    return fake


# ── L6: one failed PortAudio restart does not leave the microphone dead ──────

def test_portaudio_is_started_again_even_when_stopping_it_fails(sd):
    """After a failed start, every later stop raises "not initialised"; the
    start used to share one try with it and was never retried."""
    def refuse():
        sd.log.append("terminate")
        raise RuntimeError("PortAudio not initialized")
    sd._terminate = refuse
    rec = AudioRecorder()
    rec.start_monitoring()
    assert sd.log.count("initialize") == 1, "the start was skipped because the stop failed"
    assert rec._stream is not None and rec._stream.active


def test_a_start_that_fails_is_tried_again_at_the_next_stream(sd):
    outcomes = iter([RuntimeError("coreaudiod is restarting"), None])
    tries = []

    def flaky():
        tries.append(1)
        failure = next(outcomes)
        if failure:
            raise failure
    sd._initialize = flaky
    rec = AudioRecorder()
    rec.start_monitoring()                       # the restart fails: the stream is still made, on what there is
    assert rec._stream is not None
    rec.stop_monitoring()
    rec.start_monitoring()                       # and the next stream tries the restart again
    assert len(tries) == 2 and sd.log.count("terminate") == 2


def test_both_steps_are_separate_in_the_source():
    import ast
    tree = ast.parse((ROOT / "src" / "audio.py").read_text(encoding="utf-8"))
    create = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_create_stream")
    for node in ast.walk(create):
        if isinstance(node, ast.Try):
            body = ast.unparse(ast.Module(node.body, []))
            assert not ("sd._terminate()" in body and "sd._initialize()" in body), \
                "stopping and starting PortAudio share a try"


# ── L10: a sound cue added while a chunk is being read stays ─────────────────

def test_a_cue_window_added_during_a_chunk_is_not_wiped_by_it(sd, monkeypatch):
    rec = AudioRecorder()
    now = [1000.0]
    rec._clock = lambda: now[0]
    rec._deaf = ((900.0, 901.0),)                # long over: this chunk finds nothing live
    real = audio_mod.cue_mask_range

    def mask_then_a_cue_starts(*args, **kwargs):
        rec.deafen_for(0.5)                      # another thread arms a cue just now
        return real(*args, **kwargs)
    monkeypatch.setattr(audio_mod, "cue_mask_range", mask_then_a_cue_starts)
    rec._silence_cue(block(100))
    assert len(rec._deaf) == 1 and rec._deaf[0][0] >= 1000.0, "the new cue's window was thrown away"


def test_an_expired_cue_window_is_still_cleared(sd):
    rec = AudioRecorder()
    rec._clock = lambda: 1000.0
    rec._deaf = ((900.0, 901.0),)
    rec._silence_cue(block(100))
    assert rec._deaf == ()


# ── L10: a preference saved while the resolver runs is not lost ──────────────

def test_a_preference_saved_while_a_stream_is_being_made_makes_it_stale(sd):
    rec = AudioRecorder(device_resolver=lambda: (rec.note_preference_changed(), None)[1])
    rec.start_monitoring()
    assert rec._stream_pref_version != rec._pref_version, \
        "the stream was taken for one made after the change, but it was chosen before it"
    rec.start()                                  # the next take opens it again
    assert len(sd.streams) == 2


def test_a_stream_made_with_no_change_in_between_is_current(sd):
    rec = AudioRecorder()
    rec.note_preference_changed()
    rec.start_monitoring()
    assert rec._stream_pref_version == rec._pref_version


# ── L10: the device monitor ──────────────────────────────────────────────────

def test_the_monitors_fallback_query_waits_for_the_stream_lock(monkeypatch):
    seen = []
    monkeypatch.setattr(adm, "_HAS_SD", True)
    fake = FakeSD()
    monkeypatch.setattr(audio_mod, "sd", fake)
    real_query = fake.query_devices

    def spying(device=None, kind=None):
        seen.append(audio_mod._STREAM_LOCK.locked())
        return real_query(device, kind)
    fake.query_devices = spying
    m = adm.AudioDeviceMonitor(on_change=lambda *a: None, log_fn=lambda m: None, reader=lambda: None)
    assert m._current_default_name() == "MacBook Pro Microphone"
    assert seen and all(seen), "PortAudio was queried without the stream lock held"


def test_the_monitors_fallback_query_gives_up_while_a_stream_is_being_made(monkeypatch):
    monkeypatch.setattr(audio_mod, "sd", FakeSD())
    monkeypatch.setattr(adm.AudioDeviceMonitor, "QUERY_WAIT_S", 0.05)
    m = adm.AudioDeviceMonitor(on_change=lambda *a: None, log_fn=lambda m: None, reader=lambda: None)
    held, done = threading.Event(), threading.Event()

    def hold():
        with audio_mod._STREAM_LOCK:
            held.set()
            done.wait(5)
    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert held.wait(5)
    started = time.monotonic()
    assert m._current_default_name() is None, "a stream is being made: not now"
    assert time.monotonic() - started < 2.0
    done.set()
    t.join(5)


def monitor(answers, source="live"):
    polls = iter(answers)
    seen, log = [], []
    m = adm.AudioDeviceMonitor(on_change=lambda o, n: None, log_fn=log.append, on_devices=seen.append)
    m._source = source
    m._read_devices = lambda: next(polls)
    return m, seen, log


BUILTIN = md.Device("MacBook Pro Microphone", is_default=True)


def test_unplugging_the_last_microphone_is_reported_when_the_system_says_so():
    m, seen, _ = monitor([[BUILTIN], []], source="live")
    m._current_default_name = lambda: None
    m._tick()
    m._tick()
    assert [len(s) for s in seen] == [1, 0], "an empty list from CoreAudio is 'there are none'"


def test_an_empty_list_from_portaudio_is_still_not_taken_for_none():
    m, seen, _ = monitor([[BUILTIN], []], source="portaudio")
    m._current_default_name = lambda: None
    m._tick()
    m._tick()
    assert [len(s) for s in seen] == [1], "PortAudio answers [] when it cannot say"


def test_the_supervisor_sees_the_last_microphone_leave():
    import mic_supervisor as ms
    h = ms.Hooks(settings=lambda: {}, recording=lambda: False, busy=lambda: False, toast_allowed=lambda k: True,
                 show_toast=lambda *a, **k: None, hide_toast=lambda *a, **k: None, release_toast=lambda k: None,
                 health=lambda: {}, reopen=lambda: True, opened_device=lambda: "Yeti", stop_take=lambda: None,
                 ranking=lambda: [{"name": "Yeti"}], promote=lambda e: None, note_changed=lambda: None,
                 rebuild_idle=lambda: None, memory=__import__("mic_hotplug").DeviceMemory(), log=lambda m: None,
                 platform="darwin", clock=time.monotonic)
    sup = ms.MicSupervisor(h)
    sup.on_devices([md.Device("Yeti", is_default=True)])
    sup.on_devices([])
    assert sup._device_present("Yeti") is False


# ── L10: the setup practice keeps the microphone it was given by name ────────

def test_the_index_the_screen_sends_is_turned_into_a_name_now():
    devices = [md.Device("MacBook Pro Microphone", index=0), md.Device("Yeti", index=3)]
    assert mr.name_at_index(3, devices) == "Yeti"
    assert mr.name_at_index(2, devices) is None
    assert mr.name_at_index(None, devices) is None and mr.name_at_index(True, devices) is None
    assert mr.name_at_index("3", devices) is None


def test_the_pick_leads_the_ranking_and_nothing_else_is_lost():
    stored = {"mic_ranking": [{"name": "Scarlett"}, {"name": "Yeti"}], "language": "en"}
    got = mr.with_pick_first(stored, "Yeti")
    assert [e["name"] for e in got["mic_ranking"]] == ["Yeti", "Scarlett"] and got["language"] == "en"
    assert [e["name"] for e in stored["mic_ranking"]] == ["Scarlett", "Yeti"], "the settings are not changed"
    assert mr.with_pick_first(stored, None) == stored and mr.with_pick_first(None, "") == {}
    assert mr.with_pick_first({}, "Yeti")["mic_ranking"] == [{"name": "Yeti"}]


def test_the_practice_recorder_follows_the_pick_when_the_list_is_renumbered():
    names = ["MacBook Pro Microphone", "USB Headset", "Yeti"]

    def listed():
        infos = [{"name": n, "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0, "index": i}
                 for i, n in enumerate(names)]
        return md.devices_from_portaudio(infos, [{"name": "Core Audio"}], 0, 0)
    resolver = mr.open_resolver(lambda: mr.with_pick_first({}, "Yeti"), pa_reader=listed, live_reader=lambda: None)
    assert resolver() == 2
    names.insert(0, "New Interface")                 # everything after it is numbered one higher
    assert resolver() == 3, "found again by name, not by the old index"


def test_the_setup_practice_does_not_pin_an_index_on_the_recorder():
    import ast
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "wizard_start_hotkey_test")
    for call in ast.walk(fn):
        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "AudioRecorder":
            kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
            assert kw["device_index"] == "None", "the practice pinned a PortAudio index"
            assert "with_pick_first" in kw["device_resolver"]
