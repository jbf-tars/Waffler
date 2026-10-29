"""Safe paste inside a dictation: what _process does with the outcome.

The real pipeline methods from app.py run against the fakes in
tests/_pipeline_harness.py (no network, no keys, no real clipboard, no
overlay). The paste itself is either the harness's FakeClipboard, which says
what a paste reported, or the real SafePaster over an in-memory pasteboard, to
test the one thing the fakes cannot: a paste that outlives the watchdog's
deadline must never put the user's old clipboard back over the words the
pipeline has just told them are there.
"""
import ast
import json
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
import notifier  # noqa: E402
import pasteboard  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import user_messages  # noqa: E402
from _pipeline_harness import (  # noqa: E402
    FakeClipboard, Hang, fast_limits, history, make_pipeline, run_process, wait_for,
)
from mac_input import OPAQUE, FocusState  # noqa: E402
from pasteboard import FAILED, PASTED, UNVERIFIED, PasteOutcome, SafePaster  # noqa: E402
from test_pasteboard import FakeFocus, FakePasteboard, STRING, text_state  # noqa: E402

STYLED = "Ok so ship it on monday."
RAW = "ok so ship it on monday"


@pytest.fixture
def limits(monkeypatch):
    fast_limits(monkeypatch)


def _settings(tmp_path, **kw):
    (tmp_path / "settings.json").write_text(json.dumps(kw), encoding="utf-8")


def _dictate(p, timeout=5):
    worker = run_process(p)
    worker.join(timeout)
    assert not worker.is_alive()
    return p


def _headings(p):
    return [t["heading"] for t in p.overlay.toasts()]


# ── what the paste reported ──────────────────────────────────────────────

def test_a_paste_that_went_in_leaves_no_message_and_is_remembered(tmp_path, limits):
    p = _dictate(make_pipeline(tmp_path))
    [call] = p.clipboard.paste_calls
    assert call["text"] == STYLED and call["restore"] is True and call["window"] is None
    assert isinstance(call["ticket"], pasteboard.PasteTicket)
    assert p.clipboard.pastes == [STYLED]
    assert p.overlay.toasts() == []
    assert history(p)[0]["styled"] == STYLED
    assert p.page.statuses[-1] == "done"
    assert p._last_result.get() == STYLED, "Paste last can reach it"


def test_the_paste_is_one_step_under_the_watchdog_not_a_copy_then_a_keystroke():
    process = next(n for n in ast.walk(ast.parse((ROOT / "app.py").read_text(encoding="utf-8")))
                   if isinstance(n, ast.FunctionDef) and n.name == "_process")
    src = ast.unparse(process)
    assert "run.call(self.clipboard.paste_text" in src
    assert "auto_paste(" not in src and ".auto_paste" not in src
    assert "self.clipboard.paste_text(" not in src.replace("run.call(self.clipboard.paste_text", "")
    assert src.count("ClipboardManager.paste()") == 0, "no clipboard reads in the pipeline"


def test_a_failed_paste_says_so_once_and_keeps_the_words(tmp_path, limits):
    out = PasteOutcome(FAILED, "no_focus", on_clipboard=True)
    p = _dictate(make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=out)))
    [toast] = p.overlay.toasts()
    assert toast["style"] == "warn" and toast["heading"] == "Couldn't paste"
    assert toast["body"] == "Your text is on the clipboard. Click a text box and press Ctrl+V."
    assert [b["action"] for b in toast["buttons"]] == ["copy_last", "dismiss"]
    assert "primary" not in toast
    assert history(p)[0]["styled"] == STYLED, "and the Journal has it"
    assert p.page.statuses[-1] == "done"
    assert p.clipboard.pastes == []


def test_a_failed_paste_message_comes_once_every_30_seconds(tmp_path, limits):
    out = PasteOutcome(FAILED, "no_focus", on_clipboard=True)
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=out))
    clock = [1000.0]
    p._notifier = notifier.Notifier(store=notifier.MemoryStore(), clock=lambda: clock[0],
                                    context=p._notifier_context)
    _dictate(p)
    clock[0] += 10
    _dictate(p)
    assert _headings(p) == ["Couldn't paste"], "the second one inside 30 s is not announced"
    assert any("suppressed" in m for m in p.logged)
    clock[0] += 25
    _dictate(p)
    assert _headings(p) == ["Couldn't paste", "Couldn't paste"]


def test_without_accessibility_the_message_has_a_button_to_that_pane(tmp_path, limits):
    out = PasteOutcome(FAILED, "accessibility_off", on_clipboard=True)
    p = _dictate(make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=out)))
    [toast] = p.overlay.toasts()
    assert toast["buttons"][0] == {"label": "Open Settings", "action": "open_ax_settings", "kind": "primary"}


