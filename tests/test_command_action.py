"""The command flow (S8), from the held shortcut to the result, with fakes.

Nothing real is touched: no microphone, no Accessibility, no clipboard, no
keystroke, no browser, no model. The hooks are plain functions that record
what they were asked, so the tests can say what was pasted, what was opened,
what was saved, which toast was shown and what the person's text was left as.
"""
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import command_action as ca  # noqa: E402
import command_mode as cm  # noqa: E402
import pasteboard  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import text_context as tc  # noqa: E402
import user_messages as um  # noqa: E402
from style_openai import ChatError, ChatReply  # noqa: E402

SEL = ("I just wanted to reach out to let you know that the meeting has now been moved "
       "to Thursday at 3pm because the room is not free on Wednesday.")
SHORT = "The meeting moved to Thursday at 3pm because the room is not free on Wednesday."
NOTES = tc.make_context("com.apple.Notes", "Notes")
MAIL = tc.make_context("com.apple.mail", "Mail")
SPEECH = H.speech_wav(3.0)


def wait_for(predicate, timeout=5.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())


def selected(text=SEL, method="ax", reason=tc.OK):
    return tc.SelectionResult(text, method, reason)


NONE_SELECTED = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
UNREADABLE = tc.SelectionResult("", tc.METHOD_NONE, tc.NOT_READABLE)


def reply(text, **kw):
    kw.setdefault("provider", "groq")
    kw.setdefault("model", "openai/gpt-oss-120b")
    kw.setdefault("input_tokens", 150)
    kw.setdefault("output_tokens", 30)
    kw.setdefault("finish_reason", "stop")
    return ChatReply(text=text, **kw)


class Rig:
    """A runner wired to recorders. Set the ``*_result`` attributes to change
    what the fakes answer; read the lists to see what happened."""

    def __init__(self, *, settings=None, **runner):
        self.stored = {"command_mode": {"enabled": True}}
        self.stored.update(settings or {})
        self.busy = False
        self.permissions = True
        self.front = NOTES
        self.window = None
        self.audio = SPEECH
        self.audio_block = None
        self.spoken = "make this shorter"
        self.stt_error = None
        self.stt_block = None
        self.ax_result = selected()
        self.copy_result = selected(method="copy")
        self.copy_block = None
        self.context = ("", "")
        self.reread = None
        self.chat_text = f"ACTION: edit\n{SHORT}"
        self.chat_error = None
        self.chat_block = None
        self.chat_finish = "stop"
        self.paste_outcome = pasteboard.PasteOutcome(pasteboard.PASTED, restored=True)
        self.paste_block = None
        self.save_ok = True
        self.copy_ok = True
        self.open_ok = True
        self.skew = 0.0
        self.events = []
        self.toasts, self.chats, self.pastes, self.saved = [], [], [], []
        self.copies, self.remembered, self.usage, self.speech_usage = [], [], [], []
        self.announced, self.opened, self.reads, self.ui, self.logged = [], [], [], [], []
        self.no_audio_calls, self.cues, self.context_reads, self.rereads = [], [], [], []
        self.released = False
        self.reads_after_release = []
        self.threads = []
        self.runner = ca.CommandRunner(self.hooks(), read_deadline_s=2.0, stt_deadline_s=3.0, total_deadline_s=6.0,
                                       paste_deadline_s=2.0, max_hold_s=30.0, capture_read_s=1.0,
                                       capture_wait_s=1.5, min_ask_s=1.0, **runner)

    def hooks(self):
        return ca.Hooks(
            settings=lambda: self.stored,
            busy_elsewhere=lambda: self.busy,
            permissions_ok=lambda: self.permissions,
            start_audio=lambda: self.events.append("audio_start"),
            stop_audio=self._stop_audio,
            transcribe=self._transcribe,
            read_selection=self._read,
            read_context=self._context,
            chat=self._chat,
            paste=self._paste,
            settle_paste=self._settle,
            copy=self._copy,
            save_history=self._save,
            say=lambda *a: self.toasts.append(a),
            open_url=self._open,
            level=lambda: 0.4,
            apply_vocab=lambda t: t.replace("Wafler", "Waffler"),
            classify_speech_error=lambda e: "offline" if "connection" in str(e).lower() else "error",
            speech_provider=lambda: "Groq",
            front_app=lambda: self.front,
            window=lambda: self.window,
            reread_selection=self._reread,
            remember=self.remembered.append,
            record_usage=self.usage.append,
            record_speech=lambda s, info: self.speech_usage.append((round(s, 1), info)),
            announce_item=self.announced.append,
            cue=self.cues.append,
            no_audio=lambda: self.no_audio_calls.append(1),
            ui_begin=lambda run: self.ui.append("begin"),
            ui_level=lambda v: None,
            ui_working=lambda run: self.ui.append("working"),
            ui_tick=lambda run, s: None,
            ui_end=lambda run, outcome: self.ui.append(("end", outcome, run.shown)),
            log=self.logged.append,
            now_iso=lambda: "2026-10-16T09:30:00",
            monotonic=lambda: time.monotonic() + self.skew,
            platform="darwin",
            spawn=self._spawn)

    def _spawn(self, fn):
        t = threading.Thread(target=fn, daemon=True)
        self.threads.append(t)
        t.start()

    # -- the fakes ---------------------------------------------------------

    def _stop_audio(self):
        self.events.append("audio_stop")
        if self.audio_block is not None:
            self.audio_block.wait(10)
        return self.audio

    def _transcribe(self, audio):
        if self.stt_block is not None:
            self.stt_block.wait(10)
        if self.stt_error is not None:
            raise self.stt_error
        return self.spoken, {"backend": "groq"}

    def _read(self, allow_copy):
        self.reads.append(allow_copy)
        if allow_copy:
            self.reads_after_release.append(self.released)
            if self.copy_block is not None:
                self.copy_block.wait(10)
            return self.copy_result
        return self.ax_result

    def _context(self, chars):
        self.context_reads.append(chars)
        return self.context

    def _reread(self, method):
        self.rereads.append(method)
        return self.reread

    def _chat(self, system, user, **kw):
        self.chats.append({"system": system, "user": user, **kw})
        if self.chat_block is not None:
            self.chat_block.wait(10)
        if self.chat_error is not None:
            raise self.chat_error
        return reply(self.chat_text, finish_reason=self.chat_finish)

    def _paste(self, text, window, restore, ticket):
        self.pastes.append({"text": text, "window": window, "restore": restore, "ticket": ticket})
        if self.paste_block is not None:
            self.paste_block.wait(10)
        return self.paste_outcome

    def _settle(self, result, ticket):
        if result.ok:
            return result.value.on_clipboard, result.value.status != pasteboard.FAILED
        return True, False

    def _copy(self, text):
        self.copies.append(text)
        return self.copy_ok

    def _save(self, entry):
        if not self.save_ok:
            return False
        entry["id"] = 71 + len(self.saved)
        self.saved.append(entry)
        return True

    def _open(self, url):
        self.opened.append(url)
        return self.open_ok

    # -- driving it --------------------------------------------------------

    def press(self):
        return self.runner.down()

    def let_go(self, held=1.0):
        """The keys come up after ``held`` seconds (the clock is moved on)."""
        self.skew += held
        self.released = True
        self.runner.up()

    def finished(self):
        return any(isinstance(u, tuple) and u[0] == "end" for u in self.ui) and not self.runner.busy()

    def go(self, held=1.0, wait=True):
        got = self.press()
        if got != ca.STARTED:
            return got
        assert wait_for(lambda: "begin" in self.ui or self.finished()), "the recording did not start"
        self.let_go(held)
        if wait:
            assert wait_for(self.finished), "the run did not finish"
        return got

    def shown(self, kind=None):
        return [t for t in self.toasts if kind is None or t[0] == kind]

    def last(self):
        return self.toasts[-1]

    def end(self):
        return next(u for u in self.ui if isinstance(u, tuple) and u[0] == "end")


