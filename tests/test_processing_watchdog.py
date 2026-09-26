"""Nothing can sit on "Processing" forever, and processing can be cancelled.

After the hotkey was released nothing on screen showed a dictation was being
processed, there was no overall deadline (one request could run 240 s, and a
fallback and a retry could chain three), Esc only worked while recording, and
the release handler waited on the window (evaluate_js) before it even started
processing. The owner saw a Mac sit on "processing" for ages.

These tests drive src/pipeline_watchdog.py directly, and the real _process
from app.py with fakes (tests/_pipeline_harness.py): a provider that hangs as
if it would take 30 s, a paste that never returns, and exceptions on the
worker threads. The limits are shrunk so each case takes about a second. No
network, no keys, no real clipboard, no overlay process.
"""
import ast
import ctypes
import sys
import threading
import time
import types

import pytest

from _pipeline_harness import (ROOT, FakeClipboard, FakeStyler, FakeTranscriber, Hang,
                               fast_limits, history, make_pipeline, run_process, wait_for)
import pipeline_watchdog as pw


# ── the limits ───────────────────────────────────────────────────────────────

def test_the_offer_comes_after_eight_seconds_or_0_4_of_the_recording():
    assert pw.offer_after_s(0) == 8.0
    assert pw.offer_after_s(10) == 8.0
    assert pw.offer_after_s(20) == 8.0
    assert pw.offer_after_s(60) == pytest.approx(24.0)


def test_speech_to_text_has_one_overall_deadline_that_grows_with_the_recording():
    assert pw.transcribe_deadline_s(10) == 45.0
    assert pw.transcribe_deadline_s(60) == 60.0
    assert pw.transcribe_deadline_s(300) == 180.0
    assert pw.transcribe_deadline_s(720) == 330.0     # the 12-minute cap
    # Far below the 240 s one request could take, three times over.
    assert pw.transcribe_deadline_s(30) < 240.0


def test_the_elapsed_time_reads_simply():
    assert pw.elapsed_label(0.4) == "0s"
    assert pw.elapsed_label(7.9) == "7s"
    assert pw.elapsed_label(65) == "1:05"


# ── the bounded wait ─────────────────────────────────────────────────────────

def test_a_step_that_answers_returns_its_value():
    run = pw.DictationRun(1)
    r = run.call(lambda a, b: a + b, 2, 3, stage=pw.STYLING, deadline=1.0)
    assert r.ok and r.value == 5


def test_an_exception_on_the_worker_thread_becomes_a_result_not_a_hang():
    run = pw.DictationRun(1)
    r = run.call(lambda: 1 / 0, stage=pw.TRANSCRIBING, deadline=5.0)
    assert r.status == "error" and isinstance(r.error, ZeroDivisionError)


def test_a_hung_step_is_given_up_on_at_its_deadline():
    run = pw.DictationRun(1)
    hang = Hang(30)
    t0 = time.monotonic()
    r = run.call(hang.wait, stage=pw.TRANSCRIBING, deadline=0.4)
    took = time.monotonic() - t0
    hang.release.set()
    assert r.status == pw.DEADLINE
    assert 0.35 < took < 1.5


def test_cancel_wakes_the_wait_at_once():
    run = pw.DictationRun(1)
    hang = Hang(30)
    threading.Timer(0.2, lambda: run.decide(pw.CANCEL)).start()
    t0 = time.monotonic()
    r = run.call(hang.wait, stage=pw.TRANSCRIBING, deadline=30)
    took = time.monotonic() - t0
    hang.release.set()
    assert r.status == pw.CANCEL
    assert took < 1.0
    # Cancel sticks: a later stage does not start.
    assert run.call(lambda: "x", stage=pw.STYLING, deadline=1).status == pw.CANCEL


def test_each_stage_takes_only_its_own_choices():
    run = pw.DictationRun(1)
    run.enter(pw.TRANSCRIBING)
    assert run.decide(pw.PASTE_RAW) is False       # no words yet to paste
    assert run.decide(pw.SEND_LATER) is True
    run.enter(pw.STYLING)
    assert run.decide(pw.SEND_LATER) is False      # the words exist now
    assert run.decide(pw.PASTE_RAW) is True
    run.enter(pw.PASTING)
    assert run.decide(pw.CANCEL) is False          # too late: keep the words
    assert run.cancelled is False


