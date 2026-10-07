"""The single-word Vocabulary pass must not rewrite real words.

Found in one user's real log (app.log, 225 "Vocabulary corrections applied"
lines, 2026-10-05), vocabulary: Waffler, Morta, Ashkan, COBie, COBieQC,
craic, Rohan, Kandola, James Farrelly, Malak, XBim.

False rewrites (each a real word, or the entry with letters added to or
taken off one end):
    waffle -> Waffler (16), waffled -> Waffler (1), mortar -> Morta (15),
    bim -> XBim (9)
and from testing: Phillips -> Phillip, Matthews -> Mathew, linked -> LinkedIn.

Genuine corrections that must keep working, from the same log:
    ashkahn -> Ashkan, ashcan -> Ashkan, malek -> Malak, morty -> Morta,
    mora -> Morta, rowan -> Rohan, woffler -> Waffler, cobiec -> COBieQC,
    kobi qc -> COBieQC, and capitals ("cobie" -> COBie)
plus the website's five examples.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import (  # noqa: E402
    apply_vocab_changes,
    apply_vocab_corrections,
    clean_vocab,
    fuzzy_match_word,
)

OWNER = ["Ashkan", "COBieQC", "COBie", "Morta", "Waffler", "craic", "Rohan",
         "Kandola", "James Farrelly", "Malak", "XBim"]
NAMES = OWNER + ["Phillip", "Mathew", "LinkedIn", "Isobel", "Caitlyn", "Sinéad",
                 "Hailey", "Clubcard", "Postgres"]


# ── the false rewrites ────────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "I'll waffle on about it for a bit.",
    "He waffled for ten minutes before getting to the point.",
    "Waffle and syrup for breakfast.",
    "The mortar between the bricks needs repointing.",
    "We use BIM on every project now.",
    "Pass me the Phillips screwdriver from the drawer, please.",
    "Mrs Matthews said the maths homework is due on Monday.",
    "The two accounts are linked.",
])
def test_a_real_word_near_an_entry_is_left_alone(sentence):
    corrected, applied = apply_vocab_corrections(sentence, NAMES)
    assert corrected == sentence, applied
    assert applied == []


@pytest.mark.parametrize("word, entry", [
    ("waffle", "Waffler"), ("waffled", "Waffler"), ("waffles", "Waffler"),
    ("mortar", "Morta"), ("bim", "XBim"), ("phillips", "Phillip"),
    ("matthews", "Mathew"), ("linked", "LinkedIn"), ("cobie", "COBieQC"),
    ("kandolas", "Kandola"), ("mortal", "Morta"), ("clubcards", "Clubcard"),
])
def test_an_entry_with_letters_added_or_taken_off_is_not_a_mishearing(word, entry):
    assert fuzzy_match_word(word, [entry]) == []


@pytest.mark.parametrize("word, entry", [
    ("posters", "Postgres"), ("hailed", "Hailey"), ("aiding", "Aidan"),
])
def test_an_everyday_word_with_an_ending_is_left_alone(word, entry):
    """"posters" is not in common_words.py ("post" is) and scored 0.75
    against Postgres."""
    assert fuzzy_match_word(word, [entry]) == []


@pytest.mark.parametrize("word, entry", [
    ("will", "Will"), ("it", "IT"), ("us", "US"), ("mark", "Mark"), ("apple", "Apple"),
])
def test_an_everyday_word_keeps_its_capitals_even_when_it_is_an_entry(word, entry):
    sentence = f"I think {word} is fine."
    assert apply_vocab_corrections(sentence, [entry]) == (sentence, [])


# ── what must keep working ───────────────────────────────────────────────────

@pytest.mark.parametrize("heard, expected", [
    ("I spoke to Ashkahn today.", "I spoke to Ashkan today."),
    ("I spoke to ashcan today.", "I spoke to Ashkan today."),
    ("Malek sent the file.", "Malak sent the file."),
    ("Morty sent the file.", "Morta sent the file."),
    ("Mora sent the file.", "Morta sent the file."),
    ("Rowan sent the file.", "Rohan sent the file."),
    ("Open Woffler and try it.", "Open Waffler and try it."),
    ("Run the cobiec check.", "Run the COBieQC check."),
    ("Run the kobi qc check.", "Run the COBieQC check."),
    ("Export the cobie file.", "Export the COBie file."),
    ("Send it to kandola.", "Send it to Kandola."),
    ("Open waffler and try it.", "Open Waffler and try it."),
    ("Open the xbim viewer.", "Open the XBim viewer."),
])
def test_the_genuine_corrections_in_the_log_still_happen(heard, expected):
    assert apply_vocab_corrections(heard, OWNER)[0] == expected


@pytest.mark.parametrize("heard, expected", [
    ("Send the slides to Isabel.", "Send the slides to Isobel."),
    ("Caitlin is on her way.", "Caitlyn is on her way."),
    ("Sinead is going to ring you.", "Sinéad is going to ring you."),
    ("Hayley booked the room.", "Hailey booked the room."),
    ("Scan your club card at the till.", "Scan your Clubcard at the till."),
    ("Check the post grass migration.", "Check the Postgres migration."),
    ("Check the post-grass migration.", "Check the Postgres migration."),
    ("Check the postgress migration.", "Check the Postgres migration."),
    ("Check the postgres migration.", "Check the Postgres migration."),
])
def test_the_website_examples_still_work(heard, expected):
    assert apply_vocab_corrections(heard, NAMES)[0] == expected


def test_the_closest_entry_wins_not_the_first_listed():
    assert fuzzy_match_word("cobiec", ["COBie", "COBieQC"]) == [("cobiec", "COBieQC")]


def test_an_accent_only_difference_is_corrected_even_for_a_short_entry():
    assert apply_vocab_corrections("zoe rang", ["Zoë"])[0] == "Zoë rang"


def test_a_correctly_written_entry_is_not_reported_as_a_correction():
    """Before 3.15 every exact match was logged ("'morta' → 'Morta'", 84 of
    the 310 corrections in 225 log lines), whether or not the text changed."""
    assert apply_vocab_corrections("Morta and COBie are fine.", OWNER) == (
        "Morta and COBie are fine.", [])


def test_changes_report_the_words_as_they_stood():
    assert apply_vocab_changes("Malek and malek", OWNER) == (
        "Malak and Malak", [("Malek", "Malak"), ("malek", "Malak")])


def test_a_replacement_is_not_corrected_again():
    """One pass: "Isabel" -> Isobel is not then fed to a later rule."""
    text, changes = apply_vocab_changes("Isabel and Isobel", ["Isobel", "Isobelle"])
    assert text == "Isobel and Isobel"
    assert changes == [("Isabel", "Isobel")]


# ── entries of several words (never matched before 3.15) ─────────────────────

@pytest.mark.parametrize("heard, expected", [
    ("Email james farrelly about it.", "Email James Farrelly about it."),
    ("Email James Farrely about it.", "Email James Farrelly about it."),
    ("Email James Farrelly about it.", "Email James Farrelly about it."),
])
def test_a_multi_word_entry_is_matched(heard, expected):
    assert apply_vocab_corrections(heard, OWNER)[0] == expected


def test_a_multi_word_entry_needs_one_word_spelt_exactly():
    assert fuzzy_match_word("Jams Farrely", ["James Farrelly"]) == []


def test_a_multi_word_entry_of_everyday_words_leaves_them_alone():
    text = "I left it in the office."
    assert apply_vocab_corrections(text, ["The Office"]) == (text, [])


def test_hyphen_and_apostrophe_entries():
    assert apply_vocab_corrections("ask jean luc", ["Jean-Luc"])[0] == "ask Jean-Luc"
    assert apply_vocab_corrections("ask o'brian", ["O'Brien"])[0] == "ask O'Brien"


def test_an_entry_with_digits_is_never_matched_by_its_letters():
    text = "the gpt model"
    assert apply_vocab_corrections(text, ["GPT-4"]) == (text, [])


# ── the list itself ──────────────────────────────────────────────────────────

def test_clean_vocab_trims_drops_and_dedupes():
    assert clean_vocab(["  Sinéad ", "", "sinéad", "James   Farrelly", 3, None, "COBie"]) == [
        "Sinéad", "James Farrelly", "COBie"]
    assert clean_vocab({"Sinéad": 1}) == []
    assert clean_vocab(None) == []


def test_load_vocab_survives_a_hand_edited_file(tmp_path, monkeypatch):
    f = tmp_path / "vocab.json"
    f.write_text(json.dumps(["Zoë", None, " Zoë ", "Rohan"]), encoding="utf-8")
    monkeypatch.setattr(tw, "VOCAB_FILE", f)
    assert tw.load_vocab() == ["Zoë", "Rohan"]
    f.write_text('{"not": "a list"}', encoding="utf-8")
    assert tw.load_vocab() == []


def test_a_long_list_stays_quick():
    import time
    vocab = [f"Name{chr(97 + i % 26)}{chr(97 + (i // 26) % 26)}xq" for i in range(tw.VOCAB_MAX_ENTRIES)]
    text = " ".join(["the quick brown fox jumps over a lazy dog and then"] * 30)
    start = time.perf_counter()
    apply_vocab_corrections(text, vocab)
    assert time.perf_counter() - start < 2.0