def test_a_clipboard_that_cannot_be_written_says_so_without_promising_it(tmp_path, limits):
    p = _dictate(make_pipeline(tmp_path, clipboard=FakeClipboard(copy_ok=False)))
    [toast] = p.overlay.toasts()
    assert toast["heading"] == "Couldn't copy to the clipboard"
    assert "Journal" in toast["body"] and "on the clipboard" not in toast["body"]
    assert history(p)[0]["styled"] == STYLED


@pytest.mark.parametrize("status", [PASTED, UNVERIFIED])
def test_a_paste_that_may_have_gone_in_is_never_called_a_failure(tmp_path, limits, status):
    out = PasteOutcome(status, "no_change" if status == UNVERIFIED else "", restored=True)
    p = _dictate(make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=out)))
    assert p.overlay.toasts() == []


# ── the words and the clipboard after the paste ──────────────────────────

def _crash_after_the_paste(p):
    """Something after the paste fails: the error handler runs."""
    p.ns["notify_js_new_item"] = lambda item: (_ for _ in ()).throw(RuntimeError("page gone"))


def test_after_a_restore_the_error_handler_does_not_put_raw_words_over_the_users_clipboard(tmp_path, limits):
    p = make_pipeline(tmp_path)         # default: pasted, then the user's clipboard put back
    _crash_after_the_paste(p)
    _dictate(p)
    assert p.clipboard.copies == [STYLED], "only the dictation, once: nothing salvaged over the restore"
    [toast] = p.overlay.toasts()
    assert toast["heading"] == "Something went wrong"
    assert toast["body"] == "Your text was pasted and is in the Journal."


def test_when_the_words_stay_on_the_clipboard_the_message_says_so(tmp_path, limits):
    out = PasteOutcome(PASTED, restored=False, on_clipboard=True)       # "keep" mode
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=out))
    _crash_after_the_paste(p)
    _dictate(p)
    assert p.clipboard.copies == [STYLED]
    assert p.overlay.toasts()[-1]["body"] == "Your words are on the clipboard and in the Journal."


def test_a_failed_paste_leaves_the_words_so_nothing_is_salvaged_over_them(tmp_path, limits):
    out = PasteOutcome(FAILED, "no_focus", on_clipboard=True)
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=out))
    _crash_after_the_paste(p)
    _dictate(p)
    assert p.clipboard.copies == [STYLED]
    assert p.overlay.toasts()[-1]["body"] == "Your words are on the clipboard and in the Journal."


def test_the_clipboard_choice_reaches_the_paste(tmp_path, limits):
    cases = [({}, True), ({"clipboard_after_paste": "restore"}, True),
             ({"clipboard_after_paste": "keep"}, False), ({"clipboard_after_paste": "bogus"}, True)]
    for settings, restore in cases:
        _settings(tmp_path, **settings)
        p = _dictate(make_pipeline(tmp_path))
        assert p.clipboard.paste_calls[0]["restore"] is restore, settings


def test_settings_with_a_byte_order_mark_are_still_read(tmp_path, limits):
    (tmp_path / "settings.json").write_bytes(b"\xef\xbb\xbf" + b'{"clipboard_after_paste": "keep"}')
    p = _dictate(make_pipeline(tmp_path))
    assert p.clipboard.paste_calls[0]["restore"] is False


def test_with_auto_paste_off_the_words_are_just_copied(tmp_path, limits):
    _settings(tmp_path, auto_paste=False)
    p = _dictate(make_pipeline(tmp_path))
    assert p.clipboard.paste_calls == [] and p.clipboard.pastes == []
    assert p.clipboard.copies == [STYLED], "left on the clipboard: there is nothing to restore"
    assert p.overlay.toasts() == []
    assert p._last_result.get() == STYLED


def test_with_auto_paste_off_a_failed_copy_is_said(tmp_path, limits):
    _settings(tmp_path, auto_paste=False)
    p = _dictate(make_pipeline(tmp_path, clipboard=FakeClipboard(copy_ok=False)))
    assert wait_for(lambda: p.overlay.toasts(), timeout=2)
    assert p.overlay.toasts()[0]["heading"] == "Couldn't copy to the clipboard"


def test_a_dictation_that_is_not_pasted_is_still_remembered_for_paste_last(tmp_path, limits):
    """A newer recording started: no paste (it owns the clipboard), words kept."""
    hang = Hang(30)
    p = make_pipeline(tmp_path, styler=H.FakeStyler(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: hang.entered.is_set(), timeout=2)
    with p._processing_lock:
        p.is_recording = True
        p._processing_id += 1
    hang.release.set()
    worker.join(3)
    assert p.clipboard.paste_calls == []
    assert p._last_result.get() == STYLED


def test_a_cancelled_dictation_is_not_remembered(tmp_path, limits):
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=H.FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: hang.entered.is_set(), timeout=2)
    p._on_overlay_cancel()
    worker.join(3)
    hang.release.set()
    assert p._last_result.get() == "" and p.clipboard.paste_calls == []


