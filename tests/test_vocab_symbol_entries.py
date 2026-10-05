"""Vocabulary entries with digits or symbols are matched whole.

The corrector only knew words of letters, so "GPT-4o", "M365", "C++" or
"COVID-19" in the list did nothing after speech to text: Groq gets no
vocabulary prompt, so on the main provider "gpt-4o" stayed lower case. They
are now matched as whole phrases in any capitals, with a space, hyphen or
nothing where the entry has a separator, and their letters alone are still
never matched ("the gpt model" stays as said).

Also pinned here, from the same audit: names that are everyday words do not
capitalise every use, and an entry of two words is applied.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from transcribe_whisper import apply_vocab_changes  # noqa: E402


@pytest.mark.parametrize("heard, vocab, want", [
    ("we use gpt-4o for that", ["GPT-4o"], "we use GPT-4o for that"),
    ("the gpt 4o model", ["GPT-4o"], "the GPT-4o model"),
    ("use gpt4o", ["GPT-4o"], "use GPT-4o"),
    ("try m365 today", ["M365"], "try M365 today"),
    ("use c++ here", ["C++"], "use C++ here"),
    ("see covid 19 data", ["COVID-19"], "see COVID-19 data"),
    ("the w3c spec", ["W3C"], "the W3C spec"),
    ("node js app", ["Node.js"], "Node.js app"),
    ("the .net app", [".NET"], "the .NET app"),
    ("at&t bill", ["AT&T"], "AT&T bill"),
    ("use gpt-4o-mini", ["GPT-4o", "GPT-4o-mini"], "use GPT-4o-mini"),   # the longer entry wins
    ("ask cobie about gpt-4o", ["COBie", "GPT-4o"], "ask COBie about GPT-4o"),
])
def test_entries_with_digits_or_symbols_are_corrected(heard, vocab, want):
    text, changes = apply_vocab_changes(heard, vocab)
    assert text == want
    assert changes, "the change is reported for the Journal"


@pytest.mark.parametrize("heard, vocab", [
    ("the gpt model", ["GPT-4o"]),          # its letters alone are not the entry
    ("a 1 hour wait", ["A1"]),              # touching in the entry: no space allowed
    ("it costs 365 a year", ["M365"]),
    ("covid 19th wave", ["COVID-19"]),
    ("my profile.net page", [".NET"]),
    ("GPT-4o already", ["GPT-4o"]),         # already right: no change reported
    ("the path to abc", ["Pat\\h", 5]),     # odd entries do not raise
])
def test_near_misses_and_odd_entries_are_left_alone(heard, vocab):
    assert apply_vocab_changes(heard, vocab) == (heard, [])


def test_names_that_are_everyday_words_do_not_capitalise_every_use():
    text = "I will mark it and bill them in may"
    assert apply_vocab_changes(text, ["Will", "Mark", "Bill", "May"]) == (text, [])


def test_an_entry_of_two_words_is_applied():
    assert apply_vocab_changes("open claude code now", ["Claude Code"]) == (
        "open Claude Code now", [("claude code", "Claude Code")])