# ── an edit ──────────────────────────────────────────────────────────────────

def test_a_held_instruction_edits_the_selection_pastes_it_and_records_it():
    rig = Rig()
    rig.window = 77
    assert rig.go() == ca.STARTED

    assert rig.events == ["audio_start", "audio_stop"]
    assert rig.cues == ["start", "stop"]
    assert rig.ui[0] == "begin" and rig.ui[1] == "working" and rig.end()[1:] == (pw.DONE, "working")
    assert rig.reads == [False], "an app that shares its selection needs no Copy"

    (call,) = rig.chats
    assert call["purpose"] == "command" and call["temperature"] == 0.3
    assert call["max_tokens"] >= 1200 and call["deadline"] > time.monotonic()
    assert call["user"].startswith("Instruction: make this shorter")
    assert SEL in call["user"] and SEL not in call["system"]

    (paste,) = rig.pastes
    assert paste["text"] == SHORT and paste["window"] == 77 and paste["restore"] is True

    (entry,) = rig.saved
    assert entry["kind"] == "command" and entry["action"] == "edit" and entry["instruction"] == "make this shorter"
    assert entry["before"] == SEL and entry["after"] == SHORT
    assert entry["provider"] == {"command": "groq"} and entry["model"] == "openai/gpt-oss-120b"
    assert entry["paste"] == "pasted" and entry["app"] == "Notes" and entry["app_bundle"] == "com.apple.Notes"
    assert entry["timestamp"] == "2026-10-16T09:30:00" and entry["took_s"] >= 0
    assert "text" not in entry and "styled" not in entry
    assert rig.announced == [entry]

    (reply_used,) = rig.usage
    assert reply_used.provider == "groq" and reply_used.input_tokens == 150
    assert [s for s, _ in rig.speech_usage] == [3.0]

    kind, style, heading, body, buttons, timeout = rig.last()
    assert (kind, style, heading) == (ca.RESULT, "info", "Text edited") and "Cmd+Z" in body
    assert buttons[0] == {"label": "Show in Journal", "action": "show_command:71", "kind": "primary"}
    assert timeout == ca.DONE_S


def test_the_instruction_has_the_vocabulary_applied_and_nothing_else():
    rig = Rig()
    rig.spoken = "  ...Make this shorter for Wafler.  "
    rig.go()
    assert rig.chats[0]["user"].startswith("Instruction: Make this shorter for Waffler.")


def test_the_selection_and_the_app_are_noted_before_the_person_speaks():
    rig = Rig()
    order = []
    rig.runner.h.front_app = lambda: (order.append("app"), NOTES)[1]
    rig.runner.h.start_audio = lambda: order.append("audio")
    rig.runner.h.read_selection = lambda copy: (order.append("selection"), selected())[1]
    rig.press()
    assert wait_for(lambda: "selection" in order)
    assert order[:2] == ["app", "audio"] and "selection" in order
    assert not rig.chats, "nothing is asked until the keys come up"
    rig.let_go()
    assert wait_for(rig.finished)


def test_a_translation_is_allowed_to_share_no_wording():
    rig = Rig()
    rig.chat_text = "ACTION: edit\nLa réunion a été déplacée à jeudi à 15 h."
    rig.go()
    assert rig.pastes and rig.pastes[0]["text"].startswith("La réunion")


def test_an_edit_that_changes_nothing_pastes_nothing_and_says_so():
    rig = Rig()
    rig.chat_text = f"ACTION: edit\n{SEL}"
    rig.go()
    assert not rig.pastes and not rig.saved
    assert rig.last()[2:4] == um.COMMAND_NO_CHANGE


@pytest.mark.parametrize("answer,message", [
    ("ACTION: edit\n", um.COMMAND_NO_ANSWER),
    ("ACTION: edit\nI'm sorry, but I can't help with that.", um.COMMAND_REFUSED),
    ("ACTION: edit\nok", um.COMMAND_BAD_LENGTH),
])
def test_an_answer_that_looks_wrong_replaces_nothing(answer, message):
    rig = Rig()
    rig.selection = None
    rig.ax_result = selected(" ".join(f"word{i}" for i in range(60)))
    rig.chat_text = answer
    rig.go()
    assert not rig.pastes and not rig.saved and not rig.copies
    assert rig.last()[:2] == (ca.FAILED, "warn") and rig.last()[2:4] == message


def test_an_answer_the_model_said_it_cut_off_replaces_nothing():
    rig = Rig()
    rig.chat_finish = "length"
    rig.go()
    assert not rig.pastes and rig.last()[2:4] == um.COMMAND_CUT_SHORT


