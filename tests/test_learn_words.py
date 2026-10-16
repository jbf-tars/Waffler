"""Words to suggest from an edit in the Journal (src/learn_words.py).

After someone corrects a dictation in the Journal, the difference between what
was pasted and what they wrote can show a word the Vocabulary should know. The
rules are narrow on purpose, because a suggestion that is wrong is noise and a
"sounds like" that is wrong rewrites everything said afterwards:

* a single word that became a single other word, and that looks like a
  spelling fix (not a different word), or the same word in capitals;
* at least three letters, not an everyday word, not already in the Vocabulary
  (as a word or a "sounds like");
* what it sounds like is what the speech service wrote (the entry's ``text``),
  never the clean-up's version, because the Vocabulary is applied before the
  clean-up; and never an everyday word;
* at most four, names and acronyms first. Nothing is ever added by this code.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import learn_words as L  # noqa: E402


def sug(heard, pasted, edited, words=(), sounds=None):
    return L.suggest(heard, pasted, edited, list(words), dict(sounds or {}))


# ── the plain case ───────────────────────────────────────────────────────────

def test_a_misheard_name_corrected_is_suggested_with_what_was_heard():
    got = sug("I use waffle every day for dictation",
              "I use waffle every day for dictation.",
              "I use Waffler every day for dictation.")
    assert got == [{"word": "Waffler", "sounds_like": ["waffle"]}]


def test_punctuation_and_line_breaks_do_not_matter():
    got = sug("hello waffle, how are you",
              "Hello, waffle! How are you?",
              "Hello,\nWaffler!\nHow are you?")
    assert got == [{"word": "Waffler", "sounds_like": ["waffle"]}]


def test_nothing_changed_is_nothing_to_suggest():
    assert sug("a b c", "A b c.", "A b c.") == []
    assert sug("a b c", "A b c.", "  A b c.  ") == []


def test_an_empty_edit_or_no_pasted_text_is_nothing():
    assert sug("x", "", "Waffler") == []
    assert sug("x", "Hello there", "") == []
    assert sug("", "", "") == []


# ── the speech service's spelling, not the clean-up's ────────────────────────

def test_what_it_sounds_like_is_how_the_speech_service_wrote_it_not_the_pasted_word():
    # The clean-up capitalised the name: the Vocabulary runs before it, so the
    # spelling to match is the lower case one.
    got = sug("send it to shivon today", "Send it to Shivon today.", "Send it to Siobhan today.")
    assert got == [{"word": "Siobhan", "sounds_like": ["shivon"]}]


def test_when_the_clean_up_changed_the_word_itself_the_speech_services_word_is_found_by_position():
    got = sug("meet me at the cafay on friday",
              "Meet me at the café on Friday.",
              "Meet me at the Kafe on Friday.")
    assert got == [{"word": "Kafe", "sounds_like": ["cafay"]}]


def test_a_word_the_speech_service_never_wrote_cannot_be_given_a_sounds_like():
    # The pasted word is not in what was heard and does not sit in a one-for-one
    # change, so there is no honest "sounds like". Nothing is suggested.
    assert sug("alpha beta gamma delta", "Zorro and Quill went home.", "Zorro and Quilt went home.") == []


def test_the_sounds_like_is_never_an_everyday_word():
    # "their" -> "Thayer" would make every "their" Thayer.
    assert sug("this is their book", "This is their book.", "This is Thayer book.") == []


def test_the_word_it_was_changed_to_is_never_an_everyday_word():
    assert sug("we went theyre today", "We went theyre today.", "We went there today.") == []


# ── what is not a vocabulary word ────────────────────────────────────────────

def test_a_different_word_is_not_a_spelling_fix():
    assert sug("a quick brown fox", "A quick brown fox.", "A quick brown wolf.") == []
    assert sug("a quick brown zorrow", "A quick brown zorrow.", "A quick brown wolf.") == []     # lower case: must look alike


@pytest.mark.parametrize("old, new", [
    ("tuesday", "Thursday"), ("monday", "Friday"), ("sunday", "Monday"),
    ("october", "November"), ("august", "September"),
    ("fifteen", "Sixteen"), ("fourteen", "Fifteen"), ("second", "Third"),
])
def test_changing_a_day_a_month_or_a_number_for_another_is_a_change_of_mind(old, new):
    assert sug(f"see you on {old} ok", f"See you on {old} ok.", f"See you on {new} ok.") == []


def test_a_name_may_be_written_nothing_like_how_it_was_heard():
    assert sug("this is neev from sales", "This is neev from sales.", "This is Niamh from sales.") == [
        {"word": "Niamh", "sounds_like": ["neev"]}]


def test_two_words_into_one_or_one_into_two_is_left_alone():
    assert sug("we use waffle her daily", "We use waffle her daily.", "We use Waffler daily.") == []
    assert sug("we use waffler daily", "We use waffler daily.", "We use waffle her daily.") == []


def test_two_changed_words_side_by_side_are_a_rewrite_not_a_spelling():
    assert sug("a morter nuvva b", "A morter nuvva b.", "A Morta Nova b.") == []


def test_a_word_of_fewer_than_three_letters_is_left_alone():
    assert sug("ask xi about it", "Ask xi about it.", "Ask Xi about it.") == []
    assert sug("it costs 5 dollars", "It costs 5 dollars.", "It costs 6 dollars.") == []


def test_numbers_are_not_words():
    assert sug("we have twelve", "We have twelve.", "We have 12.") == []
    assert sug("room four oh two", "Room four oh two.", "Room 402.") == []


def test_a_word_already_in_the_vocabulary_is_not_suggested_again():
    assert sug("use waffle daily", "Use waffle daily.", "Use Waffler daily.", words=["waffler"]) == []


def test_a_word_that_is_already_how_something_sounds_is_not_suggested():
    got = sug("use waffle daily", "Use waffle daily.", "Use Waffler daily.", words=["Morta"],
              sounds={"Morta": ["Waffler"]})
    assert got == []


def test_a_sounds_like_that_is_already_a_word_or_a_spelling_is_not_suggested():
    assert sug("use waffle daily", "Use waffle daily.", "Use Waffler daily.", words=["Waffle"]) == []
    assert sug("use waffle daily", "Use waffle daily.", "Use Waffler daily.", words=["Morta"],
               sounds={"Morta": ["waffle"]}) == []


# ── capitals only ────────────────────────────────────────────────────────────

def test_capitals_in_the_middle_of_a_word_are_a_vocabulary_word_with_nothing_heard():
    assert sug("we use posthog daily", "We use posthog daily.", "We use PostHog daily.") == [
        {"word": "PostHog", "sounds_like": []}]
    assert sug("the cobie report", "The cobie report.", "The COBie report.") == [
        {"word": "COBie", "sounds_like": []}]


def test_a_name_capitalised_in_the_middle_of_a_sentence_is_suggested():
    assert sug("ask kubernetes people", "Ask kubernetes people.", "Ask Kubernetes people.") == [
        {"word": "Kubernetes", "sounds_like": []}]


def test_a_capital_at_the_start_of_a_sentence_is_only_a_sentence():
    assert sug("kubernetes is big", "kubernetes is big.", "Kubernetes is big.") == []
    assert sug("it is big. kubernetes is big", "It is big. kubernetes is big.", "It is big. Kubernetes is big.") == []
    assert sug("it is big\nkubernetes is big", "It is big\nkubernetes is big.", "It is big\nKubernetes is big.") == []


def test_lower_casing_a_word_is_not_a_suggestion():
    assert sug("use PostHog", "Use PostHog.", "Use posthog.") == []


# ── several, repeats, order ──────────────────────────────────────────────────

def test_the_same_fix_made_twice_is_one_suggestion():
    got = sug("waffle is good and waffle is fast", "Waffle is good and waffle is fast.",
              "Waffler is good and Waffler is fast.")
    assert got == [{"word": "Waffler", "sounds_like": ["waffle"]}]


def test_the_same_word_fixed_from_two_spellings_collects_both():
    got = sug("waffle is good and wafler is fast", "Waffle is good and wafler is fast.",
              "Waffler is good and Waffler is fast.")
    assert got == [{"word": "Waffler", "sounds_like": ["waffle", "wafler"]}]


def test_at_most_four_names_first_in_the_order_they_appear():
    heard = "one zorrow two quil three morter four novva five posthog six sinead seven kobie"
    pasted = "One zorrow two quil three morter four novva five posthog six sinead seven kobie."
    edited = "One Zorro two Quill three Morta four Nova five PostHog six Sinéad seven COBie."
    got = sug(heard, pasted, edited)
    assert len(got) == 4
    assert [g["word"] for g in got] == ["Zorro", "Quill", "Morta", "Nova"]


def test_capitalised_candidates_come_before_lower_case_ones():
    got = sug("a quil and mortar", "A quil and mortar.", "A quill and Morta.")
    assert [g["word"] for g in got] == ["Morta", "quill"]


def test_a_word_added_to_the_text_is_not_a_substitution():
    assert sug("hello there", "Hello there.", "Hello there Waffler.") == []


def test_a_word_taken_out_is_not_a_substitution():
    assert sug("hello there waffle", "Hello there waffle.", "Hello there.") == []


# ── other scripts and marks ──────────────────────────────────────────────────

def test_accents_are_part_of_the_word():
    got = sug("ask sinead today", "Ask sinead today.", "Ask Sinéad today.")
    assert got == [{"word": "Sinéad", "sounds_like": ["sinead"]}]


def test_an_apostrophe_or_hyphen_inside_a_name_stays_in_it():
    got = sug("ask obrian today", "Ask obrian today.", "Ask O'Brian today.")
    assert got == [{"word": "O'Brian", "sounds_like": ["obrian"]}]
    got = sug("ask jean luc today", "Ask jean luc today.", "Ask Jean-Luc today.")
    assert got == []            # two words into one


# ── the setting ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, want", [
    ({}, True), ({"learn_from_edits": True}, True), ({"learn_from_edits": False}, False),
    ({"learn_from_edits": "no"}, True), ({"learn_from_edits": 0}, True), (None, True), ("x", True),
])
def test_it_is_on_unless_the_setting_is_exactly_false(stored, want):
    assert L.enabled(stored) is want


@pytest.mark.parametrize("payload, bad", [
    ({}, False), ({"learn_from_edits": True}, False), ({"learn_from_edits": False}, False),
    ({"learn_from_edits": "false"}, True), ({"learn_from_edits": 1}, True), ({"learn_from_edits": None}, True),
])
def test_the_setting_is_a_true_or_a_false(payload, bad):
    assert bool(L.settings_problem(payload)) is bad


# ── for entries ──────────────────────────────────────────────────────────────

def test_suggestions_for_an_entry_use_its_text_and_what_was_pasted():
    entry = {"text": "send it to shivon today", "styled": "Send it to Shivon today."}
    assert L.suggest_for_entry(entry, "Send it to Siobhan today.", ["Groq"], {}) == [
        {"word": "Siobhan", "sounds_like": ["shivon"]}]


def test_an_entry_without_words_has_no_suggestions():
    assert L.suggest_for_entry({"failed": True}, "x", [], {}) == []
    assert L.suggest_for_entry({"kind": "transform", "before": "a", "after": "b"}, "b c", [], {}) == []
    assert L.suggest_for_entry("junk", "x", [], {}) == []


def test_the_pasted_words_are_the_baseline_even_after_an_earlier_edit():
    entry = {"text": "use waffle daily", "styled": "Use waffle daily.", "edited_text": "Use Waffler daily."}
    assert L.suggest_for_entry(entry, "Use Waffler daily. Thanks.", [], {}) == [
        {"word": "Waffler", "sounds_like": ["waffle"]}]
