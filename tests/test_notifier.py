"""The notification governor (src/notifier.py): the one place that decides
whether a toast or a tip is shown.

It is pure logic with an injectable clock, context and store, so every rule
is checked here without an overlay, a window or a sleep:

  errors   always pass the cap and every quiet rule; a repeat inside its
           minimum gap is a duplicate and is dropped (once per run)
  tips     wait for a quiet moment: not while recording or processing, not
           before setup is done, not with tips off, and never over a message
           that is already up; then the cap, the gap, two dismissals and
           "Don't show again"
  one slot a pending error pre-empts a tip, and a tip never replaces anything
"""
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import notifier as N  # noqa: E402
from notifier import ERROR, INFO, TIP, Context, Kind, MemoryStore, Notifier  # noqa: E402


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def kinds(*ks):
    return {k.id: k for k in ks}


TIP_A = Kind("tip_a", TIP, max_times=3, min_gap_s=3600, category="hints")
TIP_B = Kind("tip_b", TIP, min_gap_s=0)
ERR = Kind("err", ERROR)
ERR_GAP = Kind("err_gap", ERROR, min_gap_s=60)
ERR_CAPPED = Kind("err_capped", ERROR, max_times=1)
INFO_K = Kind("info_k", INFO, hold_s=None)
INFO_CAP = Kind("info_cap", INFO, max_times=2)


def make(*ks, clock=None, context=None, store=None, log=None):
    clock = clock or Clock()
    n = Notifier(store=store or MemoryStore(), clock=clock,
                 context=context or (lambda: Context()), kinds=kinds(*ks),
                 log=log)
    return n, clock


def shown(n, kind_id):
    return n.request(kind_id).show


# ── the registry ─────────────────────────────────────────────────────────

def test_every_registered_kind_is_well_formed():
    assert N.KINDS, "the registry is empty"
    for kid, k in N.KINDS.items():
        assert kid == k.id and k.severity in (ERROR, TIP, INFO), kid
        assert k.max_times is None or k.max_times >= 1, kid
        assert k.min_gap_s >= 0, kid
        assert k.hold_s is None or k.hold_s > 0, kid


def test_a_kind_with_an_unknown_severity_is_refused():
    with pytest.raises(ValueError):
        Kind("x", "loud")


def test_tips_are_the_only_kinds_a_person_can_hide():
    """Every tip is in the registry as a tip, and the hold-not-tap hint is
    once per 12 hours."""
    hint = N.KINDS["hold_hint"]
    assert hint.severity == TIP and hint.min_gap_s == 12 * 3600
    assert hint.category == "hints"
    assert all(k.severity == TIP for k in N.KINDS.values() if k.category == "hints")


def test_the_permission_and_paste_gaps_are_the_ones_the_old_limiter_used():
    assert N.KINDS["permission_microphone"].min_gap_s == 60
    assert N.KINDS["permission_accessibility"].min_gap_s == 60
    assert N.KINDS["permission_keyboard"].min_gap_s == 600
    assert N.KINDS["permission_accessibility_idle"].min_gap_s == float("inf")
    assert N.KINDS["paste_failed"].min_gap_s == 30


# ── errors ───────────────────────────────────────────────────────────────

def test_an_error_is_shown_every_time_it_has_no_gap():
    n, _ = make(ERR)
    assert [shown(n, "err") for _ in range(5)] == [True] * 5


def test_an_error_passes_the_cap_but_the_count_goes_on():
    n, _ = make(ERR_CAPPED)
    assert [shown(n, "err_capped") for _ in range(4)] == [True] * 4
    assert n.snapshot()["err_capped"]["shown"] == 4


def test_an_error_ignores_tips_off_recording_processing_and_setup():
    n, _ = make(ERR, context=lambda: Context(recording=True, processing=True,
                                              setup_done=False, tips_enabled=False))
    assert shown(n, "err")


def test_a_repeat_inside_the_gap_is_a_duplicate_and_one_after_it_is_not():
    n, clock = make(ERR_GAP)
    assert shown(n, "err_gap")
    clock.now += 59
    d = n.request("err_gap")
    assert not d.show and d.reason == "gap"
    clock.now += 2
    assert shown(n, "err_gap")


