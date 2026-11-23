"""Meetings: a transcript's lines get capitals and punctuation, and never a
changed word.

The speech model writes fast, running speech with no capitals and no full
stops ("this is yeah so sorry so going back to it then this is a wrap-up").
After a meeting is written down, and before its notes are written, whatever
writes the notes (a key, Waffler Plus, the model on this computer) is asked to
add the capitals and the punctuation: src/meetings/tidy_lines.py, called from
Service._enhance.

The rule that matters is "no made-up words, ever", so no reply is trusted.
Every returned line is compared with the line it was made from, letters and
digits only, case folded: identical, and it is stored; anything else, and the
line stays exactly as it was. A writer that fails, refuses or says nonsense
changes nothing, and the notes are still written.

Everything here runs against a stand-in writer: no network, no model.
"""
import random
import re
import sys
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from meetings import llm, tidy_lines, transcribe as mt  # noqa: E402
from meetings.service import Service  # noqa: E402
from meetings.store import Store, wait_until  # noqa: E402
from test_plus_meetings import _meeting as _plus_meeting, plus  # noqa: E402,F401  (the Plus stand-in)

RATE = 16000
KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NATTER_CUSTOM_API_KEY")

SPEC_SYSTEM = (
    "You add capital letters and punctuation to a transcript. You never add, remove, reorder, "
    "translate or respell a word, and you never change a number. Keep every line: the same lines, "
    "in the same order, with the same numbers. A sentence may run on from one line to the next; "
    "then the first line does not end with a full stop and the next does not start with a capital. "
    "Reply with the numbered lines and nothing else.")

A = "this is yeah so sorry so going back to it then this is a wrap-up"
B = "of my day wait wait wait okay i'm close all right so right now waffler if you're listening to this"
A_TIDY = "This is, yeah, so sorry. So going back to it then, this is a wrap-up"
B_TIDY = "of my day. Wait, wait, wait. Okay, I'm close. All right, so right now, Waffler, if you're listening to this."
C = "we said the cabinets arrive on the fourteenth and tom books the electrician"
C_TIDY = "We said the cabinets arrive on the fourteenth, and Tom books the electrician."


def letters_and_digits(text: str) -> str:
    """The spec's comparison, written out here on its own."""
    return "".join(ch for ch in text if ch.isalnum()).casefold()


def segs(*lines):
    """Transcript lines as the store returns them. A line is its text, or
    (source, text)."""
    out = []
    for i, line in enumerate(lines):
        source, text = line if isinstance(line, tuple) else ("mic", line)
        out.append({"id": f"seg{i:03d}", "session_n": 1, "source": source, "start_s": i * 3.0,
                    "end_s": i * 3.0 + 2.5, "text": text, "created_at": "2026-11-23T10:00:00.000+00:00"})
    return out


def numbered(user: str) -> dict[int, str]:
    """The numbered lines of a request, as the writer is sent them."""
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"(?m)^(\d+): (.*)$", user)}


def polish(text: str) -> str:
    """What a careful writer does to one line: a capital and a full stop."""
    return text[:1].upper() + text[1:] + ("" if text[-1:] in ".?!" else ".")


def careful(user: str) -> str:
    return "\n".join(f"{n}: {polish(t)}" for n, t in numbered(user).items())


class Run:
    """One pass over `lines` with `reply(user text, request number)` as the writer."""

    def __init__(self, lines, reply=lambda user, n: careful(user)):
        self.before = [dict(s) for s in lines]
        self.asked: list[tuple[str, str]] = []
        self.text = {s["id"]: s["text"] for s in lines}

        def write(system, user):
            self.asked.append((system, user))
            return reply(user, len(self.asked))

        def save(changes):
            for sid, old, new in changes:
                assert self.text[sid] == old
                self.text[sid] = new
        self.counts = tidy_lines.tidy(lines, write, save)
        self.after = [self.text[s["id"]] for s in lines]