# ── a paste that outlives the deadline ───────────────────────────────────

def test_the_pipeline_tells_a_paste_it_stopped_waiting_for_to_leave_the_words(tmp_path, monkeypatch):
    fast_limits(monkeypatch)
    hang = Hang(30)
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(paste_hang=hang))
    t0 = time.monotonic()
    worker = run_process(p)
    worker.join(5)
    hang.release.set()
    assert time.monotonic() - t0 < pw.PASTE_DEADLINE_S + 1.5
    [call] = p.clipboard.paste_calls
    assert call["ticket"].abandoned is True
    assert _headings(p) == ["Not pasted"]
    assert p.overlay.toasts()[0]["body"] == "Your text is on the clipboard and in the Journal. Press Ctrl+V to paste it."
    assert history(p)[0]["styled"] == STYLED
    assert p.page.statuses[-1] == "done"
    assert wait_for(lambda: p.clipboard.pastes == [] , timeout=1), "no late keystroke after the message"


def test_a_paste_that_had_already_begun_to_restore_is_not_called_not_pasted(tmp_path, monkeypatch):
    fast_limits(monkeypatch)
    hang = Hang(30)

    class Committing(FakeClipboard):
        def paste_text(self, text, window_handle=None, restore=True, settle_delay=None, ticket=None):
            self.paste_calls.append({"ticket": ticket})
            assert ticket.commit_restore()          # the old clipboard is on its way back
            hang.wait()
            self.pastes.append(text)
            return PasteOutcome(PASTED, restored=True)
    p = make_pipeline(tmp_path, clipboard=Committing())
    worker = run_process(p)
    worker.join(5)
    hang.release.set()
    assert not worker.is_alive()
    assert "Not pasted" not in _headings(p), "the paste went in; saying otherwise would be wrong"
    assert any("had gone in" in m for m in p.logged)
    assert history(p)[0]["styled"] == STYLED and p.page.statuses[-1] == "done"


def test_a_paste_that_raises_is_treated_like_one_that_did_not_finish(tmp_path, limits):
    class Raising(FakeClipboard):
        def paste_text(self, *a, **k):
            raise RuntimeError("pasteboard daemon gone")
    p = _dictate(make_pipeline(tmp_path, clipboard=Raising()))
    assert _headings(p) == ["Not pasted"]
    assert history(p)[0]["styled"] == STYLED


class _RealPasterClipboard(FakeClipboard):
    """The real SafePaster over an in-memory pasteboard, as the pipeline's
    clipboard. Its steps can be held up to stand in for a slow system."""

    def __init__(self, hold=None, hold_in="flags", old=b"my old clipboard", states=None):
        super().__init__()
        self.events = []
        self.logged = []
        self.pb = FakePasteboard(self.events, items=[[(STRING, old)]])
        self.finished = threading.Event()
        self.hold, self.hold_in = hold, hold_in
        states = states or [text_state(5), text_state(30)]
        focus = FakeFocus(self.events, states)
        real_read = focus.read

        def read():
            if self.hold is not None and self.hold_in == "after" and focus.reads == 1:
                self.hold.wait()
            return real_read()
        focus.read = read
        if self.hold is not None and self.hold_in == "restore":
            self.pb.on_restore = self.hold.wait

        def flags():
            if self.hold is not None and self.hold_in == "flags":
                self.hold.wait()
            return 0
        self.paster = SafePaster(
            self.pb, flags=flags, keycode_resolver=lambda: 9,
            send_key=lambda kc: self.events.append(f"key:{kc}"), focus_reader=focus,
            trusted=lambda: True, restore_delay=0.02, unconfirmed_extra_delay=0.02,
            modifier_timeout=0.05, settle_delay=0.0, log=self.logged.append)

    def paste_text(self, text, window_handle=None, restore=True, settle_delay=None, ticket=None):
        self.paste_calls.append({"text": text, "restore": restore, "ticket": ticket})
        try:
            out = self.paster.paste(text, restore=restore, settle_delay=settle_delay, ticket=ticket)
            if out.ok:
                self.pastes.append(text)
            return out
        finally:
            self.finished.set()


def test_end_to_end_a_normal_paste_puts_the_users_clipboard_back(tmp_path, limits):
    clip = _RealPasterClipboard()
    p = _dictate(make_pipeline(tmp_path, clipboard=clip))
    assert clip.finished.wait(3), "the restore may finish after the dictation has moved on"
    assert clip.pb.text() == "my old clipboard"
    assert [e for e in clip.events if e != "ax"] == ["snapshot", "write:transient", "key:9", "restore"]
    assert p.overlay.toasts() == [] and p.clipboard.pastes == [STYLED]