def test_a_dropped_duplicate_does_not_start_a_new_gap():
    n, clock = make(ERR_GAP)
    assert shown(n, "err_gap")
    clock.now += 40
    assert not shown(n, "err_gap")
    clock.now += 21        # 61 s after the one that was shown
    assert shown(n, "err_gap")


def test_the_gap_is_per_kind():
    other = Kind("err_other", ERROR, min_gap_s=60)
    n, _ = make(ERR_GAP, other)
    assert shown(n, "err_gap") and shown(n, "err_other")
    assert not shown(n, "err_gap") and not shown(n, "err_other")


def test_an_endless_gap_holds_until_it_is_reset():
    once = Kind("once", ERROR, min_gap_s=float("inf"))
    n, clock = make(once)
    assert shown(n, "once")
    clock.now += 10 ** 7
    assert not shown(n, "once")
    n.reset_gap("once")
    assert shown(n, "once")
    assert n.snapshot()["once"]["shown"] == 2, "a reset forgets the gap, not the count"


def test_an_error_gap_starts_afresh_with_each_run_of_the_app():
    """The gap is a duplicate guard for this run. Quitting and opening Waffler
    to switch a permission on must not hide the message for a minute."""
    store = MemoryStore()
    n1, clock = make(ERR_GAP, store=store)
    assert shown(n1, "err_gap")
    clock.now += 5
    n2, _ = make(ERR_GAP, store=store, clock=clock)
    assert shown(n2, "err_gap")
    assert not shown(n2, "err_gap")
    assert n2.snapshot()["err_gap"]["shown"] == 2, "the counter carries on across runs"


# ── tips: the table ──────────────────────────────────────────────────────

def test_a_tip_is_shown_when_everything_is_quiet():
    n, _ = make(TIP_A)
    d = n.request("tip_a")
    assert d.show and d.reason == "ok"


@pytest.mark.parametrize("ctx,reason", [
    (Context(tips_enabled=False), "tips_off"),
    (Context(recording=True), "recording"),
    (Context(processing=True), "processing"),
    (Context(setup_done=False), "setup"),
])
def test_a_tip_waits_for_a_quiet_moment(ctx, reason):
    n, _ = make(TIP_A, context=lambda: ctx)
    d = n.request("tip_a")
    assert not d.show and d.reason == reason
    assert "tip_a" not in n.snapshot(), "a suppressed tip is not counted"


def test_a_tip_stops_at_its_cap():
    n, clock = make(TIP_A)
    for _ in range(3):
        assert shown(n, "tip_a")
        n.release()
        clock.now += 3601
    d = n.request("tip_a")
    assert not d.show and d.reason == "capped"


def test_a_tip_keeps_its_minimum_gap():
    n, clock = make(TIP_A)
    assert shown(n, "tip_a")
    n.release()
    clock.now += 3599
    assert n.request("tip_a").reason == "gap"
    clock.now += 2
    assert shown(n, "tip_a")


def test_a_tip_dismissed_twice_stays_away():
    n, clock = make(TIP_B)
    assert shown(n, "tip_b")
    n.dismiss("tip_b")
    assert shown(n, "tip_b")
    n.dismiss("tip_b")
    d = n.request("tip_b")
    assert not d.show and d.reason == "dismissed"
    assert n.snapshot()["tip_b"]["dismissed"] == 2


def test_dont_show_again_is_for_good():
    n, clock = make(TIP_A)
    assert shown(n, "tip_a")
    n.mute("tip_a")
    clock.now += 10 ** 7
    d = n.request("tip_a")
    assert not d.show and d.reason == "muted"
    assert n.snapshot()["tip_a"]["muted"] is True


def test_a_muted_category_silences_its_tips():
    n, _ = make(TIP_A, TIP_B)
    n.mute_category("hints")
    assert n.request("tip_a").reason == "category_muted"
    assert shown(n, "tip_b"), "a tip with no category is not affected"


