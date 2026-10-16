"""Safe paste: snapshot and restore, the paste sequence, outcomes, "paste last".

Nothing here touches the real clipboard, the keyboard, Accessibility or the
microphone. Snapshot and restore run against a private
``NSPasteboard.pasteboardWithUniqueName()`` (macOS only); the sequence runs
against in-memory fakes with an injected clock, so no test sleeps for real
except the few that race two threads.
"""
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mac_input  # noqa: E402
import pasteboard  # noqa: E402
from mac_input import (  # noqa: E402
    BLOCKED, MOD_COMMAND, MOD_FN, MOD_SHIFT, NONE, NOT_EDITABLE, OPAQUE, TEXT, FocusState,
)
from pasteboard import (  # noqa: E402
    FAILED, PASTED, UNVERIFIED, LastResult, MacPasteboard, PasteOutcome, PasteTicket,
    SafePaster, Snapshot, classify_paste, paste_failure_toast, report_paste_failure,
    resolve_v_keycode, restore_after_paste, wait_for_modifiers_released,
)
from rate_limit import RateLimiter  # noqa: E402

IS_MAC = sys.platform == "darwin"
mac_only = pytest.mark.skipif(not IS_MAC, reason="needs macOS (AppKit)")
STRING = "public.utf8-plain-text"


# ── fakes ────────────────────────────────────────────────────────────────

class Clock:
    """Fake monotonic clock; sleeping just moves time forward."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, dt):
        self.slept.append(dt)
        self.now += dt


class FakePasteboard:
    """In memory, with the surface of MacPasteboard that SafePaster uses."""

    def __init__(self, events, items=None, skipped=""):
        self.events = events
        self.items = [list(i) for i in (items or [])]
        self.skipped = skipped
        self.cc = 10
        self.transient = False
        self.raise_on_snapshot = False
        self.restore_raises = False
        self.stamp = lambda: None
        self.restored_at = None
        self.on_restore = None

    def change_count(self):
        return self.cc

    def snapshot(self):
        self.events.append("snapshot")
        if self.raise_on_snapshot:
            raise RuntimeError("boom")
        if self.skipped:
            return Snapshot(items=(), skipped=self.skipped, change_count=self.cc)
        return Snapshot(items=tuple(tuple(i) for i in self.items), change_count=self.cc)

    def put_text(self, text, transient=False):
        self.events.append(f"write:{'transient' if transient else 'plain'}")
        self.items = [[(STRING, text.encode())]]
        self.transient = transient
        self.cc += 1
        return self.cc

    def restore(self, snap, expect_change_count):
        if self.on_restore:
            self.on_restore()
        if snap is None or snap.skipped:
            self.events.append("restore:skipped")
            return False
        if self.cc != expect_change_count:
            self.events.append("restore:guard")
            return False
        if self.restore_raises:
            self.events.append("restore:error")
            self.items = []          # the real one clears before it writes
            self.cc += 1
            raise RuntimeError("could not write the restored items")
        self.events.append("restore")
        self.restored_at = self.stamp()
        self.items = [list(i) for i in snap.items]
        self.cc += 1
        return True

    def user_copies(self, text):
        self.items = [[(STRING, text.encode())]]
        self.cc += 1

    def text(self):
        for item in self.items:
            for t, d in item:
                if t == STRING:
                    return d.decode()
        return None


class FakeFocus:
    """Returns queued FocusStates, then repeats the last one."""

    def __init__(self, events, states, clock=None, read_cost=0.0):
        self.events = events
        self.states = list(states)
        self.reads = 0
        self.clock = clock
        self.read_cost = read_cost

    def read(self):
        self.events.append("ax")
        self.reads += 1
        if self.clock is not None and self.read_cost:
            self.clock.now += self.read_cost
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]


def text_state(n):
    return FocusState(TEXT, (n, n, 0, 0), "AXTextArea")


def make_paster(events, pb, focus_states, flags=None, trusted=True, keycode=9, send=None,
                restore_delay=0.5, resolver=None, read_cost=0.0, verify_budget=2.5):
    clock = Clock()

    def send_key(kc):
        events.append(f"key:{kc}")
        if send:
            send(kc)

    paster = SafePaster(
        pb, flags=flags if flags is not None else (lambda: 0),
        keycode_resolver=resolver or (lambda: keycode), send_key=send_key,
        focus_reader=FakeFocus(events, focus_states, clock, read_cost),
        trusted=(lambda: trusted), sleep=clock.sleep, clock=clock,
        restore_delay=restore_delay, verify_budget=verify_budget)
    paster.clock = clock
    return paster


# ── snapshot and restore on a private NSPasteboard ───────────────────────

def _types_and_data(pb):
    out = []
    for item in pb.pasteboardItems() or []:
        out.append(sorted((str(t), bytes(item.dataForType_(t))) for t in item.types()))
    return out


def _write_items(pb, items):
    from AppKit import NSPasteboardItem
    from Foundation import NSData
    objs = []
    for types in items:
        it = NSPasteboardItem.alloc().init()
        for t, data in types:
            it.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), t)
        objs.append(it)
    pb.clearContents()
    assert pb.writeObjects_(objs)


def _text_on(pb):
    from AppKit import NSPasteboardTypeString
    return str(pb.stringForType_(NSPasteboardTypeString) or "")


@pytest.fixture
def private_pb():
    """A pasteboard of our own: never the one the user copies to."""
    if not IS_MAC:
        pytest.skip("needs macOS (AppKit)")
    from AppKit import NSPasteboard
    pb = NSPasteboard.pasteboardWithUniqueName()
    yield pb
    pb.releaseGlobally()


@mac_only
def test_snapshot_restore_round_trips_every_item_and_type(private_pb):
    original = [
        [(STRING, "hello wörld".encode()), ("public.rtf", b"{\\rtf1 hi}"),
         ("com.waffler.test", b"\x00\x01\x02binary")],
        [("public.png", b"\x89PNG-not-really" * 100)],
        [("public.file-url", b"file:///tmp/a.txt")],
    ]
    _write_items(private_pb, original)
    before = _types_and_data(private_pb)
    assert len(before) == 3, before

    mp = MacPasteboard(private_pb)
    snap = mp.snapshot()
    assert snap.skipped == "" and len(snap.items) == 3

    cc = mp.put_text("dictated text")
    assert _types_and_data(private_pb) != before
    assert mp.restore(snap, cc) is True
    assert _types_and_data(private_pb) == before, "restore must bring back every type's bytes"


@mac_only
def test_restore_is_skipped_when_someone_else_wrote_in_the_meantime(private_pb):
    """The changeCount guard: the user's newer copy is never overwritten."""
    _write_items(private_pb, [[(STRING, b"original")]])
    mp = MacPasteboard(private_pb)
    snap = mp.snapshot()
    cc = mp.put_text("dictated")
    _write_items(private_pb, [[(STRING, b"user copied this")]])
    assert mp.restore(snap, cc) is False
    assert _text_on(private_pb) == "user copied this"