def test_the_dictation_ends_when_the_words_land_and_the_restore_follows_on_its_own(
        tmp_path, monkeypatch):
    """28 of one user's last 30 pastes went into apps whose text Accessibility
    cannot read (Chrome, Electron). Such a paste keeps the words on the
    clipboard for a second after the keystroke before putting the user's own
    back, and the pill, the Journal and the tick used to wait for that second
    too: the median paste step went from 160 ms to 1,100 ms with safe paste.
    They now come when the words land. The restore follows on the paste's
    own thread, which keeps the clipboard to itself until it is done."""
    fast_limits(monkeypatch, PASTE_DEADLINE_S=30.0)     # no deadline may end this wait
    hold = Hang(30)
    clip = _RealPasterClipboard(hold=hold, hold_in="restore", states=[FocusState(OPAQUE)])
    p = make_pipeline(tmp_path, clipboard=clip)
    try:
        worker = run_process(p)
        worker.join(5)
        assert not worker.is_alive(), "the dictation waited for the user's clipboard to be put back"
        assert ("end_working", ("done",), {}) in p.overlay.calls, "the pill ended with its tick"
        assert p.page.statuses[-1] == "done" and p.overlay.toasts() == []
        [entry] = history(p)
        assert entry["styled"] == STYLED and entry["paste"] == "unverified"
        assert "restore" not in clip.events and clip.pb.text() == STYLED, "all before the restore"
        assert pasteboard.paste_in_progress(), "which still holds the clipboard"
        assert not any("past its deadline" in m or "did not finish" in m for m in p.logged)
        assert any(m.startswith("[pipeline] clipboard+paste:") for m in p.logged)
    finally:
        hold.release.set()
    assert clip.finished.wait(3)
    assert clip.pb.text() == "my old clipboard", "then the user's own clipboard came back"
    assert any(m.startswith("[paste] outcome=unverified reason=opaque restored=True")
               for m in clip.logged)
    assert not pasteboard.paste_in_progress()


def test_a_paste_whose_words_stay_says_so_at_the_report(tmp_path, limits):
    """"Keep what I dictated on the clipboard": nothing is put back, and the
    pipeline is told the words are on the clipboard."""
    _settings(tmp_path, clipboard_after_paste="keep")
    clip = _RealPasterClipboard(states=[FocusState(OPAQUE)])
    p = make_pipeline(tmp_path, clipboard=clip)
    _crash_after_the_paste(p)
    _dictate(p)
    assert clip.finished.wait(3)
    assert p.overlay.toasts()[-1]["body"] == "Your words are on the clipboard and in the Journal."
    assert clip.pb.text() == STYLED and "restore" not in clip.events


def test_end_to_end_a_paste_stuck_before_the_keystroke_leaves_the_words_and_types_nothing(
        tmp_path, monkeypatch):
    fast_limits(monkeypatch)
    hold = Hang(30)
    clip = _RealPasterClipboard(hold=hold, hold_in="flags")
    p = make_pipeline(tmp_path, clipboard=clip)
    worker = run_process(p)
    worker.join(5)
    assert _headings(p) == ["Not pasted"], "the user is told the words are on the clipboard"
    hold.release.set()
    assert clip.finished.wait(3)
    assert not any(e.startswith("key:") for e in clip.events), "no keystroke into whatever is in front now"
    assert clip.pb.text() == STYLED and clip.pb.transient is False
    assert "restore" not in clip.events


def test_end_to_end_a_paste_stuck_after_the_keystroke_never_restores_over_the_promised_words(
        tmp_path, monkeypatch):
    fast_limits(monkeypatch)
    hold = Hang(30)
    clip = _RealPasterClipboard(hold=hold, hold_in="after")
    p = make_pipeline(tmp_path, clipboard=clip)
    worker = run_process(p)
    worker.join(5)
    assert _headings(p) == ["Not pasted"]
    hold.release.set()
    assert clip.finished.wait(3)
    assert clip.pb.text() == STYLED, "the old clipboard did not come back over the words"
    assert "restore" not in clip.events
    assert clip.pb.transient is False, "and they are a normal copy, not a transient one"


def test_end_to_end_a_restore_that_has_begun_wins_and_nothing_is_said(tmp_path, monkeypatch):
    fast_limits(monkeypatch)
    hold = Hang(30)
    clip = _RealPasterClipboard(hold=hold, hold_in="restore")
    p = make_pipeline(tmp_path, clipboard=clip)
    worker = run_process(p)
    worker.join(5)
    hold.release.set()
    assert clip.finished.wait(3)
    assert "Not pasted" not in _headings(p)
    assert clip.pb.text() == "my old clipboard"
    assert history(p)[0]["styled"] == STYLED