@pytest.fixture(autouse=True)
def log_lines(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(tidy_lines, "_log", lines.append)
    return lines


# ── which lines need it ──────────────────────────────────────────────────────

def test_the_request_is_the_one_the_phone_sends_too():
    assert tidy_lines.SYSTEM == SPEC_SYSTEM
    run = Run(segs(A, B))
    assert run.asked == [(SPEC_SYSTEM, f"1: {A}\n2: {B}")]


@pytest.mark.parametrize("text, needs", [
    (A, True),                                                   # no punctuation, no capital
    ("So this one has no punctuation at all in it", True),       # a capital, but none of . , ? !
    ("so this one starts small, and has a comma.", True),        # punctuation, but starts lower case
    ("So this one is fine, it was written properly.", False),
    ("yeah okay", False),                                        # 25 letters or fewer: left alone
    ("abcdefghijklmnopqrstuvwxy", False),                        # exactly 25
    ("abcdefghijklmnopqrstuvwxyz", True),                        # 26
    ("12 34 56 78 90 12 34 56 78 90 12 34 56 78 90 ok", False),  # digits are not letters
])
def test_a_line_needs_it_when_it_is_long_and_unpunctuated_or_starts_small(text, needs):
    assert tidy_lines.needs_tidying(text) is needs


def test_nothing_needing_it_means_no_request():
    run = Run(segs("So this one is fine, it was written properly.", "yeah okay", "Tom books the electrician today."))
    assert run.asked == [] and run.after == [s["text"] for s in run.before]
    assert run.counts["requests"] == 0


def test_no_lines_at_all_means_no_request():
    assert Run([]).asked == []


def test_one_line_needing_it_sends_its_neighbours_too():
    fine = "So this one is fine, it was written properly."
    run = Run(segs(fine, A, "yeah okay"))
    assert len(run.asked) == 1 and numbered(run.asked[0][1]) == {1: fine, 2: A, 3: "yeah okay"}


# ── what is accepted ─────────────────────────────────────────────────────────

def test_capitals_and_punctuation_are_accepted():
    run = Run(segs(A, B), lambda user, n: f"1: {A_TIDY}\n2: {B_TIDY}")
    assert run.after == [A_TIDY, B_TIDY]
    assert run.counts["changed"] == 2 and run.counts["refused"] == 0


@pytest.mark.parametrize("after", [
    "This is yeah, so sorry — so going back to it; then this is a wrap up!",    # hyphen to space, a dash, a semicolon
    "\"This is yeah so sorry so going back to it then,\" this is a wrap-up…",
    "This is yeah so sorry so going back to it then this is a wrapup.",          # spacing only
    "THIS IS YEAH SO SORRY SO GOING BACK TO IT THEN THIS IS A WRAP-UP?",
])
def test_only_case_punctuation_hyphens_apostrophes_and_spacing_may_change(after):
    assert Run(segs(A), lambda user, n: f"1: {after}").after == [after]
    assert letters_and_digits(after) == letters_and_digits(A)


def test_an_apostrophe_may_be_added_or_curled():
    before = "so i dont think its going to work and im not sure whats next"
    after = "So I don’t think it's going to work, and I'm not sure what's next."
    assert Run(segs(before), lambda user, n: f"1: {after}").after == [after]


def test_a_reply_is_stored_as_one_line_with_single_spaces():
    run = Run(segs(A), lambda user, n: f"1:    {A_TIDY.replace(' so ', '   so ')}   ")
    assert run.after == [A_TIDY]


# ── what is refused, for that line only ──────────────────────────────────────

@pytest.mark.parametrize("why, bad", [
    ("a changed word", "This is, yeah, so sorry. So going back to it now, this is a wrap-up."),
    ("an added word", "This is, yeah, so sorry. So going back to it then, this is really a wrap-up."),
    ("a dropped word", "This is so sorry. So going back to it then, this is a wrap-up."),
    ("a filler dropped", "This is, yeah, sorry. So going back to it then, this is a wrap-up."),
    ("a respelled word", "This is, yeah, so sorry. So going back to it then, this is a rap-up."),
    ("two words swapped", "This is, yeah, so sorry. So back going to it then, this is a wrap-up."),
    ("translated", "Das ist, ja, tut mir leid. Also zurück dazu, das ist eine Zusammenfassung."),
    ("a summary", "A wrap-up of the day."),
    ("nothing", ""),
    ("the next line's words", B_TIDY),
])
def test_a_changed_word_is_refused_for_that_line_only(why, bad):
    run = Run(segs(A, C), lambda user, n: f"1: {bad}\n2: {C_TIDY}")
    assert run.after == [A, C_TIDY], why
    assert run.counts["changed"] == 1 and run.counts["refused"] == 1


@pytest.mark.parametrize("before, bad", [
    ("we agreed on 5 of them for the kitchen and the rest later", "We agreed on five of them for the kitchen, and the rest later."),
    ("we agreed on five of them for the kitchen and the rest later", "We agreed on 5 of them for the kitchen, and the rest later."),
    ("it came to 412000 pounds in the end which is more than we said", "It came to 421000 pounds in the end, which is more than we said."),
    ("it came to 15 percent in the end which is more than we said", "It came to 1.5 percent in the end, which is more than we said."),
    ("it came to 1.5 percent in the end which is more than we said", "It came to 15 percent in the end, which is more than we said."),
    ("it came to 1,500 pounds in the end which is more than we said", "It came to 1.500 pounds in the end, which is more than we said."),
    ("we meet at 5 30 on the fourteenth and then again the week after", "We meet at 5:30 on the fourteenth, and then again the week after."),
    ("there were 5 6 maybe 7 of them in the room when we started", "There were 56, maybe 7 of them in the room when we started."),
    ("it came to 15 in the end which is quite a lot more than we said", "It came to £15 in the end, which is quite a lot more than we said."),
    ("it came to 15 in the end which is quite a lot more than we said", "It came to 15% in the end, which is quite a lot more than we said."),
])
def test_a_number_is_never_changed(before, bad):
    assert Run(segs(before), lambda user, n: f"1: {bad}").after == [before]


def test_a_number_keeps_its_own_punctuation_and_gains_the_sentences():
    before = "it came to 1,500.50 at 5:30 on 14/01 and then 3 4 maybe 5 more"
    after = "It came to 1,500.50 at 5:30 on 14/01, and then 3, 4, maybe 5 more."
    assert Run(segs(before), lambda user, n: f"1: {after}").after == [after]


@pytest.mark.parametrize("before, bad", [
    ("we went to the cafe on the corner and had a long talk about it", "We went to the café on the corner and had a long talk about it."),
    ("we went to the café on the corner and had a long talk about it", "We went to the cafe on the corner and had a long talk about it."),
    # The accent written as a separate mark (e + U+0301) is still an accent.
    ("we went to the cafe on the corner and had a long talk about it",
     "We went to the café on the corner and had a long talk about it."),
    ("me gustaria saber que piensas tu de todo esto que hemos hablado", "Me gustaría saber qué piensas tú de todo esto que hemos hablado."),
])
def test_an_accent_is_a_change(before, bad):
    assert Run(segs(before), lambda user, n: f"1: {bad}").after == [before]


# ── replies that are not what was asked for ──────────────────────────────────

def test_fewer_lines_back_leaves_the_missing_ones_as_they_were():
    run = Run(segs(A, B, C), lambda user, n: f"1: {A_TIDY}\n3: {C_TIDY}")
    assert run.after == [A_TIDY, B, C_TIDY]


def test_lines_cut_off_at_the_end_leave_the_rest_as_they_were():
    run = Run(segs(A, B, C), lambda user, n: f"1: {A_TIDY}\n2: of my day. Wait, wait")
    assert run.after == [A_TIDY, B, C]


def test_more_lines_back_are_ignored():
    run = Run(segs(A, C), lambda user, n: f"1: {A_TIDY}\n2: {C_TIDY}\n3: And one more thing we never said.\n0: nor this")
    assert run.after == [A_TIDY, C_TIDY]


def test_two_lines_merged_into_one_are_both_left_as_they_were():
    run = Run(segs(A, B, C), lambda user, n: f"1: {A_TIDY} {B_TIDY}\n2: {C_TIDY}")
    assert run.after == [A, B, C]


def test_merged_lines_with_the_numbers_kept_lose_only_the_lines_affected():
    run = Run(segs(A, B, C), lambda user, n: f"1: {A_TIDY} {B_TIDY}\n3: {C_TIDY}")
    assert run.after == [A, B, C_TIDY]


def test_a_line_split_in_two_is_left_as_it_was():
    head, tail = A_TIDY[:24], A_TIDY[24:]
    # ... over two lines of the reply, and over two numbers.
    assert Run(segs(A, C), lambda user, n: f"1: {head}\n{tail}\n2: {C_TIDY}").after == [A, C_TIDY]
    assert Run(segs(A, C), lambda user, n: f"1: {head}\n2: {tail}\n3: {C_TIDY}").after == [A, C]


def test_a_number_given_twice_is_not_guessed_at():
    run = Run(segs(A, C), lambda user, n: f"1: {A_TIDY}\n1: {A}\n2: {C_TIDY}")
    assert run.after == [A, C_TIDY]


def test_chatter_round_the_numbered_lines_is_ignored():
    reply = ("Sure! Here are the lines with capitals and punctuation:\n\n```\n"
             f"1: {A_TIDY}\n\n  2: {C_TIDY}\n```\n\nI kept every word. Let me know if you need anything else.")
    assert Run(segs(A, C), lambda user, n: reply).after == [A_TIDY, C_TIDY]


@pytest.mark.parametrize("reply", [
    "", "I can't help with that.", "{\"lines\": [\"This is a wrap-up.\"]}", "1. This is. 2. Not numbered as asked.",
    None, 42, "\x00\x00", "1:\n2:\n", "9999999999999999999999: what",
])
def test_a_reply_that_cannot_be_read_changes_nothing(reply):
    run = Run(segs(A, C), lambda user, n: reply)
    assert run.after == [A, C] and run.counts["changed"] == 0


def test_a_line_that_looks_like_a_numbered_line_is_still_its_own_line():
    before = "10: 30 was the time we said and then 2: nobody turned up for it"
    after = "10: 30 was the time we said, and then 2: nobody turned up for it."
    run = Run(segs(before), lambda user, n: f"1: {after}")
    assert run.asked[0][1] == f"1: {before}" and run.after == [after]


# ── nothing here may fail a meeting ──────────────────────────────────────────

@pytest.mark.parametrize("error", [TimeoutError("timed out"), llm.LLMError("This month's Waffler Plus is used up.", "plus"),
                                   RuntimeError(f"refused: {A}"), KeyboardInterrupt()])
def test_a_writer_that_fails_leaves_every_line_as_it_was(error, log_lines):
    def reply(user, n):
        raise error
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):                    # the app being closed is not swallowed
            Run(segs(A, B), reply)
        return
    run = Run(segs(A, B), reply)
    assert run.after == [A, B] and run.counts["changed"] == 0 and run.counts["stopped"] == type(error).__name__
    assert len(log_lines) == 1 and type(error).__name__ in log_lines[0]
    assert "wrap-up" not in log_lines[0] and "refused:" not in log_lines[0], "what went wrong is named, never quoted"