@mac_only
def test_an_empty_pasteboard_restores_to_empty(private_pb):
    private_pb.clearContents()
    mp = MacPasteboard(private_pb)
    snap = mp.snapshot()
    assert snap.items == () and snap.skipped == ""
    cc = mp.put_text("dictated")
    assert _text_on(private_pb) == "dictated"
    assert mp.restore(snap, cc) is True
    assert not (private_pb.pasteboardItems() or []), "an empty clipboard should end up empty"


@mac_only
@pytest.mark.parametrize("marker,reason", [
    ("org.nspasteboard.ConcealedType", "concealed"),
    ("org.nspasteboard.TransientType", "transient"),
    ("org.nspasteboard.AutoGeneratedType", "auto_generated"),
])
def test_concealed_transient_and_autogenerated_contents_are_never_captured(private_pb, marker, reason):
    _write_items(private_pb, [[(STRING, b"hunter2"), (marker, b"")]])
    mp = MacPasteboard(private_pb)
    snap = mp.snapshot()
    assert snap.skipped == reason
    assert snap.items == (), "secret bytes must not be held in memory"
    cc = mp.put_text("dictated")
    assert mp.restore(snap, cc) is False
    assert _text_on(private_pb) == "dictated"


@mac_only
def test_the_dictation_is_marked_transient_only_when_asked(private_pb):
    mp = MacPasteboard(private_pb)
    mp.put_text("temp", transient=True)
    types = {str(t) for t in private_pb.pasteboardItems()[0].types()}
    assert "org.nspasteboard.TransientType" in types
    assert "org.nspasteboard.AutoGeneratedType" in types
    assert _text_on(private_pb) == "temp"
    mp.put_text("kept", transient=False)
    types = {str(t) for t in private_pb.pasteboardItems()[0].types()}
    assert not any(t.startswith("org.nspasteboard.") for t in types), types
    assert _text_on(private_pb) == "kept"


@mac_only
def test_an_oversized_pasteboard_is_not_snapshotted_but_does_not_raise(private_pb):
    _write_items(private_pb, [[("com.waffler.big", b"x" * 4096)]])
    snap = MacPasteboard(private_pb, max_bytes=1024).snapshot()
    assert snap.skipped == "too_large" and snap.items == ()


@mac_only
def test_legacy_non_uti_flavours_survive_the_round_trip(private_pb):
    """Types declared under non-UTI names exist only as dyn.* types; dropping
    them would silently lose spreadsheet and CAD style copies."""
    from Foundation import NSData
    private_pb.declareTypes_owner_(["Biff12", "XML Spreadsheet"], None)
    private_pb.setData_forType_(NSData.dataWithBytes_length_(b"abc", 3), "Biff12")
    private_pb.setData_forType_(NSData.dataWithBytes_length_(b"<x/>", 4), "XML Spreadsheet")
    before = _types_and_data(private_pb)
    assert before and all(t.startswith("dyn.") for t, _ in before[0]), before
    mp = MacPasteboard(private_pb)
    snap = mp.snapshot()
    assert snap.skipped == "" and snap.items
    cc = mp.put_text("dictated")
    assert mp.restore(snap, cc) is True
    assert _types_and_data(private_pb) == before


@mac_only
def test_put_text_reports_the_count_that_is_ours(private_pb):
    mp = MacPasteboard(private_pb)
    assert mp.put_text("hi") == mp.change_count()


def test_put_text_detects_a_write_it_lost_a_race_on():
    class Racing:
        """Another app writes between our clear and our check."""

        def __init__(self):
            self.c = 0

        def clearContents(self):
            self.c += 1
            return self.c

        def writeObjects_(self, objs):
            self.c += 1              # someone else's write
            return True

        def changeCount(self):
            return self.c

    if not IS_MAC:
        pytest.skip("needs macOS (AppKit)")
    with pytest.raises(RuntimeError):
        MacPasteboard(Racing()).put_text("hi")


class _NSItem:
    def __init__(self, types, loader=lambda t: None):
        self._types, self._loader = types, loader

    def types(self):
        return self._types

    def dataForType_(self, t):
        return self._loader(t)


class _NSData(bytes):
    def length(self):
        return len(self)


class _NSPB:
    def __init__(self, items, cc=7):
        self._items, self._cc = items, cc

    def changeCount(self):
        return self._cc

    def pasteboardItems(self):
        return self._items


def test_an_unreadable_clipboard_is_not_mistaken_for_an_empty_one():
    snap = MacPasteboard(_NSPB([_NSItem(["public.png"], lambda t: None)])).snapshot()
    assert snap.skipped == "unreadable" and snap.items == ()
    # ...so restoring it does nothing, instead of clearing the clipboard.
    pb = FakePasteboard([], items=[[(STRING, b"old")]])
    assert pb.restore(snap, pb.cc) is False


def test_universal_clipboard_contents_are_skipped_without_reading_them():
    reads = []

    def loader(t):
        reads.append(t)
        return _NSData(b"x")
    snap = MacPasteboard(_NSPB([_NSItem(["public.png", "com.apple.is-remote-clipboard"], loader)])).snapshot()
    assert snap.skipped == "remote" and reads == [], "no fetch from the other device"


def test_slow_clipboard_reads_hit_a_time_budget():
    clock = Clock()

    def loader(t):
        clock.now += 1.5             # each lazily provided type takes 1.5 s
        return _NSData(b"x")
    snap = MacPasteboard(_NSPB([_NSItem(["a", "b", "c"], loader)]), budget=2.0, clock=clock).snapshot()
    assert snap.skipped == "slow" and snap.items == ()