# ── the watchdog's tick (fake clock) ─────────────────────────────────────────

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _watchdog(clock, current=True):
    seen = {"begin": [], "working": [], "offer": [], "withdraw": [], "finished": [], "stuck": []}
    wd = pw.PipelineWatchdog(
        on_begin=lambda r: seen["begin"].append(r.generation),
        on_working=lambda r, s: seen["working"].append(s),
        on_offer=lambda r, st: seen["offer"].append(st),
        on_withdraw_offer=lambda r: seen["withdraw"].append(r.generation),
        on_finished=lambda r, o: seen["finished"].append(o),
        on_stuck=lambda r: seen["stuck"].append(r.generation),
        is_current=lambda r: current, clock=clock, autostart=False)
    return wd, seen


def test_the_offer_appears_at_the_threshold_and_only_once():
    clock = Clock()
    wd, seen = _watchdog(clock)
    run = wd.begin(7)
    run.set_audio_seconds(5)
    run.enter(pw.TRANSCRIBING)
    clock.t += 7.9
    wd.tick()
    assert seen["offer"] == []
    clock.t += 0.2
    wd.tick()
    wd.tick()
    assert seen["offer"] == [pw.TRANSCRIBING]


def test_a_long_recording_waits_longer_for_the_offer():
    clock = Clock()
    wd, seen = _watchdog(clock)
    run = wd.begin(1)
    run.set_audio_seconds(60)        # offer after 24 s
    run.enter(pw.TRANSCRIBING)
    clock.t += 20
    wd.tick()
    assert seen["offer"] == []
    clock.t += 5
    wd.tick()
    assert seen["offer"] == [pw.TRANSCRIBING]


def test_the_pill_hears_about_each_whole_second():
    clock = Clock()
    wd, seen = _watchdog(clock)
    wd.begin(1)
    for _ in range(12):
        wd.tick()
        clock.t += 0.25
    assert seen["working"] == [0, 1, 2]


def test_moving_on_withdraws_an_unanswered_offer():
    clock = Clock()
    wd, seen = _watchdog(clock)
    run = wd.begin(1)
    run.enter(pw.TRANSCRIBING)
    clock.t += 9
    wd.tick()
    run.enter(pw.STYLING)
    wd.tick()
    assert seen["withdraw"] == [1]


def test_a_stage_that_outlives_its_limit_is_given_up_on_and_the_app_is_usable_again():
    clock = Clock()
    wd, seen = _watchdog(clock)
    run = wd.begin(3)
    run.enter(pw.SAVING)             # e.g. a History write that never returns
    clock.t += pw.SAVE_LIMIT_S + pw.STUCK_GRACE_S + 1
    wd.tick()
    assert wait_for(lambda: seen["stuck"] == [3], timeout=2)
    assert seen["finished"] == [pw.STUCK]
    assert run.abandoned and run.finished
    assert wd.active() == []
    # The dictation's own ending later changes nothing.
    assert wd.end(run, pw.DONE) is False
    assert seen["finished"] == [pw.STUCK]


def test_every_run_ends_exactly_once():
    wd, seen = _watchdog(Clock())
    run = wd.begin(1)
    assert wd.end(run, pw.DONE) is True
    assert wd.end(run, pw.ERROR) is False
    assert seen["finished"] == [pw.DONE]


def test_an_older_dictation_never_touches_the_pill_of_a_newer_recording():
    clock = Clock()
    wd, seen = _watchdog(clock, current=False)
    run = wd.begin(1)
    run.enter(pw.TRANSCRIBING)
    clock.t += 30
    wd.tick()
    assert seen["working"] == [] and seen["offer"] == []


def test_the_watchdog_thread_stops_when_nothing_is_processing():
    wd = pw.PipelineWatchdog(tick_s=0.02)
    run = wd.begin(1)
    assert wait_for(lambda: wd._thread is not None and wd._thread.is_alive(), timeout=1)
    wd.end(run, pw.DONE)
    assert wait_for(lambda: wd._thread is None, timeout=1)


# ── the real _process, end to end, with fakes ────────────────────────────────

@pytest.fixture
def limits(monkeypatch):
    """Offers after 0.4 s; the deadlines are 5 s, well clear of the offer, so
    a test can answer it before any deadline. Deadline tests shrink them."""
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=5.0, TRANSCRIBE_DEADLINE_BASE_S=5.0,
                TRANSCRIBE_DEADLINE_MAX_S=5.0, STYLE_DEADLINE_S=5.0)


@pytest.fixture
def short_deadlines(monkeypatch):
    fast_limits(monkeypatch)