def test_an_edit_when_nothing_is_selected_is_written_at_the_cursor_as_a_draft():
    rig = Rig()
    rig.ax_result = NONE_SELECTED
    rig.chat_text = "ACTION: edit\nHello there, how are you?"
    rig.go()
    assert rig.pastes[0]["text"] == "Hello there, how are you?"
    assert rig.saved[0]["action"] == "draft"


# ── a draft ──────────────────────────────────────────────────────────────────

def test_a_draft_is_written_at_the_cursor_with_the_text_before_it_as_context():
    rig = Rig()
    rig.ax_result = NONE_SELECTED
    rig.context = ("Hi Sam, are we still on for lunch?", "")
    rig.spoken = "reply saying I'll be 10 minutes late"
    rig.chat_text = "ACTION: draft\nHi Sam, yes, but I'll be about ten minutes late. See you soon."
    rig.go()
    assert rig.context_reads == [500]
    (call,) = rig.chats
    assert "Nothing is selected" in call["user"] and "Hi Sam, are we still on for lunch?" in call["user"]
    assert rig.pastes[0]["text"] == "Hi Sam, yes, but I'll be about ten minutes late. See you soon."
    (entry,) = rig.saved
    assert entry["action"] == "draft" and entry["after"].startswith("Hi Sam, yes") and "before" not in entry
    assert "Hi Sam, are we still on for lunch?" not in str(entry), "the context is not kept"
    assert rig.last()[2] == "Draft added" and "Cmd+Z" in rig.last()[3]


def test_a_draft_with_a_selection_is_put_on_the_clipboard_and_never_replaces_it():
    rig = Rig()
    rig.spoken = "reply saying yes"
    rig.chat_text = "ACTION: draft\nYes, Thursday works for me."
    rig.go()
    assert not rig.pastes, "pasting would replace the selected text"
    assert rig.copies == ["Yes, Thursday works for me."] and rig.remembered == rig.copies
    (entry,) = rig.saved
    assert entry["action"] == "draft" and entry["paste"] == "copied" and "before" not in entry
    assert rig.last()[2:4] == um.COMMAND_DRAFT_COPIED
    assert rig.chats[0]["user"].count(SEL) == 1


def test_a_draft_with_no_selection_and_no_context_is_still_written():
    rig = Rig()
    rig.ax_result = NONE_SELECTED
    rig.spoken = "write a haiku about rain"
    rig.chat_text = "ACTION: draft\nRain on the window,\nsoft taps keep the evening company."
    rig.go()
    assert "there is no text before the cursor" in rig.chats[0]["user"]
    assert rig.pastes[0]["text"].startswith("Rain on the window")


def test_a_draft_is_not_pasted_into_another_app():
    rig = Rig()
    rig.ax_result = NONE_SELECTED
    rig.chat_text = "ACTION: draft\nSure, see you then."
    block = threading.Event()
    rig.chat_block = block
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.let_go()
    assert wait_for(lambda: rig.chats)
    rig.front = MAIL                     # the person switched app while the model was thinking
    block.set()
    assert wait_for(rig.finished)
    assert not rig.pastes and rig.copies == ["Sure, see you then."]
    assert rig.saved[0]["paste"] == "copied"
    assert rig.last()[2:4] == um.transform_not_pasted_message("darwin")


# ── a search ─────────────────────────────────────────────────────────────────

def test_a_question_opens_the_engine_with_the_spoken_question_and_nothing_else():
    rig = Rig()
    rig.spoken = "what is the capital of Australia"
    rig.chat_text = "ACTION: ask\ncapital of Australia"
    rig.go()
    assert rig.opened == ["https://www.perplexity.ai/search?q=capital%20of%20Australia"]
    assert not rig.pastes and not rig.copies
    (entry,) = rig.saved
    assert entry["action"] == "ask" and entry["query"] == "capital of Australia" and entry["engine"] == "perplexity"
    assert entry["instruction"] == "what is the capital of Australia" and "after" not in entry and "paste" not in entry
    assert rig.last()[:4] == (ca.RESULT, "info", "Searching Perplexity", "Your question is open in your browser.")
    assert rig.last()[4][0]["action"] == "show_command:71"


def test_the_chosen_engine_is_used():
    for engine, start in (("google", "https://www.google.com/search?q="), ("duckduckgo", "https://duckduckgo.com/?q="),
                          ("chatgpt", "https://chatgpt.com/?q="), ("claude", "https://claude.ai/new?q=")):
        rig = Rig(settings={"command_mode": {"enabled": True, "web_engine": engine}})
        rig.spoken = "who wrote Emma"
        rig.chat_text = "ACTION: ask\nwho wrote Emma"
        rig.go()
        assert rig.opened and rig.opened[0].startswith(start), engine
        assert rig.last()[2] == f"Searching {cm.engine_for(engine).label}"


def test_the_selected_text_and_the_context_never_reach_the_address():
    rig = Rig()
    rig.spoken = "who wrote this"
    rig.chat_text = "ACTION: ask\nwho wrote the quarterly report on Project Falcon"     # the model looked at the selection
    rig.ax_result = selected("The quarterly report on Project Falcon, by A. N. Other, is late.")
    rig.go()
    (url,) = rig.opened
    assert parse_qs(urlsplit(url).query) == {"q": ["who wrote this"]}
    assert "Falcon" not in url and "quarterly" not in url


def test_context_text_is_not_in_the_address_either():
    rig = Rig()
    rig.ax_result = NONE_SELECTED
    rig.context = ("Dear Mr Hargreaves, regarding invoice 4471", "")
    rig.spoken = "what does regarding mean"
    rig.chat_text = "ACTION: ask\nwhat does regarding mean invoice 4471"
    rig.go()
    (url,) = rig.opened
    assert "4471" not in url and parse_qs(urlsplit(url).query) == {"q": ["what does regarding mean"]}


def test_a_question_with_odd_characters_arrives_as_one_parameter():
    rig = Rig()
    rig.spoken = "is fish & chips #1? café 日本 a=b"
    rig.chat_text = "ACTION: ask\n"
    rig.go()
    (url,) = rig.opened
    parts = urlsplit(url)
    assert parts.fragment == "" and list(parse_qs(parts.query)) == ["q"]
    assert parse_qs(parts.query)["q"] == ["is fish & chips #1? café 日本 a=b"]
    assert "%26" in url and "%23" in url and "%3F" in url and "%C3%A9" in url