# ── paste last and copy last ─────────────────────────────────────────────

def _with_last(tmp_path, text="one two three four five", **kw):
    p = make_pipeline(tmp_path, **kw)
    p._last_result.set(text)
    return p


def test_paste_last_pastes_through_the_same_safe_paste_and_returns_the_outcome(tmp_path):
    p = _with_last(tmp_path)
    r = p.paste_last()
    assert r == {"ok": True, "outcome": "pasted"}
    [call] = p.clipboard.paste_calls
    assert call["text"] == "one two three four five" and call["restore"] is True
    assert call["settle_delay"] == 0.2, "the menu has just closed"
    assert call["ticket"] is None
    assert any("[paste-last] outcome=pasted words=5" in m for m in p.logged)


def test_paste_last_honours_the_clipboard_choice(tmp_path):
    _settings(tmp_path, clipboard_after_paste="keep")
    p = _with_last(tmp_path)
    p.paste_last()
    assert p.clipboard.paste_calls[0]["restore"] is False


def test_paste_last_on_windows_aims_at_the_app_last_dictated_into(tmp_path):
    p = _with_last(tmp_path)
    p._prev_window = 4242
    p.paste_last()                                       # the harness is a Windows machine
    assert p.clipboard.paste_calls[0]["window"] == 4242
    p2 = _with_last(tmp_path)
    p2._prev_window = 4242
    p2.ns["_platform"] = types.SimpleNamespace(system=lambda: "Darwin")
    p2.paste_last()
    assert p2.clipboard.paste_calls[0]["window"] is None, "macOS pastes into whatever has focus"


def test_a_failed_paste_last_says_so_and_returns_not_ok(tmp_path):
    out = PasteOutcome(FAILED, "no_focus", on_clipboard=True)
    p = _with_last(tmp_path, clipboard=FakeClipboard(outcome=out))
    assert p.paste_last() == {"ok": False, "outcome": "failed"}
    assert _headings(p) == ["Couldn't paste"]


def test_the_message_after_paste_last_follows_the_cursor_but_one_after_a_dictation_does_not(tmp_path):
    out = PasteOutcome(FAILED, "no_focus", on_clipboard=True)
    p = _with_last(tmp_path, clipboard=FakeClipboard(outcome=out))
    p.paste_last()
    assert p.overlay.toasts()[-1]["reposition"] is True, "no recording put the pill on this screen"
    p2 = _dictate(make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=out)))
    assert p2.overlay.toasts()[-1]["reposition"] is False, "the pill is already here"


def test_paste_last_and_copy_last_with_nothing_to_paste(tmp_path):
    p = make_pipeline(tmp_path)
    assert p.paste_last() == {"ok": False, "error": "no_transcript"}
    assert p.copy_last() == {"ok": False, "error": "no_transcript"}
    assert p.clipboard.paste_calls == [] and p.clipboard.copies == []


def test_copy_last_puts_the_text_on_the_clipboard(tmp_path):
    p = _with_last(tmp_path)
    assert p.copy_last() == {"ok": True}
    assert p.clipboard.copies == ["one two three four five"] and p.clipboard.paste_calls == []
    p = _with_last(tmp_path, clipboard=FakeClipboard(copy_ok=False))
    assert p.copy_last() == {"ok": False}


def test_after_a_restart_the_last_result_is_the_newest_journal_entry(tmp_path):
    p = make_pipeline(tmp_path)
    p._last_result = pasteboard.LastResult(history_loader=lambda: [
        {"styled": "an older one", "text": "an older one"},
        {"styled": "the newest dictation here", "text": "raw"},
        {"failed": True, "text": "", "styled": "a card waiting to be sent"}])
    assert p.paste_last() == {"ok": True, "outcome": "pasted"}
    assert p.clipboard.paste_calls[0]["text"] == "the newest dictation here"


def test_the_menu_shows_a_word_count_and_never_the_text(tmp_path):
    p = make_pipeline(tmp_path)
    assert p.last_transcript_titles() == ("Paste last transcript", "Copy last transcript", False)
    secret = "the password is hunter two and the launch is on friday"
    p._last_result.set(secret)
    paste, copy, enabled = p.last_transcript_titles()
    assert (paste, copy, enabled) == ("Paste last transcript (11 words)", "Copy last transcript (11 words)", True)
    assert "hunter" not in paste + copy
    p._last_result.set("hello")
    assert p.last_transcript_titles()[0] == "Paste last transcript (1 word)"


def test_forgetting_clears_the_memory_and_the_history_fallback(tmp_path):
    p = make_pipeline(tmp_path)
    p._last_result = pasteboard.LastResult(history_loader=lambda: [{"styled": "from the journal"}])
    p._last_result.set("in memory")
    p.forget_last_result()
    assert p.paste_last() == {"ok": False, "error": "no_transcript"}
    assert p.last_transcript_titles()[2] is False