def test_a_provider_that_hangs_gets_the_offer_then_cancel_stops_it_without_pasting(
        tmp_path, limits):
    """The verify line for SR2: a provider that would sleep 30 s. The offer
    appears at the threshold, and cancel ends the dictation, nothing pasted."""
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    t0 = time.monotonic()
    worker = run_process(p)

    # Straight away: the pill stays up in its working look, the window says
    # it is working, and Esc is armed.
    assert wait_for(lambda: "show_working" in p.overlay.names(), timeout=1)
    assert p.page.statuses[:1] == ["processing"]
    assert p.hotkey_listener.processing[:1] == [True]
    assert "hide" not in p.overlay.names()

    # At the threshold (shrunk to 0.4 s), the choices.
    assert wait_for(lambda: p.overlay.toasts(), timeout=2)
    offer = p.overlay.toasts()[0]
    assert offer["style"] == "info" and offer["heading"] == "Still working on it"
    assert [b["label"] for b in offer["buttons"]] == ["Keep waiting", "Send later", "Cancel"]
    assert time.monotonic() - t0 >= pw.OFFER_MIN_S * 0.9

    # Esc (the hotkey listener calls _on_overlay_cancel while processing).
    p._on_overlay_cancel()
    worker.join(2)
    hang.release.set()
    assert not worker.is_alive(), "cancel did not end the dictation"
    assert p.clipboard.copies == [] and p.clipboard.pastes == []
    assert history(p) == []
    assert p.page.statuses[-1] == "cancelled"
    assert ("end_working", ("quiet",), {}) in p.overlay.calls
    assert p.hotkey_listener.processing[-1] is False
    assert list((tmp_path / "unsent").glob("*.wav")) == [], "a cancel keeps nothing"


def test_the_pill_x_cancels_too(tmp_path, limits):
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: hang.entered.is_set(), timeout=2)
    p._on_overlay_cancel_request()        # the working pill's X
    worker.join(2)
    hang.release.set()
    assert not worker.is_alive()
    assert p.clipboard.pastes == [] and p.page.statuses[-1] == "cancelled"


def test_the_offers_cancel_button_cancels(tmp_path, limits):
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: p.overlay.toasts(), timeout=2)
    p._on_toast_action("cancel_processing")
    worker.join(2)
    hang.release.set()
    assert not worker.is_alive() and p.clipboard.pastes == []


def test_a_provider_that_never_answers_is_stopped_at_the_deadline_and_kept_as_not_sent(
        tmp_path, short_deadlines):
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    t0 = time.monotonic()
    worker = run_process(p)
    worker.join(5)
    took = time.monotonic() - t0
    hang.release.set()
    assert not worker.is_alive()
    assert took < pw.TRANSCRIBE_DEADLINE_MAX_S + 1.5
    wavs = list((tmp_path / "unsent").glob("recording-*.wav"))
    assert len(wavs) == 1
    [entry] = history(p)
    assert entry["failed"] and entry["unsent_id"] == wavs[0].name
    assert entry["not_sent_reason"] == "timeout"
    assert entry["will_retry"] is True
    assert p.clipboard.pastes == []
    assert p.page.statuses[-1] == "not_sent"
    toast = p.overlay.toasts()[-1]
    assert toast["heading"] == "Not sent" and "Journal" in toast["body"]
    assert [b["action"] for b in toast["buttons"]] == ["open_journal", "dismiss"]


def test_send_later_keeps_the_recording_for_later(tmp_path, limits):
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: p.overlay.toasts(), timeout=2)
    p._on_toast_action(pw.SEND_LATER)
    worker.join(2)
    hang.release.set()
    assert not worker.is_alive()
    [entry] = history(p)
    assert entry["failed"] and entry["not_sent_reason"] == "later"
    assert p.overlay.toasts()[-1]["heading"] == "Saved for later"


def test_keep_waiting_keeps_waiting_and_the_dictation_then_finishes(tmp_path, limits):
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: p.overlay.toasts(), timeout=2)
    p._on_toast_action(pw.KEEP_WAITING)
    time.sleep(0.3)
    assert worker.is_alive()
    hang.release.set()
    worker.join(2)
    assert p.clipboard.pastes == ["Ok so ship it on monday."]
    assert p.page.statuses[-1] == "done"
    assert ("end_working", ("done",), {}) in p.overlay.calls