def test_when_the_browser_will_not_open_the_person_is_told_and_nothing_is_saved():
    rig = Rig()
    rig.chat_text = "ACTION: ask\nx"
    rig.open_ok = False
    rig.go()
    assert not rig.saved and rig.last()[:2] == (ca.FAILED, "warn") and rig.last()[2:4] == um.COMMAND_OPEN_FAILED


def test_the_model_ignoring_the_tag_for_a_question_still_opens_the_search():
    rig = Rig()
    rig.ax_result = NONE_SELECTED
    rig.spoken = "what is the capital of Australia"
    rig.chat_text = "Canberra is the capital of Australia."
    rig.go()
    assert rig.opened == ["https://www.perplexity.ai/search?q=what%20is%20the%20capital%20of%20Australia"]
    assert not rig.pastes, "the model's answer is not pasted as if it were a draft"


# ── when it may not run ──────────────────────────────────────────────────────

def test_it_does_nothing_while_it_is_off():
    rig = Rig(settings={"command_mode": {"enabled": False}})
    assert rig.press() == ca.OFF
    assert not rig.toasts and not rig.events and not rig.runner.busy()


def test_it_never_starts_during_a_dictation_or_another_action():
    rig = Rig()
    rig.busy = True
    assert rig.press() == ca.BUSY_ELSEWHERE
    assert rig.last()[:4] == (ca.BUSY, "info", *um.COMMAND_BUSY_ELSEWHERE)
    assert not rig.events and not rig.runner.busy() and not rig.chats
    rig.runner.up()                                  # the keys coming up change nothing
    assert not rig.events


def test_a_second_command_while_one_runs_is_refused_and_the_first_is_not_disturbed():
    rig = Rig()
    block = threading.Event()
    rig.chat_block = block
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.let_go()
    assert wait_for(lambda: rig.chats)
    assert rig.press() == ca.ALREADY_RUNNING
    assert rig.last()[2:4] == um.COMMAND_BUSY_ALREADY
    assert rig.runner.busy()
    block.set()
    assert wait_for(rig.finished)
    assert len(rig.chats) == 1 and len(rig.pastes) == 1
    assert rig.press() == ca.STARTED, "and a new one may start once it is over"
    rig.let_go()
    assert wait_for(lambda: len(rig.saved) == 2 and rig.finished())


def test_the_recording_does_not_start_when_a_permission_is_off():
    rig = Rig()
    rig.permissions = False
    rig.press()
    assert wait_for(rig.finished)
    assert not rig.events and rig.end()[1] == pw.NOTHING and not rig.chats


def test_a_permission_check_that_errors_does_not_stop_the_command():
    rig = Rig()
    rig.runner.h.permissions_ok = lambda: 1 / 0
    rig.go()
    assert rig.pastes


def test_keys_coming_up_with_no_command_running_are_ignored():
    rig = Rig()
    rig.runner.up()
    assert not rig.events and not rig.toasts
    rig.go()
    rig.runner.up()                                  # a second up after it all
    assert len(rig.chats) == 1


def test_keys_up_a_moment_after_down_still_end_the_hold_even_if_the_thread_is_slow():
    """The key-up can arrive before the recording thread has begun."""
    rig = Rig()
    started = threading.Event()
    gate = threading.Event()

    def slow_spawn(fn):
        def later():
            gate.wait(5)
            started.set()
            fn()
        threading.Thread(target=later, daemon=True).start()
    rig.runner.h.spawn = slow_spawn
    assert rig.press() == ca.STARTED
    rig.let_go()
    gate.set()
    assert wait_for(rig.finished) and rig.pastes


# ── what is selected ─────────────────────────────────────────────────────────

def test_more_than_a_thousand_words_stops_at_once_with_a_plain_message_and_asks_nothing():
    rig = Rig()
    rig.ax_result = selected("word " * 1001)
    rig.press()
    assert wait_for(lambda: rig.toasts)
    assert rig.toasts[0][2:4] == um.TRANSFORM_TOO_LONG
    rig.let_go()
    assert wait_for(rig.finished)
    assert rig.end()[1] == pw.NOTHING and not rig.chats and not rig.pastes
    assert rig.events == ["audio_start", "audio_stop"], "the recording is stopped and thrown away"


def test_exactly_a_thousand_words_is_fine():
    rig = Rig()
    rig.ax_result = selected(" ".join(f"w{i}" for i in range(1000)))
    rig.chat_text = "ACTION: edit\n" + " ".join(f"w{i}" for i in range(1000)).replace("w1 ", "x1 ", 1)
    rig.go()
    assert rig.chats


def test_a_password_field_is_left_alone():
    rig = Rig()
    rig.ax_result = tc.SelectionResult("", tc.METHOD_NONE, tc.SECURE_FIELD)
    rig.press()
    assert wait_for(lambda: rig.toasts)
    assert rig.toasts[0][2:4] == um.transform_read_message(tc.SECURE_FIELD)
    rig.let_go()
    assert wait_for(rig.finished) and not rig.chats and not rig.context_reads


def test_accessibility_off_says_so_with_a_button_to_the_pane():
    rig = Rig()
    rig.ax_result = tc.SelectionResult("", tc.METHOD_NONE, tc.ACCESSIBILITY_OFF)
    rig.press()
    assert wait_for(lambda: rig.toasts)
    kind, style, heading, body, buttons, timeout = rig.toasts[0]
    assert heading == "Accessibility is off" and buttons[0]["action"] == "open_ax_settings" and timeout >= 10
    rig.let_go()
    assert wait_for(rig.finished) and not rig.chats


def test_an_app_that_hides_its_selection_is_read_by_copying_only_after_the_keys_are_up():
    """A Copy sent while the shortcut's modifier keys are held would reach the
    app as another shortcut."""
    rig = Rig()
    rig.ax_result = UNREADABLE
    rig.go()
    assert rig.reads == [False, True]
    assert rig.reads_after_release == [True], "the Copy was after the person let go"
    assert rig.chats and SEL in rig.chats[0]["user"]
    assert rig.pastes[0]["text"] == SHORT
    assert rig.saved[0]["before"] == SEL


