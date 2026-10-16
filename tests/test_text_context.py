"""The app in front, the selection and the text around the cursor (S5).

Every platform piece is a fake: no NSWorkspace, no Accessibility, no window,
no clipboard and no keystroke is touched, and nothing here reads the title or
text of anything on the machine running the tests.
"""
import ast
import dataclasses
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pipeline_watchdog as pw  # noqa: E402
import selection_copy as sc  # noqa: E402
import text_context as tc  # noqa: E402
from text_context import (  # noqa: E402
    AXRead, AppContext, ContextReader, Front, MacAX, SelectionResult, UNKNOWN,
)

SAFARI = Front("com.apple.Safari", "Safari", 501)
CHROME = Front("com.google.Chrome", "Google Chrome", 502)
MAIL = Front("com.apple.mail", "Mail", 503)
NOTES = Front("com.apple.Notes", "Notes", 504)
SLACK = Front("com.tinyspeck.slackmacgap", "Slack", 505)
WIN_SLACK = Front("", "Slack", 0)


class FakeAX:
    """A MacAX-shaped object; every call is recorded."""

    def __init__(self, trusted=True, title="", selection=None, cursor=None, boom=None):
        self._trusted = trusted
        self.title = title
        self.selection = selection if selection is not None else AXRead(tc.AX_UNAVAILABLE)
        self.cursor = cursor
        self.boom = boom
        self.calls = []

    def warm(self):
        self.calls.append(("warm",))

    def trusted(self):
        self.calls.append(("trusted",))
        return self._trusted

    def window_title(self, pid, timeout):
        self.calls.append(("title", pid, timeout))
        if self.boom:
            raise self.boom
        return self.title

    def selected_text(self, pid, timeout):
        self.calls.append(("selection", pid, timeout))
        if self.boom:
            raise self.boom
        return self.selection

    def cursor_text(self, pid, timeout):
        self.calls.append(("cursor", pid, timeout))
        if self.boom:
            raise self.boom
        return self.cursor

    def names(self):
        return [c[0] for c in self.calls]


class FakeTrip:
    """A CopyRoundTrip-shaped object."""

    def __init__(self, result=sc.CopyResult("copied words", sc.OK)):
        self.result = result
        self.budgets = []

    def read(self, budget):
        self.budgets.append(budget)
        return self.result


def reader(front=SAFARI, ax=None, trip=None, platform="darwin", clock=None, logs=None):
    return ContextReader(front=(front if callable(front) else (lambda: front)), ax=ax, round_trip=trip,
                         platform=platform, clock=clock or time.monotonic,
                         log=(logs.append if logs is not None else (lambda m: None)))


# ── the record ───────────────────────────────────────────────────────────────