# ── keycode resolution ───────────────────────────────────────────────────

QWERTY = {9: "v", 11: "b", 47: ".", 8: "c"}
DVORAK = {47: "v", 9: ".", 8: "j"}          # "v" lives where QWERTY has "."
COLEMAK = {9: "v", 11: "b"}                 # Colemak keeps V where QWERTY has it
DVORAK_QWERTY_CMD = {9: "v", 47: "."}       # the command table keeps QWERTY positions
RUSSIAN = {9: "м", 11: "и"}


def test_keycode_qwerty_and_colemak_are_9():
    assert resolve_v_keycode(QWERTY.get) == 9
    assert resolve_v_keycode(COLEMAK.get) == 9


def test_keycode_follows_dvorak():
    assert resolve_v_keycode(DVORAK.get) == 47


def test_keycode_dvorak_with_qwerty_command_stays_9():
    assert resolve_v_keycode(DVORAK_QWERTY_CMD.get) == 9


def test_keycode_falls_back_to_9_without_a_latin_v_or_a_mapper():
    assert resolve_v_keycode(RUSSIAN.get) == 9
    assert resolve_v_keycode(None) == 9

    def boom(_k):
        raise RuntimeError("no layout")
    assert resolve_v_keycode(boom) == 9


def test_keycode_prefers_9_when_two_keys_make_v():
    assert resolve_v_keycode({9: "V", 30: "v"}.get) == 9


def test_the_keycode_is_asked_afresh_for_each_paste():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    codes = iter([47, 9])
    paster = make_paster(events, pb, [FocusState(OPAQUE)], resolver=lambda: next(codes))
    paster.paste("a")
    paster.paste("b")
    assert [e for e in events if e.startswith("key:")] == ["key:47", "key:9"]


# ── waiting for the hotkey's modifiers to come up ────────────────────────

def test_modifier_wait_returns_at_once_when_nothing_is_held():
    clock = Clock()
    assert wait_for_modifiers_released(lambda: 0, 0.4, clock.sleep, clock) is True
    assert clock.slept == []


def test_modifier_wait_polls_until_released():
    clock = Clock()
    seq = iter([MOD_FN | MOD_SHIFT, MOD_FN, 0])
    assert wait_for_modifiers_released(lambda: next(seq), 0.4, clock.sleep, clock) is True
    assert len(clock.slept) == 2


def test_modifier_wait_times_out_at_400_ms_and_says_so():
    clock = Clock()
    assert wait_for_modifiers_released(lambda: MOD_COMMAND, 0.4, clock.sleep, clock) is False
    assert 0.39 <= sum(clock.slept) <= 0.45, sum(clock.slept)


def test_modifier_wait_ignores_caps_lock_and_unrelated_bits():
    clock = Clock()
    caps = 0x10000
    assert wait_for_modifiers_released(lambda: caps | 0x200000, 0.4, clock.sleep, clock) is True


def test_modifier_wait_survives_a_broken_flags_reader():
    clock = Clock()

    def boom():
        raise RuntimeError("no event source")
    assert wait_for_modifiers_released(boom, 0.4, clock.sleep, clock) is True


# ── outcome mapping ──────────────────────────────────────────────────────

def test_changed_text_is_pasted():
    assert classify_paste(text_state(5), text_state(25))[0] == PASTED


def test_unchanged_text_is_unverified_not_failed():
    """Custom editors keep a stale Accessibility value; "failed" would be a
    false alarm on a paste that worked."""
    status, reason = classify_paste(text_state(5), text_state(5))
    assert status == UNVERIFIED and reason == "no_change"


def test_no_focused_element_is_failed():
    status, reason = classify_paste(FocusState(NONE), FocusState(NONE))
    assert status == FAILED and reason == "no_focus"


def test_a_focused_button_is_failed():
    status, reason = classify_paste(FocusState(NOT_EDITABLE, role="AXButton"), FocusState(NOT_EDITABLE))
    assert status == FAILED and reason == "not_editable"


def test_apps_that_hide_their_text_are_unverified_never_failed():
    """Electron, terminals and custom editors."""
    assert classify_paste(FocusState(OPAQUE), FocusState(OPAQUE))[0] == UNVERIFIED
    assert classify_paste(FocusState(OPAQUE), None)[0] == UNVERIFIED
    assert classify_paste(FocusState(BLOCKED), FocusState(BLOCKED))[0] == UNVERIFIED
    assert classify_paste(None, None)[0] == UNVERIFIED


def test_focus_lost_after_the_paste_is_unverified():
    assert classify_paste(text_state(5), FocusState(OPAQUE))[0] == UNVERIFIED
    assert classify_paste(text_state(5), None)[0] == UNVERIFIED


# ── the paste sequence ───────────────────────────────────────────────────

def test_sequence_order_and_the_clipboard_is_restored():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"my old clipboard")]])
    paster = make_paster(events, pb, [text_state(5), text_state(30)], keycode=47)
    out = paster.paste("hello there")
    assert out == PasteOutcome(PASTED, "", restored=True, on_clipboard=False)
    assert pb.text() == "my old clipboard"
    assert [e for e in events if e != "ax"] == ["snapshot", "write:transient", "key:47", "restore"]
    last_ax = len(events) - 1 - events[::-1].index("ax")
    assert events.index("ax") < events.index("key:47") < last_ax, "Accessibility before and after the key"
    assert 0.5 <= sum(paster.clock.slept) < 1.2, "restore waits about 500 ms after the keystroke"


def test_unverified_is_not_a_failure_and_still_restores_after_about_a_second():
    events, stamps = [], {}
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    paster = make_paster(events, pb, [FocusState(OPAQUE)],
                         send=lambda kc: stamps.setdefault("key", paster.clock.now))
    pb.stamp = lambda: paster.clock.now
    out = paster.paste("hello")
    assert out.status == UNVERIFIED and out.ok and out.restored
    assert pb.text() == "old"
    assert 0.95 <= pb.restored_at - stamps["key"] <= 1.15, "a slow app gets about a second"


def test_a_confirmed_paste_restores_after_about_half_a_second():
    events, stamps = [], {}
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    paster = make_paster(events, pb, [text_state(5), text_state(30)],
                         send=lambda kc: stamps.setdefault("key", paster.clock.now))
    pb.stamp = lambda: paster.clock.now
    assert paster.paste("hello").status == PASTED
    assert 0.45 <= pb.restored_at - stamps["key"] <= 0.65


