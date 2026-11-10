"""The first letter of a clean-up, and what removing a filler leaves behind.

Two things a review of real use and of the short-take path found:

  * the model removes an opening filler ("And yeah the dog got out." ->
    "the dog got out.") and leaves the next word in lower case, so the paste
    starts with a small letter (11 of 303 recent takes);
  * the quick path for short takes (no model) deletes "um" / "uh" / "er" and
    leaves the punctuation that followed it: "Uh, yes." was pasted as
    ", yes.", and "um can you ring me" as "can you ring me".

Every example was written for this file.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from style_openai import OpenAIStyler  # noqa: E402

S = object.__new__(OpenAIStyler)


# ── the first letter of the model's answer ───────────────────────────────────

@pytest.mark.parametrize("styled, raw, wanted", [
    # The model took the opener off: the take began with a capital, so does the paste.
    ("the dog did get out twice last week.",
     "And yeah the dog did get out twice last week.",
     "The dog did get out twice last week."),
    ("the opening hours are showing the winter times.",
     "Yeah, so the opening hours are showing the winter times.",
     "The opening hours are showing the winter times."),
    # Sentence case everywhere else: a lower-case start is a slip, not a register.
    ("but in addition the forms need a date. Can you add one?",
     "but in addition the forms need a date. Can you add one?",
     "But in addition the forms need a date. Can you add one?"),
    ("not yet. It has been in the inbox since Tuesday.",
     "okay well not yet it has been in the inbox since tuesday",
     "Not yet. It has been in the inbox since Tuesday."),
    ("\"the usual\" is what she said. Then she left.",
     "\"the usual\" is what she said. Then she left.",
     "\"The usual\" is what she said. Then she left."),
])
def test_a_sentence_case_answer_starts_with_a_capital(styled, raw, wanted):
    assert S._capitalise_opening(styled, raw) == wanted


@pytest.mark.parametrize("styled, raw", [
    # A lower-case register the speaker kept throughout (prompt rule 11).
    ("yo can you grab me a coffee when you go on lunch break, the usual please, ta",
     "yo can you grab me a coffee when you go on lunch break, the usual please, ta"),
    ("sounds good. see you there.", "sounds good. see you there."),
    # "I" alone is no sign of sentence case.
    ("yeah i think. I will check", "yeah i think. I will check"),
    # Words that are written small wherever they stand.
    ("iPhone storage is full again. Can you clear it?", "iPhone storage is full again. Can you clear it?"),
    ("npm install fails on the build box. It worked yesterday.",
     "npm install fails on the build box. It worked yesterday."),
    ("git push was rejected. Then it went through.", "And git push was rejected. Then it went through."),
    ("example.com is down again. Nobody noticed.", "example.com is down again. Nobody noticed."),
    ("src/app.py has the bug. Line forty.", "src/app.py has the bug. Line forty."),
    ("x-ray results are back. Nothing broken.", "x-ray results are back. Nothing broken."),
    # Already right, or nothing to capitalise.
    ("The report is late.", "the report is late"),
    ("575 grams of rice. Then the eggs.", "575 grams of rice. Then the eggs."),
    ("- milk\n- eggs", "milk and eggs"),
    ("", "anything"),
])
def test_everything_else_is_left_as_the_model_wrote_it(styled, raw):
    assert S._capitalise_opening(styled, raw) == styled


def test_a_list_or_a_line_break_does_not_hide_the_first_word():
    assert S._capitalise_opening("the plan:\n\n1. Order the skip.\n2. Book the electrician.",
                                 "So the plan number one order the skip number two book the electrician"
                                 ) == "The plan:\n\n1. Order the skip.\n2. Book the electrician."


def test_other_scripts_and_letters_without_a_single_capital_are_left():
    assert S._capitalise_opening("ß ist ein Buchstabe. Das ist so.", "Ja ß ist ein Buchstabe.") == \
        "ß ist ein Buchstabe. Das ist so."
    assert S._capitalise_opening("это тест. Да.", "Ну это тест. Да.") == "Это тест. Да."


def test_every_providers_answer_goes_through_it():
    assert S._finish_cleanup("the dog got out. It came back.", "And yeah the dog got out. It came back.") == \
        "The dog got out. It came back."


# ── the quick path for short takes ───────────────────────────────────────────

@pytest.mark.parametrize("raw, wanted", [
    ("Uh, yes.", "Yes."),
    ("Um, can you ring me back?", "Can you ring me back?"),
    ("Er... no.", "No."),
    ("Erm - maybe later.", "Maybe later."),
    ("um can you ring me back when you are free", "can you ring me back when you are free"),
    ("Um can you ring me back", "Can you ring me back"),
    ("I think so, um, yes.", "I think so, yes."),
    ("It was, uh, fine.", "It was fine."),
    ("I'll bring the, uh, updated numbers.", "I'll bring the updated numbers."),
    ("Can we do, uh Thursday at two?", "Can we do Thursday at two?"),
    ("We left early, um, because of the rain.", "We left early, because of the rain."),
    ("Yes, um.", "Yes."),
    ("Well, uh.", "Well."),
    ("Is it, er?", "Is it?"),
    ("the the report is late", "the report is late"),
    ("Thanks.", "Thanks."),
    ("um", ""),
])
def test_removing_a_filler_leaves_no_stray_punctuation(raw, wanted):
    assert S._basic_clean(raw, language="en") == wanted


def test_text_without_fillers_is_untouched_by_the_tidy_up():
    for text in ("...and then it stopped.", ", yes.", "Wait... what?", "- milk", "e.g. this, that."):
        assert S._basic_clean(text, language="en") == text


def test_another_language_keeps_its_words():
    # "um" is a word in Portuguese and German: only the shared hesitations go.
    assert S._basic_clean("Um, das ist gut.", language="de") == "Um, das ist gut."
    assert S._basic_clean("Uh, das ist gut.", language="de") == "Das ist gut."