def test_the_context_is_asked_only_for_tips():
    asked = []

    def ctx():
        asked.append(1)
        return Context()
    n, _ = make(ERR, INFO_K, TIP_B, context=ctx)
    shown(n, "err")
    shown(n, "info_k")
    assert asked == []
    shown(n, "tip_b")
    assert asked == [1]


def test_a_tip_is_held_back_when_the_context_cannot_be_read():
    def boom():
        raise RuntimeError("no window")
    n, _ = make(TIP_B, ERR, context=boom)
    assert n.request("tip_b").reason == "context"
    assert shown(n, "err"), "an error never depends on it"


# ── info ─────────────────────────────────────────────────────────────────

def test_info_is_not_a_tip():
    n, _ = make(INFO_K, context=lambda: Context(tips_enabled=False, recording=True,
                                                 setup_done=False))
    assert shown(n, "info_k")


def test_info_respects_its_cap():
    n, _ = make(INFO_CAP)
    assert shown(n, "info_cap") and shown(n, "info_cap")
    assert n.request("info_cap").reason == "capped"


# ── one slot ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("holder", [ERR, INFO_K, TIP_B])
def test_a_tip_never_replaces_a_message_that_is_up(holder):
    n, _ = make(holder, Kind("late", TIP))
    assert shown(n, holder.id)
    d = n.request("late")
    assert not d.show and d.reason == "slot_busy"


def test_a_tip_is_not_queued_it_is_dropped():
    n, _ = make(ERR, TIP_B)
    shown(n, "err")
    assert not shown(n, "tip_b")
    n.release()
    assert "tip_b" not in n.snapshot()


def test_the_slot_frees_itself_when_the_message_would_have_gone():
    n, clock = make(Kind("e", ERROR, hold_s=6), TIP_B)
    shown(n, "e")
    clock.now += 5.9
    assert n.request("tip_b").reason == "slot_busy"
    clock.now += 0.2
    assert shown(n, "tip_b")


def test_a_message_with_no_time_limit_holds_the_slot_until_it_is_released():
    n, clock = make(INFO_K, TIP_B)
    shown(n, "info_k")
    clock.now += 100
    assert n.request("tip_b").reason == "slot_busy"
    n.release("info_k")
    assert shown(n, "tip_b")


def test_releasing_another_kind_leaves_the_slot_alone():
    n, _ = make(ERR, TIP_B)
    shown(n, "err")
    n.release("something_else")
    assert n.request("tip_b").reason == "slot_busy"


def test_an_error_pre_empts_a_tip():
    n, _ = make(TIP_B, ERR)
    assert shown(n, "tip_b")
    assert shown(n, "err")
    assert n.request("tip_b").reason == "slot_busy", "the error holds the slot now"


def test_a_tip_pre_empted_before_it_could_be_read_is_not_counted():
    n, clock = make(TIP_A, ERR)
    assert shown(n, "tip_a")
    clock.now += 1.0
    assert shown(n, "err")
    assert n.snapshot()["tip_a"]["shown"] == 0
    assert n.snapshot()["tip_a"]["last_shown"] is None
    n.release()
    assert shown(n, "tip_a"), "so its gap has not been used up either"


def test_a_tip_that_was_up_long_enough_stays_counted():
    n, clock = make(TIP_A, ERR)
    assert shown(n, "tip_a")
    clock.now += 3.5
    assert shown(n, "err")
    assert n.snapshot()["tip_a"]["shown"] == 1


def test_dismissing_and_muting_free_the_slot():
    n, _ = make(TIP_B, TIP_A)
    shown(n, "tip_b")
    n.dismiss("tip_b")
    assert shown(n, "tip_a")
    n.mute("tip_a")
    assert shown(n, "tip_b")


# ── odd inputs ───────────────────────────────────────────────────────────

def test_a_kind_nobody_registered_is_shown_and_logged():
    lines = []
    n, _ = make(ERR, log=lines.append)
    d = n.request("typo")
    assert d.show and d.reason == "unregistered"
    assert any("typo" in line for line in lines)


