"""Custom vocabulary must never overwrite ordinary English words.

Live failure (2026-09-11, Mac, v3.14.97): the user has "Nour" in vocab.json,
dictated "really appreciate your time today", and the pasted text read
"really appreciate Nour time today".

It was NOT the Whisper prompt — v3.14.97 stopped sending vocab as a decoder
prompt. It was the post-hoc fuzzy corrector: at threshold 0.75 a four-letter
vocab entry matches ANY four-letter word one edit away
(1 - 1/4 == 0.75, exactly at the bar), so "your" / "our" / "hour" / "tour" /
"pour" all collapsed to "Nour". A name that short carries too little signal
to correct on, and the most common words in English are the ones it hits.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcribe_whisper import (  # noqa: E402
    apply_vocab_corrections,
    fuzzy_match_word,
)


# ── the reported regression ──────────────────────────────────────────────────

def test_your_is_never_rewritten_to_a_short_vocab_name():
    """The exact live failure."""
    text = "Thanks, really appreciate your time today."
    corrected, applied = apply_vocab_corrections(text, ["Nour"])
    assert corrected == text, corrected
    assert applied == []


@pytest.mark.parametrize("word", ["your", "our", "hour", "tour", "pour", "four"])
def test_short_common_words_never_match_a_short_vocab_entry(word):
    assert fuzzy_match_word(word, ["Nour"]) == []


@pytest.mark.parametrize("common,vocab", [
    ("times", "Timms"),
    ("right", "Wright"),
    ("years", "Yeats"),
    ("today", "Toddy"),
    ("people", "Pepple"),
])
def test_common_english_words_are_never_fuzzy_corrected(common, vocab):
    """A vocab entry may not overwrite an ordinary word, however close.

    Every pair here DOES clear the 0.75 similarity bar — verified by
    disabling the guard — so each case fails without it.

    Deliberately excluded: "roman" vs vocab "Rohan" also clears the bar, but
    there the correction is the feature working. A surname misheard as a
    capitalised proper noun is the case custom vocabulary exists for; only
    everyday words are protected.
    """
    assert fuzzy_match_word(common, [vocab]) == []


# ── what must keep working ───────────────────────────────────────────────────

def test_exact_match_still_canonicalises_case():
    """Short entries still correct on an EXACT match — only fuzzy is refused."""
    corrected, applied = apply_vocab_corrections("spoke to nour today", ["Nour"])
    assert corrected == "spoke to Nour today"
    assert applied == ["'nour' → 'Nour'"]


def test_genuine_mishearing_of_a_long_name_is_still_corrected():
    assert fuzzy_match_word("cobia", ["COBie"]) == [("cobia", "COBie")]


def test_bigram_collapse_still_recovers_a_split_compound():
    """The "Nash can" -> "Ashkan" case that pass 2 exists for."""
    assert fuzzy_match_word("spoke to nash can today", ["Ashkan"]) == [
        ("nash can", "Ashkan")
    ]


def test_real_vocab_list_leaves_an_ordinary_sentence_untouched():
    vocab = ["Ashkan", "COBieQC", "COBie", "Morta", "Waffler", "craic",
             "Rohan", "Kandola", "James Farrelly", "Malak", "XBim", "Nour"]
    text = ("Just wanted to check which version of the toolkit you are on, "
            "and really appreciate your time today. I will send over the "
            "four files within the hour.")
    corrected, applied = apply_vocab_corrections(text, vocab)
    assert corrected == text, applied
    assert applied == []