# ── delete my data, clear history and the resets forget it ───────────────

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _api(name, **ns):
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)
    return ns[name]


class _Forgetful:
    def __init__(self):
        self.forgot = 0
        self._unsent_lock = threading.Lock()
        self._unsent_waiting = 3

    def forget_last_result(self):
        self.forgot += 1

    def forget_notifier(self):
        self.forgot_notifier = getattr(self, "forgot_notifier", 0) + 1


def test_delete_my_data_forgets_the_last_result(tmp_path):
    pipe = _Forgetful()
    privacy = types.SimpleNamespace(delete_my_data=lambda d: {"ok": True, "failed": []})
    fn = _api("delete_my_data", _pipeline=pipe, _history_lock=threading.Lock(), _privacy=privacy,
              DATA_DIR=tmp_path, _tray_state_now="idle",
              _tray_state=types.SimpleNamespace(NOT_SENT="not_sent", IDLE="idle"),
              _set_tray_state=lambda s: None, _log_to_file=lambda m: None)
    assert fn(object()) == {"ok": True}
    assert pipe.forgot == 1 and pipe._unsent_waiting == 0
    assert pipe.forgot_notifier == 1, "and the tips it remembered"


def test_clear_history_forgets_the_last_result(tmp_path):
    pipe = _Forgetful()
    saved = []
    fn = _api("clear_history", _pipeline=pipe, _history_lock=threading.Lock(),
              save_history=saved.append)
    assert fn(object()) == {"ok": True}
    assert saved == [[]] and pipe.forgot == 1


def test_factory_reset_forgets_the_last_result_before_the_data_goes(tmp_path):
    pipe = _Forgetful()
    data = tmp_path / "waffler"
    data.mkdir()
    seen = []
    pipe.forget_last_result = lambda: seen.append(data.exists())
    fn = _api("factory_reset", _pipeline=pipe, DATA_DIR=data, _log_to_file=lambda m: None,
              LoginItem=lambda: types.SimpleNamespace(disable=lambda: None),
              threading=types.SimpleNamespace(
                  Thread=lambda **k: types.SimpleNamespace(start=lambda: None)),
              time=time, _window_ref=None)
    assert fn(object())["ok"] is True
    assert seen == [True], "forgotten while the folder was still there"
    assert not data.exists()


def test_the_menu_bar_reset_forgets_it_too():
    src = ast.unparse(next(n for n in _TREE.body
                           if isinstance(n, ast.FunctionDef) and n.name == "_perform_factory_reset"))
    assert "_pipeline.forget_last_result()" in src
    assert src.index("forget_last_result") < src.index("rmtree")


# ── the bridge: paste_last, copy_last, and the setting ───────────────────

def test_the_bridge_methods_reach_the_pipeline_or_say_it_is_not_ready():
    calls = []
    pipe = types.SimpleNamespace(paste_last=lambda: calls.append("paste") or {"ok": True},
                                 copy_last=lambda: calls.append("copy") or {"ok": True})
    paste, copy = _api("paste_last", _pipeline=pipe), _api("copy_last", _pipeline=pipe)
    assert paste(object()) == {"ok": True} and copy(object()) == {"ok": True}
    assert calls == ["paste", "copy"]
    assert _api("paste_last", _pipeline=None)(object()) == {"ok": False, "error": "not_ready"}
    assert _api("copy_last", _pipeline=None)(object()) == {"ok": False, "error": "not_ready"}


def test_the_journals_copy_waits_its_turn_behind_a_paste(monkeypatch):
    """A dictation's Journal entry now shows up to a second before its paste
    has put the user's clipboard back. Copy on that entry goes through the
    same lock as every other copy, so it lands after the restore and is what
    is left on the clipboard."""
    order = []
    fake = types.SimpleNamespace(copy=lambda text: order.append(("copied", text)))
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    copy = _api("copy_item", _pasteboard=pasteboard, pyperclip=fake, print=lambda *a, **k: None)
    with pasteboard._PASTE_LOCK:
        done = []
        t = threading.Thread(target=lambda: done.append(copy(object(), "from the Journal")))
        t.start()
        t.join(0.3)
        assert t.is_alive() and order == [], "it waits while a paste holds the clipboard"
        order.append("restored")
    t.join(5)
    assert done == [True] and order == ["restored", ("copied", "from the Journal")]


def _settings_methods(tmp_path, initial=None):
    from _settings_fake import settings_api
    api = settings_api(tmp_path, initial)
    ns = {"os": __import__("os"), "_pipeline": None, "_log_to_file": lambda m: None,
          "CLIPBOARD_CHOICE_UNKNOWN": user_messages.CLIPBOARD_CHOICE_UNKNOWN,
          "_languages": __import__("languages")}
    return api, _api("save_settings", **ns), _api("get_settings", **ns)


