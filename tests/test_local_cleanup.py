"""The clean-up done by the model on this computer (src/local_llm.py through
src/style_openai.py): the app's own prompt and guards, used when the model is
here and there is no key, or the person chose it; within the clean-up's
deadline; and never a provider behind the person's back.

The model is replaced by a function. The real one is scripts/local_llm_check.py.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import local_llm  # noqa: E402
import style_openai  # noqa: E402
import user_messages  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402

RAW = ("So um I was thinking about the onboarding flow again and, you know, I think the problem is "
       "that we ask for too much up front.")
TIDY = "I was thinking about the onboarding flow again and I think the problem is that we ask for too much up front."


class FakeLocal:
    """local_llm.chat, recorded."""

    def __init__(self, text=TIDY, error=None, finish="stop"):
        self.text, self.error, self.finish, self.calls = text, error, finish, []

    def __call__(self, system, messages, **kw):
        self.calls.append({"system": system, "messages": messages, **kw})
        if self.error:
            raise self.error
        return {"text": self.text, "input_tokens": 5700, "output_tokens": 30, "finish_reason": self.finish}


@pytest.fixture
def here(monkeypatch):
    """The writing model is downloaded and this build has its engine."""
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(local_llm, "is_available", lambda: True)
    monkeypatch.setattr(local_llm, "is_ready", lambda: True)
    fake = FakeLocal()
    monkeypatch.setattr(local_llm, "chat", fake)
    return fake


def _no_provider(styler, monkeypatch):
    """Any call to a provider fails the test."""
    def never(*a, **k):
        raise AssertionError("a provider was asked")
    for name in ("_style_groq", "_style_cerebras", "_style_openai"):
        monkeypatch.setattr(styler, name, never)


def test_with_no_key_the_model_here_tidies_the_dictation(here):
    styled, usage = OpenAIStyler().style(RAW)
    assert styled == TIDY
    assert usage["provider"] == "ondevice" == local_llm.PROVIDER
    assert usage["api_used"] is False, "nothing to count as a provider's tokens"
    assert "fallback_reason" not in usage
    (call,) = here.calls
    assert call["kind"] == "cleanup" and call.get("background") in (None, False)


def test_it_is_sent_the_apps_own_prompt_and_system_message(here):
    styler = OpenAIStyler()
    styler.style(RAW)
    (call,) = here.calls
    assert call["system"] == style_openai._CLEANUP_SYSTEM
    (message,) = call["messages"]
    assert message["role"] == "user"
    every_provider = styler.prompt_template.format(
        transcript=RAW, dialect_instruction=style_openai._dialect_instruction({}))
    # The prompt every provider gets, to the byte, with the reminder of the
    # rules this small model misses just before the transcript
    # (tests/test_local_cleanup_reminder.py).
    assert message["content"] == OpenAIStyler._for_local(every_provider)
    assert message["content"].replace(style_openai._LOCAL_REMINDER, "", 1) == every_provider
    # No chance in the choice of words: measured to give the same text for
    # the same dictation whatever came before it, which 0.1 did not.
    assert call["temperature"] == style_openai._LOCAL_CLEANUP_TEMPERATURE == 0.0
    assert call["max_tokens"] >= 2048


def test_it_is_given_the_clean_ups_own_deadline(here):
    before = time.monotonic()
    OpenAIStyler().style(RAW)
    deadline = here.calls[0]["deadline"]
    words = len(RAW.split())
    assert before < deadline <= time.monotonic() + style_openai._style_deadline_for(words)


def test_the_guards_run_on_what_it_writes(here):
    raw = "Honestly this fucking build has failed four times today and I have had it with the whole thing."
    here.text = "Honestly this build has failed four times today — and I have had it with the whole thing."
    styled, usage = OpenAIStyler().style(raw)
    assert "this fucking build" in styled, "the swear word it left out is put back"
    assert "—" not in styled, "no dashes"
    assert usage["provider"] == "ondevice"


def test_an_answer_that_dropped_most_of_the_words_is_not_used(here):
    here.text = "Onboarding asks too much."
    styled, usage = OpenAIStyler().style(RAW)
    assert usage["provider"] == "basic_clean" and usage.get("truncation_guard")
    assert "onboarding flow again" in styled, "the words as said, lightly tidied"


def test_an_answer_cut_off_at_the_limit_is_not_used(here):
    here.finish = "length"
    _styled, usage = OpenAIStyler().style(RAW)
    assert usage["provider"] == "basic_clean" and usage["truncation_guard"] == "finish_reason=length"


def test_a_short_take_still_asks_no_model(here):
    styled, usage = OpenAIStyler().style("call the dentist tomorrow")
    assert here.calls == [] and usage["api_used"] is False and styled == "Call the dentist tomorrow."


def test_too_slow_pastes_the_words_as_said_and_says_so(here):
    here.error = local_llm.LocalLLMError(local_llm.TOO_SLOW, "deadline")
    styled, usage = OpenAIStyler().style(RAW)
    assert usage["provider"] == "basic_clean" and "TIMEOUT|" in usage["fallback_reason"]
    assert "onboarding flow" in styled
    heading, body = user_messages.cleanup_skipped_message(usage["fallback_reason"])
    assert body == "The clean-up took too long, so your words went in as you said them."


@pytest.mark.parametrize("kind, sentence", [
    ("failed", "The clean-up on this computer didn't work this time, so your words went in as you "
               "said them. Nothing was sent anywhere."),
    ("too_long", "That was too long to tidy on this computer, so your words went in as you said them."),
])
def test_a_failure_here_is_a_plain_sentence_and_names_no_provider(here, kind, sentence):
    here.error = local_llm.LocalLLMError("whatever the module said", kind)
    styled, usage = OpenAIStyler().style(RAW)
    assert usage["provider"] == "basic_clean" and "LOCAL|" in usage["fallback_reason"]
    assert "whatever the module said" not in usage["fallback_reason"]
    heading, body = user_messages.cleanup_skipped_message(usage["fallback_reason"])
    assert heading == user_messages.CLEANUP_SKIPPED and body == sentence
    for name in ("Groq", "OpenAI", "Cerebras", "key", "VPN", "online"):
        assert name not in body


def test_with_a_key_nothing_changes_unless_the_person_chose_this_computer(here, monkeypatch, _isolated_data_dir):
    styler = OpenAIStyler(groq_api_key="gsk_test")
    monkeypatch.setattr(styler, "_style_groq", lambda prompt, start: ("from groq " + TIDY, {
        "input_tokens": 1, "output_tokens": 1, "api_used": True, "provider": "groq", "finish_reason": "stop"}))
    styled, usage = styler.style(RAW)
    assert usage["provider"] == "groq" and here.calls == []
    # Chosen in Settings: this computer, and the key is not asked.
    (_isolated_data_dir / "settings.json").write_text('{"writing_mode": "local"}', encoding="utf-8")
    _no_provider(styler, monkeypatch)
    styled, usage = styler.style(RAW)
    assert usage["provider"] == "ondevice" and styled == TIDY


def test_a_failure_here_never_falls_back_to_a_key_the_person_chose_not_to_use(here, monkeypatch, _isolated_data_dir):
    (_isolated_data_dir / "settings.json").write_text('{"writing_mode": "local"}', encoding="utf-8")
    styler = OpenAIStyler(groq_api_key="gsk_test", api_key="sk-test")
    _no_provider(styler, monkeypatch)
    here.error = local_llm.LocalLLMError(local_llm.FAILED, "failed")
    styled, usage = styler.style(RAW)
    assert usage["provider"] == "basic_clean" and "LOCAL|" in usage["fallback_reason"]


def test_not_downloaded_or_not_in_this_build_means_it_is_not_asked(here, monkeypatch):
    monkeypatch.setattr(local_llm, "is_ready", lambda: False)
    _styled, usage = OpenAIStyler().style(RAW)
    assert here.calls == [] and usage["provider"] == "basic_clean"
    monkeypatch.setattr(local_llm, "is_ready", lambda: True)
    monkeypatch.setattr(local_llm, "is_available", lambda: False)
    OpenAIStyler().style(RAW)
    assert here.calls == []


def test_the_long_prompt_it_reads_ahead_is_the_start_of_every_clean_up(here, monkeypatch):
    """What warm() reads must be the very text a dictation's prompt starts
    with, or reading it ahead saves nothing."""
    monkeypatch.setattr(local_llm, "_primer", None)
    styler = OpenAIStyler()
    assert local_llm._primer is not None, "the styler says what to read ahead"
    system, user = local_llm._primer()
    styler.style(RAW)
    sent = here.calls[0]["messages"][0]["content"]
    assert system == here.calls[0]["system"]
    shared = 0
    for i, (a, b) in enumerate(zip(user, sent)):
        if a != b:
            break
        shared = i + 1
    assert shared > 0.95 * len(sent[:sent.index("Transcript: ")]), "all the rules are shared"


# ── a list it numbered wrongly or left on one line ───────────────────────────

@pytest.mark.parametrize("wrote, pasted", [
    # Measured: "three things ... first ..." came back numbered 3, 2, 3.
    ("3. Chase the invoice.\n2. Sort out the VAT return.\n3. Book the van.",
     "1. Chase the invoice.\n2. Sort out the VAT return.\n3. Book the van."),
    # Measured: the list written along one line.
    ("Right, three things for today. 1. Chase the invoice. 2. Sort out the VAT return. 3. Book the van.",
     "Right, three things for today.\n1. Chase the invoice.\n2. Sort out the VAT return.\n3. Book the van."),
    ("Here's the plan:\n\n1. Fix it.\n2. Ship it.", "Here's the plan:\n\n1. Fix it.\n2. Ship it."),
])
def test_a_numbered_list_is_put_right(wrote, pasted):
    assert OpenAIStyler._tidy_numbered_list(wrote) == pasted


@pytest.mark.parametrize("text", [
    "2. Sort out the VAT return.\n3. Book the van.",                 # items two and three of something: theirs
    "Number three, we'll just skip that at the moment.",
    "We had 1,240 sign-ups and the p95 on v3.12.5 is 230ms.",
    "It is version 3. 12 people have it. 2 of them complained.",
    "1. Only one item.",
    "The meeting is at 3. Bring the 2 reports.",
    "",
])
def test_nothing_that_is_not_a_broken_list_is_touched(text):
    assert OpenAIStyler._tidy_numbered_list(text) == text


def test_the_list_is_put_right_only_for_the_model_here(here, monkeypatch):
    raw = ("Right, three things for today. First, chase the Hartley invoice because it is late. Second, sort "
           "out the VAT return. And third, book the van for Saturday.")
    here.text = ("3. Chase the Hartley invoice because it is late.\n2. Sort out the VAT return.\n"
                 "3. Book the van for Saturday.")
    styled, usage = OpenAIStyler().style(raw)
    assert usage["provider"] == "ondevice" and styled.splitlines()[1].startswith("1. Chase")
    # A provider's answer goes through what it always did.
    styler = OpenAIStyler(groq_api_key="gsk_test")
    monkeypatch.setattr(styler, "_chat_groq", lambda *a, **k: style_openai.ChatReply(
        text=here.text, provider="groq", finish_reason="stop"))
    styled, usage = styler.style(raw)
    assert usage["provider"] == "groq" and styled.splitlines()[0].startswith("3. Chase")


# ── the sentence before a list, which it was measured to leave out ───────────

LIST = "1. Chase the Hartley invoice.\n2. Sort out the VAT return.\n3. Book the van."


@pytest.mark.parametrize("raw, lead", [
    ("Right, three things for today. First, chase the Hartley invoice. Second, um, sort out the VAT return. "
     "And third, book the van.", "Right, three things for today."),
    ("A few things for tomorrow. Number one, chase the Hartley invoice. Number two, sort out the VAT return. "
     "Number three, book the van.", "A few things for tomorrow."),
    ("So for the trip, first, chase the Hartley invoice. Second, sort out the VAT return. Third, book the van.",
     "So for the trip:"),
    ("Just a quick one. Firstly, chase the Hartley invoice. Secondly, sort out the VAT return. Thirdly, book "
     "the van.", "Just a quick one."),
])
def test_the_sentence_that_led_into_a_list_is_put_back(raw, lead):
    styler = OpenAIStyler()
    assert styler._restore_list_lead_in(LIST, raw) == lead + "\n" + LIST


@pytest.mark.parametrize("styled, raw", [
    # It kept the lead-in: nothing to do.
    ("Three things.\n" + LIST, "Three things. First, chase the Hartley invoice. Second, sort out the VAT "
                              "return. Third, book the van."),
    # The dictation began with the list.
    (LIST, "First, chase the Hartley invoice. Second, sort out the VAT return. Third, book the van."),
    # Not a list at all.
    ("First we tried the old approach, but it didn't work.", "First we tried the old approach, but it didn't work."),
    # A long opening is not a lead-in: if that went missing, the truncation guard is what catches it.
    (LIST, " ".join(["word"] * 40) + ". First, chase the Hartley invoice. Second, sort out the VAT return."),
])
def test_nothing_is_put_back_where_nothing_was_left_out(styled, raw):
    assert OpenAIStyler()._restore_list_lead_in(styled, raw) == styled


def test_the_lead_in_comes_back_in_a_dictation_tidied_here(here):
    raw = ("Right, three things for today. First, chase the Hartley invoice because it is late. Second, sort "
           "out the VAT return. And third, book the van for Saturday.")
    here.text = ("3. Chase the Hartley invoice because it is late.\n2. Sort out the VAT return.\n"
                 "3. Book the van for Saturday.")
    styled, _usage = OpenAIStyler().style(raw)
    assert styled == ("Right, three things for today.\n1. Chase the Hartley invoice because it is late.\n"
                      "2. Sort out the VAT return.\n3. Book the van for Saturday.")