def test_the_log_gets_one_line_of_counts_and_never_the_words(log_lines):
    run = Run(segs(A, B, C), lambda user, n: f"1: {A_TIDY}\n2: nothing like it\n3: {C_TIDY}")
    assert run.after == [A_TIDY, B, C_TIDY]
    assert len(log_lines) == 1
    line = log_lines[0]
    assert "3 lines" in line and "1 request" in line and "2 changed" in line and "1 refused" in line
    for said in (A, B, C, A_TIDY, C_TIDY, "nothing like it"):
        for word in said.split():
            if len(word) > 4 and word.lower() not in ("lines", "changed", "refused", "request"):
                assert word.lower() not in line.lower(), word


def test_nothing_asked_means_nothing_logged(log_lines):
    Run(segs("So this one is fine, it was written properly."))
    assert log_lines == []


def test_a_failure_part_way_keeps_what_was_done_and_asks_no_more():
    lines = segs(*[f"{A} number {i}" for i in range(100)])

    def reply(user, n):
        if n == 2:
            raise TimeoutError()
        return careful(user)
    run = Run(lines, reply)
    assert len(run.asked) == 2, "the third request is never made"
    assert run.after[:40] == [polish(s["text"]) for s in lines[:40]]
    assert run.after[40:] == [s["text"] for s in lines[40:]]
    assert run.counts["requests"] == 2 and run.counts["changed"] == 40 and run.counts["stopped"] == "TimeoutError"