def test_a_clock_that_went_backwards_does_not_hide_a_tip_for_ever():
    n, clock = make(TIP_A)
    assert shown(n, "tip_a")
    n.release()
    clock.now -= 5 * 24 * 3600          # the date was set back
    assert shown(n, "tip_a")


def test_claim_is_the_yes_or_no():
    n, _ = make(ERR_GAP)
    assert n.claim("err_gap") is True
    assert n.claim("err_gap") is False


def test_a_decision_carries_the_kinds_sound():
    n, _ = make(Kind("chime", ERROR, sound="stop"))
    assert n.request("chime").sound == "stop"
    assert make(ERR)[0].request("err").sound is None


# ── persistence ──────────────────────────────────────────────────────────

def test_counts_gaps_dismissals_and_hidden_tips_survive_a_restart(tmp_path):
    path = tmp_path / "notifications.json"
    clock = Clock()
    n1, _ = make(TIP_A, TIP_B, clock=clock, store=N.FileStore(path))
    assert shown(n1, "tip_a")
    n1.release()
    assert shown(n1, "tip_b")
    n1.dismiss("tip_b")
    n1.mute("tip_b")
    n1.mute_category("hints")

    n2, _ = make(TIP_A, TIP_B, clock=clock, store=N.FileStore(path))
    snap = n2.snapshot()
    assert snap["tip_a"]["shown"] == 1 and snap["tip_a"]["last_shown"] == clock.now
    assert snap["tip_b"] == {"shown": 1, "last_shown": clock.now, "dismissed": 1, "muted": True}
    assert n2.request("tip_b").reason == "muted"
    assert n2.request("tip_a").reason == "category_muted"


def test_a_tips_gap_holds_across_a_restart(tmp_path):
    path = tmp_path / "notifications.json"
    clock = Clock()
    n1, _ = make(TIP_A, clock=clock, store=N.FileStore(path))
    assert shown(n1, "tip_a")
    clock.now += 1800
    n2, _ = make(TIP_A, clock=clock, store=N.FileStore(path))
    assert n2.request("tip_a").reason == "gap"
    clock.now += 1801
    assert shown(n2, "tip_a")


def test_the_state_file_is_small_json_with_counters_and_no_text(tmp_path):
    path = tmp_path / "notifications.json"
    n, clock = make(TIP_A, ERR, store=N.FileStore(path))
    shown(n, "tip_a")
    n.release()
    shown(n, "err")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert set(data["kinds"]) == {"tip_a", "err"}
    assert set(data["kinds"]["tip_a"]) == {"shown", "last_shown", "dismissed", "muted"}
    assert path.stat().st_size < 2000


@pytest.mark.parametrize("junk", [
    "not json at all {{{", "[1, 2, 3]", '"a string"', "null", "",
    '{"kinds": "oops"}', '{"kinds": {"tip_a": 7}}',
    '{"kinds": {"tip_a": {"shown": "many", "last_shown": "yesterday", "dismissed": -4, "muted": "yes"}}}',
    '{"kinds": {"tip_a": {"shown": 99}}, "muted_categories": 5}',
])
def test_a_state_file_that_cannot_be_read_is_ignored(tmp_path, junk):
    path = tmp_path / "notifications.json"
    path.write_text(junk, encoding="utf-8")
    n, _ = make(TIP_A, ERR, store=N.FileStore(path))
    if "99" in junk:
        assert n.request("tip_a").reason == "capped", "what is readable is used"
    else:
        assert shown(n, "tip_a"), "a damaged file means a fresh start"
    assert shown(n, "err")
    json.loads(path.read_text(encoding="utf-8"))    # and the next save repaired it


def test_a_file_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "notifications.json"
    path.write_text('﻿{"version": 1, "kinds": {"tip_a": {"shown": 3}}}', encoding="utf-8")
    n, _ = make(TIP_A, store=N.FileStore(path))
    assert n.request("tip_a").reason == "capped"


def test_a_missing_file_is_a_fresh_start(tmp_path):
    n, _ = make(TIP_A, store=N.FileStore(tmp_path / "nope" / "notifications.json"))
    assert shown(n, "tip_a")