def test_a_read_that_hangs_is_copied_once_the_keys_are_up_instead():
    rig = Rig()
    rig.runner.capture_read_s = 0.2
    hang = threading.Event()
    calls = []

    def read(allow_copy):
        calls.append(allow_copy)
        if not allow_copy:
            hang.wait(5)
            return NONE_SELECTED
        return selected(method="copy")
    rig.runner.h.read_selection = read
    rig.go()
    hang.set()
    assert calls == [False, True] and rig.pastes


def test_nothing_selected_in_an_app_that_hides_it_is_a_draft_not_an_error():
    rig = Rig()
    rig.ax_result = UNREADABLE
    rig.copy_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.spoken = "write a haiku"
    rig.chat_text = "ACTION: draft\nRain on the window."
    rig.go()
    assert rig.reads == [False, True] and rig.pastes[0]["text"] == "Rain on the window."
    assert "Nothing is selected" in rig.chats[0]["user"]


@pytest.mark.parametrize("reason", ["clipboard_not_restorable", "clipboard_busy", "not_allowed_here", "timed_out"])
def test_a_copy_that_cannot_be_done_says_why_and_asks_nothing(reason):
    rig = Rig()
    rig.ax_result = UNREADABLE
    rig.copy_result = tc.SelectionResult("", tc.METHOD_NONE, reason)
    rig.go()
    assert not rig.chats and not rig.pastes and not rig.saved
    assert rig.last()[:2] == (ca.FAILED, "warn") and rig.last()[2:4] == um.transform_read_message(reason)


def test_a_copy_that_never_finishes_gives_up_after_its_deadline():
    rig = Rig()
    rig.ax_result = UNREADABLE
    rig.copy_block = threading.Event()
    rig.runner.read_deadline_s = 0.3
    t0 = time.monotonic()
    rig.go()
    assert time.monotonic() - t0 < 4 and not rig.chats
    assert rig.last()[:2] == (ca.FAILED, "warn")
    rig.copy_block.set()


def test_the_selection_is_checked_again_before_pasting_and_a_changed_one_is_not_replaced():
    rig = Rig()
    rig.reread = selected("Something else the person selected in the meantime, not the original text at all.")
    rig.go()
    assert not rig.pastes and rig.copies == [SHORT]
    assert rig.saved[0]["paste"] == "copied" and rig.rereads == ["ax"]
    assert rig.last()[2:4] == um.transform_not_pasted_message("darwin")


def test_a_selection_that_has_gone_is_not_replaced_and_the_result_is_kept():
    rig = Rig()
    rig.reread = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.go()
    assert not rig.pastes and rig.copies == [SHORT]


def test_a_check_that_cannot_be_made_in_time_puts_the_result_on_the_clipboard_instead():
    rig = Rig()

    def stuck(method):
        time.sleep(0.6)
    rig.runner.h.reread_selection = stuck
    old = ca.CHECK_DEADLINE_S
    ca.CHECK_DEADLINE_S = 0.2
    try:
        rig.go()
    finally:
        ca.CHECK_DEADLINE_S = old
    assert not rig.pastes and rig.copies == [SHORT]
    assert rig.last()[2:4] == um.transform_not_sure_message("darwin")


def test_the_person_switching_app_while_it_works_means_nothing_is_pasted():
    rig = Rig()
    block = threading.Event()
    rig.chat_block = block
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.let_go()
    assert wait_for(lambda: rig.chats)
    rig.front = MAIL
    block.set()
    assert wait_for(rig.finished)
    assert not rig.pastes and rig.copies == [SHORT] and rig.saved[0]["paste"] == "copied"


# ── hearing the instruction ──────────────────────────────────────────────────

def test_a_brush_of_the_keys_is_nothing_at_all():
    rig = Rig()
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.let_go(held=0.0)
    assert wait_for(rig.finished)
    assert not rig.toasts and not rig.chats and not rig.no_audio_calls and rig.end()[1] == pw.NOTHING


def test_silence_gets_the_could_not_hear_you_message():
    rig = Rig()
    rig.audio = H.silence_wav(2.0)
    rig.go()
    assert rig.no_audio_calls == [1] and not rig.chats and rig.end()[1] == pw.NOTHING


def test_no_words_heard_gets_the_same_message():
    rig = Rig()
    rig.spoken = "   "
    rig.go()
    assert rig.no_audio_calls == [1] and not rig.chats and not rig.pastes


def test_an_instruction_that_is_a_speech_is_turned_down_with_a_plain_sentence():
    rig = Rig()
    rig.spoken = "word " * 400
    rig.go()
    assert not rig.chats and rig.last()[2:4] == um.COMMAND_TOO_LONG


def test_speech_to_text_failing_says_why_and_changes_nothing():
    rig = Rig()
    rig.stt_error = RuntimeError("Connection error: private words")
    rig.go()
    assert not rig.chats and not rig.pastes and not rig.saved
    assert rig.last()[:2] == (ca.FAILED, "warn")
    assert rig.last()[2:4] == um.command_speech_failed_message("offline", "Groq")
    assert "private words" not in str(rig.toasts)
    assert "private words" not in " ".join(rig.logged)


def test_speech_to_text_that_never_answers_gives_up_at_its_deadline():
    rig = Rig()
    rig.stt_block = threading.Event()
    rig.runner.stt_deadline_s = 0.4
    t0 = time.monotonic()
    rig.go()
    assert time.monotonic() - t0 < 4 and not rig.chats
    assert rig.last()[2:4] == um.COMMAND_TOO_SLOW
    rig.stt_block.set()


def test_the_recorder_that_does_not_stop_is_given_up_on():
    rig = Rig()
    rig.audio_block = threading.Event()
    old = pw.PREPARE_DEADLINE_S
    pw.PREPARE_DEADLINE_S = 0.4
    try:
        rig.go()
    finally:
        pw.PREPARE_DEADLINE_S = old
        rig.audio_block.set()
    assert not rig.chats and rig.last()[:2] == (ca.FAILED, "warn") and not rig.runner.busy()