def test_an_app_context_has_four_fields_and_is_immutable():
    ctx = AppContext("com.apple.mail", "Mail", "Inbox", "email")
    assert (ctx.bundle_id, ctx.name, ctx.title, ctx.category) == ("com.apple.mail", "Mail", "Inbox", "email")
    assert [f.name for f in dataclasses.fields(AppContext)] == ["bundle_id", "name", "title", "category"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.name = "x"
    assert hash(ctx) == hash(AppContext("com.apple.mail", "Mail", "Inbox", "email"))


def test_the_unknown_record_is_empty_and_other():
    assert UNKNOWN == AppContext("", "", "", "other") and not UNKNOWN.known
    assert AppContext(name="Mail").known and AppContext(bundle_id="x").known


def test_a_window_title_never_appears_in_a_logged_record():
    ctx = AppContext("com.apple.mail", "Mail", "Re: salary for Sam", "email")
    assert "salary" not in repr(ctx) and "salary" not in str(ctx) and "salary" not in f"{ctx}"
    assert "Mail" in repr(ctx) and "email" in repr(ctx)


def test_make_context_trims_bounds_and_categorises():
    ctx = tc.make_context("  com.apple.mail ", "  Mail\n", "x" * 1000)
    assert ctx.bundle_id == "com.apple.mail" and ctx.name == "Mail" and len(ctx.title) == tc.TITLE_MAX
    assert ctx.category == "email"
    assert tc.make_context(None, 5, b"x") == UNKNOWN
    assert len(tc.make_context("", "n" * 500, "").name) == tc.NAME_MAX


# ── the app in front ─────────────────────────────────────────────────────────

def test_the_app_is_read_with_its_category():
    ctx = reader(MAIL, FakeAX()).frontmost_app()
    assert ctx == AppContext("com.apple.mail", "Mail", "", "email")
    assert reader(SLACK, FakeAX()).frontmost_app().category == "work"
    assert reader(NOTES, FakeAX()).frontmost_app().category == "other"


def test_a_browser_title_is_read_and_decides_the_category():
    ax = FakeAX(title="Inbox (3) - me@example.com - Gmail")
    ctx = reader(SAFARI, ax).frontmost_app()
    assert ctx.title == "Inbox (3) - me@example.com - Gmail" and ctx.category == "email"
    assert ("title", 501, tc.TITLE_TIMEOUT_S) in ax.calls


def test_the_title_is_not_read_for_an_app_that_is_not_a_browser():
    ax = FakeAX(title="Secret plans")
    ctx = reader(MAIL, ax).frontmost_app()
    assert ctx.title == "" and "title" not in ax.names()


def test_the_title_can_be_asked_for_or_refused():
    ax = FakeAX(title="Doc")
    assert reader(NOTES, ax).frontmost_app(read_title=True).title == "Doc"
    ax2 = FakeAX(title="Doc")
    assert reader(SAFARI, ax2).frontmost_app(read_title=False).title == ""
    assert "title" not in ax2.names()


def test_without_accessibility_the_title_is_not_read_but_the_app_is_still_known():
    ax = FakeAX(trusted=False, title="never read")
    ctx = reader(CHROME, ax).frontmost_app()
    assert ctx == AppContext("com.google.Chrome", "Google Chrome", "", "other")
    assert "title" not in ax.names()


def test_a_title_that_fails_to_read_leaves_the_app_known():
    logs = []
    ctx = reader(CHROME, FakeAX(boom=RuntimeError("AX hung")), logs=logs).frontmost_app()
    assert ctx.name == "Google Chrome" and ctx.title == "" and ctx.category == "other"
    assert logs and "RuntimeError" in logs[0]


def test_no_accessibility_layer_at_all_gives_the_name_only():
    ctx = reader(WIN_SLACK, ax=None, platform="win32").frontmost_app()
    assert ctx == AppContext("", "Slack", "", "work")


def test_windows_names_are_categorised_without_a_bundle_id():
    for name, want in (("OUTLOOK", "email"), ("Teams", "work"), ("WhatsApp", "personal"), ("notepad", "other")):
        assert reader(Front("", name, 0), None, platform="win32").frontmost_app().category == want, name


def test_a_front_app_that_cannot_be_found_is_unknown():
    assert reader(None, FakeAX()).frontmost_app() == UNKNOWN


def test_a_lookup_that_raises_is_unknown_and_logged_without_detail():
    logs = []

    def boom():
        raise OSError("no window server")
    assert reader(boom, FakeAX(), logs=logs).frontmost_app() == UNKNOWN
    assert logs == ["[context] app lookup failed: OSError"]


def test_the_public_call_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(tc, "_default_reader", boom)
    assert tc.frontmost_app() == UNKNOWN
    assert tc.get_selected_text() == ("", "none")
    assert tc.read_selection().reason == tc.NOT_READABLE
    assert tc.get_text_around_cursor() == ("", "")
    tc.warm()                      # and warming up is best effort too


def test_the_public_calls_use_this_computers_reader(monkeypatch):
    r = reader(MAIL, FakeAX(selection=AXRead(tc.AX_TEXT, "hi there")))
    monkeypatch.setattr(tc, "_reader", r)
    assert tc.frontmost_app().name == "Mail"
    assert tc.get_selected_text() == ("hi there", "ax")


def test_off_a_mac_and_a_pc_there_is_no_front_app_and_no_selection(monkeypatch):
    monkeypatch.setattr(tc, "_reader", None)
    monkeypatch.setattr(tc.sys, "platform", "linux")
    assert tc.frontmost_app() == UNKNOWN
    assert tc.get_selected_text(allow_copy_fallback=True) == ("", "none")
    monkeypatch.setattr(tc, "_reader", None)          # leave no reader behind


# ── the selection through Accessibility ──────────────────────────────────────

def test_the_selection_is_read_through_accessibility():
    ax = FakeAX(selection=AXRead(tc.AX_TEXT, "Rewrite this please"))
    trip = FakeTrip()
    r = reader(NOTES, ax, trip).read_selection(allow_copy_fallback=True)
    assert r == SelectionResult("Rewrite this please", "ax", "")
    assert r.as_tuple() == ("Rewrite this please", "ax")
    assert trip.budgets == [], "no copy when Accessibility could read it"
    assert ax.calls[-1][1] == NOTES.pid


def test_every_accessibility_message_has_a_short_timeout():
    ax = FakeAX(selection=AXRead(tc.AX_TEXT, "x"))
    reader(NOTES, ax).read_selection(timeout=10.0)
    sel = [c for c in ax.calls if c[0] == "selection"][0]
    assert sel[2] <= tc.AX_CALL_TIMEOUT_S, "a hung app answers after a moment, not after the whole budget"
    ax2 = FakeAX(selection=AXRead(tc.AX_TEXT, "x"))
    reader(NOTES, ax2).read_selection(timeout=0.2)
    assert [c for c in ax2.calls if c[0] == "selection"][0][2] <= 0.1


def test_nothing_selected_is_an_answer_and_does_not_start_a_copy():
    ax = FakeAX(selection=AXRead(tc.AX_EMPTY))
    trip = FakeTrip()
    r = reader(NOTES, ax, trip).read_selection(allow_copy_fallback=True)
    assert r == SelectionResult("", "none", tc.NO_SELECTION)
    assert trip.budgets == []


def test_a_password_field_is_never_read_or_copied():
    ax = FakeAX(selection=AXRead(tc.AX_SECURE))
    trip = FakeTrip()
    r = reader(NOTES, ax, trip).read_selection(allow_copy_fallback=True)
    assert r == SelectionResult("", "none", tc.SECURE_FIELD) and trip.budgets == []


def test_a_selection_too_large_is_refused_not_cut():
    ax = FakeAX(selection=AXRead(tc.AX_TOO_LARGE))
    r = reader(NOTES, ax, FakeTrip()).read_selection(allow_copy_fallback=True)
    assert r == SelectionResult("", "none", tc.TOO_LARGE)


def test_without_the_accessibility_permission_there_is_no_read_and_no_copy_key():
    ax = FakeAX(trusted=False, selection=AXRead(tc.AX_TEXT, "never read"))
    trip = FakeTrip()
    r = reader(NOTES, ax, trip).read_selection(allow_copy_fallback=True)
    assert r == SelectionResult("", "none", tc.ACCESSIBILITY_OFF)
    assert "selection" not in ax.names() and trip.budgets == []


# ── the copy fallback ────────────────────────────────────────────────────────

def test_the_copy_is_refused_unless_the_caller_allows_it():
    ax = FakeAX(selection=AXRead(tc.AX_UNAVAILABLE))
    trip = FakeTrip()
    r = reader(SLACK, ax, trip).read_selection()                       # the default
    assert r == SelectionResult("", "none", tc.NOT_READABLE)
    assert trip.budgets == [], "ordinary dictation never presses Copy"
    assert tc.get_selected_text.__defaults__[0] is False
    assert tc.read_selection.__defaults__[0] is False


def test_the_copy_is_used_when_accessibility_cannot_say_and_it_is_allowed():
    ax = FakeAX(selection=AXRead(tc.AX_UNAVAILABLE))
    trip = FakeTrip(sc.CopyResult("words from Slack", sc.OK))
    r = reader(SLACK, ax, trip).read_selection(allow_copy_fallback=True, timeout=1.5)
    assert r == SelectionResult("words from Slack", "copy", "")
    assert trip.budgets and 0 < trip.budgets[0] <= 1.5


def test_a_copy_that_found_nothing_means_no_selection():
    ax = FakeAX(selection=AXRead(tc.AX_UNAVAILABLE))
    r = reader(SLACK, ax, FakeTrip(sc.CopyResult("", sc.NO_COPY))).read_selection(True)
    assert r == SelectionResult("", "none", tc.NO_SELECTION)


@pytest.mark.parametrize("why", [sc.NOT_RESTORABLE, sc.BUSY, sc.KEYSTROKE_FAILED, sc.UNREADABLE])
def test_why_a_copy_gave_nothing_is_passed_on(why):
    ax = FakeAX(selection=AXRead(tc.AX_UNAVAILABLE))
    r = reader(SLACK, ax, FakeTrip(sc.CopyResult("", why))).read_selection(True)
    assert r == SelectionResult("", "none", why)


def test_no_time_left_after_accessibility_means_no_copy():
    now = [0.0]

    class SlowAX(FakeAX):
        def selected_text(self, pid, timeout):
            now[0] += 1.4                       # a hung app used up the budget
            return AXRead(tc.AX_UNAVAILABLE)
    trip = FakeTrip()
    r = reader(SLACK, SlowAX(), trip, clock=lambda: now[0]).read_selection(True, timeout=1.5)
    assert r == SelectionResult("", "none", sc.TIMED_OUT) and trip.budgets == []


def test_a_copied_selection_that_is_far_too_large_is_refused():
    big = "x" * (tc.MAX_SELECTION_CHARS + 1)
    ax = FakeAX(selection=AXRead(tc.AX_UNAVAILABLE))
    r = reader(SLACK, ax, FakeTrip(sc.CopyResult(big, sc.OK))).read_selection(True)
    assert r == SelectionResult("", "none", tc.TOO_LARGE)


def test_the_clipboard_object_is_not_made_until_a_copy_is_wanted():
    made = []

    def factory():
        made.append(1)
        return FakeTrip(sc.CopyResult("copied", sc.OK))
    ax = FakeAX(selection=AXRead(tc.AX_TEXT, "from ax"))
    rd = ContextReader(front=lambda: SLACK, ax=ax, round_trip_factory=factory)
    rd.frontmost_app()
    rd.read_selection(allow_copy_fallback=True)                 # Accessibility answered
    rd.text_around_cursor()
    assert made == []
    ax.selection = AXRead(tc.AX_UNAVAILABLE)
    assert rd.read_selection(allow_copy_fallback=True).as_tuple() == ("copied", "copy")
    rd.read_selection(allow_copy_fallback=True)
    assert made == [1], "made once, then kept"


def test_no_round_trip_available_means_not_readable():
    ax = FakeAX(selection=AXRead(tc.AX_UNAVAILABLE))
    assert reader(SLACK, ax, None).read_selection(True).reason == tc.NOT_READABLE


# ── Windows: no Accessibility, the copy fallback, never into a console ───────

def test_windows_has_no_accessibility_selection_but_may_copy_when_allowed():
    trip = FakeTrip(sc.CopyResult("from Word", sc.OK))
    r = reader(Front("", "WINWORD", 0), None, trip, platform="win32")
    assert r.read_selection().as_tuple() == ("", "none")
    assert trip.budgets == []
    assert r.read_selection(allow_copy_fallback=True).as_tuple() == ("from Word", "copy")


@pytest.mark.parametrize("name", ["powershell", "cmd", "WindowsTerminal", "pwsh", ""])
def test_windows_never_sends_ctrl_c_into_a_console_or_an_unknown_window(name):
    trip = FakeTrip()
    r = reader(Front("", name, 0), None, trip, platform="win32").read_selection(True)
    assert r == SelectionResult("", "none", tc.NOT_ALLOWED_HERE) and trip.budgets == []


def test_a_mac_terminal_may_be_copied_from():
    trip = FakeTrip(sc.CopyResult("ls -la", sc.OK))
    ax = FakeAX(selection=AXRead(tc.AX_UNAVAILABLE))
    r = reader(Front("com.apple.Terminal", "Terminal", 9), ax, trip).read_selection(True)
    assert r.as_tuple() == ("ls -la", "copy")


# ── nothing raises, nothing is logged but the kind of failure ────────────────

def test_a_reader_that_blows_up_gives_nothing_and_logs_only_the_type():
    logs = []
    ax = FakeAX(boom=ValueError("SECRET-SELECTION"))
    r = reader(NOTES, ax, logs=logs).read_selection(True)
    assert r == SelectionResult("", "none", tc.NOT_READABLE)
    assert logs and all("SECRET" not in m for m in logs)


def test_a_front_lookup_that_raises_while_reading_gives_nothing():
    def boom():
        raise RuntimeError("x")
    assert reader(boom, FakeAX()).read_selection(True) == SelectionResult("", "none", tc.NOT_READABLE)
    assert reader(boom, FakeAX()).text_around_cursor() == ("", "")


def test_junk_arguments_do_not_raise():
    r = reader(NOTES, FakeAX())
    assert r.read_selection(allow_copy_fallback="yes", timeout=None).method == "none"
    assert r.text_around_cursor("many", None) == ("", "")


# ── through the watchdog, as the pipeline will call it ───────────────────────

def test_a_hung_read_is_given_up_on_by_the_watchdog_and_the_pipeline_moves_on():
    """get_selected_text blocks; the pipeline runs it with run.call, so a read
    that never returns costs the deadline, not the dictation."""
    release = threading.Event()

    class HungAX(FakeAX):
        def selected_text(self, pid, timeout):
            release.wait(10)
            return AXRead(tc.AX_TEXT, "too late")
    rd = reader(NOTES, HungAX())
    run = pw.DictationRun("select")
    started = time.monotonic()
    result = run.call(rd.read_selection, True, stage=pw.STYLING, deadline=0.3)
    try:
        assert result.status == pw.DEADLINE
        assert time.monotonic() - started < 2.0
    finally:
        release.set()


def test_a_read_that_finishes_comes_back_through_the_watchdog_as_a_plain_value():
    rd = reader(NOTES, FakeAX(selection=AXRead(tc.AX_TEXT, "selected")))
    run = pw.DictationRun("select")
    result = run.call(rd.read_selection, stage=pw.STYLING, deadline=2.0)
    assert result.ok and result.value.as_tuple() == ("selected", "ax")


# ── the Accessibility reads (fake bindings) ──────────────────────────────────

class Ref:
    def __init__(self, kind, value):
        self.kind, self.value = kind, value


class FakeAXLibs:
    """Stands in for mac_input._AXLibs: a tree of elements by pid, attribute
    lookups that succeed or fail like Accessibility's, and a count of what is
    retained and released (a leak here would be a leak in a real session)."""

    CANNOT_COMPLETE = -25204
    UNSUPPORTED = -25205
    NO_VALUE = -25212

    def __init__(self, apps):
        self.apps = apps                 # pid -> {"AXFocusedUIElement": element, ...}
        self.timeouts = []
        self.retained = 0
        self.ax = types.SimpleNamespace(
            AXUIElementSetMessagingTimeout=lambda el, t: self.timeouts.append(t))
        self.hung_pids = set()

    def application(self, pid, timeout):
        self.timeouts.append(timeout)
        self.retained += 1
        return ("app", pid)

    def copy_attr(self, element, name):
        if isinstance(element, tuple) and element[0] == "app":
            if element[1] in self.hung_pids:
                return self.CANNOT_COMPLETE, None
            attrs = self.apps.get(element[1], {})
        else:
            attrs = element.value if isinstance(element, Ref) else {}
        if name not in attrs:
            return self.UNSUPPORTED, None
        value = attrs[name]
        self.retained += 1
        if isinstance(value, dict):
            return 0, Ref("element", value)
        if isinstance(value, tuple):
            return 0, Ref("range", value)
        return 0, Ref("string", value)

    def release(self, ref):
        if ref is not None:
            self.retained -= 1

    def release_element(self, element):
        if element:
            self.retained -= 1

    def is_string(self, ref):
        return ref is not None and ref.kind == "string"

    def string_length(self, ref):
        return len(ref.value)

    def to_str(self, ref):
        return ref.value

    def to_range(self, ref):
        return ref.value if ref is not None and ref.kind == "range" else None


def mac_ax(apps, trusted=True):
    libs = FakeAXLibs(apps)
    return MacAX(libs_factory=lambda: libs, trusted=lambda: trusted), libs


def test_mac_ax_reads_the_selected_text_of_the_focused_element():
    ax, libs = mac_ax({7: {"AXFocusedUIElement": {"AXSelectedText": "hello there", "AXRole": "AXTextArea"}}})
    assert ax.selected_text(7, 0.2) == AXRead(tc.AX_TEXT, "hello there")
    assert libs.retained == 0, "everything retained was released"
    assert libs.timeouts and all(t == 0.2 for t in libs.timeouts), "a timeout on every element used"


def test_mac_ax_says_empty_for_no_selection_and_for_whitespace_only():
    for value in ("", "   \n"):
        ax, libs = mac_ax({7: {"AXFocusedUIElement": {"AXSelectedText": value}}})
        assert ax.selected_text(7, 0.2).status == tc.AX_EMPTY
        assert libs.retained == 0


def test_mac_ax_never_reads_a_password_field():
    ax, libs = mac_ax({7: {"AXFocusedUIElement": {"AXSubrole": "AXSecureTextField",
                                                  "AXSelectedText": "hunter2", "AXValue": "hunter2",
                                                  "AXSelectedTextRange": (0, 7)}}})
    assert ax.selected_text(7, 0.2) == AXRead(tc.AX_SECURE)
    assert ax.cursor_text(7, 0.2) is None
    assert libs.retained == 0


def test_mac_ax_is_unavailable_when_the_app_does_not_share_a_selection():
    for tree in ({7: {}}, {7: {"AXFocusedUIElement": {"AXRole": "AXWebArea"}}}, {}):
        ax, libs = mac_ax(tree)
        assert ax.selected_text(7, 0.2).status == tc.AX_UNAVAILABLE
        assert libs.retained == 0


def test_a_hung_app_comes_back_unavailable_not_stuck():
    ax, libs = mac_ax({7: {"AXFocusedUIElement": {"AXSelectedText": "x"}}})
    libs.hung_pids.add(7)                         # Accessibility times out: CannotComplete
    assert ax.selected_text(7, 0.1).status == tc.AX_UNAVAILABLE
    assert ax.cursor_text(7, 0.1) is None
    assert ax.window_title(7, 0.1) == ""
    assert libs.retained == 0 and libs.timeouts and all(t == 0.1 for t in libs.timeouts)


def test_mac_ax_refuses_a_selection_beyond_the_cap():
    big = "x" * (tc.MAX_SELECTION_CHARS + 1)
    ax, _ = mac_ax({7: {"AXFocusedUIElement": {"AXSelectedText": big}}})
    assert ax.selected_text(7, 0.2) == AXRead(tc.AX_TOO_LARGE)


def test_mac_ax_reads_the_title_of_the_focused_window():
    ax, libs = mac_ax({7: {"AXFocusedWindow": {"AXTitle": "Inbox - Gmail"}}})
    assert ax.window_title(7, 0.1) == "Inbox - Gmail"
    assert libs.retained == 0
    ax2, _ = mac_ax({7: {}})
    assert ax2.window_title(7, 0.1) == ""
    ax3, _ = mac_ax({7: {"AXFocusedWindow": {}}})
    assert ax3.window_title(7, 0.1) == ""


def test_mac_ax_reads_the_value_and_the_selection_range():
    ax, libs = mac_ax({7: {"AXFocusedUIElement": {"AXValue": "hello world", "AXSelectedTextRange": (6, 0)}}})
    assert ax.cursor_text(7, 0.2) == ("hello world", 6, 0)
    assert libs.retained == 0
    ax2, _ = mac_ax({7: {"AXFocusedUIElement": {"AXValue": "hello"}}})
    assert ax2.cursor_text(7, 0.2) is None, "a value with no caret position cannot be split"


def test_mac_ax_never_raises_whatever_the_bindings_do():
    def boom():
        raise OSError("framework missing")
    ax = MacAX(libs_factory=boom, trusted=lambda: True)
    assert ax.selected_text(1, 0.1).status == tc.AX_UNAVAILABLE
    assert ax.cursor_text(1, 0.1) is None and ax.window_title(1, 0.1) == ""
    assert ax.selected_text("not a pid", 0.1).status == tc.AX_UNAVAILABLE


def test_mac_ax_trust_is_asked_each_time_and_failure_means_no():
    state = {"v": False}
    ax = MacAX(libs_factory=lambda: None, trusted=lambda: state["v"])
    assert ax.trusted() is False
    state["v"] = True
    assert ax.trusted() is True
    assert MacAX(libs_factory=lambda: None, trusted=lambda: None).trusted() is False

    def boom():
        raise RuntimeError("x")
    assert MacAX(libs_factory=lambda: None, trusted=boom).trusted() is False


# ── text around the cursor ───────────────────────────────────────────────────

def test_the_text_either_side_of_the_caret():
    ax = FakeAX(cursor=("Hello brave new world", 12, 0))
    assert reader(NOTES, ax).text_around_cursor(300) == ("Hello brave ", "new world")


def test_a_selection_is_left_out_of_both_sides():
    ax = FakeAX(cursor=("Hello brave new world", 6, 5))
    assert reader(NOTES, ax).text_around_cursor(300) == ("Hello ", " new world")


def test_the_cut_falls_on_a_word_boundary():
    value = "alpha bravo charlie delta echo foxtrot golf hotel"
    start = value.index("echo")
    assert reader(NOTES, FakeAX(cursor=(value, start, 0))).text_around_cursor(12) == ("delta ", "echo foxtrot")
    # Wide enough to hold everything: nothing is cut.
    assert reader(NOTES, FakeAX(cursor=(value, start, 0))).text_around_cursor(300) == (
        "alpha bravo charlie delta ", "echo foxtrot golf hotel")


def test_tail_and_head_cut_at_word_boundaries():
    assert tc.tail("one two three four", 9) == "four", "'hree four' would split a word"
    assert tc.tail("one two three four", 10) == "three four"
    assert tc.tail("one two three four", 100) == "one two three four"
    assert tc.tail("abcdefghij", 4) == "ghij", "one long word is cut as it is"
    assert tc.tail("ab cd", 3) == " cd", "a cut that lands on a space is already a boundary"
    assert tc.head("one two three four", 9) == "one two", "'one two t' would split a word"
    assert tc.head("one two three four", 7) == "one two"
    assert tc.head("one two three four", 100) == "one two three four"
    assert tc.head("abcdefghij", 4) == "abcd"
    assert tc.tail("x", 0) == "" and tc.head("x", 0) == "" and tc.tail("x", -1) == ""


def test_positions_count_utf16_units_like_accessibility_does():
    value = "a\U0001F600b c"                 # the emoji is two UTF-16 units
    assert tc.split_at_selection(value, 3, 0) == ("a\U0001F600", "b c")
    assert tc.split_at_selection(value, 1, 2) == ("a", "b c")
    assert tc.split_at_selection("héllo", 2, 0) == ("hé", "llo")


def test_split_at_selection_survives_bad_positions():
    assert tc.split_at_selection("abc", 99, 5) == ("abc", "")
    assert tc.split_at_selection("abc", -1, 0) == ("", "")
    assert tc.split_at_selection("abc", 1, -1) == ("", "")
    assert tc.split_at_selection(None, 0, 0) == ("", "")
    assert tc.split_at_selection("a\U0001F600b", 2, 0)[1].endswith("b"), "a split emoji is dropped, not raised"


def test_cursor_text_is_empty_when_it_cannot_be_read():
    assert reader(NOTES, FakeAX(cursor=None)).text_around_cursor() == ("", "")
    assert reader(NOTES, FakeAX(trusted=False, cursor=("a", 1, 0))).text_around_cursor() == ("", "")
    assert reader(NOTES, None).text_around_cursor() == ("", "")
    assert reader(NOTES, FakeAX(cursor=("a", 1, 0))).text_around_cursor(0) == ("", "")
    assert reader(Front("", "Notes", 0), FakeAX(cursor=("a", 1, 0))).text_around_cursor() == ("", "")
    assert reader(NOTES, FakeAX(boom=RuntimeError("x"))).text_around_cursor() == ("", "")


def test_cursor_text_uses_a_short_timeout():
    ax = FakeAX(cursor=("abc", 1, 0))
    reader(NOTES, ax).text_around_cursor(300, timeout=10)
    assert ax.calls[-1][2] <= tc.AX_CALL_TIMEOUT_S


# ── what is promised about logging and use ───────────────────────────────────

SOURCES = ("text_context.py", "selection_copy.py")
TEXT_NAMES = {"text", "title", "selected", "selection", "before", "after", "value", "copied",
              "items", "read"}


def _calls_named(tree, names):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in names:
                yield node


@pytest.mark.parametrize("fname", SOURCES)
def test_nothing_these_modules_log_contains_text_a_title_or_a_selection(fname):
    tree = ast.parse((ROOT / "src" / fname).read_text(encoding="utf-8"))
    checked = 0
    for call in _calls_named(tree, {"_log", "_logger", "log"}):
        checked += 1
        for arg in call.args:
            names = {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}
            attrs = {n.attr for n in ast.walk(arg) if isinstance(n, ast.Attribute)}
            assert not (names | attrs) & TEXT_NAMES, f"{fname}:{call.lineno} logs {names | attrs}"
    assert not list(_calls_named(tree, {"print"})), f"{fname} prints"
    assert checked or fname == "text_context.py"


def test_ordinary_dictation_never_reads_the_selection_or_the_clipboard():
    """The dictation path may ask which app is in front; it never asks what is
    selected, never presses Copy and never reads the clipboard."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    for name in ("_process", "on_hotkey_press", "on_hotkey_release", "_tidy_transcript", "redo_take"):
        body = ast.unparse(next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name))
        for forbidden in ("get_selected_text", "read_selection", "get_text_around_cursor",
                          "allow_copy_fallback", "selection_copy", "CopyRoundTrip"):
            assert forbidden not in body, f"{name} uses {forbidden}"
    # One caller asks for it: a transform the person started (Settings,
    # Transforms), through its hooks. Nothing on the dictation path does.
    assert src.count("allow_copy_fallback=True") == 1, "only the Transform hook asks for the copy fallback"
    hooks = ast.unparse(next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "_transform_hooks"))
    assert "allow_copy_fallback=True" in hooks