def test_a_write_that_fails_never_stops_a_message():
    class Broken(MemoryStore):
        def save(self, data):
            raise OSError("disk full")
    lines = []
    n, _ = make(ERR, TIP_B, store=Broken(), log=lines.append)
    assert shown(n, "err")
    n.release()
    assert shown(n, "tip_b")
    assert lines and all("disk full" not in line for line in lines), \
        "the reason is the error's name, not its text"


def test_the_file_is_found_through_the_data_folder_at_call_time(tmp_path, monkeypatch):
    store = N.FileStore()
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path / "one"))
    (tmp_path / "one").mkdir()
    store.save({"version": 1, "kinds": {}})
    assert (tmp_path / "one" / N.STATE_FILE).exists()
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path / "two"))
    assert store.path() == tmp_path / "two" / N.STATE_FILE


def test_forget_clears_memory_and_the_file(tmp_path):
    path = tmp_path / "notifications.json"
    n, _ = make(TIP_A, store=N.FileStore(path))
    shown(n, "tip_a")
    n.release()
    n.forget()
    assert not path.exists()
    assert n.snapshot() == {}
    assert shown(n, "tip_a"), "deleting the data brings the tips back"


# ── threads ──────────────────────────────────────────────────────────────

def test_many_threads_cannot_show_a_once_only_tip_twice():
    once = Kind("once_tip", TIP, max_times=1)
    n, _ = make(once)
    results, gate = [], threading.Barrier(32)

    def go():
        gate.wait()
        results.append(n.claim("once_tip"))
    threads = [threading.Thread(target=go) for _ in range(32)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1
    assert n.snapshot()["once_tip"]["shown"] == 1


def test_many_threads_cannot_get_past_an_errors_gap():
    n, _ = make(ERR_GAP)
    results, gate = [], threading.Barrier(32)

    def go():
        gate.wait()
        results.append(n.claim("err_gap"))
    threads = [threading.Thread(target=go) for _ in range(32)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1


def test_mixed_calls_from_many_threads_leave_a_consistent_file(tmp_path):
    path = tmp_path / "notifications.json"
    n, _ = make(ERR, TIP_B, store=N.FileStore(path))

    def go(i):
        for _ in range(20):
            n.claim("err")
            n.claim("tip_b")
            n.dismiss("tip_b") if i % 2 else n.release()
    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kinds"]["err"]["shown"] == 160


# ── the tips setting ─────────────────────────────────────────────────────

def test_tips_are_on_unless_the_setting_says_false():
    assert N.tips_enabled({}) is True
    assert N.tips_enabled(None) is True
    assert N.tips_enabled({"tips": True}) is True
    assert N.tips_enabled({"tips": False}) is False
    for stray in ("false", 0, None, [], "off"):
        assert N.tips_enabled({"tips": stray}) is True, stray


def test_the_old_gate_calls_still_read_the_same():
    """paste_failed and the permission kinds were behind a RateLimiter; the
    governor answers the same question."""
    n = Notifier(store=MemoryStore(), clock=Clock())
    assert n.claim("paste_failed") is True
    assert n.claim("paste_failed") is False


# ── the buttons every tip carries ────────────────────────────────────────

def test_every_tip_has_got_it_and_dont_show_again():
    buttons = N.tip_buttons("hold_hint")
    assert [b["label"] for b in buttons] == ["Got it", "Don't show again"]
    assert [b["kind"] for b in buttons] == ["primary", "secondary"]
    for b in buttons:
        assert N.parse_tip_action(b["action"])[1] == "hold_hint"


@pytest.mark.parametrize("action,expect", [
    ("tip_dismiss:hold_hint", ("tip_dismiss", "hold_hint")),
    ("tip_mute:hold_hint", ("tip_mute", "hold_hint")),
    ("tip_mute:", None), ("tip_mute", None), ("dismiss", None), ("open_journal", None),
    ("tip_other:hold_hint", None), (None, None), (7, None),
])
def test_a_tip_buttons_action_is_understood_or_ignored(action, expect):
    assert N.parse_tip_action(action) == expect