def test_a_late_change_in_a_readable_field_becomes_pasted_before_restoring():
    events, stamps = [], {}
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    paster = make_paster(events, pb, [text_state(5), text_state(5), text_state(5), text_state(30)],
                         send=lambda kc: stamps.setdefault("key", paster.clock.now))
    pb.stamp = lambda: paster.clock.now
    out = paster.paste("hello")
    assert out.status == PASTED and out.restored
    assert pb.restored_at - stamps["key"] < 1.1


def test_failed_leaves_the_text_on_the_clipboard_as_a_normal_copy_and_does_not_restore():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    out = make_paster(events, pb, [FocusState(NONE)]).paste("hello there")
    assert out == PasteOutcome(FAILED, "no_focus", restored=False, on_clipboard=True)
    assert pb.text() == "hello there", "the words must not be lost"
    assert pb.transient is False, "the surviving copy is a normal one, not a transient one"
    assert "restore" not in events


def test_unchanged_text_is_unverified_and_the_clipboard_is_restored():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    out = make_paster(events, pb, [text_state(5)]).paste("hello there")
    assert out.status == UNVERIFIED and out.reason == "no_change" and out.restored
    assert pb.text() == "old"


def test_without_accessibility_the_keystroke_is_not_sent_and_the_text_stays():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    out = make_paster(events, pb, [text_state(5)], trusted=False).paste("hello there")
    assert out == PasteOutcome(FAILED, "accessibility_off", on_clipboard=True)
    assert not any(e.startswith("key:") for e in events), "macOS would drop it anyway"
    assert pb.text() == "hello there" and pb.transient is False


def test_a_trust_check_that_cannot_run_pastes_as_before():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    paster = make_paster(events, pb, [FocusState(OPAQUE)])
    paster._trusted = lambda: None
    assert paster.paste("hello").status == UNVERIFIED
    assert "key:9" in events
    paster._trusted = lambda: (_ for _ in ()).throw(OSError("ctypes"))
    assert paster.paste("hello").status == UNVERIFIED


def test_keep_mode_takes_no_snapshot_and_leaves_the_dictation():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    out = make_paster(events, pb, [text_state(5), text_state(30)]).paste("hello there", restore=False)
    assert out == PasteOutcome(PASTED, "", restored=False, on_clipboard=True)
    assert pb.text() == "hello there" and pb.transient is False
    assert "snapshot" not in events and "write:plain" in events and "restore" not in events


def test_restore_is_skipped_if_the_user_copied_during_the_wait():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    paster = make_paster(events, pb, [text_state(5), text_state(30)],
                         send=lambda kc: pb.user_copies("fresh copy"))
    out = paster.paste("hello there")
    assert out.status == PASTED and out.restored is False
    assert out.on_clipboard is False, "their newer copy is on the clipboard, not our text"
    assert pb.text() == "fresh copy"


def test_a_modifier_that_never_comes_up_costs_400_ms_and_the_paste_still_goes():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    paster = make_paster(events, pb, [text_state(5), text_state(30)], flags=lambda: MOD_FN)
    assert paster.paste("hello there").status == PASTED
    assert "key:9" in events
    assert sum(paster.clock.slept) >= 0.4 + 0.5 - 0.02


def test_modifiers_released_part_way_pastes_early():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    seq = iter([MOD_FN, MOD_FN, 0, 0, 0, 0, 0])
    paster = make_paster(events, pb, [text_state(5), text_state(30)], flags=lambda: next(seq, 0))
    assert paster.paste("hello there").status == PASTED


def test_a_keystroke_error_fails_and_keeps_the_text():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])

    def explode(_kc):
        raise OSError("event source gone")
    out = make_paster(events, pb, [text_state(5)], send=explode).paste("hello there")
    assert out == PasteOutcome(FAILED, "keystroke_error", on_clipboard=True)
    assert pb.text() == "hello there" and pb.transient is False


def test_a_snapshot_error_still_pastes_but_cannot_restore():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    pb.raise_on_snapshot = True
    out = make_paster(events, pb, [text_state(5), text_state(30)]).paste("hello there")
    assert out.status == PASTED and out.restored is False and out.on_clipboard is True
    assert pb.text() == "hello there"


def test_a_concealed_clipboard_is_not_restored_and_the_text_stays_as_a_normal_copy():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"s3cret")]], skipped="concealed")
    out = make_paster(events, pb, [text_state(5), text_state(30)]).paste("hello there")
    assert out.status == PASTED and out.restored is False and out.on_clipboard is True
    assert pb.transient is False


def test_a_restore_that_fails_after_clearing_leaves_the_dictation_not_an_empty_clipboard():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    pb.restore_raises = True
    out = make_paster(events, pb, [text_state(5), text_state(30)]).paste("hello there")
    assert out.status == PASTED and out.restored is False and out.on_clipboard is True
    assert pb.text() == "hello there" and pb.transient is False


def test_a_clipboard_write_that_fails_is_a_failure_with_nothing_on_the_clipboard():
    events = []
    pb = FakePasteboard(events)
    pb.put_text = lambda text, transient=False: (_ for _ in ()).throw(RuntimeError("refused"))
    out = make_paster(events, pb, [text_state(5)]).paste("hello")
    assert out == PasteOutcome(FAILED, "clipboard_error", on_clipboard=False)
    assert not any(e.startswith("key:") for e in events)


def test_a_hung_target_app_cannot_stretch_the_paste_past_the_budget():
    """Each Accessibility read of a beach-balling app runs to its timeout. The
    paste stops looking once the budget is spent, so it stays well inside the
    pipeline's 8 s deadline and is simply unverified."""
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    paster = make_paster(events, pb, [text_state(5)], read_cost=1.2, verify_budget=2.5)
    out = paster.paste("hello there")
    assert out.status == UNVERIFIED
    assert paster._focus.reads <= 3, f"{paster._focus.reads} reads on a hung app"
    assert sum(paster.clock.slept) + paster._focus.reads * 1.2 < 6.0


# ── a paste the caller stopped waiting for ───────────────────────────────