def test_the_recording_that_cannot_start_says_so_and_changes_nothing():
    rig = Rig()

    def boom():
        raise OSError("no device")
    rig.runner.h.start_audio = boom
    rig.press()
    assert wait_for(rig.finished)
    assert rig.last()[2] == "Couldn't record" and not rig.chats and rig.end()[1] == pw.ERROR


# ── the model ────────────────────────────────────────────────────────────────

def test_a_provider_failure_shows_the_message_and_changes_nothing():
    rig = Rig()
    rig.chat_error = ChatError("RATE_LIMIT|tokens per minute (TPM)|9s|Groq: busy")
    rig.go()
    assert not rig.pastes and not rig.saved and not rig.copies
    assert rig.last()[:2] == (ca.FAILED, "warn")
    assert rig.last()[2:4] == um.command_failed_message("RATE_LIMIT|tokens per minute (TPM)|9s|Groq: busy")
    assert rig.end()[1] == pw.ERROR


def test_an_unexpected_model_error_is_a_plain_message_with_no_text_in_the_log():
    rig = Rig()
    rig.chat_error = RuntimeError("the secret selection text")
    rig.go()
    assert rig.last()[:2] == (ca.FAILED, "warn") and not rig.pastes
    assert "secret" not in " ".join(rig.logged) and "secret" not in str(rig.toasts)


def test_a_model_that_never_answers_gives_up_at_the_deadline_and_the_text_is_untouched():
    rig = Rig()
    rig.chat_block = threading.Event()
    rig.runner.total_deadline_s = 1.5
    rig.runner.min_ask_s = 0.3
    t0 = time.monotonic()
    rig.go()
    assert time.monotonic() - t0 < 5
    assert not rig.pastes and not rig.saved
    assert rig.last()[2:4] == um.COMMAND_TOO_SLOW and rig.end()[1] == pw.ERROR
    rig.chat_block.set()


def test_the_model_call_is_told_to_stop_before_the_watchdog_does():
    rig = Rig()
    rig.go()
    (call,) = rig.chats
    # The deadline given to the provider chain is earlier than the run's own.
    assert call["deadline"] < time.monotonic() + rig.runner.total_deadline_s


def test_a_model_call_given_up_on_is_still_counted_when_it_finishes():
    rig = Rig()
    block = threading.Event()
    rig.chat_block = block
    rig.runner.total_deadline_s = 1.0
    rig.runner.min_ask_s = 0.3
    rig.go()
    assert not rig.usage
    block.set()
    assert wait_for(lambda: len(rig.usage) == 1)


# ── the paste ────────────────────────────────────────────────────────────────

def test_a_paste_that_failed_leaves_the_result_for_copy_again_and_is_not_a_success():
    rig = Rig()
    rig.paste_outcome = pasteboard.PasteOutcome(pasteboard.FAILED, "keystroke_error", on_clipboard=True)
    rig.go()
    assert rig.remembered == [SHORT]
    assert rig.saved[0]["paste"] == "failed" and rig.saved[0]["after"] == SHORT
    assert not rig.shown(ca.RESULT), "no 'Text edited' for a paste that did not land"
    assert rig.end()[1] == pw.ERROR


def test_a_paste_that_could_not_be_verified_still_counts_as_done():
    rig = Rig()
    rig.paste_outcome = pasteboard.PasteOutcome(pasteboard.UNVERIFIED, restored=True)
    rig.go()
    assert rig.saved[0]["paste"] == "unverified" and rig.last()[2] == "Text edited"


def test_with_auto_paste_off_the_result_is_only_copied():
    rig = Rig(settings={"auto_paste": False})
    rig.go()
    assert not rig.pastes and rig.copies == [SHORT]
    assert rig.saved[0]["paste"] == "copied" and rig.last()[2:4] == um.COMMAND_COPIED


def test_the_journal_failing_does_not_cost_the_edit():
    rig = Rig()
    rig.save_ok = False
    rig.go()
    assert rig.pastes and rig.last()[2] == "Text edited" and rig.last()[4] is None
    assert not rig.announced


# ── Esc ──────────────────────────────────────────────────────────────────────

def test_esc_while_holding_throws_the_recording_away_and_asks_nothing():
    rig = Rig()
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    assert rig.runner.cancel() is True
    assert wait_for(rig.finished)
    assert rig.end()[1:] == (pw.CANCELLED, "recording")
    assert rig.events == ["audio_start", "audio_stop"] and not rig.chats and not rig.pastes
    assert rig.last()[2:4] == um.COMMAND_CANCELLED
    rig.let_go()                                     # the keys coming up afterwards change nothing
    time.sleep(0.1)
    assert len(rig.chats) == 0 and rig.ui.count("working") == 0


def test_esc_while_the_model_thinks_pastes_nothing_and_says_so():
    rig = Rig()
    block = threading.Event()
    rig.chat_block = block
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.let_go()
    assert wait_for(lambda: rig.chats)
    assert rig.runner.cancel() is True
    assert wait_for(rig.finished)
    assert rig.end()[1] == pw.CANCELLED and not rig.pastes and not rig.saved
    assert rig.last()[2:4] == um.COMMAND_CANCELLED
    block.set()


def test_esc_while_the_reply_is_being_read_frees_the_window(monkeypatch):
    """Reading the reply holds the interpreter, so it runs under the watchdog: a
    Cancel ends the command even if the reader were to drag on."""
    rig = Rig()
    stuck = threading.Event()
    started = threading.Event()

    def slow_reader(*a, **kw):
        started.set()
        stuck.wait(10)
        return cm.Parsed(cm.EDIT, SHORT)

    monkeypatch.setattr(cm, "parse_reply", slow_reader)
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.let_go()
    assert started.wait(5)
    assert rig.runner.cancel() is True
    assert wait_for(rig.finished)
    assert rig.end()[1] == pw.CANCELLED and not rig.pastes and not rig.saved
    stuck.set()


def test_a_reader_that_never_finishes_gives_up_and_changes_nothing(monkeypatch):
    rig = Rig()
    stuck = threading.Event()
    monkeypatch.setattr(cm, "parse_reply", lambda *a, **kw: stuck.wait(10))
    monkeypatch.setattr(ca, "READ_REPLY_S", 0.3)
    rig.go()
    assert not rig.pastes and not rig.saved and rig.end()[1] == pw.ERROR
    assert rig.last()[:2] == (ca.FAILED, "warn")
    stuck.set()