def test_the_literals_in_app_py_match_the_modules_values():
    """get_settings and save_settings run in tests (and in other tools) without
    the pasteboard module, so they spell the two choices out. They must agree
    with the module."""
    def method(name):
        return next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)

    refusal = [c for c in ast.walk(method("save_settings"))
               if isinstance(c, ast.Compare) and isinstance(c.ops[0], ast.NotIn)
               and "clipboard_after_paste" in ast.unparse(c.left)]
    assert len(refusal) == 1
    assert tuple(ast.literal_eval(refusal[0].comparators[0])) == pasteboard.AFTER_PASTE_MODES
    reading = ast.unparse(method("get_settings"))
    assert f"== '{pasteboard.AFTER_PASTE_KEEP}'" in reading
    assert f"else '{pasteboard.AFTER_PASTE_RESTORE}'" in reading
    assert (pasteboard.AFTER_PASTE_RESTORE, pasteboard.AFTER_PASTE_KEEP) == ("restore", "keep")


@pytest.mark.parametrize("value", ["restore", "keep"])
def test_save_settings_stores_the_two_clipboard_choices(tmp_path, value):
    api, save, _get = _settings_methods(tmp_path, {"hotkey_keys": ["fn"]})
    r = save(api, {"clipboard_after_paste": value})
    assert r["ok"] is True, r
    assert api.data == {"hotkey_keys": ["fn"], "clipboard_after_paste": value}


@pytest.mark.parametrize("bad", ["", "Keep", "always", None, True, 1, ["keep"], {"a": 1}])
def test_save_settings_refuses_any_other_clipboard_choice_and_saves_nothing(tmp_path, bad):
    api, save, _get = _settings_methods(tmp_path, {"clipboard_after_paste": "keep", "dialect": "en-GB"})
    r = save(api, {"clipboard_after_paste": bad, "dialect": "en-US"})
    assert r == {"ok": False, "error": "Waffler doesn't know that clipboard choice. Pick one from the list."}
    assert "—" not in r["error"]
    assert api.data == {"clipboard_after_paste": "keep", "dialect": "en-GB"}
    assert api.saves == 0


def test_save_settings_without_the_key_leaves_the_choice_alone(tmp_path):
    api, save, _get = _settings_methods(tmp_path, {"clipboard_after_paste": "keep"})
    assert save(api, {"auto_paste": False})["ok"] is True
    assert api.data == {"clipboard_after_paste": "keep", "auto_paste": False}


def test_get_settings_reports_the_choice_and_defaults_to_restore(tmp_path):
    ns = {"os": __import__("os"), "_pipeline": None, "_log_to_file": lambda m: None}
    get = _api("get_settings", **ns)
    for stored, want in (({}, "restore"), ({"clipboard_after_paste": "restore"}, "restore"),
                         ({"clipboard_after_paste": "keep"}, "keep"),
                         ({"clipboard_after_paste": "garbage"}, "restore")):
        api = types.SimpleNamespace(_load_settings_file=lambda s=stored: s)
        assert get(api)["clipboard_after_paste"] == want, stored


# ── the menu bar and the tray ────────────────────────────────────────────

