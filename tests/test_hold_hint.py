"""The "Don't tap, hold" hint: a quiet tip when a quick tap of the hotkey
was discarded as too short.

Waffler throws away a press under half a second that caught no speech. Until
now it did so silently, which leaves a new user wondering why nothing
happened. The hint says what to do, once every 12 hours at most, naming the
person's own hotkey, with "Don't show again". The pure pieces are in
src/hold_hint.py; the pipeline pieces run through the same harness as the
dictation tests.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import hold_hint as H  # noqa: E402
import notifier as N  # noqa: E402
import user_messages as um  # noqa: E402
import _pipeline_harness as P  # noqa: E402


# ── the pure part ────────────────────────────────────────────────────────

@pytest.mark.parametrize("duration,hands_free,expect", [
    (0.05, False, True),
    (0.49, False, True),
    (0.50, False, False),       # half a second is a press, not a tap
    (2.0, False, False),
    (0.20, True, False),        # it locked hands-free: not a tap at all
])
def test_which_presses_get_the_hint(duration, hands_free, expect):
    assert H.wants_hint(duration, hands_free=hands_free) is expect


def test_a_duration_that_makes_no_sense_gets_no_hint():
    for bad in (None, "x", float("nan"), -1):
        assert H.wants_hint(bad, hands_free=False) is False


def test_the_threshold_is_the_pipelines_accidental_tap_threshold():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "if recording_duration < 0.5:" in src and H.TAP_MAX_S == 0.5


@pytest.mark.parametrize("keys,system,expect", [
    (None, "Darwin", "Fn"),
    (["fn"], "Darwin", "Fn"),
    (["cmd", "shift", "space"], "Darwin", "Command + Shift + Space"),
    (["option", "space"], "Darwin", "Option + Space"),
    (None, "Windows", "Win + Ctrl"),
    (["ctrl", "win"], "Windows", "Win + Ctrl"),
    (["alt", "ctrl", "k"], "Windows", "Ctrl + Alt + K"),
])
def test_the_hint_names_the_hotkey_the_person_actually_uses(keys, system, expect):
    assert H.hotkey_name(keys, system) == expect


@pytest.mark.parametrize("junk", [[], "fn", 7, [1, 2], [""], {"a": 1}, [None]])
def test_a_hotkey_setting_that_makes_no_sense_falls_back_to_the_default(junk):
    assert H.hotkey_name(junk, "Darwin") == "Fn"
    assert H.hotkey_name(junk, "Windows") == "Win + Ctrl"


def test_the_message_names_the_key_and_says_to_hold_it():
    heading, body = um.hold_hint_message("Fn")
    assert heading == "Don't tap, hold"
    assert body == "Hold Fn while you speak, then let go."
    assert "Hold Win + Ctrl while" in um.hold_hint_message("Win + Ctrl")[1]
    for text in (heading, body):
        assert "—" not in text and "–" not in text


def test_the_hint_is_a_tip_once_every_12_hours_with_dont_show_again():
    k = N.KINDS["hold_hint"]
    assert (k.severity, k.min_gap_s, k.category) == (N.TIP, 43200, "hints")
    assert k.hold_s == H.HINT_SECONDS, "the slot is held as long as the toast stays up"
    labels = [b["label"] for b in N.tip_buttons("hold_hint")]
    assert labels == ["Got it", "Don't show again"]
    assert [b["action"] for b in N.tip_buttons("hold_hint")] == [
        "tip_dismiss:hold_hint", "tip_mute:hold_hint"]


# ── in the pipeline ──────────────────────────────────────────────────────

@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(H, "SETTLE_S", 0.15)


def tap_pipeline(tmp_path, clock=None, **kw):
    """A pipeline whose recording was a 0.2 s tap with nothing in it."""
    p = P.make_pipeline(tmp_path, wav=P.silence_wav(0.3), **kw)
    p._recording_start_time = time.time() - 0.2
    p._hands_free_press_id = 0
    if clock is not None:
        p._notifier = N.Notifier(store=N.MemoryStore(), clock=clock,
                                 context=p._notifier_context)
    return p


def tap(p, generation=1):
    """One more tap: the next press has already been counted when it ends."""
    p._processing_id = p._current_press_id = generation
    p._recording_start_time = time.time() - 0.2         # this press lasted 0.2 s
    t = P.run_process(p, generation)
    t.join(5)
    return t


def hints(p):
    return [t for t in p.overlay.toasts() if t.get("heading") == "Don't tap, hold"]


def test_a_discarded_tap_shows_the_hint_with_the_real_hotkey(tmp_path, fast):
    p = tap_pipeline(tmp_path)
    tap(p)
    assert P.wait_for(lambda: hints(p), timeout=3)
    [toast] = hints(p)
    assert toast["body"] == "Hold Win + Ctrl while you speak, then let go."
    assert toast["style"] == "info"
    assert [b["label"] for b in toast["buttons"]] == ["Got it", "Don't show again"]
    assert toast["timeout"] and toast["timeout"] <= 12
    assert p.transcriber.calls == 0, "and the tap was still dropped, not transcribed"


def test_it_names_a_custom_hotkey(tmp_path, fast):
    p = tap_pipeline(tmp_path)
    (tmp_path / "settings.json").write_text('{"hotkey_keys": ["alt", "ctrl", "k"]}', encoding="utf-8")
    tap(p)
    assert P.wait_for(lambda: hints(p), timeout=3)
    assert hints(p)[0]["body"] == "Hold Ctrl + Alt + K while you speak, then let go."


def test_once_every_12_hours(tmp_path, fast):
    clock = [1_000_000.0]
    p = tap_pipeline(tmp_path, clock=lambda: clock[0])
    tap(p)
    assert P.wait_for(lambda: len(hints(p)) == 1, timeout=3)
    p._notifier.release()
    clock[0] += 3600
    tap(p, 2)
    time.sleep(0.6)
    assert len(hints(p)) == 1, "an hour later: not again"
    p._notifier.release()
    clock[0] += 11 * 3600
    tap(p, 3)
    assert P.wait_for(lambda: len(hints(p)) == 2, timeout=3), "twelve hours on: again"


def test_a_second_press_straight_after_means_no_hint(tmp_path, fast):
    p = tap_pipeline(tmp_path)
    tap(p)
    with p._processing_lock:
        p._processing_id += 1
        p.is_recording = True              # they pressed again, to hold this time
    time.sleep(0.6)
    assert hints(p) == []


def test_still_holding_the_key_when_the_pause_ends_means_no_hint(tmp_path, fast):
    p = tap_pipeline(tmp_path)
    p.is_recording = True
    tap(p)
    time.sleep(0.6)
    assert hints(p) == []


def test_a_take_that_locked_hands_free_gets_no_hint(tmp_path, fast):
    p = tap_pipeline(tmp_path)
    p._hands_free_press_id = 1
    tap(p)
    time.sleep(0.6)
    assert hints(p) == []


def test_a_real_one_word_answer_is_transcribed_not_hinted(tmp_path, fast):
    p = P.make_pipeline(tmp_path, wav=P.speech_wav(0.4))
    p._recording_start_time = time.time() - 0.3
    p._hands_free_press_id = 0
    tap(p)
    time.sleep(0.5)
    assert p.transcriber.calls == 1
    assert hints(p) == []


def test_an_ordinary_dictation_never_gets_it(tmp_path, fast):
    p = P.make_pipeline(tmp_path)
    p._hands_free_press_id = 0
    tap(p)
    time.sleep(0.5)
    assert hints(p) == []


def test_tips_off_means_no_hint(tmp_path, fast):
    p = tap_pipeline(tmp_path)
    (tmp_path / "settings.json").write_text('{"tips": false}', encoding="utf-8")
    tap(p)
    time.sleep(0.6)
    assert hints(p) == []
    assert p._notifier.snapshot().get("hold_hint", {}).get("shown", 0) == 0


def test_before_setup_is_finished_there_is_no_hint(tmp_path, fast):
    p = tap_pipeline(tmp_path)
    p.ns["_is_setup_complete"] = lambda: False
    tap(p)
    time.sleep(0.6)
    assert hints(p) == []


def test_dont_show_again_is_remembered(tmp_path, fast):
    clock = [1_000_000.0]
    p = tap_pipeline(tmp_path, clock=lambda: clock[0])
    tap(p)
    assert P.wait_for(lambda: hints(p), timeout=3)
    p._on_toast_action("tip_mute:hold_hint")
    clock[0] += 10 ** 7
    tap(p, 2)
    time.sleep(0.6)
    assert len(hints(p)) == 1
    assert p._notifier.snapshot()["hold_hint"]["muted"] is True
    assert "hide_toast" in p.overlay.names()


def test_got_it_twice_is_enough(tmp_path, fast):
    clock = [1_000_000.0]
    p = tap_pipeline(tmp_path, clock=lambda: clock[0])
    for generation in (1, 2):
        tap(p, generation)
        assert P.wait_for(lambda: len(hints(p)) == generation, timeout=3)
        p._on_toast_action("tip_dismiss:hold_hint")
        clock[0] += 13 * 3600
    tap(p, 3)
    time.sleep(0.6)
    assert len(hints(p)) == 2


def test_it_does_not_hide_the_we_couldnt_hear_you_message(tmp_path, fast):
    """A quick tap makes no "couldn't hear you" (that is for takes of a
    second or more), but if an error is already up the hint waits its turn:
    it is dropped, never shown over it."""
    p = tap_pipeline(tmp_path)
    assert p._notifier.claim("no_audio")                # an error is on screen
    tap(p)
    time.sleep(0.6)
    assert hints(p) == []


def test_a_failure_to_show_it_never_breaks_a_dictation(tmp_path, fast):
    p = tap_pipeline(tmp_path)

    def boom(**kw):
        raise RuntimeError("overlay gone")
    p.overlay.show_toast = boom
    t = tap(p)
    assert not t.is_alive()
    time.sleep(0.5)
    assert p.page.statuses[-1] in ("idle", "done", "cancelled")