def test_a_reply_longer_than_any_reply_can_be_replaces_nothing():
    """A model that fills its answer with white space is read only up to the most a reply
    can hold, and what it wrote is not used."""
    rig = Rig()
    rig.chat_text = "ACTION: edit\n" + SHORT + " " * 200_000 + "and then some more words"
    rig.go()
    assert not rig.pastes and not rig.saved and not rig.copies
    assert rig.last()[2:4] == um.COMMAND_CUT_SHORT


def test_esc_while_speech_is_turned_to_text_cancels_it():
    rig = Rig()
    rig.stt_block = threading.Event()
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.let_go()
    assert wait_for(lambda: "working" in rig.ui)
    time.sleep(0.1)
    rig.runner.cancel()
    assert wait_for(rig.finished)
    assert not rig.chats and rig.end()[1] == pw.CANCELLED
    rig.stt_block.set()


def test_esc_once_the_paste_has_begun_is_too_late_and_the_text_goes_in():
    rig = Rig()
    block = threading.Event()
    rig.paste_block = block
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.let_go()
    assert wait_for(lambda: rig.pastes)
    assert rig.runner.cancel() is True               # the key is ours, but too late
    block.set()
    assert wait_for(rig.finished)
    assert rig.end()[1] == pw.DONE and rig.saved and any("too late" in m for m in rig.logged)


def test_esc_with_nothing_running_is_not_ours():
    rig = Rig()
    assert rig.runner.cancel() is False


# ── the hold cap and the unexpected ──────────────────────────────────────────

def test_a_key_up_that_never_comes_ends_the_hold_at_the_cap():
    rig = Rig()
    rig.runner.max_hold_s = 0.3
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    time.sleep(0.05)
    rig.skew += 1.0                                  # the clock moves past the cap with no key-up
    assert wait_for(rig.finished) and rig.pastes
    assert any("no key-up" in m for m in rig.logged)


def test_an_exception_in_the_flow_is_a_plain_message_and_the_recorder_is_stopped():
    rig = Rig()
    rig.runner.h.apply_vocab = lambda t: t
    rig.runner.h.audio_problem = lambda audio, held: 1 / 0
    rig.go()
    assert rig.last()[:2] == (ca.FAILED, "warn") and rig.last()[2] == "Couldn't run that"
    assert rig.events.count("audio_stop") == 1 and not rig.runner.busy()
    assert not rig.pastes


def test_a_toast_that_fails_never_ends_the_run_badly():
    rig = Rig()

    def boom(*a):
        raise RuntimeError("overlay gone")
    rig.runner.h.say = boom
    rig.go()
    assert rig.pastes and not rig.runner.busy()


def test_the_pill_is_put_back_exactly_once():
    rig = Rig()
    rig.go()
    time.sleep(0.1)
    assert [u for u in rig.ui if isinstance(u, tuple)] == [("end", pw.DONE, "working")]


def test_the_busy_flag_clears_after_every_kind_of_ending():
    for setup in (lambda r: None,
                  lambda r: setattr(r, "chat_error", RuntimeError("x")),
                  lambda r: setattr(r, "audio", H.silence_wav(1.0)),
                  lambda r: setattr(r, "stt_error", RuntimeError("x"))):
        rig = Rig()
        setup(rig)
        rig.go()
        assert not rig.runner.busy()


def test_nothing_the_flow_logs_contains_the_persons_words():
    rig = Rig()
    rig.spoken = "make this shorter please secret-instruction"
    rig.go()
    logged = " ".join(rig.logged)
    assert "secret-instruction" not in logged and SEL[:30] not in logged and SHORT[:30] not in logged


# ── the audio test ───────────────────────────────────────────────────────────

def test_the_audio_test_tells_a_brush_from_silence_from_speech():
    assert ca.audio_problem(SPEECH, 1.0) is None
    assert ca.audio_problem(SPEECH, 0.1) == "tap"
    assert ca.audio_problem(H.silence_wav(2.0), 2.0) == "silent"
    assert ca.audio_problem(b"", 2.0) == "none" and ca.audio_problem(None, 2.0) == "none"
    assert ca.audio_problem(b"RIFF" + bytes(40), 2.0) == "none"
    assert ca.audio_problem(b"RIFF" + bytes(44) + b"\x01", 2.0) in (None, "silent")


# ── the real shortcut registry in front of the runner ────────────────────────

def test_a_press_and_release_in_a_blink_through_the_real_registry_never_leaves_the_recorder_running():
    """The registry posts down and up within milliseconds. Each runs on the
    action's own thread, in order, so the up always finds the run the down made."""
    import action_hotkeys as ah
    for _ in range(20):
        rig = Rig()
        registry = ah.ActionHotkeys(ah.MAC)
        assert registry.register_hold("command", ["control", "option", "c"], rig.runner.down, rig.runner.up)["ok"]
        registry.key_down("c", ["control", "option"])
        registry.key_up("c")
        assert wait_for(rig.finished, 5), "the run never ended: the recorder would still be on"
        assert rig.events == ["audio_start", "audio_stop"] and not rig.runner.busy()
        assert rig.end()[1] == pw.NOTHING, "a brush of the keys is nothing"


def test_a_held_instruction_through_the_real_registry_and_the_real_threads():
    import action_hotkeys as ah
    rig = Rig()
    registry = ah.ActionHotkeys(ah.MAC)
    registry.register_hold("command", ["control", "option", "c"], rig.runner.down, rig.runner.up)
    registry.key_down("c", ["control", "option"])
    assert wait_for(lambda: "begin" in rig.ui)
    rig.skew += 1.0
    registry.modifiers_changed(["option"])                 # Control came up first
    assert wait_for(rig.finished, 5)
    assert rig.pastes and rig.pastes[0]["text"] == SHORT
    registry.key_up("c")                                   # the letter coming up afterwards changes nothing
    time.sleep(0.1)
    assert len(rig.chats) == 1


