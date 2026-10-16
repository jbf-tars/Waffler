"""The transform flow (S7), from the shortcut to the replaced text, with fakes.

Nothing real is touched: no Accessibility, no clipboard, no keystroke, no
model. The hooks are plain functions that record what they were asked, so the
tests can say what was pasted, what was saved, which toast was shown and what
the person's text was left as.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pasteboard  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import text_context as tc  # noqa: E402
import transform_action as ta  # noqa: E402
import user_messages as um  # noqa: E402
from style_openai import ChatError, ChatReply  # noqa: E402

EM = chr(0x2014)
SOURCE = ("so yesterday i went to the shop and bought some milk and eggs and then i came home "
          "and made a cake for the party")
GOOD = ("Yesterday I went to the shop and bought some milk and eggs. Then I came home "
        "and made a cake for the party.")
NOTES = tc.make_context("com.apple.Notes", "Notes")
MAIL = tc.make_context("com.apple.mail", "Mail")


def wait_for(predicate, timeout=5.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())


def selection(text=SOURCE, method="ax", reason=tc.OK):
    return tc.SelectionResult(text, method, reason)


def reply(text=GOOD, **kw):
    kw.setdefault("provider", "groq")
    kw.setdefault("model", "openai/gpt-oss-120b")
    kw.setdefault("input_tokens", 120)
    kw.setdefault("output_tokens", 40)
    kw.setdefault("finish_reason", "stop")
    return ChatReply(text=text, **kw)


class Rig:
    """A runner wired to recorders. Set the ``*_result`` attributes to change
    what the fakes answer; read the lists to see what happened."""

    def __init__(self, *, settings=None, **runner):
        self.stored = {"transforms": {"enabled": True}}
        self.stored.update(settings or {})
        self.dictating = False
        self.front = NOTES
        self.reread = None
        self.window = None
        self.read_result = selection()
        self.read_block = None
        self.chat_result = reply()
        self.chat_block = None
        self.chat_error = None
        self.paste_outcome = pasteboard.PasteOutcome(pasteboard.PASTED, restored=True)
        self.paste_block = None
        self.save_ok = True
        self.copy_ok = True
        self.toasts, self.chats, self.pastes, self.saved = [], [], [], []
        self.copies, self.remembered, self.usage, self.announced = [], [], [], []
        self.ui, self.logged, self.refocused = [], [], []
        self.threads = []
        self.runner = ta.TransformRunner(self.hooks(), **runner)

    def hooks(self):
        return ta.Hooks(
            settings=lambda: self.stored,
            dictation_busy=lambda: self.dictating,
            read_selection=self._read,
            chat=self._chat,
            paste=self._paste,
            settle_paste=self._settle,
            copy=self._copy,
            save_history=self._save,
            say=lambda *a: self.toasts.append(a),
            front_app=lambda: self.front,
            window=lambda: self.window,
            reread_selection=lambda: self.reread,
            refocus=lambda: self.refocused.append(1),
            remember=self.remembered.append,
            record_usage=self.usage.append,
            announce_item=self.announced.append,
            ui_begin=lambda run: self.ui.append(("begin",)),
            ui_tick=lambda run, s: self.ui.append(("tick", s)),
            ui_end=lambda run, outcome: self.ui.append(("end", outcome)),
            log=self.logged.append,
            now_iso=lambda: "2026-10-16T09:30:00",
            platform="darwin",
            spawn=self._spawn)

    def _spawn(self, fn):
        t = threading.Thread(target=fn, daemon=True)
        self.threads.append(t)
        t.start()

    # -- the fakes ---------------------------------------------------------

    def _read(self):
        self.read_thread = threading.current_thread()
        if self.read_block is not None:
            self.read_block.wait(10)
        return self.read_result

    def _chat(self, system, user, **kw):
        self.chats.append({"system": system, "user": user, **kw})
        if self.chat_block is not None:
            self.chat_block.wait(10)
        if self.chat_error is not None:
            raise self.chat_error
        return self.chat_result

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
        entry["id"] = 41 + len(self.saved)
        self.saved.append(entry)
        return True

    # -- driving it --------------------------------------------------------

    def go(self, origin="shortcut", wait=True):
        got = self.runner.start(origin)
        if wait and got == ta.STARTED:
            assert wait_for(lambda: not self.runner.busy() and self.ui and self.ui[-1][0] == "end"), \
                "the run did not finish"
        return got

    def shown(self, kind=None):
        return [t for t in self.toasts if kind is None or t[0] == kind]

    def last(self):
        return self.toasts[-1]


# ── the happy path ───────────────────────────────────────────────────────────

def test_a_selection_is_rewritten_pasted_and_recorded():
    rig = Rig()
    rig.window = 77
    assert rig.go() == ta.STARTED

    (call,) = rig.chats
    assert call["purpose"] == "transform" and call["temperature"] == 0.3
    assert call["max_tokens"] >= 700
    assert call["deadline"] > time.monotonic()
    assert "Task: polish the text." in call["system"] and SOURCE not in call["system"]
    assert call["user"].endswith(f"<<<SOURCE\n{SOURCE}\nSOURCE>>>")

    (paste,) = rig.pastes
    assert paste["text"] == GOOD and paste["window"] == 77 and paste["restore"] is True

    (entry,) = rig.saved
    assert entry["kind"] == "transform" and entry["before"] == SOURCE and entry["after"] == GOOD
    assert entry["transform_id"] == "polish" and entry["transform_name"] == "Polish"
    assert entry["provider"] == {"transform": "groq"} and entry["model"] == "openai/gpt-oss-120b"
    assert entry["paste"] == "pasted" and entry["app"] == "Notes" and entry["app_bundle"] == "com.apple.Notes"
    assert entry["timestamp"] == "2026-10-16T09:30:00" and "text" not in entry and "styled" not in entry
    assert rig.announced == [entry]

    heading, body = rig.last()[2:4]
    assert rig.last()[:2] == (ta.RESULT, "info")
    assert heading == "Polish done" and "Cmd+Z" in body
    buttons = rig.last()[4]
    assert buttons[0] == {"label": "Show in Journal", "action": "show_transform:41", "kind": "primary"}

    assert rig.usage == [rig.chat_result], "the cost is recorded"
    assert rig.remembered == [], "'Paste last' is still the last dictation"
    assert rig.ui[0] == ("begin",) and rig.ui[-1] == ("end", pw.DONE)
    assert not rig.runner.busy()


def test_the_undo_key_in_the_toast_is_the_platforms():
    rig = Rig()
    rig.runner.h.platform = "win32"
    rig.go()
    assert "Ctrl+Z" in rig.last()[3] and "Cmd+Z" not in rig.last()[3]


def test_the_whole_run_is_on_a_thread_of_its_own_and_start_returns_at_once():
    rig = Rig()
    rig.read_block = threading.Event()
    t0 = time.monotonic()
    assert rig.runner.start() == ta.STARTED
    assert time.monotonic() - t0 < 0.5, "start() waited for the selection to be read"
    assert rig.runner.busy()
    assert wait_for(lambda: hasattr(rig, "read_thread"))
    assert rig.read_thread is not threading.current_thread()
    rig.read_block.set()
    assert wait_for(lambda: not rig.runner.busy())


def test_the_chosen_transform_is_the_one_that_runs():
    custom = [{"id": "c1", "name": "Boss mode", "prompt": "Make it firm and short."}]
    rig = Rig(settings={"transforms": {"enabled": True, "custom": custom, "default": "c1"}})
    rig.go()
    assert "Make it firm and short." in rig.chats[0]["system"]
    assert rig.saved[0]["transform_id"] == "c1" and rig.saved[0]["transform_name"] == "Boss mode"
    assert rig.last()[2] == "Boss mode done"

    pe = Rig(settings={"transforms": {"enabled": True, "default": "prompt_engineer"}})
    pe.chat_result = reply("# Title\nCake\n\n## Role\nYou bake.\n\n## Goal\nA cake. " + "detail " * 10)
    pe.go()
    assert "Write the prompt itself" in pe.chats[0]["system"] and pe.chats[0]["max_tokens"] >= 1400
    assert pe.saved[0]["transform_name"] == "Prompt Engineer"


def test_the_clipboard_choice_is_respected():
    rig = Rig(settings={"clipboard_after_paste": "keep"})
    rig.go()
    assert rig.pastes[0]["restore"] is False


def test_a_menu_start_brings_the_last_app_back_first_and_a_shortcut_does_not():
    menu = Rig()
    menu.go("menu")
    assert menu.refocused == [1] and menu.pastes
    key = Rig()
    key.go("shortcut")
    assert key.refocused == []


# ── not now ──────────────────────────────────────────────────────────────────

def test_while_off_nothing_happens():
    rig = Rig(settings={"transforms": {"enabled": False}})
    assert rig.go() == ta.OFF
    assert rig.chats == [] and rig.toasts == [] and rig.ui == []
    rig.stored = {}
    assert rig.go() == ta.OFF
    rig.stored = None
    assert rig.go() == ta.OFF


def test_never_while_a_dictation_is_recording_or_processing():
    rig = Rig()
    rig.dictating = True
    assert rig.go() == ta.DICTATING
    assert rig.chats == [] and rig.pastes == [] and rig.ui == [] and not rig.runner.busy()
    kind, style, heading, body = rig.last()[:4]
    assert (kind, style) == (ta.BUSY, "info") and (heading, body) == um.TRANSFORM_BUSY_DICTATING


def test_if_it_cannot_be_told_whether_a_dictation_is_running_it_does_not_guess():
    rig = Rig()

    def broken():
        raise RuntimeError("x")
    rig.runner.h.dictation_busy = broken
    assert rig.go() == ta.DICTATING and rig.chats == []


def test_a_second_start_while_one_runs_is_refused():
    rig = Rig()
    rig.chat_block = threading.Event()
    assert rig.runner.start() == ta.STARTED
    assert wait_for(lambda: rig.chats)
    assert rig.runner.start() == ta.ALREADY_RUNNING
    assert rig.last()[0] == ta.BUSY and rig.last()[2:4] == um.TRANSFORM_BUSY_ALREADY
    rig.chat_block.set()
    assert wait_for(lambda: not rig.runner.busy())
    assert len(rig.chats) == 1 and len(rig.pastes) == 1


def test_a_dictation_started_by_the_hotkey_is_told_why_it_waits():
    rig = Rig()
    rig.runner.refuse_dictation()
    assert rig.last()[:2] == (ta.BUSY, "info") and rig.last()[2:4] == um.TRANSFORM_BLOCKS_DICTATION


# ── the selection ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sel", [
    selection("", "none", tc.NO_SELECTION),
    selection("   \n  ", "ax", tc.OK),
    selection("", "none", tc.OK),
])
def test_no_selection_says_select_some_text_first(sel):
    rig = Rig()
    rig.read_result = sel
    assert rig.go() == ta.STARTED
    assert rig.last()[:4] == (ta.RESULT, "info", "Select some text first", um.TRANSFORM_NO_SELECTION[1])
    assert rig.chats == [] and rig.pastes == [] and rig.saved == []
    assert rig.ui[-1] == ("end", pw.NOTHING)


def test_over_a_thousand_words_is_too_much_and_the_model_is_not_asked():
    rig = Rig()
    rig.read_result = selection(" ".join(["word"] * 1001))
    rig.go()
    assert "That's too much text for one go (limit 1,000 words)" in rig.last()[3]
    assert rig.chats == [] and rig.pastes == [] and rig.saved == []
    ok = Rig()
    ok.read_result = selection(" ".join(["word"] * 1000))
    ok.chat_result = reply(" ".join(["Word"] * 1000))
    ok.go()
    assert ok.chats and ok.pastes


def test_a_selection_the_system_called_too_large_is_too_much_too():
    rig = Rig()
    rig.read_result = selection("", "none", tc.TOO_LARGE)
    rig.go()
    assert rig.last()[2:4] == um.TRANSFORM_TOO_LONG


def test_accessibility_off_has_a_button_to_the_pane():
    rig = Rig()
    rig.read_result = selection("", "none", tc.ACCESSIBILITY_OFF)
    rig.go()
    kind, style, heading, body, buttons, timeout = rig.last()
    assert (kind, style, heading) == (ta.FAILED, "warn", "Accessibility is off")
    assert buttons[0]["action"] == "open_ax_settings" and buttons[0]["label"] == "Open Settings"
    assert rig.chats == []


@pytest.mark.parametrize("reason,heading", [
    (tc.SECURE_FIELD, "Can't read this field"),
    (tc.NOT_ALLOWED_HERE, "Can't copy from here"),
    ("timed_out", "Couldn't read the selection"),
    (tc.NOT_READABLE, "Couldn't read the selection"),
])
def test_other_reasons_a_selection_cannot_be_read_have_their_own_sentence(reason, heading):
    rig = Rig()
    rig.read_result = selection("", "none", reason)
    rig.go()
    assert rig.last()[0] == ta.FAILED and rig.last()[2] == heading
    assert rig.chats == [] and rig.pastes == []


def test_a_selection_read_that_hangs_is_given_up_on():
    rig = Rig(read_deadline_s=0.3)
    rig.read_block = threading.Event()
    rig.go()
    assert rig.last()[2] == "Couldn't read the selection"
    assert rig.chats == [] and rig.pastes == []
    rig.read_block.set()


def test_a_selection_read_that_raises_is_a_plain_message():
    rig = Rig()

    def boom():
        raise RuntimeError("secret words")
    rig.runner.h.read_selection = boom
    rig.go()
    assert rig.last()[2] == "Couldn't read the selection"
    assert not any("secret words" in str(l) for l in rig.logged)


# ── the model ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reason,heading,fragment", [
    ("RATE_LIMIT|tokens per minute (TPM)|7.6s|Groq: busy", "Limit reached", "Groq says"),
    ("AUTH: Groq auth/network blocked", "Couldn't rewrite", "refused the connection"),
    ("CONNECTION: Groq connection failed", "Couldn't rewrite", "couldn't reach"),
    ("MODEL_UNAVAILABLE: Groq model gone", "Couldn't rewrite", "no longer available"),
    ("No styling providers configured. Add a key in Settings.", "Couldn't rewrite", "No key is set up"),
    ("TIMEOUT|transform budget exhausted", "Took too long", "didn't answer in time"),
    ("Groq: something odd", "Couldn't rewrite", "Something went wrong"),
])
def test_a_provider_failure_is_a_plain_sentence_and_the_text_is_untouched(reason, heading, fragment):
    rig = Rig()
    rig.chat_error = ChatError(reason, [("Groq", reason)])
    rig.go()
    kind, style, got_heading, body = rig.last()[:4]
    assert (kind, style, got_heading) == (ta.FAILED, "warn", heading)
    assert fragment in body
    assert "RATE_LIMIT|" not in body and "AUTH:" not in body
    assert rig.pastes == [] and rig.saved == [] and rig.copies == []
    assert rig.usage == [], "nothing was answered, nothing is counted"
    assert rig.ui[-1] == ("end", pw.ERROR)
    assert not any(SOURCE in str(l) for l in rig.logged), "the text is never logged"


def test_a_failure_that_is_not_a_provider_error_is_still_plain():
    rig = Rig()
    rig.chat_error = ValueError("the private words " + SOURCE)
    rig.go()
    assert rig.last()[2] == "Couldn't rewrite" and "Your text is unchanged" in rig.last()[3]
    assert not any("private words" in str(l) for l in rig.logged)


def test_the_model_taking_too_long_is_given_up_on_and_counted_if_it_answers_late():
    rig = Rig(ask_deadline_s=0.4)
    rig.chat_block = threading.Event()
    rig.go()
    assert rig.last()[:3] == (ta.FAILED, "warn", "Took too long")
    assert rig.pastes == [] and rig.saved == [] and rig.usage == []
    rig.chat_block.set()                     # the provider answers after all, and bills for it
    assert wait_for(lambda: rig.usage == [rig.chat_result])
    assert rig.pastes == [], "a late answer is never pasted"


def test_esc_cancels_while_the_model_is_thinking_and_nothing_changes():
    rig = Rig()
    rig.chat_block = threading.Event()
    assert rig.runner.start() == ta.STARTED
    assert wait_for(lambda: rig.chats)
    assert rig.runner.cancel() is True
    assert wait_for(lambda: not rig.runner.busy())
    assert rig.last()[2:4] == um.TRANSFORM_CANCELLED
    assert rig.pastes == [] and rig.saved == [] and rig.ui[-1] == ("end", pw.CANCELLED)
    rig.chat_block.set()
    assert wait_for(lambda: rig.usage == [rig.chat_result]), "the answer that came late is still counted"
    assert rig.pastes == []


def test_esc_cancels_while_the_selection_is_being_read():
    rig = Rig()
    rig.read_block = threading.Event()
    assert rig.runner.start() == ta.STARTED
    assert wait_for(lambda: hasattr(rig, "read_thread"))
    assert rig.runner.cancel() is True
    assert wait_for(lambda: not rig.runner.busy())
    assert rig.chats == [] and rig.pastes == [] and rig.last()[2] == "Transform cancelled"
    rig.read_block.set()


def test_esc_with_nothing_running_is_not_ours():
    rig = Rig()
    assert rig.runner.cancel() is False
    rig.go()
    assert rig.runner.cancel() is False


def test_esc_during_the_paste_is_too_late_and_the_paste_finishes():
    rig = Rig()
    rig.paste_block = threading.Event()
    assert rig.runner.start() == ta.STARTED
    assert wait_for(lambda: rig.pastes)
    assert rig.runner.cancel() is True, "still ours: the key is not passed on to a dictation"
    rig.paste_block.set()
    assert wait_for(lambda: not rig.runner.busy())
    assert rig.saved and rig.ui[-1] == ("end", pw.DONE)
    assert any("too late" in str(l) for l in rig.logged)


def test_a_cancel_that_arrives_just_as_the_run_starts_is_not_lost():
    rig = Rig()
    rig.runner._active = True           # start() has claimed the run; the thread has not begun
    assert rig.runner.cancel() is True
    rig.runner._active = False
    rig.read_block = threading.Event()
    rig.runner._cancel_early = True
    rig.runner._active = True
    rig.runner._thread("shortcut")
    assert rig.chats == [] and rig.pastes == []


# ── what the answer is worth ─────────────────────────────────────────────────

def test_the_same_text_back_says_it_already_looks_good_and_replaces_nothing():
    rig = Rig()
    rig.chat_result = reply(SOURCE)
    rig.go()
    assert rig.last()[:4] == (ta.RESULT, "info", "Your text already looks good", "Nothing was changed.")
    assert rig.pastes == [] and rig.saved == [] and rig.usage == [rig.chat_result]
    assert rig.ui[-1] == ("end", pw.NOTHING)


@pytest.mark.parametrize("answer,finish,message", [
    ("I bought milk.", "stop", um.TRANSFORM_BAD_LENGTH),
    (GOOD + " " + GOOD + " " + GOOD, "stop", um.TRANSFORM_BAD_LENGTH),
    ("Yesterday I went to the shop and bought some milk and eggs. Then I came", "length", um.TRANSFORM_CUT_SHORT),
    ("   ", "stop", um.TRANSFORM_NO_ANSWER),
])
def test_an_answer_that_looks_wrong_replaces_nothing_and_says_so(answer, finish, message):
    rig = Rig()
    rig.chat_result = reply(answer, finish_reason=finish)
    rig.go()
    assert rig.last()[0] == ta.FAILED and rig.last()[2:4] == message
    assert "untouched" in rig.last()[3]
    assert rig.pastes == [] and rig.saved == [] and rig.copies == []


def test_what_is_pasted_has_no_preamble_no_fence_and_no_dashes():
    rig = Rig()
    rig.chat_result = reply(f"Here is the polished text:\n\n```\nYesterday I went to the shop {EM} milk and "
                            f"eggs {EM} then made a cake for the party.\n```")
    rig.go()
    pasted = rig.pastes[0]["text"]
    assert pasted == "Yesterday I went to the shop, milk and eggs, then made a cake for the party."
    assert rig.saved[0]["after"] == pasted


def test_a_swear_word_the_model_dropped_comes_back():
    swear = ("that was a really fucking long meeting and i am tired of all the pointless slides "
             "we sat through today")
    rig = Rig()
    rig.read_result = selection(swear)
    rig.chat_result = reply("That was a really long meeting, and I am tired of all the pointless slides "
                            "we sat through today.")
    rig.go()
    assert "fucking" in rig.pastes[0]["text"].lower()


def test_the_selections_own_space_around_it_is_kept_when_it_is_pasted_over():
    rig = Rig()
    rig.read_result = selection("  " + SOURCE + "\n")
    rig.go()
    assert rig.pastes[0]["text"] == "  " + GOOD + "\n"


# ── the paste ────────────────────────────────────────────────────────────────

def test_a_failed_paste_leaves_the_rewrite_on_the_clipboard_and_in_the_journal():
    rig = Rig()
    rig.paste_outcome = pasteboard.PasteOutcome(pasteboard.FAILED, "keystroke_error", on_clipboard=True)
    rig.go()
    assert rig.remembered == [GOOD], "so the failed-paste toast's Copy again copies this"
    assert rig.saved[0]["paste"] == "failed" and rig.saved[0]["after"] == GOOD
    assert rig.shown(ta.RESULT) == [], "no 'done' message for text that did not go in"
    assert rig.ui[-1] == ("end", pw.ERROR)


def test_a_paste_that_could_not_be_verified_still_counts_as_done():
    rig = Rig()
    rig.paste_outcome = pasteboard.PasteOutcome(pasteboard.UNVERIFIED, "no_verification", on_clipboard=True)
    rig.go()
    assert rig.saved[0]["paste"] == "unverified" and rig.last()[2] == "Polish done"
    assert rig.remembered == [GOOD], "it is on the clipboard"


def test_a_paste_that_never_finishes_is_given_up_on_with_the_text_kept():
    rig = Rig(paste_deadline_s=0.3)
    rig.paste_block = threading.Event()
    rig.go()
    assert rig.saved and rig.remembered == [GOOD]
    assert rig.saved[0]["paste"] == "failed"
    rig.paste_block.set()


def test_a_paste_whose_outcome_cannot_be_read_is_not_reported_as_unchanged_text():
    rig = Rig()

    def broken(result, ticket):
        raise RuntimeError("x")
    rig.runner.h.settle_paste = broken
    rig.go()
    assert rig.saved and rig.last()[2] == "Polish done"


def test_with_auto_paste_off_the_rewrite_is_only_copied():
    rig = Rig(settings={"auto_paste": False})
    rig.go()
    assert rig.pastes == [] and rig.copies == [GOOD] and rig.remembered == [GOOD]
    assert rig.saved[0]["paste"] == "copied"
    assert rig.last()[2:4] == um.TRANSFORM_COPIED


# ── the person moved on ──────────────────────────────────────────────────────

def test_another_app_in_front_when_the_model_answers_means_nothing_is_pasted():
    rig = Rig()
    def chat(system, user, **kw):
        rig.chats.append(kw)
        rig.front = MAIL                      # the person switched while the model thought
        return rig.chat_result
    rig.runner.h.chat = chat
    rig.go()
    assert rig.pastes == []
    assert rig.copies == [GOOD] and rig.remembered == [GOOD]
    assert rig.saved[0]["paste"] == "copied" and rig.saved[0]["app"] == "Notes", "the app it was meant for"
    assert rig.last()[2:4] == um.transform_not_pasted_message("darwin")
    assert rig.last()[4][0]["action"].startswith("show_transform:")
    assert rig.ui[-1] == ("end", pw.DONE)


def test_a_selection_that_changed_or_went_means_nothing_is_pasted():
    same = Rig()
    same.reread = selection(SOURCE, "ax", tc.OK)
    same.go()
    assert same.pastes, "the same selection is still there"

    for again in (selection("something else entirely that I typed", "ax", tc.OK),
                  selection("", "none", tc.NO_SELECTION)):
        rig = Rig()
        rig.reread = again
        rig.go()
        assert rig.pastes == [] and rig.copies == [GOOD], again


def test_when_the_selection_cannot_be_checked_the_paste_goes_ahead():
    for again in (None, selection("", "none", tc.NOT_READABLE), selection("", "none", tc.ACCESSIBILITY_OFF)):
        rig = Rig()
        rig.reread = again
        rig.go()
        assert rig.pastes, again
    unknown = Rig()
    unknown.front = tc.UNKNOWN
    unknown.go()
    assert unknown.pastes, "an app that could not be told is not a reason to stop"


def test_moved_and_the_copy_failed_is_said_plainly():
    rig = Rig()
    calls = []

    def front():
        calls.append(1)
        return NOTES if len(calls) == 1 else MAIL       # the app when it began, then another
    rig.runner.h.front_app = front
    rig.copy_ok = False
    rig.go()
    assert rig.pastes == [] and rig.remembered == []
    assert rig.saved[0]["paste"] == "not_pasted"
    assert rig.last()[2:4] == um.TRANSFORM_MOVED_NOT_COPIED


# ── bookkeeping never fails a rewrite ────────────────────────────────────────

def test_a_journal_that_cannot_be_written_does_not_undo_the_paste():
    rig = Rig()
    rig.save_ok = False
    rig.go()
    assert rig.pastes and rig.announced == []
    assert rig.last()[2] == "Polish done" and rig.last()[4] is None, "no Show in Journal for an entry that is not there"


def test_a_usage_row_that_cannot_be_written_does_not_stop_the_rewrite():
    rig = Rig()

    def broken(reply_):
        raise OSError("disk full")
    rig.runner.h.record_usage = broken
    rig.go()
    assert rig.pastes and rig.saved


def test_a_toast_that_cannot_be_shown_does_not_stop_the_rewrite():
    rig = Rig()

    def broken(*a):
        raise RuntimeError("overlay is gone")
    rig.runner.h.say = broken
    rig.go()
    assert rig.pastes and rig.saved and rig.ui[-1] == ("end", pw.DONE)


def test_an_app_that_cannot_be_told_is_recorded_without_one():
    rig = Rig()

    def broken():
        raise RuntimeError("x")
    rig.runner.h.front_app = broken
    rig.go()
    assert rig.pastes and "app" not in rig.saved[0]


def test_the_log_never_holds_the_text():
    rig = Rig()
    rig.go()
    rig.chat_result = reply(SOURCE)
    rig.go()
    for line in rig.logged:
        assert SOURCE not in line and GOOD not in line


def test_it_runs_again_after_a_run():
    rig = Rig()
    rig.go()
    rig.ui.clear()
    rig.go()
    assert len(rig.pastes) == 2 and len(rig.saved) == 2
    assert rig.saved[1]["id"] == 42


# ── Windows: the tray menu leaves a window of ours in front ─────────────────

def window(hwnd, title="Doc - Editor", pid=500, cls="Notepad", visible=True, tool=False, cloaked=False):
    return {"hwnd": hwnd, "pid": pid, "cls": cls, "title": title, "visible": visible,
            "tool": tool, "cloaked": cloaked}


def test_the_window_to_go_back_to_is_the_topmost_ordinary_one_that_is_not_ours():
    stack = [
        window(1, "", pid=900, cls="TkTopLevel"),                      # our pill: no title
        window(2, "Waffler", pid=100),                                 # our own window (a skipped pid)
        window(3, "Tray", pid=4, cls="Shell_TrayWnd"),                 # the taskbar
        window(4, "Hidden", pid=5, visible=False),
        window(5, "Tool", pid=6, tool=True),
        window(6, "Other desktop", pid=7, cloaked=True),
        window(7, "Report.docx - Word", pid=8, cls="OpusApp"),         # this one
        window(8, "Older", pid=9),
    ]
    assert ta.pick_window(stack, skip_pids={100, 900}) == 7
    assert ta.pick_window(stack[:6], skip_pids={100, 900}) is None
    assert ta.pick_window([], skip_pids=()) is None
    assert ta.pick_window([window(9, "x", pid=None)], skip_pids={None}) is None, "a pid that is not known is skipped"


def test_going_back_to_the_last_app_is_only_for_windows():
    import sys
    if sys.platform != "win32":
        assert ta.refocus_last_app({1}) is False