def test_a_store_that_fails_is_not_the_meetings_problem():
    def save(changes):
        raise OSError("disk full")
    counts = tidy_lines.tidy(segs(A, B), lambda system, user: careful(user), save)
    assert counts["stopped"] == "OSError"


# ── batches ──────────────────────────────────────────────────────────────────

def test_forty_lines_at_most_go_in_one_request_and_the_next_carries_the_last_line():
    lines = segs(*[f"{A} number {i}" for i in range(85)])
    run = Run(lines)
    assert [len(numbered(user)) for _, user in run.asked] == [40, 40, 5]
    first, second, third = (user for _, user in run.asked)
    assert first.startswith("1: ") and "Earlier" not in first
    # The line before, as it was stored (tidied), for context; not numbered, not to be returned.
    assert second.split("\n")[0] == f"Earlier, for context only: {polish(lines[39]['text'])}"
    assert third.split("\n")[0] == f"Earlier, for context only: {polish(lines[79]['text'])}"
    assert second.split("\n")[1] == f"1: {lines[40]['text']}" and numbered(third)[5] == lines[84]["text"]
    assert run.after == [polish(s["text"]) for s in lines]


def test_the_context_line_is_the_original_when_its_tidying_was_refused():
    lines = segs(*[f"{A} number {i}" for i in range(41)])
    run = Run(lines, lambda user, n: "nothing useful" if n == 1 else careful(user))
    assert run.asked[1][1].split("\n")[0] == f"Earlier, for context only: {lines[39]['text']}"
    assert run.after == [s["text"] for s in lines[:40]] + [polish(lines[40]["text"])]


def test_a_context_line_sent_back_is_not_stored_anywhere():
    lines = segs(*[f"{A} number {i}" for i in range(41)])

    def reply(user, n):
        if n == 1:
            return careful(user)
        return f"Earlier, for context only: SOMETHING ELSE\n0: {polish(lines[39]['text'])}\n" + careful(user)
    run = Run(lines, reply)
    assert run.after == [polish(s["text"]) for s in lines]


def test_about_four_thousand_characters_at_most_go_in_one_request():
    long = " ".join(["so we kept talking about the plan for the kitchen and the hallway"] * 11)   # 725 characters: five fit, six do not
    lines = segs(*[long] * 13)
    run = Run(lines)
    sizes = [sum(len(t) for t in numbered(user).values()) for _, user in run.asked]
    assert sizes == [len(long) * 5, len(long) * 5, len(long) * 3] and all(size <= 4000 for size in sizes)
    assert run.after == [polish(long)] * 13


def test_one_enormous_line_goes_alone_and_is_never_cut():
    huge = " ".join(["so we kept talking about the plan for the kitchen and the hallway"] * 80)
    run = Run(segs(A, huge, C))
    assert [list(numbered(user).values()) for _, user in run.asked] == [[A], [huge], [C]]
    assert run.after == [polish(A), polish(huge), polish(C)]


# ── two sources ──────────────────────────────────────────────────────────────

def test_the_microphone_and_the_computers_sound_are_asked_about_apart():
    lines = segs(("mic", A), ("system", C), ("mic", B), ("system", "and then they said it would be ready by the end of the week"))
    run = Run(lines)
    assert [list(numbered(user).values()) for _, user in run.asked] == [
        [A, B], [C, "and then they said it would be ready by the end of the week"]]
    assert all("Earlier" not in user for _, user in run.asked), "one source is never context for the other"
    assert run.after == [polish(s["text"]) for s in lines]


def test_a_source_with_nothing_needing_it_is_still_sent_when_the_meeting_needs_it():
    lines = segs(("mic", A), ("system", "They said it would be ready by Friday."))
    run = Run(lines)
    assert [list(numbered(user).values()) for _, user in run.asked] == [[A], ["They said it would be ready by Friday."]]


def test_each_source_carries_its_own_context_line():
    lines = segs(*[(("mic", "system")[i % 2], f"{A} number {i}") for i in range(82)])
    run = Run(lines)
    mic = [s for s in lines if s["source"] == "mic"]
    system = [s for s in lines if s["source"] == "system"]
    users = [user for _, user in run.asked]
    assert [len(numbered(u)) for u in users] == [40, 1, 40, 1]
    assert users[1].split("\n")[0] == f"Earlier, for context only: {polish(mic[39]['text'])}"
    assert users[3].split("\n")[0] == f"Earlier, for context only: {polish(system[39]['text'])}"