def test_the_ticket_lets_exactly_one_of_abandon_and_restore_win():
    t = PasteTicket()
    assert t.abandon() is True
    assert t.abandoned and t.commit_restore() is False

    t = PasteTicket()
    assert t.commit_restore() is True
    assert t.abandon() is False and not t.abandoned


@pytest.mark.parametrize("round_", range(50))
def test_the_ticket_race_never_has_two_winners(round_):
    t = PasteTicket()
    wins = {}
    go = threading.Event()

    def abandoner():
        go.wait()
        wins["abandon"] = t.abandon()

    def restorer():
        go.wait()
        wins["restore"] = t.commit_restore()

    threads = [threading.Thread(target=abandoner), threading.Thread(target=restorer)]
    for th in threads:
        th.start()
    go.set()
    for th in threads:
        th.join(2)
    assert wins["abandon"] != wins["restore"], wins


def test_abandoned_during_the_wait_after_the_keystroke_the_text_stays_and_nothing_is_restored():
    """The deadline passed while the target app was reading the pasteboard.
    The caller has told the user the text is on the clipboard."""
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    ticket = PasteTicket()
    paster = make_paster(events, pb, [text_state(5), text_state(30)],
                         send=lambda kc: ticket.abandon())
    out = paster.paste("hello there", ticket=ticket)
    assert out.status == PASTED and out.restored is False and out.on_clipboard is True
    assert pb.text() == "hello there" and pb.transient is False
    assert "restore" not in events


def test_abandoned_before_the_keystroke_nothing_is_typed_and_the_text_stays():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    ticket = PasteTicket()
    gate = {"calls": 0}

    def flags():
        gate["calls"] += 1
        ticket.abandon()               # the deadline passes while waiting for the keys
        return 0
    out = make_paster(events, pb, [text_state(5)], flags=flags).paste("hello there", ticket=ticket)
    assert out == PasteOutcome(FAILED, "gave_up", on_clipboard=True)
    assert not any(e.startswith("key:") for e in events), "no late keystroke into another window"
    assert pb.text() == "hello there" and pb.transient is False


def test_a_restore_already_begun_wins_the_race_and_the_paste_counts_as_done():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    ticket = PasteTicket()
    seen = {}
    pb.on_restore = lambda: seen.setdefault("abandon", ticket.abandon())
    out = make_paster(events, pb, [text_state(5), text_state(30)]).paste("hello", ticket=ticket)
    assert out.restored is True and pb.text() == "old"
    assert seen["abandon"] is False, "the pipeline must be told the paste went in"


def test_a_busy_clipboard_pastes_without_restoring_instead_of_waiting_for_ever():
    events = []
    pb = FakePasteboard(events, items=[[(STRING, b"old")]])
    paster = make_paster(events, pb, [text_state(5), text_state(30)])
    paster.lock_wait = 0.05
    held, release = threading.Event(), threading.Event()

    def holder():
        with pasteboard._PASTE_LOCK:
            held.set()
            release.wait(5)
    th = threading.Thread(target=holder)
    th.start()
    assert held.wait(2)
    try:
        out = paster.paste("hello there")
    finally:
        release.set()
        th.join(2)
    assert out.status == PASTED and out.restored is False and out.on_clipboard is True
    assert "snapshot" not in events, "two overlapping sequences must not restore each other's data"


# ── Windows: copy and Ctrl+V, as before ──────────────────────────────────

class _FakePyperclip:
    def __init__(self):
        self.copied = []

    def copy(self, text):
        self.copied.append(text)


@pytest.fixture
def as_windows(monkeypatch):
    fake, sent = _FakePyperclip(), []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(pasteboard, "_windows_ctrl_v", lambda hwnd=None: sent.append(hwnd))
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    return fake, sent


def test_windows_copies_then_sends_ctrl_v_and_cannot_verify_or_restore(as_windows):
    fake, sent = as_windows
    out = pasteboard.paste_text("hello", window_handle=77, restore=True)
    assert out == PasteOutcome(UNVERIFIED, "no_verification", restored=False, on_clipboard=True)
    assert fake.copied == ["hello"] and sent == [77]


def test_windows_keystroke_error_fails_and_keeps_the_text(as_windows, monkeypatch):
    fake, sent = as_windows

    def boom(hwnd=None):
        raise OSError("no foreground window")
    monkeypatch.setattr(pasteboard, "_windows_ctrl_v", boom)
    out = pasteboard.paste_text("hello")
    assert out.status == FAILED and out.reason == "keystroke_error" and out.on_clipboard
    assert fake.copied == ["hello"]


def test_windows_copy_failure_fails_without_a_keystroke(as_windows, monkeypatch):
    fake, sent = as_windows
    monkeypatch.setattr(fake, "copy", lambda t: (_ for _ in ()).throw(RuntimeError("locked")))
    out = pasteboard.paste_text("hello")
    assert out == PasteOutcome(FAILED, "clipboard_error")
    assert sent == []


def test_windows_does_not_send_a_late_keystroke_once_the_caller_gave_up(as_windows):
    fake, sent = as_windows
    ticket = PasteTicket()
    ticket.abandon()
    out = pasteboard.paste_text("hello", ticket=ticket)
    assert out.status == FAILED and out.reason == "gave_up" and out.on_clipboard
    assert sent == []


# ── the setting ──────────────────────────────────────────────────────────

def test_clipboard_after_paste_setting():
    assert restore_after_paste({}) is True, "the default puts the clipboard back"
    assert restore_after_paste({"clipboard_after_paste": "restore"}) is True
    assert restore_after_paste({"clipboard_after_paste": "keep"}) is False
    assert restore_after_paste({"clipboard_after_paste": "nonsense"}) is True
    assert restore_after_paste(None) is True
    assert pasteboard.AFTER_PASTE_MODES == ("restore", "keep")


# ── the message for a paste that did not land ────────────────────────────

def test_failure_toast_wording_and_buttons_on_mac_and_windows():
    out = PasteOutcome(FAILED, "no_focus")
    mac = paste_failure_toast(out, "darwin")
    assert mac["heading"] == "Couldn't paste"
    assert mac["body"] == "Your text is on the clipboard. Click a text box and press Cmd+V."
    assert [(b["label"], b["action"], b["kind"]) for b in mac["buttons"]] == [
        ("Copy again", "copy_last", "primary"), ("Dismiss", "dismiss", "secondary")]
    assert "primary" not in mac
    win = paste_failure_toast(out, "win32")
    assert "Ctrl+V" in win["body"] and "Cmd" not in win["body"]