def test_a_slow_clean_up_offers_paste_as_is_which_pastes_the_words_as_said(tmp_path, limits):
    hang = Hang(30)
    p = make_pipeline(tmp_path, styler=FakeStyler(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: p.overlay.toasts(), timeout=2)
    offer = p.overlay.toasts()[0]
    assert offer["heading"] == "Still cleaning up"
    assert [b["label"] for b in offer["buttons"]] == ["Paste as is", "Keep waiting", "Cancel"]
    p._on_toast_action(pw.PASTE_RAW)
    worker.join(2)
    hang.release.set()
    assert not worker.is_alive()
    assert p.clipboard.pastes == ["ok so ship it on monday"]
    assert history(p)[0]["styled"] == "ok so ship it on monday"
    # Chosen by the user: no "clean-up failed" message on top.
    assert [t["heading"] for t in p.overlay.toasts()] == ["Still cleaning up"]


def test_a_clean_up_that_never_answers_is_pasted_as_said_at_its_deadline(
        tmp_path, short_deadlines):
    hang = Hang(30)
    p = make_pipeline(tmp_path, styler=FakeStyler(hang=hang))
    worker = run_process(p)
    worker.join(5)
    hang.release.set()
    assert not worker.is_alive()
    assert p.clipboard.pastes == ["ok so ship it on monday"]
    assert p.overlay.toasts()[-1]["heading"] == "Pasted without the clean-up"


def test_a_paste_that_never_returns_cannot_hold_the_dictation(tmp_path, short_deadlines):
    hang = Hang(30)
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(paste_hang=hang))
    t0 = time.monotonic()
    worker = run_process(p)
    worker.join(5)
    took = time.monotonic() - t0
    hang.release.set()
    assert not worker.is_alive(), "a hung paste held the dictation"
    assert took < pw.PASTE_DEADLINE_S + 1.5
    # The words are safe on the clipboard and in the Journal, and the user
    # is told how to paste them.
    assert p.clipboard.copies == ["Ok so ship it on monday."]
    assert history(p)[0]["styled"] == "Ok so ship it on monday."
    toast = p.overlay.toasts()[-1]
    assert toast["heading"] == "Not pasted" and "Ctrl+V" in toast["body"]
    assert p.page.statuses[-1] == "done"


def test_an_exception_in_speech_to_text_keeps_the_recording(tmp_path, limits):
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    worker = run_process(p)
    worker.join(3)
    assert not worker.is_alive()
    [entry] = history(p)
    assert entry["failed"] and entry["not_sent_reason"] == "offline"
    assert p.page.statuses[-1] == "not_sent"


def test_an_exception_in_the_clean_up_still_pastes_the_words(tmp_path, limits):
    p = make_pipeline(tmp_path, styler=FakeStyler(error=RuntimeError("boom")))
    worker = run_process(p)
    worker.join(3)
    assert not worker.is_alive()
    assert p.clipboard.pastes == ["ok so ship it on monday"]
    assert p.page.statuses[-1] == "done"


def test_an_exception_in_the_pipeline_itself_ends_in_a_plain_message(tmp_path, limits):
    p = make_pipeline(tmp_path)
    p._apply_snippets = lambda text: (_ for _ in ()).throw(ValueError("bad snippet"))
    worker = run_process(p)
    worker.join(3)
    assert not worker.is_alive()
    assert p.page.statuses[-1] == "error"
    assert ("end_working", ("quiet",), {}) in p.overlay.calls
    toast = p.overlay.toasts()[-1]
    assert toast["heading"] == "Something went wrong"
    assert toast["body"] == "Your words are on the clipboard and in the Journal."
    assert p.clipboard.copies == ["ok so ship it on monday"]      # salvaged
    assert p.hotkey_listener.processing[-1] is False


def test_something_hanging_outside_a_bounded_step_is_given_up_on(tmp_path, monkeypatch):
    fast_limits(monkeypatch, STYLE_DEADLINE_S=0.3, STUCK_GRACE_S=0.3)
    hang = Hang(30)

    p = make_pipeline(tmp_path)

    def stuck_snippets(text):
        hang.wait()
        return text
    p._apply_snippets = stuck_snippets
    worker = run_process(p)
    # The watchdog gives up: the window and the pill are free again.
    assert wait_for(lambda: "error" in p.page.statuses, timeout=3)
    assert ("end_working", ("quiet",), {}) in p.overlay.calls
    assert p.overlay.toasts()[-1]["heading"] == "That took too long"
    # When the hang clears, the dictation must not paste into whatever the
    # user is doing by then, but the words are still kept.
    hang.release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert p.clipboard.pastes == []
    assert history(p)[0]["text"] == "ok so ship it on monday"