def test_a_read_that_answers_after_the_run_has_gone_on_without_it_changes_nothing():
    """The key-down read answers slowly, after the keys are up and the run has
    stopped waiting for it: its "too much text" must not stop a run that has
    already read the selection another way."""
    rig = Rig()
    rig.runner.capture_wait_s = 0.05
    reads = []

    def slow(allow_copy):
        reads.append(allow_copy)
        if not allow_copy:
            time.sleep(0.4)
            return selected("word " * 1500)             # far too much, but too late to matter
        return selected(method="copy")
    rig.runner.h.read_selection = slow
    rig.go()
    time.sleep(0.5)
    assert reads == [False, True], "the run copied the selection itself when the first read had not answered"
    assert rig.pastes and rig.pastes[0]["text"] == SHORT
    assert not any(t[2:4] == um.TRANSFORM_TOO_LONG for t in rig.toasts)
    assert rig.end()[1] == pw.DONE


# ── found by an independent review ───────────────────────────────────────────

def test_a_cancel_that_lands_while_the_working_pill_starts_still_stops_the_recorder():
    rig = Rig()
    original = rig.runner.h.ui_working

    def cancel_while_starting(run):
        original(run)
        rig.runner.cancel()                 # Esc, just as the pill goes to its working look
    rig.runner.h.ui_working = cancel_while_starting
    rig.go()
    assert rig.events.count("audio_stop") == 1, "the recorder is stopped, not left listening with no pill"
    assert not rig.chats and not rig.pastes
    assert rig.end()[1] == pw.CANCELLED and rig.last()[2:4] == um.COMMAND_CANCELLED


def test_a_cancel_before_the_pill_has_a_watchdog_run_still_stops_the_recorder():
    rig = Rig()
    rig.press()
    assert wait_for(lambda: "begin" in rig.ui)
    rig.runner.cancel()
    rig.let_go()
    assert wait_for(rig.finished)
    assert rig.events == ["audio_start", "audio_stop"] and not rig.runner.busy()


def test_nothing_selected_found_by_a_copy_is_checked_again_before_a_draft_is_pasted():
    """A Copy that copies nothing is "no selection" or an app that was slow or
    ignored it. Pasting over a selection that is really there would lose it."""
    rig = Rig()
    rig.ax_result = UNREADABLE
    rig.copy_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.reread = selected("The text that was selected all along, but the first Copy missed it.", method="copy")
    rig.spoken = "reply saying yes"
    rig.chat_text = "ACTION: draft\nYes, that works for me."
    rig.go()
    assert not rig.pastes, "the draft did not go over a selection"
    assert rig.copies == ["Yes, that works for me."] and rig.saved[0]["paste"] == "copied"
    assert rig.last()[2:4] == um.COMMAND_DRAFT_COPIED
    assert rig.rereads == ["copy"]


def test_when_the_second_look_agrees_nothing_is_selected_the_draft_is_pasted():
    rig = Rig()
    rig.ax_result = UNREADABLE
    rig.copy_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.reread = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.chat_text = "ACTION: draft\nYes, that works for me."
    rig.go()
    assert rig.pastes and rig.pastes[0]["text"] == "Yes, that works for me."


@pytest.mark.parametrize("reason", ["timed_out", "clipboard_busy", "clipboard_not_restorable"])
def test_a_second_look_that_cannot_be_made_is_a_reason_not_to_paste_the_draft(reason):
    rig = Rig()
    rig.ax_result = UNREADABLE
    rig.copy_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.reread = tc.SelectionResult("", tc.METHOD_NONE, reason)
    rig.chat_text = "ACTION: draft\nYes, that works for me."
    rig.go()
    assert not rig.pastes and rig.copies == ["Yes, that works for me."]


def test_an_app_that_said_nothing_is_selected_is_believed_without_a_second_copy():
    rig = Rig()
    rig.ax_result = NONE_SELECTED
    rig.chat_text = "ACTION: draft\nYes, that works for me."
    rig.go()
    assert rig.pastes and rig.rereads == [] and rig.reads == [False]


def test_a_reply_with_no_tag_that_does_not_look_like_the_selection_replaces_nothing():
    rig = Rig()
    rig.spoken = "what does this mean"
    rig.chat_text = "The sender says the event has a new day and place, so check your calendar and tell your colleagues."
    rig.go()
    assert not rig.pastes and not rig.saved
    assert rig.last()[2:4] == um.TRANSFORM_UNRELATED


def test_a_reply_with_no_tag_that_is_a_real_edit_is_still_used():
    rig = Rig()
    rig.chat_text = SHORT
    rig.go()
    assert rig.pastes and rig.pastes[0]["text"] == SHORT


def test_a_tag_after_a_short_lead_in_is_found_and_never_pasted():
    rig = Rig()
    rig.chat_text = f"Sure!\nACTION: edit\n{SHORT}"
    rig.go()
    assert rig.pastes[0]["text"] == SHORT


@pytest.mark.parametrize("make", [
    lambda r: setattr(r, "chat_error", RuntimeError("PRIVATE-WORDS from the provider")),
    lambda r: setattr(r, "stt_error", RuntimeError("PRIVATE-WORDS from speech")),
    lambda r: setattr(r, "spoken", "PRIVATE-WORDS " * 400),
    lambda r: setattr(r, "chat_text", "ACTION: edit\nPRIVATE-WORDS"),
    lambda r: setattr(r, "chat_text", "I'm sorry, but I can't help with that. PRIVATE-WORDS"),
    lambda r: setattr(r, "chat_text", "ACTION: draft\nPRIVATE-WORDS"),
    lambda r: setattr(r, "ax_result", selected("PRIVATE-WORDS " * 1100)),
    lambda r: setattr(r, "reread", selected("PRIVATE-WORDS moved.")),
    lambda r: setattr(r, "paste_outcome", pasteboard.PasteOutcome(pasteboard.FAILED, "x", on_clipboard=True)),
    lambda r: setattr(r, "front", MAIL),
])
def test_no_ending_of_a_command_puts_the_persons_words_in_the_log_or_a_toast_they_did_not_ask_for(make):
    rig = Rig()
    rig.ax_result = selected("The PRIVATE-WORDS selection text in the app with a handful of words to edit.")
    rig.chat_text = "ACTION: ask\nPRIVATE-WORDS"
    make(rig)
    rig.go()
    assert "PRIVATE-WORDS" not in " ".join(rig.logged)
    assert "PRIVATE-WORDS" not in " ".join(f"{t[2]} {t[3]}" for t in rig.toasts)