def test_failure_toast_for_missing_accessibility_opens_that_pane():
    t = paste_failure_toast(PasteOutcome(FAILED, "accessibility_off"), "darwin")
    assert t["buttons"][0] == {"label": "Open Settings", "action": "open_ax_settings", "kind": "primary"}
    assert "Accessibility" in t["body"] and "Cmd+V" in t["body"]


def test_failure_toast_for_a_clipboard_error_has_no_button_and_does_not_promise_the_clipboard():
    t = paste_failure_toast(PasteOutcome(FAILED, "clipboard_error"), "darwin")
    assert t["buttons"] is None
    assert "on the clipboard" not in t["body"]


def test_failure_messages_are_plain_sentences_with_no_dash_or_raw_error():
    for reason in ("no_focus", "not_editable", "keystroke_error", "gave_up", "accessibility_off",
                   "clipboard_error"):
        for platform in ("darwin", "win32"):
            t = paste_failure_toast(PasteOutcome(FAILED, reason), platform)
            for text in (t["heading"], t["body"]):
                assert "—" not in text and "–" not in text
                assert reason not in text and "Error" not in text and "Exception" not in text


def test_failure_toast_is_rate_limited_to_one_per_30_seconds():
    clock = Clock()
    limiter = RateLimiter(30, clock)
    out = PasteOutcome(FAILED, "no_focus")
    assert report_paste_failure(out, limiter, "darwin") is not None
    clock.now += 10
    assert report_paste_failure(out, limiter, "darwin") is None
    clock.now += 21
    assert report_paste_failure(out, limiter, "darwin") is not None
    assert pasteboard.PASTE_FAILURE_GAP_SECONDS == 30


def test_only_failures_produce_a_toast():
    limiter = RateLimiter(30, Clock())
    assert report_paste_failure(PasteOutcome(PASTED), limiter, "darwin") is None
    assert report_paste_failure(PasteOutcome(UNVERIFIED), limiter, "darwin") is None
    assert report_paste_failure(None, limiter, "darwin") is None


# ── paste last / copy last ───────────────────────────────────────────────

def _history(*items):
    return lambda: list(items)


def test_last_result_prefers_memory():
    last = LastResult(_history({"styled": "old history", "text": "old"}))
    last.set("fresh dictation")
    assert last.get() == "fresh dictation"


def test_last_result_after_a_restart_is_the_newest_usable_history_entry():
    last = LastResult(_history(
        {"styled": "first", "text": "first"},
        {"styled": "second cleaned", "text": "second raw"},
        {"styled": "warning", "text": "", "failed": True},      # a Not sent card
        {"styled": "", "text": ""},
    ))
    assert last.get() == "second cleaned", "skips Not sent cards and empty entries, prefers the clean-up"


def test_last_result_uses_the_raw_text_when_there_is_no_cleaned_text():
    assert LastResult(_history({"text": "only raw"})).get() == "only raw"


def test_last_result_is_empty_when_nothing_exists_or_the_history_breaks():
    assert LastResult(_history()).get() == ""
    assert LastResult(_history("not a dict", {"styled": 5})).get() == ""

    def boom():
        raise OSError("disk")
    assert LastResult(boom).get() == ""
    assert LastResult().get() == ""


def test_last_result_clear_forgets_memory_and_the_history_fallback():
    last = LastResult(_history({"styled": "from history"}))
    last.set("mem")
    last.clear()
    assert last.get() == "", "the user deleted their data: nothing to paste"
    assert last.word_count() == 0
    last.set("a new dictation")
    assert last.get() == "a new dictation", "and the next dictation is remembered again"


def test_last_result_ignores_blank_text():
    last = LastResult(_history({"styled": "kept"}))
    last.set("   ")
    last.set("")
    assert last.get() == "kept"


def test_last_result_counts_words_and_never_needs_to_show_them():
    last = LastResult()
    assert last.word_count() == 0
    last.set("one two  three\nfour")
    assert last.word_count() == 4
    last = LastResult(_history({"styled": "five words from the history ok"}))
    assert last.word_count() == 6


# ── the Accessibility reader's decisions, against scripted fake frameworks ─

NO_VALUE, CANNOT_COMPLETE, API_DISABLED, NOT_IMPLEMENTED = -25212, -25204, -25211, -25208


class Ref:
    def __init__(self, kind, value):
        self.kind, self.value = kind, value


class FakeAXLibs:
    """Answers attribute reads from a script and checks that every retained
    reference is released exactly once."""

    def __init__(self, script, trusted=True):
        self.script = script
        self._trusted = trusted
        self.outstanding = []
        self.asked = []

    def trusted(self):
        return self._trusted

    def system_wide(self, _timeout):
        return "sys"

    def application(self, _pid, _timeout):
        self.outstanding.append("app-element")
        return "app"

    def release_element(self, el):
        self.outstanding.remove("app-element")

    def copy_attr(self, element, name):
        self.asked.append(name)
        err, ref = self.script.get((element, name), (NOT_IMPLEMENTED, None))
        if ref is not None:
            self.outstanding.append(ref)
        return err, ref

    def release(self, ref):
        if ref is not None:
            self.outstanding.remove(ref)

    def is_string(self, ref):
        return ref is not None and ref.kind == "str"

    def string_length(self, ref):
        return len(ref.value)

    def to_str(self, ref):
        return ref.value

    def to_range(self, ref):
        return ref.value if ref is not None and ref.kind == "range" else None


def _reader(script, trusted=True, **kw):
    reader = mac_input.AXFocusReader(**kw)
    reader._libs = FakeAXLibs(script, trusted)
    reader._frontmost_pid = lambda: 4242
    return reader


def _field(role="AXTextArea", value="abc", rng=(3, 0), subrole=None):
    el = Ref("el", "element")
    script = {
        ("sys", "AXFocusedUIElement"): (0, el),
        (el, "AXRole"): (0, Ref("str", role)),
        (el, "AXValue"): (0, Ref("str", value)),
        (el, "AXSelectedTextRange"): (0, Ref("range", rng)),
    }
    if subrole:
        script[(el, "AXSubrole")] = (0, Ref("str", subrole))
    return script


