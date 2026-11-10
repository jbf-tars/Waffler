"""Short lower-case takes get a capital and, when it is safe, a final mark.

A take of ten words or fewer skips the model, so nothing punctuated it: when
speech to text wrote it in lower case with no punctuation ("can you ring me
back when you are free") that is how it was pasted. Such a take now gets its
first capital, "I", the days and months, and a final "?" or "." when what it
is can be told from its first words. Anything that might be a command, code,
an address, a single word or two, or that already has any punctuation or any
capital, is pasted exactly as before. Every example was written for this file.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import style_openai as so  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402

S = object.__new__(OpenAIStyler)


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setattr(so, "_dictation_language", lambda: "en")
    monkeypatch.setattr(OpenAIStyler, "_small_vocabulary", staticmethod(lambda: frozenset({"pywebview"})))


@pytest.mark.parametrize("take, wanted", [
    # Questions: a question word with its verb, or a verb before its subject.
    ("can you ring me back when you are free", "Can you ring me back when you are free?"),
    ("what is the wifi password", "What is the wifi password?"),
    ("where did you put the keys", "Where did you put the keys?"),
    ("how many people are coming on friday", "How many people are coming on Friday?"),
    ("what time does it start", "What time does it start?"),
    ("how about we move it to tuesday", "How about we move it to Tuesday?"),
    ("did the parcel arrive", "Did the parcel arrive?"),
    ("is it still raining", "Is it still raining?"),
    ("are we still on for tonight", "Are we still on for tonight?"),
    ("should i bring anything", "Should I bring anything?"),
    ("who wants a coffee", "Who wants a coffee?"),
    ("what's the plan for tomorrow", "What's the plan for tomorrow?"),
    # Statements.
    ("sounds good see you there", "Sounds good see you there."),
    ("thanks i'll have a look in the morning", "Thanks I'll have a look in the morning."),
    ("i think i left it in the car", "I think I left it in the car."),
    ("see you on monday the third of october", "See you on Monday the third of October."),
    ("running five minutes late", "Running five minutes late."),
    ("the follow-up is booked", "The follow-up is booked."),
])
def test_a_short_lower_case_take_is_given_sentence_case(take, wanted):
    assert S._sentence_case_short_take(take) == wanted


@pytest.mark.parametrize("take, wanted", [
    # It opens like a question but is not surely one: a capital and no mark.
    ("when you get there give me a call", "When you get there give me a call"),
    ("what i mean is the other one", "What I mean is the other one"),
    ("is everything ready for the demo", "Is everything ready for the demo"),
    ("do the dishes before you go", "Do the dishes before you go"),
    ("have the report ready by noon", "Have the report ready by noon"),
    ("how nice of them to ask", "How nice of them to ask"),
    ("will power is what it takes", "Will power is what it takes"),
    # It stops on a word a sentence does not end on: the take was cut short.
    ("i was going to say that the", "I was going to say that the"),
    ("put it next to the printer and", "Put it next to the printer and"),
    # It may be a question by its ending: no full stop is guessed.
    ("you got the tickets right", "You got the tickets right"),
    ("that works for you yeah", "That works for you yeah"),
])
def test_when_the_ending_cannot_be_told_only_the_capital_is_added(take, wanted):
    assert S._sentence_case_short_take(take) == wanted


@pytest.mark.parametrize("take", [
    # One or two words.
    "yes", "okay thanks", "hello there",
    # Commands and code.
    "git status", "npm run build", "docker compose up", "cd into the project folder",
    "make clean and then make all", "sudo apt install curl", "python manage dot py migrate",
    "pywebview create window", "select name from users", "import os and sys",
    "ls dash la", "echo hello world",
    # Anything that is not plain lower-case words.
    "open example.com in a new tab", "send it to sam@example.com please",
    "the file is src/app.py", "set max_tokens to four thousand", "it costs 40 pounds",
    "use the --force flag here", "press ctrl+c to stop it", "c'est la vie mon ami",
    # Already punctuated or capitalised: someone, or the model, chose that.
    "sounds good, see you there", "sounds good see you there.", "is it raining?",
    "Sounds good see you there", "meet Sam at the station", "ask about the API key",
    "yo can you grab me a coffee, ta",
    # More than ten words is the model's job.
    "can you ring me back when you are free because i have news",
    "", "   ",
])
def test_everything_else_is_pasted_as_it_was(take):
    assert S._sentence_case_short_take(take) == take


def test_i_alone_is_always_a_capital():
    assert S._sentence_case_short_take("then i said i'd do it") == "Then I said I'd do it."
    assert S._sentence_case_short_take("i've got it thanks") == "I've got it thanks."


def test_march_and_may_are_left_because_they_are_also_words():
    assert S._sentence_case_short_take("we may march on friday") == "We may march on Friday."


def test_another_language_is_left_alone(monkeypatch):
    monkeypatch.setattr(so, "_dictation_language", lambda: "de")
    assert S._sentence_case_short_take("was ist das für ein auto") == "was ist das für ein auto"
    monkeypatch.setattr(so, "_dictation_language", lambda: "auto")
    assert S._sentence_case_short_take("can you ring me back") == "can you ring me back"


# ── in the clean-up ──────────────────────────────────────────────────────────

def test_the_quick_path_uses_it_after_the_fillers_are_out():
    styled, usage = S.style("um can you ring me back when you are free")
    assert styled == "Can you ring me back when you are free?" and usage["api_used"] is False
    assert S.style("sounds good see you there")[0] == "Sounds good see you there."
    assert S.style("git status")[0] == "git status"
    assert S.style("Thanks.")[0] == "Thanks."


def test_the_fallback_after_a_failed_clean_up_still_pastes_the_words_as_heard():
    # _basic_clean alone (a provider failed, or the truncation guard fired) is
    # the speaker's own words: nothing is guessed there.
    assert S._basic_clean("can you ring me back when you are free", language="en") == \
        "can you ring me back when you are free"
