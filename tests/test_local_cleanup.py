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
    expected = styler.prompt_template.format(
        transcript=RAW, dialect_instruction=style_openai._dialect_instruction({}))
    assert message["content"] == expected, "the prompt every provider gets, to the byte"
    assert call["temperature"] == style_openai._CLEANUP_TEMPERATURE
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