def test_ax_reader_gives_a_fingerprint_that_changes_when_the_text_does():
    r1 = _reader(_field(value="hello", rng=(5, 0)))
    s1 = r1._read()
    assert s1.kind == TEXT and s1.role == "AXTextArea"
    assert s1.fingerprint[0] == 5 and s1.fingerprint[2:] == (5, 0)
    s2 = _reader(_field(value="hello there world", rng=(17, 0)))._read()
    assert s2.kind == TEXT and s2.fingerprint != s1.fingerprint
    assert classify_paste(s1, s2)[0] == PASTED
    assert r1._libs.outstanding == [], "every retained reference is released"


def test_ax_reader_never_reads_a_password_field():
    reader = _reader(_field(role="AXTextField", value="hunter2", subrole="AXSecureTextField"))
    state = reader._read()
    assert state.kind == OPAQUE, "unverified, never failed"
    assert "AXValue" not in reader._libs.asked, "its contents are never asked for"
    assert reader._libs.outstanding == []


def test_ax_reader_not_trusted_is_blocked_even_if_the_queries_say_nothing_is_focused():
    # An untrusted process gets "no value" answers; that must not read as "nothing focused".
    script = {("sys", "AXFocusedUIElement"): (NO_VALUE, None), ("app", "AXFocusedUIElement"): (NO_VALUE, None),
              ("app", "AXFocusedWindow"): (NO_VALUE, None)}
    assert _reader(script, trusted=False)._read().kind == BLOCKED


def test_ax_reader_button_is_not_editable():
    st = _reader(_field(role="AXButton"))._read()
    assert st.kind == NOT_EDITABLE and st.role == "AXButton"


def test_ax_reader_a_value_that_is_not_text_is_opaque():
    script = _field()
    el = script[("sys", "AXFocusedUIElement")][1]
    script[(el, "AXValue")] = (0, Ref("num", 7))
    assert _reader(script)._read().kind == OPAQUE
    script[(el, "AXValue")] = (NOT_IMPLEMENTED, None)
    assert _reader(script)._read().kind == OPAQUE


def test_ax_reader_huge_documents_are_not_read():
    assert _reader(_field(value="x" * 50), max_value_chars=10)._read().kind == OPAQUE


def test_ax_reader_nothing_focused_needs_no_element_and_no_window():
    script = {("sys", "AXFocusedUIElement"): (NO_VALUE, None), ("app", "AXFocusedUIElement"): (NO_VALUE, None),
              ("app", "AXFocusedWindow"): (NO_VALUE, None)}
    r = _reader(script)
    assert r._read().kind == NONE
    assert r._libs.outstanding == []
    # A focused window without a focused element is "can't tell", not "nothing".
    script[("app", "AXFocusedWindow")] = (0, Ref("el", "window"))
    r = _reader(script)
    assert r._read().kind == OPAQUE
    assert r._libs.outstanding == []


def test_ax_reader_falls_back_to_the_frontmost_app_when_the_system_wide_query_fails():
    el = Ref("el", "element")
    script = {
        ("sys", "AXFocusedUIElement"): (CANNOT_COMPLETE, None),
        ("app", "AXFocusedUIElement"): (0, el),
        (el, "AXRole"): (0, Ref("str", "AXTextField")),
        (el, "AXValue"): (0, Ref("str", "hi")),
        (el, "AXSelectedTextRange"): (0, Ref("range", (2, 0))),
    }
    r = _reader(script)
    assert r._read().kind == TEXT
    assert r._libs.outstanding == []


def test_ax_reader_unclear_failures_are_opaque_never_none():
    script = {("sys", "AXFocusedUIElement"): (CANNOT_COMPLETE, None),
              ("app", "AXFocusedUIElement"): (CANNOT_COMPLETE, None)}
    assert _reader(script)._read().kind == OPAQUE
    assert _reader({})._read().kind == OPAQUE


def test_ax_reader_api_disabled_is_blocked():
    assert _reader({("sys", "AXFocusedUIElement"): (API_DISABLED, None)})._read().kind == BLOCKED


def test_ax_reader_never_raises():
    reader = mac_input.AXFocusReader()

    class Boom:
        def trusted(self):
            raise RuntimeError("ctypes exploded")
    reader._libs = Boom()
    if IS_MAC:
        assert reader.read().kind == OPAQUE
    else:
        assert reader.read().kind == BLOCKED


# ── real layouts and the real system: read-only, no permissions ──────────