# ── other languages ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("before, after", [
    # French: accents kept, capitals and punctuation added.
    ("alors on a parlé du café à côté de l'école et ça m'a plu énormément",
     "Alors, on a parlé du café à côté de l'école, et ça m'a plu énormément."),
    # Spanish: the opening marks are punctuation too.
    ("y entonces qué piensas tú de todo esto que hemos hablado hoy aquí",
     "Y entonces, ¿qué piensas tú de todo esto que hemos hablado hoy aquí?"),
    # German: capitals on nouns.
    ("dann haben wir über die küche und den flur gesprochen und das war gut",
     "Dann haben wir über die Küche und den Flur gesprochen, und das war gut."),
    # Russian: a script with its own capitals.
    ("привет это запись нашей встречи и мы говорили о кухне и о коридоре",
     "Привет, это запись нашей встречи, и мы говорили о кухне и о коридоре."),
    # Greek.
    ("καλημέρα σας αυτή είναι η σύσκεψη για την κουζίνα και τον διάδρομο",
     "Καλημέρα σας, αυτή είναι η σύσκεψη για την κουζίνα και τον διάδρομο."),
    # Japanese: no capitals, no spaces, its own punctuation.
    ("今日は台所と廊下の計画について話しましたそして来週また会うことにしました",
     "今日は台所と廊下の計画について話しました。そして、来週また会うことにしました。"),
    # Hindi: vowel signs are marks, not letters; they stay as they are.
    ("आज हमने रसोई और गलियारे की योजना के बारे में बात की और अगले हफ्ते फिर मिलेंगे",
     "आज हमने रसोई और गलियारे की योजना के बारे में बात की, और अगले हफ्ते फिर मिलेंगे।"),
    # Arabic.
    ("اليوم تحدثنا عن خطة المطبخ والممر وسوف نلتقي مرة أخرى في الأسبوع المقبل",
     "اليوم تحدثنا عن خطة المطبخ والممر، وسوف نلتقي مرة أخرى في الأسبوع المقبل."),
])
def test_other_languages_get_their_punctuation_and_keep_every_letter(before, after):
    assert tidy_lines.needs_tidying(before)
    assert Run(segs(before), lambda user, n: f"1: {after}").after == [after]


@pytest.mark.parametrize("before, bad", [
    ("привет это запись нашей встречи и мы говорили о кухне и о коридоре",
     "Привет, это запись нашей встречи, и мы говорили о кухне и о комнате."),          # a changed word
    ("привет это запись нашей встречи и мы говорили о кухне и о коридоре",
     "Hello, this is the recording of our meeting and we talked about the kitchen."),  # translated
    ("привет это запись нашей встречи и мы говорили о кухне и о коридоре",
     "Privet, eto zapis nashey vstrechi, i my govorili o kukhne i o koridore."),       # another alphabet
    ("今日は台所と廊下の計画について話しましたそして来週また会うことにしました",
     "今日は台所と廊下の計画について話しました。そして、来月また会うことにしました。"),                  # week to month
    ("आज हमने रसोई और गलियारे की योजना के बारे में बात की और अगले हफ्ते फिर मिलेंगे",
     "आज हमने रसोई और गलियारे कि योजना के बारे में बात की, और अगले हफ्ते फिर मिलेंगे।"),      # one vowel sign
])
def test_other_languages_refuse_a_changed_word_too(before, bad):
    assert Run(segs(before), lambda user, n: f"1: {bad}").after == [before]


# ── whatever the writer says, the words are the same ─────────────────────────

WORDS = ("so yeah okay right we they the a to of and then kitchen hallway cabinets electrician fourteenth "
         "wrap-up i'm don't it's waffler café naïve über straße привет встречи кухне 台所 計画 रसोई योजना "
         "5 14 412000 1,500 3.5 2026 twenty five").split()
EXTRA = ("really", "not", "Tom", "7", "ever", "да", "と")
MARKS = (".", ",", "?", "!", ";", ":", " —", "…", "\"", "'", ")", "。")


def _random_line(rng):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(8, 22)))


def _honest(rng, text):
    """Capitals and punctuation only."""
    out = []
    for word in text.split():
        if rng.random() < 0.3:
            word = word[:1].upper() + word[1:]
        if rng.random() < 0.3:
            word += rng.choice(MARKS)
        out.append(word)
    return " ".join(out)


def _dishonest(rng, text):
    words = text.split()
    i = rng.randrange(len(words))
    kind = rng.choice(("drop", "add", "swap", "respell", "digit", "double", "unaccent", "other", "empty", "upper"))
    if kind == "drop":
        del words[i]
    elif kind == "add":
        words.insert(i, rng.choice(EXTRA))
    elif kind == "swap":
        j = rng.randrange(len(words))
        words[i], words[j] = words[j], words[i]
    elif kind == "respell":
        words[i] = words[i][:-1] + rng.choice("xqz")
    elif kind == "digit":
        words[i] = str(rng.randint(0, 99))
    elif kind == "double":
        words.insert(i, words[i])
    elif kind == "unaccent":
        words = ["".join(c for c in unicodedata.normalize("NFD", w) if not unicodedata.combining(c)) for w in words]
    elif kind == "other":
        return _random_line(rng)
    elif kind == "empty":
        return ""
    else:
        return text.upper()            # honest, as it happens: case only
    return _honest(rng, " ".join(words))