def test_a_newer_recording_does_not_throw_away_the_finished_words(tmp_path, limits):
    """Pressing again while one is being cleaned up: no paste (the new one
    owns the window and clipboard), but the words go to the Journal. They
    used to be thrown away by an early return."""
    hang = Hang(30)
    p = make_pipeline(tmp_path, styler=FakeStyler(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: hang.entered.is_set(), timeout=2)
    with p._processing_lock:           # what on_hotkey_press does
        p.is_recording = True
        p._processing_id += 1
    hang.release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert p.clipboard.copies == [] and p.clipboard.pastes == []
    assert history(p)[0]["styled"] == "Ok so ship it on monday."
    # The older dictation leaves the new recording's pill and label alone.
    assert "end_working" not in p.overlay.names()
    assert p.page.statuses == ["processing"]


def test_a_brushed_key_ends_quietly_without_a_tick(tmp_path, limits):
    p = make_pipeline(tmp_path, wav=b"")
    p._recording_start_time = time.time() - 0.1
    worker = run_process(p)
    worker.join(2)
    assert not worker.is_alive()
    assert p.page.statuses[-1] == "idle"
    assert ("end_working", ("quiet",), {}) in p.overlay.calls


def test_the_release_handler_no_longer_waits_on_the_window_or_hides_the_pill(tmp_path):
    """notify_js_status('processing') used to run before the processing
    thread started; evaluate_js has no timeout on a Mac."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("    def on_hotkey_release"):src.index("    def toggle_pause")]
    spawn = body.index("self._process(current_id)")
    assert "notify_js_status(\"processing\")" not in body
    # The pill is only hidden when processing could not start at all.
    hide = body.index("self.overlay.hide()")
    assert body.rindex("except RuntimeError", 0, hide) > spawn


def test_page_notifications_never_block(monkeypatch):
    """A window that never answers must not hold up a dictation."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    names = ("_JS_QUEUE_MAX", "_js_queue", "_js_thread", "_js_thread_lock", "_js_dropped",
             "_js_drain", "_post_js", "notify_js_status", "notify_js_new_item")
    nodes = [n for n in tree.body
             if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in names)
             or (isinstance(n, ast.FunctionDef) and n.name in names)]
    import queue as _queue
    import json as _json
    ns = {"queue": _queue, "json": _json, "threading": threading, "_log_to_file": lambda m: None}
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    stuck = threading.Event()

    class NeverAnswers:
        def evaluate_js(self, script):
            stuck.wait(30)
    ns["_window"] = NeverAnswers()
    t0 = time.monotonic()
    for _ in range(ns["_JS_QUEUE_MAX"] + 50):
        ns["notify_js_status"]("processing")
        ns["notify_js_new_item"]({"text": "x"})
    took = time.monotonic() - t0
    stuck.set()
    assert took < 1.0, f"notifications blocked for {took:.1f}s"
    assert ns["_js_dropped"][0] > 0


# ── Esc while processing ─────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="windows_hotkey binds ctypes.WINFUNCTYPE")
def test_windows_esc_cancels_while_processing_and_not_otherwise():
    import windows_hotkey as wh
    cancels = []
    listener = wh.WindowsHotkeyListener(on_press=lambda: None, on_release=lambda: None,
                                        on_cancel=lambda: cancels.append(1))

    def esc_down():
        kb = wh.KBDLLHOOKSTRUCT()
        kb.vkCode = wh.VK_ESCAPE
        kb.flags = 0
        listener._ll_keyboard_proc(0, wh.WM_KEYDOWN, ctypes.addressof(kb))

    esc_down()
    time.sleep(0.1)
    assert cancels == [], "Esc outside recording and processing must be left alone"
    listener.set_processing(True)
    esc_down()
    assert wait_for(lambda: cancels == [1], timeout=1)
    listener.set_processing(False)
    esc_down()
    time.sleep(0.1)
    assert cancels == [1]


def test_mac_esc_cancels_while_processing_and_not_otherwise():
    """smart_hotkey imports Quartz, so its two methods are lifted out."""
    tree = ast.parse((ROOT / "src" / "smart_hotkey.py").read_text(encoding="utf-8"))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name == "SmartHotkeyListener")
    nodes = [n for n in klass.body if isinstance(n, ast.FunctionDef)
             and n.name in ("_on_esc_press", "set_processing")]
    ns = {"_diag_log": lambda m: None}
    exec(compile(ast.Module(nodes, []), "<smart_hotkey.py>", "exec"), ns)
    cancels = []
    listener = types.SimpleNamespace(
        _recording=False, _processing=False, _sticky=False, _hotkey_held=False, _id="L01",
        _fire_cancel=lambda: cancels.append(1), _fire_visual_feedback=lambda e: None)
    esc = types.MethodType(ns["_on_esc_press"], listener)
    set_processing = types.MethodType(ns["set_processing"], listener)
    esc()
    assert cancels == []
    set_processing(True)
    esc()
    assert cancels == [1]
    set_processing(False)
    esc()
    assert cancels == [1]