def _layout_table(source_id):
    """The 'uchr' table of an installed layout, looked up by input-source id."""
    import ctypes
    cf, carbon = mac_input._carbon()
    v = ctypes.c_void_p
    cf.CFStringCreateWithCString.restype = v
    cf.CFStringCreateWithCString.argtypes = [v, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [v]
    cf.CFArrayGetValueAtIndex.restype = v
    cf.CFArrayGetValueAtIndex.argtypes = [v, ctypes.c_long]
    cf.CFDictionaryCreate.restype = v
    cf.CFDictionaryCreate.argtypes = [v, ctypes.POINTER(v), ctypes.POINTER(v), ctypes.c_long, v, v]
    carbon.TISCreateInputSourceList.restype = v
    carbon.TISCreateInputSourceList.argtypes = [v, ctypes.c_ubyte]
    key = v.in_dll(carbon, "kTISPropertyInputSourceID")
    data_prop = v.in_dll(carbon, "kTISPropertyUnicodeKeyLayoutData")
    kcb = ctypes.addressof(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
    vcb = ctypes.addressof(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
    keys = (v * 1)(key.value)
    vals = (v * 1)(cf.CFStringCreateWithCString(None, source_id.encode(), 0x08000100))
    arr = carbon.TISCreateInputSourceList(cf.CFDictionaryCreate(None, keys, vals, 1, kcb, vcb), 1)
    if not arr or cf.CFArrayGetCount(arr) == 0:
        return None
    data = carbon.TISGetInputSourceProperty(cf.CFArrayGetValueAtIndex(arr, 0), data_prop)
    if not data:
        return None
    n = cf.CFDataGetLength(data)
    return ctypes.create_string_buffer(ctypes.string_at(cf.CFDataGetBytePtr(data), n), n), int(carbon.LMGetKbdType())


@mac_only
def test_real_layouts_give_the_right_paste_keycode():
    expected = {
        "com.apple.keylayout.US": 9,
        "com.apple.keylayout.Colemak": 9,
        "com.apple.keylayout.Dvorak": 47,                 # V sits where QWERTY has "."
        "com.apple.keylayout.DVORAK-QWERTYCMD": 9,        # Command shortcuts keep QWERTY positions
        "com.apple.keylayout.Russian": 9,                 # the Command table has a Latin v
        "com.apple.keylayout.Arabic": 9,
    }
    checked = 0
    for source_id, want in expected.items():
        found = _layout_table(source_id)
        if found is None:
            continue                  # layout not installed on this machine
        table, kbd = found
        got = resolve_v_keycode(mac_input._mapper_from_table(table, kbd), default=None)
        assert got == want, (source_id, got, want)   # default=None: a miss cannot hide behind 9
        checked += 1
    assert checked >= 2, "expected US and one more stock layout to be installed"


@mac_only
def test_the_ascii_capable_layout_is_the_second_choice_when_the_current_one_has_no_v(monkeypatch):
    calls = []

    def fake(ascii_capable=False):
        calls.append(ascii_capable)
        return {}.get if not ascii_capable else DVORAK.get
    monkeypatch.setattr(mac_input, "_layout_char_mapper", fake)
    assert mac_input._resolve_on_main_thread() == 47
    assert calls == [False, True]


def _in_fresh_python(code):
    """Run read-only macOS calls in a clean interpreter. (Other tests replace
    Quartz in this process with a stub.)"""
    prelude = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n"
    out = subprocess.run([sys.executable, "-I", "-c", prelude + code], capture_output=True,
                         text=True, timeout=60, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@mac_only
def test_the_real_layout_keycode_and_modifier_flags_are_valid_and_safe_to_read():
    out = _in_fresh_python(
        "import mac_input as m\n"
        "print(m.v_keycode_for_current_layout(), m.modifier_flags() >= 0)")
    code, flags_ok = out.split()
    assert 0 <= int(code) < 128 and flags_ok == "True"


@mac_only
def test_the_real_ax_reader_is_blocked_without_permission_and_never_reads_the_users_field():
    out = _in_fresh_python(
        "import mac_input as m, mac_permissions as p\n"
        "r = m.AXFocusReader(); r.warm()\n"
        "print('trusted' if p.accessibility_trusted() else r.read().kind)")
    # A trusted development machine is not asked what is in the user's focused field.
    assert out in ("trusted", BLOCKED)


@mac_only
def test_corefoundation_helpers_convert_strings_and_ranges():
    import ctypes
    L = mac_input._AXLibs()
    s = ctypes.c_void_p(L.cf.CFStringCreateWithCString(
        None, "héllo wörld ☃".encode(), mac_input._kCFStringEncodingUTF8))
    try:
        assert L.is_string(s) and L.string_length(s) == 13
        assert L.to_str(s) == "héllo wörld ☃"
    finally:
        L.release(s)
    L.ax.AXValueCreate.restype = ctypes.c_void_p
    L.ax.AXValueCreate.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    rng = mac_input._CFRange(7, 3)
    val = ctypes.c_void_p(L.ax.AXValueCreate(4, ctypes.byref(rng)))
    try:
        assert L.to_range(val) == (7, 3)
        assert not L.is_string(val)
    finally:
        L.release(val)


# ── the whole sequence against a real (private) pasteboard ───────────────

@mac_only
def test_real_pasteboard_pasted_restores_every_type_and_failed_keeps_a_plain_copy(private_pb):
    original = [
        [(STRING, b"user's own copy"), ("public.rtf", b"{\\rtf1 mine}"), ("com.waffler.test", b"\x01\x02\x03")],
        [("public.png", b"\x89PNG" * 50)],
    ]
    _write_items(private_pb, original)
    before = _types_and_data(private_pb)
    mp = MacPasteboard(private_pb)
    events = []
    seen = {}

    def on_key(_kc):
        seen["text"] = _text_on(private_pb)
        seen["types"] = {str(t) for t in private_pb.pasteboardItems()[0].types()}

    # Pasted: the dictation is on the pasteboard (and transient) when the key goes out.
    out = make_paster(events, mp, [text_state(5), text_state(30)], send=on_key).paste("dictated words")
    assert out.status == PASTED and out.restored and not out.on_clipboard
    assert seen["text"] == "dictated words"
    assert "org.nspasteboard.TransientType" in seen["types"]
    assert _types_and_data(private_pb) == before, "every type of the user's clipboard is back"

    # Failed: nothing restored, and what stays is an ordinary (not transient) copy.
    out = make_paster(events, mp, [FocusState(NONE)]).paste("dictated words")
    assert out.status == FAILED and not out.restored and out.on_clipboard
    assert _text_on(private_pb) == "dictated words"
    types = {str(t) for t in private_pb.pasteboardItems()[0].types()}
    assert not any(t.startswith("org.nspasteboard.") for t in types), types


@mac_only
def test_real_pasteboard_the_user_copying_during_the_wait_is_never_overwritten(private_pb):
    _write_items(private_pb, [[(STRING, b"before")]])
    mp = MacPasteboard(private_pb)
    events = []
    out = make_paster(events, mp, [text_state(5), text_state(30)],
                      send=lambda kc: _write_items(private_pb, [[(STRING, b"copied meanwhile")]])
                      ).paste("dictated words")
    assert out.status == PASTED and out.restored is False and out.on_clipboard is False
    assert _text_on(private_pb) == "copied meanwhile"


# ── the module stays light and honest ────────────────────────────────────

def test_pasteboard_imports_no_platform_framework_at_import_time():
    """It must import on Windows and in CI. The frameworks load when used."""
    src = (ROOT / "src" / "pasteboard.py").read_text(encoding="utf-8")
    head = src[:src.index("# ── The pasteboard (macOS)")]
    for name in ("AppKit", "Quartz", "Foundation"):
        assert f"import {name}" not in head and f"from {name}" not in head


def test_pasteboard_never_logs_text():
    """Every log line is metadata (outcome, reason). No f-string in the module
    mentions the text being pasted."""
    src = (ROOT / "src" / "pasteboard.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        if "self._log(" in line or "_logger(" in line:
            assert "{text" not in line and "{old" not in line, line