def _random_reply(rng, user):
    lines = numbered(user)
    out = []
    if rng.random() < 0.2:
        out.append("Here you go:")
    for n, text in lines.items():
        roll = rng.random()
        if roll < 0.5:
            out.append(f"{n}: {_honest(rng, text)}")
        elif roll < 0.75:
            out.append(f"{n}: {_dishonest(rng, text)}")
        elif roll < 0.8:
            continue                                               # a line left out
        elif roll < 0.85:
            out.append(f"{n}: {_honest(rng, text)} {_honest(rng, lines.get(n + 1, 'and more'))}")   # merged
        elif roll < 0.9:
            cut = len(text) // 2
            out.append(f"{n}: {text[:cut]}\n{text[cut:]}")         # split
        elif roll < 0.95:
            out.append(f"{rng.randint(0, 60)}: {_honest(rng, text)}")                                # wrong number
        else:
            out.append(rng.choice(("```", "", "Note: I fixed a typo.", f"{n}. {text}", f"{n}:")))
    if rng.random() < 0.2:
        out.append(f"{len(lines) + 1}: {_random_line(rng)}")       # an extra line
    if rng.random() < 0.05:
        rng.shuffle(out)
    return "\n".join(out)


def test_whatever_the_writer_says_the_letters_and_digits_of_every_line_are_the_same():
    rng = random.Random(20261123)
    accepted = refused = 0
    for _ in range(300):
        lines = segs(*[(rng.choice(("mic", "mic", "system")), _random_line(rng)) for _ in range(rng.randint(1, 95))])
        run = Run(lines, lambda user, n: _random_reply(rng, user))
        for seg, after in zip(lines, run.after):
            assert letters_and_digits(after) == letters_and_digits(seg["text"]), (seg["text"], after)
            assert re.findall(r"\d+", after) == re.findall(r"\d+", seg["text"]), (seg["text"], after)
            assert after == " ".join(after.split()) and after
        assert lines == run.before, "nothing but the text is ever touched, and that only through the store"
        accepted += run.counts["changed"]
        refused += run.counts["refused"]
    assert accepted > 1000 and refused > 1000, "the test saw both honest and dishonest replies"


# ── the store ────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "meetings.db")


def test_the_store_changes_only_the_text_and_search_follows(store):
    note = store.create_note()
    store.add_segments(note["id"], [
        {"start_s": 0.0, "end_s": 2.5, "text": "so the cabinets arrive tuesday", "source": "mic", "session_n": 1},
        {"start_s": 3.0, "end_s": 5.5, "text": "and tom books the electrician", "source": "system", "session_n": 2}])
    before = store.segments(note["id"])
    assert store.retext_segments(note["id"], [(before[0]["id"], before[0]["text"], "So the cabinets arrive Tuesday.")]) == 1
    after = store.segments(note["id"])
    assert after[0] == {**before[0], "text": "So the cabinets arrive Tuesday."} and after[1] == before[1]
    hits = store.search_segments("cabinets")
    assert [h["text"] for h in hits] == ["So the cabinets arrive Tuesday."], "one entry in the index, the new one"
    assert [n["id"] for n in store.list_notes(query="tuesday")] == [note["id"]]
    assert store.db.execute("SELECT count(*) FROM segments_fts").fetchone()[0] == 2
    assert "So the cabinets arrive Tuesday." in store.transcript_text(note["id"])


def test_the_store_changes_a_line_only_if_it_still_says_what_was_checked(store):
    note = store.create_note()
    store.add_segments(note["id"], [{"start_s": 0.0, "end_s": 2.5, "text": "so the cabinets arrive tuesday"}])
    seg = store.segments(note["id"])[0]
    assert store.retext_segments(note["id"], [(seg["id"], "something else it said once", "Something else.")]) == 0
    assert store.retext_segments(note["id"], [("no-such-line", seg["text"], "So.")]) == 0
    assert store.retext_segments("another-note", [(seg["id"], seg["text"], "So.")]) == 0
    assert store.segments(note["id"]) == [seg]
    assert store.db.execute("SELECT count(*) FROM segments_fts").fetchone()[0] == 1


# ── a whole meeting ──────────────────────────────────────────────────────────

def _speech(seconds=12.0):
    """Bursts of tone with gaps: enough voice for the engine's voice check."""
    t = np.arange(int(seconds * RATE)) / RATE
    tone = 0.2 * np.sin(2 * np.pi * 220 * t)
    gate = (np.sin(2 * np.pi * 0.8 * t) > -0.3).astype(np.float32)
    return (tone * gate).astype(np.float32)


def _heard(audio, provider, model, language):
    return [{"start": 0.3, "end": 5.5, "text": A}, {"start": 6.0, "end": 11.5, "text": B}]


class Writer:
    """The notes' writer, standing in for a key's model: tidies when asked to
    tidy, writes notes when asked for notes."""

    def __init__(self, tidy=lambda user: f"1: {A_TIDY}\n2: {B_TIDY}"):
        self.tidy, self.tidied, self.notes_from = tidy, [], []

    def __call__(self, system, messages):
        if system == tidy_lines.SYSTEM:
            self.tidied.append(messages[-1]["content"])
            return self.tidy(messages[-1]["content"])
        self.notes_from.append(messages[-1]["content"])
        return "TITLE: The day's wrap-up\n\n### Today\n- A wrap-up of the day"


@pytest.fixture
def no_keys(monkeypatch, _isolated_data_dir):
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("WAFFLER_MEETINGS_HOME", raising=False)
    monkeypatch.delenv("NATTER_HOME", raising=False)
    monkeypatch.setattr(mt.keys, "_memory", {})
    return _isolated_data_dir