def _module_fns(*names, **ns):
    nodes = [n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    return ns


class _Item:
    def __init__(self):
        self.title, self.enabled = "", None

    def setTitle_(self, t):
        self.title = t

    def setEnabled_(self, e):
        self.enabled = e


def test_the_menu_items_get_a_word_count_and_are_enabled_only_with_a_result():
    pipe = types.SimpleNamespace(last_transcript_titles=lambda: ("Paste last transcript (42 words)",
                                                                  "Copy last transcript (42 words)", True))
    paste, copy = _Item(), _Item()
    ns = _module_fns("_last_transcript_menu", "_refresh_last_transcript_items",
                     _pipeline=pipe, _mac_last_items=(paste, copy), _log_to_file=lambda m: None,
                     last_transcript_title=user_messages.last_transcript_title)
    ns["_refresh_last_transcript_items"]()
    assert (paste.title, copy.title) == ("Paste last transcript (42 words)", "Copy last transcript (42 words)")
    assert paste.enabled is True and copy.enabled is True
    ns["_pipeline"] = None                        # not ready: plain and disabled
    ns["_refresh_last_transcript_items"]()
    assert (paste.title, copy.title) == ("Paste last transcript", "Copy last transcript")
    assert paste.enabled is False and copy.enabled is False


def test_a_menu_that_cannot_read_the_last_result_is_disabled_not_broken():
    def boom():
        raise RuntimeError("history unreadable")
    ns = _module_fns("_last_transcript_menu", _pipeline=types.SimpleNamespace(last_transcript_titles=boom),
                     _log_to_file=lambda m: None, last_transcript_title=user_messages.last_transcript_title)
    assert ns["_last_transcript_menu"]() == ("Paste last transcript", "Copy last transcript", False)


def test_the_menu_callbacks_paste_and_copy_off_the_menus_thread():
    started = []
    ran = threading.Event()
    pipe = types.SimpleNamespace(paste_last=lambda: (started.append(threading.current_thread().name), ran.set()),
                                 copy_last=lambda: started.append(threading.current_thread().name))
    ns = _module_fns("_paste_last_async", "_copy_last_async", _pipeline=pipe, threading=threading)
    ns["_paste_last_async"]()
    assert ran.wait(2) and started[0] == "PasteLast"
    ns["_copy_last_async"]()
    assert wait_for(lambda: len(started) == 2, timeout=2) and started[1] == "CopyLast"
    ns["_pipeline"] = None
    ns["_paste_last_async"]()                      # not ready: nothing, no error


def test_the_windows_tray_menu_is_rebuilt_when_the_last_result_changes():
    rebuilt = []
    tray = types.SimpleNamespace(update_menu=lambda: rebuilt.append(1))
    ns = _module_fns("_refresh_tray_menu", _platform=types.SimpleNamespace(system=lambda: "Windows"),
                     _tray_icon=tray, _mac_last_items=None)
    ns["_refresh_tray_menu"]()
    assert rebuilt == [1]
    ns["_tray_icon"] = None
    ns["_refresh_tray_menu"]()                     # no tray yet: nothing, no error
    tray.update_menu = lambda: (_ for _ in ()).throw(RuntimeError("icon gone"))
    ns["_tray_icon"] = tray
    ns["_refresh_tray_menu"]()


def test_the_mac_menu_has_both_items_a_delegate_and_no_automatic_enabling():
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_create_mac_menubar_icon")
    src = ast.unparse(fn)
    assert "menuNeedsUpdate_" in src and "menu.setDelegate_(target)" in src
    assert "menu.setAutoenablesItems_(False)" in src
    assert "b'pasteLast:'" in src and "b'copyLast:'" in src
    assert "def pasteLast_(self, _sender)" in src and "def copyLast_(self, _sender)" in src
    assert "_mac_last_items = (item_paste_last, item_copy_last)" in src
    # Between Show and Factory Reset, so Quit stays last.
    assert src.index("b'show:'") < src.index("b'pasteLast:'") < src.index("b'copyLast:'") \
        < src.index("b'factoryReset:'") < src.index("b'quit:'")


def test_the_windows_tray_menu_has_both_items_with_counts_in_their_labels():
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_create_windows_tray_icon")
    src = ast.unparse(fn)
    assert "_last_transcript_menu()[0]" in src and "_last_transcript_menu()[1]" in src
    assert "enabled=lambda item=None: _last_transcript_menu()[2]" in src
    assert src.index("'Show Waffler'") < src.index("_paste_last_async") < src.index("_copy_last_async") \
        < src.index("'Quit'")


def test_no_menu_code_reads_the_text_of_the_last_result():
    """Menus are visible on screen shares: only the count may reach them."""
    names = ("_last_transcript_menu", "_refresh_last_transcript_items", "_create_windows_tray_icon",
             "_create_mac_menubar_icon")
    for fn in _TREE.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in names:
            src = ast.unparse(fn)
            assert "_last_result.get" not in src and "last_text" not in src, fn.name
    pipe = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    titles = next(n for n in pipe.body if isinstance(n, ast.FunctionDef) and n.name == "last_transcript_titles")
    assert ".get()" not in ast.unparse(titles) and "word_count()" in ast.unparse(titles)


def test_the_toast_actions_the_new_messages_use_are_all_handled():
    toast_actions = {"copy_last", "dismiss", *["open_mic_settings", "open_ax_settings", "open_input_settings"]}
    handler = next(n for n in ast.walk(next(n for n in _TREE.body
                                           if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline"))
                   if isinstance(n, ast.FunctionDef) and n.name == "_on_toast_action")
    src = ast.unparse(handler)
    assert "action == 'copy_last'" in src and "action == 'dismiss'" in src
    assert "action in _mac_perms.PANE_ACTIONS" in src
    import mac_permissions
    assert toast_actions - {"copy_last", "dismiss"} == set(mac_permissions.PANE_ACTIONS)


def test_copy_again_copies_the_last_result_off_the_overlays_thread(tmp_path):
    p = _with_last(tmp_path)
    p._on_toast_action("copy_last")
    assert wait_for(lambda: p.clipboard.copies == ["one two three four five"], timeout=2)
