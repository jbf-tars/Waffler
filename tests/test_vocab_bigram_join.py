"""The two-word pass of vocabulary correction must not turn ordinary speech
into names.

Found on real speech (2026-09-25). Pass 2 of fuzzy_match_word joins each pair
of adjacent words and compares the join with each vocabulary entry, so that a
name Whisper split in two ("Ashkan" heard as "Nash can") can be put back. It
used a looser bar than single words (0.70) and no common-word guard, so with
"Aidan" in the vocabulary, "add an", "and an" and "said and" became Aidan (17
times in one user's real history). "Isobel" turned "is hotel" and "is model"
into Isobel, "Clubcard" turned "colour card" and "blue card" into Clubcard,
and "Sinéad" turned "the sign had" into "the Sinéad".

Every phrase is checked both ways: the ordinary phrase is left alone, and the
genuine split names ("Nash can", "club card", "post grass", "post-grass") are
still joined. The ordinary sentences are the ones the audit wrote to probe
these entries, not anyone's dictation history.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcribe_whisper import (  # noqa: E402
    _consonant_skeleton,
    apply_vocab_corrections,
    fuzzy_match_word,
)
from common_words import COMMON_WORDS  # noqa: E402

# The 15 entries the audit tested together.
MIXED = ["Isobel", "Rachael", "Jonathon", "Phillip", "Mathew", "Caitlyn", "Meghan",
         "Sinéad", "Muhammad", "Aidan", "ChatGPT", "Clubcard", "Chloë", "Hailey", "Lianne"]


# ── ordinary phrases that were rewritten ─────────────────────────────────────

ORDINARY = [
    # (sentence, the entry that used to claim part of it)
    ("Can you add an item to the shopping list?", "Aidan"),
    ("There were cats and an owl in the barn.", "Aidan"),
    ("He said and I quote, that the car can wait.", "Aidan"),
    ("I paid and left before the rush.", "Aidan"),
    ("We need first aid as well.", "Aidan"),
    ("He did an audit last week.", "Aidan"),
    ("The AI can draft it for you.", "Aidan"),
    ("The next step is model training on the new data.", "Isobel"),
    ("The biggest cost is hotel rooms in August.", "Isobel"),
    ("Grab a colour card from the paint aisle.", "Clubcard"),
    ("He got a blue card for his work visa.", "Clubcard"),
    ("The sign had blown over in the wind.", "Sinéad"),
    ("We need to reach all the parents before the end of term.", "Rachael"),
    ("Sales are up month on month.", "Jonathon"),
    ("Let me thank everyone who came along.", "Meghan"),
    ("I asked what got delayed.", "ChatGPT"),
    ("I think that got fixed yesterday.", "ChatGPT"),
    ("Did that get sorted?", "ChatGPT"),
    ("Is the chat bot working again?", "ChatGPT"),
    ("We had a quick chat but nothing came of it.", "ChatGPT"),
    ("The noise next door drives my mum mad.", "Muhammad"),
    ("We'll go via Nine Elms on the way home.", "Lianne"),
]


@pytest.mark.parametrize("sentence, entry", ORDINARY)
def test_ordinary_phrase_is_left_alone(sentence, entry):
    corrected, applied = apply_vocab_corrections(sentence, [entry])
    assert corrected == sentence, applied
    assert applied == []


@pytest.mark.parametrize("sentence, entry", ORDINARY)
def test_ordinary_phrase_is_left_alone_with_the_whole_list(sentence, entry):
    corrected, applied = apply_vocab_corrections(sentence, MIXED + ["Ashkan", "Postgres"])
    assert corrected == sentence, applied


# ── genuine splits that must still be joined ─────────────────────────────────

SPLIT_NAMES = [
    ("I spoke to Nash can today.", ["Ashkan"], "I spoke to Ashkan today."),
    ("I spoke to Nash-can today.", ["Ashkan"], "I spoke to Ashkan today."),
    ("Don't forget to scan your club card at the checkout.", ["Clubcard"],
     "Don't forget to scan your Clubcard at the checkout."),
    ("Check the post grass migration.", ["Postgres"], "Check the Postgres migration."),
    ("Check the post-grass migration.", ["Postgres"], "Check the Postgres migration."),
    ("Ash can said the same thing.", ["Ashkan"], "Ashkan said the same thing."),
]


@pytest.mark.parametrize("sentence, vocab, expected", SPLIT_NAMES)
def test_a_split_name_is_still_joined(sentence, vocab, expected):
    corrected, applied = apply_vocab_corrections(sentence, vocab)
    assert corrected == expected, applied


@pytest.mark.parametrize("sentence, vocab, expected", SPLIT_NAMES)
def test_a_split_name_is_still_joined_with_the_whole_list(sentence, vocab, expected):
    corrected, _ = apply_vocab_corrections(sentence, MIXED + ["Ashkan", "Postgres"])
    assert corrected == expected


def test_nash_can_is_found_by_the_join_pass():
    assert fuzzy_match_word("spoke to nash can today", ["Ashkan"]) == [("nash can", "Ashkan")]


# ── the single-word corrections for these names are untouched ────────────────

@pytest.mark.parametrize("heard, entry", [
    ("Aiden scored the winning goal.", "Aidan"),
    ("Send the slides to Isabel.", "Isobel"),
    ("Sinead is going to ring you.", "Sinéad"),
    ("Rachel said she'll pick them up.", "Rachael"),
    ("I'm meeting Jonathan for a coffee.", "Jonathon"),
    ("Megan's flight lands at ten.", "Meghan"),
    ("Leanne is doing the school run.", "Lianne"),
])
def test_a_misspelt_name_is_still_corrected(heard, entry):
    corrected, applied = apply_vocab_corrections(heard, MIXED)
    assert entry in corrected, applied


@pytest.mark.parametrize("entry", MIXED)
def test_a_correctly_spelt_name_is_left_alone(entry):
    sentence = f"I told {entry} about it on Friday."
    assert apply_vocab_corrections(sentence, MIXED)[0] == sentence


# ── the rule's building blocks ───────────────────────────────────────────────

def test_the_audit_sentences_get_no_two_word_rewrites():
    """Two of these still change through the single-word pass ("Phillips",
    "Matthews"); that pass is separate and unchanged here."""
    sentences = [
        "Thanks for the update, I'll take care of it first thing tomorrow.",
        "Could you book a table for four at the pub and send me the menu?",
        "My mum is making a roast on Sunday, so we'll eat around two.",
        "The catering for the gathering is sorted and the delivery comes at noon.",
        "The blood donor session is at the community centre on Thursday.",
        "I left my card at the club, can you check the lost property?",
        "She began the meeting at nine and met an old friend at lunch.",
        "Signed, sealed and delivered, the contract is back with the solicitor.",
        "Pass me the Phillips screwdriver from the drawer, please.",
        "Mrs Matthews said the maths homework is due on Monday.",
        "Is it a bell or a buzzer on the front door?",
        "I've got a golf club, a car and a bike in the garage.",
        "Is he coming to the Christmas do or not?",
    ]
    for s in sentences:
        joins = [c for c in fuzzy_match_word(s, MIXED) if " " in c[0]]
        assert joins == [], (s, joins)


@pytest.mark.parametrize("a, b", [
    ("postgrass", "Postgres"), ("clubcard", "Clubcard"), ("nashcan", "nshkn"),
])
def test_skeleton_examples(a, b):
    if b.islower():
        assert _consonant_skeleton(a) == b
    else:
        assert _consonant_skeleton(a) == _consonant_skeleton(b)


def test_skeleton_tells_the_audit_pairs_apart():
    assert _consonant_skeleton("bluecard") != _consonant_skeleton("Clubcard")
    assert _consonant_skeleton("colourcard") != _consonant_skeleton("Clubcard")
    assert _consonant_skeleton("chatbot") != _consonant_skeleton("ChatGPT")
    assert _consonant_skeleton("Sinéad") == "snd"


def test_the_common_word_list_holds_the_words_behind_the_bug_and_no_names():
    for w in ("add", "an", "and", "said", "is", "hotel", "model", "colour", "blue",
              "card", "sign", "had", "reach", "all", "month", "on", "me", "thank",
              "what", "got", "chat", "bot", "mum", "mad", "via", "nine", "ai", "post",
              "grass", "can", "club"):
        assert w in COMMON_WORDS, w
    for name in ("aidan", "isobel", "ashkan", "nash", "ash", "rachael", "sinead",
                 "postgres", "clubcard", "chatgpt"):
        assert name not in COMMON_WORDS, name
    assert all(w.isalpha() and w.islower() for w in COMMON_WORDS)