def _record(service):
    note = service.store.create_note(template_id="general")
    service.ingest_audio(note["id"], _speech())
    assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=20)
    return service.store.get_note(note["id"], with_segments=True)


def test_a_meetings_lines_are_tidied_and_its_notes_written_from_the_tidied_lines(no_keys):
    writer = Writer()
    service = Service(store=Store(), transcribe_backend=_heard, llm_backend=writer)
    note = _record(service)
    assert [s["text"] for s in note["segments"]] == [A_TIDY, B_TIDY]
    assert [(s["start_s"], s["end_s"], s["source"], s["session_n"]) for s in note["segments"]] == [
        (0.3, 5.5, "mic", 1), (6.0, 11.5, "mic", 1)], "times and sources are untouched"
    assert writer.tidied == [f"1: {A}\n2: {B}"]
    assert len(writer.notes_from) == 1 and f"Me: {A_TIDY} {B_TIDY}" in writer.notes_from[0]
    assert A not in writer.notes_from[0]
    assert note["title"] == "The day's wrap-up" and "A wrap-up of the day" in note["summary"] and note["error"] == ""
    assert [h["note_id"] for h in service.store.search_segments("Waffler listening")] == [note["id"]]


def test_a_writer_that_changes_words_gets_none_of_them_into_the_meeting(no_keys):
    writer = Writer(lambda user: "1: This is a wrap-up of my day.\n2: I am close, Waffler, if you are listening.")
    service = Service(store=Store(), transcribe_backend=_heard, llm_backend=writer)
    note = _record(service)
    assert [s["text"] for s in note["segments"]] == [A, B]
    assert f"Me: {A} {B}" in writer.notes_from[0] and note["error"] == "" and note["summary"]


def test_a_writer_that_fails_at_the_lines_still_writes_the_notes(no_keys, log_lines):
    def broken(user):
        raise TimeoutError("the writer took too long")
    writer = Writer(broken)
    service = Service(store=Store(), transcribe_backend=_heard, llm_backend=writer)
    note = _record(service)
    assert [s["text"] for s in note["segments"]] == [A, B]
    assert note["status"] == "ready" and note["error"] == "" and "A wrap-up of the day" in note["summary"]
    assert len(log_lines) == 1 and "TimeoutError" in log_lines[0] and "wrap-up" not in log_lines[0]


def test_with_no_writer_nobody_is_asked_and_the_transcript_is_left_as_it_is(no_keys, monkeypatch):
    def never(*a, **k):
        raise AssertionError("with no key, no Plus and no model here, nobody is asked")
    monkeypatch.setattr(llm, "complete", never)
    service = Service(store=Store(), transcribe_backend=_heard)
    note = _record(service)
    assert [s["text"] for s in note["segments"]] == [A, B]
    assert note["error"] == llm.NOTES_NEED_KEY and note["summary"] == ""
    service.enhance(note["id"], wait=True)                         # "Write notes", still with no writer
    assert [s["text"] for s in service.store.segments(note["id"])] == [A, B]


def test_a_meeting_written_down_before_there_was_a_writer_is_tidied_with_its_first_notes(no_keys):
    service = Service(store=Store(), transcribe_backend=_heard)
    note = _record(service)
    assert note["error"] == llm.NOTES_NEED_KEY
    service.llm_backend = writer = Writer()                        # a key is added later
    service.enhance(note["id"], wait=True)
    assert [s["text"] for s in service.store.segments(note["id"])] == [A_TIDY, B_TIDY]
    assert f"Me: {A_TIDY} {B_TIDY}" in writer.notes_from[0]


def test_writing_the_notes_again_does_not_ask_about_the_lines_again(no_keys):
    # A line that runs on still starts small after tidying: it must not be
    # sent again each time the notes are rewritten.
    writer = Writer()
    service = Service(store=Store(), transcribe_backend=_heard, llm_backend=writer)
    note = _record(service)
    service.enhance(note["id"], template_id="one-to-one", wait=True)
    assert len(writer.tidied) == 1 and len(writer.notes_from) == 2
    assert [s["text"] for s in service.store.segments(note["id"])] == [A_TIDY, B_TIDY]


def test_a_meeting_written_down_again_is_tidied_again(no_keys):
    writer = Writer()
    service = Service(store=Store(), transcribe_backend=_heard, llm_backend=writer)
    note = _record(service)
    service.retranscribe(note["id"])
    assert wait_until(lambda: len(writer.notes_from) == 2, timeout=20)
    assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=20)
    assert len(writer.tidied) == 2 and writer.tidied[1] == f"1: {A}\n2: {B}"
    assert [s["text"] for s in service.store.segments(note["id"])] == [A_TIDY, B_TIDY]


def test_with_notes_switched_off_nothing_is_asked(no_keys):
    writer = Writer()
    service = Service(store=Store(), transcribe_backend=_heard, llm_backend=writer)
    service.store.set_settings({"auto_enhance": False})
    note = _record(service)
    assert writer.tidied == [] and writer.notes_from == [] and [s["text"] for s in note["segments"]] == [A, B]
    service.enhance(note["id"], wait=True)                         # asked for by hand: lines first, then notes
    assert [s["text"] for s in service.store.segments(note["id"])] == [A_TIDY, B_TIDY]
    assert f"Me: {A_TIDY} {B_TIDY}" in writer.notes_from[0]


# ── the writers ──────────────────────────────────────────────────────────────

def test_with_waffler_plus_the_lines_go_the_way_the_notes_go(plus):
    plus.hear = lambda seconds, n: [{"start_s": 0.5, "end_s": 2.6, "text": A}, {"start_s": 3.0, "end_s": 5.5, "text": B}]
    notes = plus.write_reply
    plus.write_reply = lambda body: f"1: {A_TIDY}\n2: {B_TIDY}" if body["system"] == tidy_lines.SYSTEM else notes(body)
    note = _plus_meeting(Service(store=Store()))
    assert [s["text"] for s in note["segments"]] == [A_TIDY, B_TIDY]
    assert [w["system"] for w in plus.writes] == [tidy_lines.SYSTEM, llm.NOTES_SYSTEM], \
        "POST /api/plus/write, counted like any other writing"
    asked = plus.writes[0]
    assert asked["messages"] == [{"role": "user", "content": f"1: {A}\n2: {B}"}]
    assert set(asked) <= {"system", "messages", "max_tokens", "effort"}, "nothing the service does not know"
    assert f"Me: {A_TIDY} {B_TIDY}" in plus.writes[1]["messages"][0]["content"]


def test_with_the_plus_month_used_up_the_lines_stay_and_the_meeting_is_not_failed_by_it(plus, log_lines):
    plus.hear = lambda seconds, n: [{"start_s": 0.5, "end_s": 2.6, "text": A}, {"start_s": 3.0, "end_s": 5.5, "text": B}]
    plus.write_refusals = [(402, "This month's Waffler Plus is used up.", "month_used")]
    note = _plus_meeting(Service(store=Store()))
    assert [s["text"] for s in note["segments"]] == [A, B]
    assert note["status"] == "ready" and "Kitchen plans" == note["title"], "the notes were still asked for, and written"
    assert len(log_lines) == 1 and "LLMError" in log_lines[0]


class _Chat:
    """An OpenAI-style client that records what it is asked."""

    def __init__(self, refuses=()):
        self.asked, self.refuses = [], refuses
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.asked.append(kwargs)
        if any(name in kwargs for name in self.refuses):
            error = RuntimeError("Unsupported value")
            error.status_code = 400
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=f"1: {A_TIDY}"))])


def _through(monkeypatch, provider, client):
    monkeypatch.setattr(llm, "resolve_provider", lambda settings: provider)
    monkeypatch.setattr(llm, "resolve_model", lambda provider, settings: "a-model")
    monkeypatch.setattr(llm, "_openai_client", lambda provider, settings: client)
    monkeypatch.setattr(llm, "ollama_root", lambda url: None)
    service = SimpleNamespace(store=SimpleNamespace(settings=lambda: {}), llm_backend=None)
    return Run(segs(A), lambda user, n: Service._ask_writer(service, {}, tidy_lines.SYSTEM, user))


@pytest.mark.parametrize("provider", ["groq", "openai", "custom"])
def test_a_keys_model_is_asked_with_no_chance_in_its_choice_of_words(monkeypatch, provider):
    client = _Chat()
    run = _through(monkeypatch, provider, client)
    assert run.after == [A_TIDY]
    assert client.asked[0]["temperature"] == 0 and client.asked[0]["messages"][0] == {
        "role": "system", "content": SPEC_SYSTEM}


def test_a_model_that_takes_no_temperature_is_asked_again_without_one(monkeypatch):
    client = _Chat(refuses=("temperature",))
    run = _through(monkeypatch, "openai", client)
    assert run.after == [A_TIDY]
    assert "temperature" in client.asked[0] and "temperature" not in client.asked[-1]
    assert all(a["messages"][-1] == {"role": "user", "content": f"1: {A}"} for a in client.asked)


def test_the_model_on_this_computer_is_asked_with_no_chance_and_in_the_background(monkeypatch):
    asked = {}

    def chat(system, messages, max_tokens=0, temperature=None, background=False, on_text=None):
        asked.update(system=system, temperature=temperature, background=background)
        return {"text": f"<think>capitals</think>1: {A_TIDY}"}
    monkeypatch.setattr(llm.local_llm, "chat", chat)
    monkeypatch.setattr(llm, "resolve_provider", lambda settings: "local")
    service = SimpleNamespace(llm_backend=None)
    run = Run(segs(A), lambda user, n: Service._ask_writer(service, {}, tidy_lines.SYSTEM, user))
    assert run.after == [A_TIDY]
    assert asked == {"system": SPEC_SYSTEM, "temperature": 0.0, "background": True}, \
        "a dictation goes first, as for notes"


def test_claude_is_asked_as_it_is_for_notes(monkeypatch):
    # Anthropic's current models refuse a temperature: none is sent.
    asked = []

    def claude(system, messages, model, max_tokens):
        asked.append((system, messages, model))
        return f"1: {A_TIDY}"
    monkeypatch.setattr(llm, "resolve_provider", lambda settings: "anthropic")
    monkeypatch.setattr(llm, "_anthropic_complete", claude)
    service = SimpleNamespace(llm_backend=None)
    run = Run(segs(A), lambda user, n: Service._ask_writer(service, {}, tidy_lines.SYSTEM, user))
    assert run.after == [A_TIDY]
    assert asked == [(SPEC_SYSTEM, [{"role": "user", "content": f"1: {A}"}], llm.ANTHROPIC_DEFAULT_MODEL)]
