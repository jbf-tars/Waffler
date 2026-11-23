"""Meetings: a transcript's lines get capitals and punctuation, and never a
changed word.

The speech model writes fast, running speech with no capitals and no full
stops ("this is yeah so sorry so going back to it then this is a wrap-up").
After a meeting is written down, and before its notes are written, whatever
writes the notes (a key, Waffler Plus, the model on this computer) is asked to
add the capitals and the punctuation: src/meetings/tidy_lines.py, called from
Service._enhance.

A stretch of lines is sent as one running text and the reply is cut back into
the lines it came from. (Sent as numbered lines, a real model put a full stop
and a capital on every line, or did very little; as running text it wrote
sentences.)

The rule that matters is "no made-up words, ever", so no reply is trusted. The
whole reply must be the words that were sent, and then every line cut from it
is compared with the line it replaces, letters and digits only, case folded:
identical, and it is stored; anything else, and the line stays exactly as it
was. A writer that fails, refuses or says nonsense changes nothing, and the
notes are still written.

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
    "You punctuate a transcript of someone speaking, as a careful editor would: full stops, commas, "
    "question marks and capital letters, so that it reads as proper sentences. You never add, remove, "
    "reorder, translate or respell a word, and you never change a number: every word stays, fillers "
    "and repeats included. Reply with the punctuated text and nothing else.")

A = "this is yeah so sorry so going back to it then this is a wrap-up"
B = "of my day wait wait wait okay i'm close all right so right now waffler if you're listening to this"
A_TIDY = "This is, yeah, so sorry. So going back to it then, this is a wrap-up"
B_TIDY = "of my day. Wait, wait, wait. Okay, I'm close. All right, so right now, Waffler, if you're listening to this."
C = "we said the cabinets arrive on the fourteenth and tom books the electrician"
C_TIDY = "We said the cabinets arrive on the fourteenth, and Tom books the electrician."
# From a trial on a real model: what went in, and what came back.
TRIAL = ["which is good",
         "they also sorted out the agent so it's like just works a bit more",
         "efficiently Tested it a lot more put the connections in and it all came up fine"]
TRIAL_REPLY = ("which is good. They also sorted out the agent, so it's like just works a bit more "
               "efficiently. Tested it a lot more, put the connections in, and it all came up fine.")
TRIAL_TIDY = ["Which is good.",
              "They also sorted out the agent, so it's like just works a bit more",
              "efficiently. Tested it a lot more, put the connections in, and it all came up fine."]


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


def commas(text: str) -> str:
    """What an honest writer might do to any stretch of these lines: a comma
    before each "so". No capital and no full stop, so every line of a stretch
    changes and none of them leans on the first-letter rule."""
    return text.replace(" so ", ", so ")


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def numbered_lines(n: int) -> list[str]:
    return [f"{A} number {i}" for i in range(n)]


class Run:
    """One pass over `lines` with `reply(user text, request number)` as the writer."""

    def __init__(self, lines, reply=lambda user, n: commas(user)):
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
        self.sent = [user for _, user in self.asked]


@pytest.fixture(autouse=True)
def log_lines(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(tidy_lines, "_log", lines.append)
    return lines


# ── the request ──────────────────────────────────────────────────────────────

def test_the_request_is_the_one_the_phone_sends_too():
    assert tidy_lines.SYSTEM == SPEC_SYSTEM
    run = Run(segs(A, B))
    assert run.asked == [(SPEC_SYSTEM, f"{A} {B}")], "the lines as one running text: no numbers, nothing else"


def test_the_lines_go_with_single_spaces_whatever_they_had():
    run = Run(segs(f"  {A}\n", B.replace(" wait ", "\t wait  ")), lambda user, n: f"{A_TIDY} {B_TIDY}")
    assert run.sent == [f"{A} {B}"] and run.after == [A_TIDY, B_TIDY]


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
    assert run.sent == [f"{fine} {A} yeah okay"]


def test_a_line_with_no_letter_or_digit_is_not_sent_and_stays():
    run = Run(segs(A, "...", "♪ ♪", B), lambda user, n: f"{A_TIDY} {B_TIDY}")
    assert run.sent == [f"{A} {B}"] and run.after == [A_TIDY, "...", "♪ ♪", B_TIDY]


# ── what is accepted ─────────────────────────────────────────────────────────

def test_running_speech_comes_back_as_sentences_on_the_lines_it_was_said_on():
    run = Run(segs(*TRIAL), lambda user, n: TRIAL_REPLY)
    assert run.sent == [" ".join(TRIAL)]
    assert run.after == TRIAL_TIDY, "a sentence runs on from one line to the next, as it was said"
    assert run.counts["changed"] == 3 and run.counts["refused"] == 0


def test_capitals_and_punctuation_are_accepted():
    run = Run(segs(A, B), lambda user, n: f"{A_TIDY} {B_TIDY}")
    assert run.after == [A_TIDY, B_TIDY]
    assert run.counts["changed"] == 2 and run.counts["refused"] == 0


@pytest.mark.parametrize("after", [
    "This is yeah, so sorry — so going back to it; then this is a wrap up!",    # hyphen to space, a dash, a semicolon
    "\"This is yeah so sorry so going back to it then,\" this is a wrap-up…",
    "This is yeah so sorry so going back to it then this is a wrapup.",          # spacing only
    "THIS IS YEAH SO SORRY SO GOING BACK TO IT THEN THIS IS A WRAP-UP?",
])
def test_only_case_punctuation_hyphens_apostrophes_and_spacing_may_change(after):
    assert Run(segs(A), lambda user, n: after).after == [after]
    assert letters_and_digits(after) == letters_and_digits(A)


def test_an_apostrophe_may_be_added_or_curled():
    before = "so i dont think its going to work and im not sure whats next"
    after = "So I don’t think it's going to work, and I'm not sure what's next."
    assert Run(segs(before), lambda user, n: after).after == [after]


def test_a_reply_over_several_lines_is_stored_with_single_spaces():
    run = Run(segs(A, B), lambda user, n: f"\n  {A_TIDY.replace(' so ', '   so ')}\n\n{B_TIDY}  \n")
    assert run.after == [A_TIDY, B_TIDY]


def test_a_number_keeps_its_own_punctuation_and_gains_the_sentences():
    before = "it came to 1,500.50 at 5:30 on 14/01 and then 3 4 maybe 5 more"
    after = "It came to 1,500.50 at 5:30 on 14/01, and then 3, 4, maybe 5 more."
    assert Run(segs(before), lambda user, n: after).after == [after]


# ── the reply, cut back into lines ───────────────────────────────────────────

def test_the_punctuation_that_closes_a_word_stays_with_its_line():
    assert tidy_lines.resplit(["which is good", "they also sorted it"], "which is good. They also sorted it.") == [
        "which is good.", "They also sorted it."]
    assert tidy_lines.resplit(["is that so", "yes it is"], "Is that so?! Yes, it is…") == ["Is that so?!", "Yes, it is…"]
    assert tidy_lines.resplit(["he said no", "and left"], "He said \"no,\" and left.") == ["He said \"no,\"", "and left."]


def test_what_opens_the_next_word_goes_with_the_next_line():
    assert tidy_lines.resplit(["he said", "no way and left"], "He said, \"No way,\" and left.") == [
        "He said,", "\"No way,\" and left."]
    assert tidy_lines.resplit(["y entonces", "qué piensas tú"], "Y entonces, ¿qué piensas tú?") == [
        "Y entonces,", "¿qué piensas tú?"]
    assert tidy_lines.resplit(["a bit more", "efficiently we think"], "A bit more — efficiently, we think.") == [
        "A bit more", "— efficiently, we think."]


def test_a_line_that_was_one_word_is_one_word():
    assert tidy_lines.resplit(["more", "efficiently", "tested it"], "More efficiently. Tested it.") == [
        "More", "efficiently.", "Tested it."]
    run = Run(segs(A, "efficiently", C), lambda user, n: f"{A_TIDY} efficiently. {C_TIDY}")
    assert run.after == [A_TIDY, "efficiently.", C_TIDY]


def test_an_apostrophe_or_a_hyphen_inside_a_word_does_not_end_the_line():
    assert tidy_lines.resplit(["so i'm", "close to the wrap-up", "now"], "So I'm close to the wrap-up, now.") == [
        "So I'm", "close to the wrap-up,", "now."]


@pytest.mark.parametrize("originals, tidied, cut", [
    (["which is good", "they also"], "", ["", ""]),                                        # nothing came back
    (["which is good", "they also", "sorted it"], "Which is good.", ["Which is good.", "", ""]),   # shorter
    (["which is good", "they also"], "Which is", ["Which is", ""]),
    (["which is good"], "Which is good. And a lot more besides.", ["Which is good."]),       # longer
    ([], "Which is good.", []),
])
def test_a_reply_shorter_or_longer_than_the_lines_is_cut_without_fuss(originals, tidied, cut):
    assert tidy_lines.resplit(originals, tidied) == cut


def test_a_line_takes_no_letter_more_than_it_had():
    # Two lines the writer joined with a hyphen: each keeps its own words.
    lines = ["and so this is what we call a wrap", "up of the whole day as we said"]
    run = Run(segs(*lines), lambda user, n: "And so this is what we call a wrap-up of the whole day, as we said.")
    assert run.after == ["And so this is what we call a wrap-", "up of the whole day, as we said."]
    # No space after the full stop: the next line still starts at its own first word.
    assert tidy_lines.resplit(["which is good", "they also sorted it"], "Which is good.They also sorted it.") == [
        "Which is good.", "They also sorted it."]
    assert tidy_lines.resplit(["he said", "no way and left"], "He said,“No way,” and left.") == [
        "He said,", "“No way,” and left."]


def test_text_written_without_spaces_is_cut_at_its_lines_too():
    one, two = "今日は台所と廊下の計画について話しました", "そして来週また会うことにしました"
    assert tidy_lines.resplit([one, two], "今日は台所と廊下の計画について話しました。「そして、来週また会うことにしました」。") == [
        "今日は台所と廊下の計画について話しました。", "「そして、来週また会うことにしました」。"]


# ── a changed word: the whole reply is refused ───────────────────────────────

GOOD = f"{A_TIDY} {C_TIDY}"


@pytest.mark.parametrize("why, reply, kept", [
    # A word changed in the first line: that line keeps its original, the other is still taken.
    ("a changed word", GOOD.replace("then", "now"), [A, C_TIDY]),
    ("an added word", GOOD.replace("this is a wrap-up", "this is really a wrap-up"), [A, C_TIDY]),
    ("a filler dropped", GOOD.replace("yeah, ", ""), [A, C_TIDY]),
    ("a repeat dropped", GOOD.replace("So going", "Going"), [A, C_TIDY]),
    ("a respelled word", GOOD.replace("wrap-up", "rap-up"), [A, C_TIDY]),
    ("two words swapped", GOOD.replace("going back", "back going"), [A, C_TIDY]),
    # Lines are looked for in order: the second, written before the first, is not found.
    ("the lines swapped", f"{C_TIDY} {A_TIDY}", [A_TIDY, C]),
    ("only the first line", A_TIDY, [A_TIDY, C]),
    # Chatter around the text is not a line and is not kept.
    ("a greeting first", f"Here is the punctuated text: {GOOD}", [A_TIDY, C_TIDY]),
    ("a remark after", f"{GOOD} Let me know if you need anything else.", [A_TIDY, C_TIDY]),
    ("translated", "Das ist, ja, tut mir leid. Also zurück dazu, das ist eine Zusammenfassung.", [A, C]),
    ("a summary", "A wrap-up, and the cabinets arrive on the fourteenth.", [A, C]),
    ("a refusal", "I can't help with that.", [A, C]),
    ("nothing", "", [A, C]),
    ("spaces", "  \n ", [A, C]),
    ("not text", None, [A, C]),
    ("not text either", 42, [A, C]),
])
def test_a_changed_word_costs_its_own_line_and_no_other(why, reply, kept):
    run = Run(segs(A, C), lambda user, n: reply)
    assert run.after == kept, why
    assert run.counts["refused"] == sum(a == b for a, b in zip(kept, [A, C])), why
    for before, after in zip([A, C], run.after):
        assert tidy_lines.words_of(before) == tidy_lines.words_of(after), why


@pytest.mark.parametrize("before, bad", [
    ("we agreed on 5 of them for the kitchen and the rest later", "We agreed on five of them for the kitchen, and the rest later."),
    ("we agreed on five of them for the kitchen and the rest later", "We agreed on 5 of them for the kitchen, and the rest later."),
    ("it came to 412000 pounds in the end which is more than we said", "It came to 421000 pounds in the end, which is more than we said."),
    ("we went to the cafe on the corner and had a long talk about it", "We went to the café on the corner and had a long talk about it."),
    ("we went to the café on the corner and had a long talk about it", "We went to the cafe on the corner and had a long talk about it."),
    ("me gustaria saber que piensas tu de todo esto que hemos hablado", "Me gustaría saber qué piensas tú de todo esto que hemos hablado."),
])
def test_a_number_written_another_way_or_an_accent_keeps_that_line_as_it_was(before, bad):
    run = Run(segs(A, before), lambda user, n: f"{A_TIDY} {bad}")
    assert run.after == [A_TIDY, before]


def test_a_refused_line_is_not_a_failure_and_the_next_lines_are_still_asked_about():
    lines = segs(*numbered_lines(25))
    run = Run(lines, lambda user, n: commas(user).replace("sorry", "glad", 1) if n == 1 else commas(user))
    assert len(run.asked) == 2
    assert run.after == [lines[0]["text"]] + [commas(s["text"]) for s in lines[1:]]
    assert run.counts == {"lines": 25, "requests": 2, "changed": 24, "refused": 1, "stopped": ""}


# ── a line that fails on its own: only that line ─────────────────────────────

@pytest.mark.parametrize("before, bad", [
    ("it came to 15 percent in the end which is more than we said", "It came to 1.5 percent in the end, which is more than we said."),
    ("it came to 1.5 percent in the end which is more than we said", "It came to 15 percent in the end, which is more than we said."),
    ("it came to 1,500 pounds in the end which is more than we said", "It came to 1.500 pounds in the end, which is more than we said."),
    ("we meet at 5 30 on the fourteenth and then again the week after", "We meet at 5:30 on the fourteenth, and then again the week after."),
    ("there were 5 6 maybe 7 of them in the room when we started", "There were 56, maybe 7 of them in the room when we started."),
    ("it came to 15 in the end which is quite a lot more than we said", "It came to £15 in the end, which is quite a lot more than we said."),
    ("it came to 15 in the end which is quite a lot more than we said", "It came to 15% in the end, which is quite a lot more than we said."),
    # The accent written as a separate mark (e + U+0301) is not a letter, and is still an accent.
    ("we went to the cafe on the corner and had a long talk about it",
     "We went to the café on the corner and had a long talk about it."),
    # One vowel sign: a mark, not a letter, and another word.
    ("आज हमने रसोई और गलियारे की योजना के बारे में बात की और अगले हफ्ते फिर मिलेंगे",
     "आज हमने रसोई और गलियारे कि योजना के बारे में बात की, और अगले हफ्ते फिर मिलेंगे।"),
])
def test_a_line_whose_number_or_marks_changed_is_refused_and_its_neighbours_are_not(before, bad):
    assert letters_and_digits(before) == letters_and_digits(bad), "the whole reply passes: the line's own check catches it"
    run = Run(segs(A, before, C), lambda user, n: f"{A_TIDY} {bad} {C_TIDY}")
    assert run.after == [A_TIDY, before, C_TIDY]
    assert run.counts["changed"] == 2 and run.counts["refused"] == 1


# ── the first letter of a request's first line ───────────────────────────────

LATER = "efficiently and then we tested it a lot more than we did before"
LATER_TIDY = "fficiently, and then we tested it a lot more than we did before."      # after its first letter


def test_the_first_line_of_a_source_starts_with_a_capital_whatever_the_writer_did():
    run = Run(segs(*TRIAL), lambda user, n: TRIAL_REPLY)
    assert TRIAL_REPLY.startswith("which") and run.after[0] == "Which is good."


def test_across_a_request_boundary_a_sentence_that_runs_on_keeps_its_small_letter():
    lines = segs(*numbered_lines(20), LATER)
    # The writer saw only this line, so it began it with a capital.
    run = Run(lines, lambda user, n: commas(user) if n == 1 else "E" + LATER_TIDY)
    assert run.sent[1] == LATER, "the line before is not sent again"
    assert not run.after[19].endswith((".", "?", "!")) and run.after[20] == "e" + LATER_TIDY


def test_across_a_request_boundary_a_new_sentence_gets_its_capital():
    lines = segs(*numbered_lines(20), LATER)
    run = Run(lines, lambda user, n: commas(user) + "." if n == 1 else "e" + LATER_TIDY)
    assert run.after[19].endswith("19.") and run.after[20] == "E" + LATER_TIDY
    for end in "?!":
        run = Run(lines, lambda user, n: commas(user) + end if n == 1 else "e" + LATER_TIDY)
        assert run.after[20] == "E" + LATER_TIDY


def test_a_line_that_runs_on_keeps_the_capital_it_had():
    said = "Tom said the cabinets arrive on the fourteenth of the month"
    tidied = "om said the cabinets arrive on the fourteenth of the month."
    lines = segs(*numbered_lines(20), said)
    for first in "Tt":
        run = Run(lines, lambda user, n: commas(user) if n == 1 else first + tidied)
        assert run.after[20] == "T" + tidied, "as the original line had it"


def test_after_a_refused_request_the_line_before_is_the_original():
    lines = segs(*numbered_lines(20), LATER)
    run = Run(lines, lambda user, n: "nothing like it." if n == 1 else "E" + LATER_TIDY)
    assert run.after[:20] == [s["text"] for s in lines[:20]] and run.after[20] == "e" + LATER_TIDY


def test_each_source_starts_with_its_own_capital():
    lines = segs(("mic", A), ("system", "and they said it would be ready by the end of the week"))
    run = Run(lines, lambda user, n: commas(user))
    assert run.after == [cap(commas(A)), "And they said it would be ready by the end of the week"]


@pytest.mark.parametrize("tidied, original, earlier, kept", [
    ("which is good.", "which is good", "", "Which is good."),                       # nothing before it
    ("which is good.", "which is good", "And that was that.", "Which is good."),
    ("Which is good.", "which is good", "and that was", "which is good."),
    ("Which is good.", "which is good", "and that was,", "which is good."),
    ("which is good.", "Which is good", "and that was", "Which is good."),
    ("\"which is good,\" he said.", "which is good he said", "", "\"Which is good,\" he said."),   # the letter, not the mark
    ("5 of them came.", "5 of them came", "", "5 of them came."),                    # a digit has no case
    ("…", "...", "", "…"),
    ("", "which is good", "", ""),
    ("ßen ist gut.", "ßen ist gut", "", "ßen ist gut."),                             # no capital that is one letter
    ("今日は台所の話をしました。", "今日は台所の話をしました", "", "今日は台所の話をしました。"),
])
def test_only_the_case_of_one_letter_is_ever_decided_here(tidied, original, earlier, kept):
    assert tidy_lines.first_letter(tidied, original, earlier) == kept
    assert letters_and_digits(kept) == letters_and_digits(tidied) and len(kept) == len(tidied)


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
    fifteen = "it came to 15 percent in the end which is more than we said"
    run = Run(segs(A, fifteen, C),
              lambda user, n: f"{A_TIDY} It came to 1.5 percent in the end, which is more than we said. {C_TIDY}")
    assert run.after == [A_TIDY, fifteen, C_TIDY]
    assert len(log_lines) == 1
    line = log_lines[0]
    assert "3 lines" in line and "1 request" in line and "2 changed" in line and "1 refused" in line
    for said in (A, fifteen, C, A_TIDY, C_TIDY):
        for word in said.split():
            if len(word) > 4:
                assert word.lower().strip(".,") not in line.lower(), word


def test_nothing_asked_means_nothing_logged(log_lines):
    Run(segs("So this one is fine, it was written properly."))
    assert log_lines == []


def test_a_failure_part_way_keeps_what_was_done_and_asks_no_more():
    lines = segs(*numbered_lines(50))

    def reply(user, n):
        if n == 2:
            raise TimeoutError()
        return commas(user)
    run = Run(lines, reply)
    assert len(run.asked) == 2, "the third request is never made"
    assert run.after[:20] == [cap(commas(lines[0]["text"]))] + [commas(s["text"]) for s in lines[1:20]]
    assert run.after[20:] == [s["text"] for s in lines[20:]]
    assert run.counts == {"lines": 50, "requests": 2, "changed": 20, "refused": 0, "stopped": "TimeoutError"}


def test_a_store_that_fails_is_not_the_meetings_problem():
    def save(changes):
        raise OSError("disk full")
    counts = tidy_lines.tidy(segs(A, B), lambda system, user: f"{A_TIDY} {B_TIDY}", save)
    assert counts["stopped"] == "OSError" and counts["changed"] == 0


# ── batches ──────────────────────────────────────────────────────────────────

def test_twenty_lines_at_most_go_in_one_request():
    lines = segs(*numbered_lines(45))
    run = Run(lines)
    texts = [s["text"] for s in lines]
    assert run.sent == [" ".join(texts[:20]), " ".join(texts[20:40]), " ".join(texts[40:])]
    assert all("Earlier" not in user and "\n" not in user and not re.match(r"\d+:", user) for user in run.sent)
    assert run.after == [cap(commas(texts[0]))] + [commas(t) for t in texts[1:]]


def test_about_two_and_a_half_thousand_characters_at_most_go_in_one_request():
    long = " ".join(["and we kept talking about the plan so the kitchen and the hallway"] * 11)   # 725 characters: three fit, four do not
    run = Run(segs(*[long] * 8))
    assert run.sent == [" ".join([long] * 3), " ".join([long] * 3), " ".join([long] * 2)]
    assert all(len(user) <= 2500 for user in run.sent)
    assert run.after == [cap(commas(long))] + [commas(long)] * 7


def test_one_enormous_line_goes_alone_and_is_never_cut():
    huge = " ".join(["and we kept talking about the plan so the kitchen and the hallway"] * 80)
    run = Run(segs(A, huge, C))
    assert run.sent == [A, huge, C]
    assert run.after == [cap(commas(A)), commas(huge), C], "the last had nothing to change, and is not a refusal"
    assert run.counts["refused"] == 0


# ── two sources ──────────────────────────────────────────────────────────────

def test_the_microphone_and_the_computers_sound_are_asked_about_apart():
    theirs = "and then they said so it would be ready by the end of the week"
    lines = segs(("mic", A), ("system", C), ("mic", B), ("system", theirs))
    run = Run(lines, lambda user, n: {f"{A} {B}": f"{A_TIDY} {B_TIDY}"}.get(user, commas(user)))
    assert run.sent == [f"{A} {B}", f"{C} {theirs}"], "a sentence runs on only within one source"
    assert run.after == [A_TIDY, cap(C), B_TIDY, commas(theirs)]


def test_a_source_with_nothing_needing_it_is_still_sent_when_the_meeting_needs_it():
    lines = segs(("mic", A), ("system", "They said it would be ready by Friday."))
    assert Run(lines).sent == [A, "They said it would be ready by Friday."]


def test_each_source_is_cut_into_its_own_requests():
    lines = segs(*[(("mic", "system")[i % 2], text) for i, text in enumerate(numbered_lines(42))])
    run = Run(lines)
    mic = [s["text"] for s in lines if s["source"] == "mic"]
    system = [s["text"] for s in lines if s["source"] == "system"]
    assert run.sent == [" ".join(mic[:20]), mic[20], " ".join(system[:20]), system[20]]


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
    assert Run(segs(before), lambda user, n: after).after == [after]


def test_other_languages_are_cut_back_into_their_lines_too():
    one, two = "привет это запись нашей встречи", "и мы говорили о кухне и о коридоре"
    run = Run(segs(one, two), lambda user, n: "Привет, это запись нашей встречи, и мы говорили о кухне и о коридоре.")
    assert run.after == ["Привет, это запись нашей встречи,", "и мы говорили о кухне и о коридоре."]
    one, two = "今日は台所と廊下の計画について詳しく話しましたそれから少し休みました", "そして来週また会うことにしました"
    run = Run(segs(one, two), lambda user, n: "今日は台所と廊下の計画について詳しく話しました。それから少し休みました。そして、来週また会うことにしました。")
    assert run.sent == [f"{one} {two}"]
    assert run.after == ["今日は台所と廊下の計画について詳しく話しました。それから少し休みました。", "そして、来週また会うことにしました。"]


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
    assert Run(segs(before), lambda user, n: bad).after == [before]


# ── whatever the writer says, the words are the same ─────────────────────────

WORDS = ("so yeah okay right we they the a to of and then kitchen hallway cabinets electrician fourteenth "
         "wrap-up i'm don't it's waffler café naïve über straße привет встречи кухне 台所 計画 रसोई योजना "
         "5 14 412000 1,500 3.5 2026 twenty five").split()
EXTRA = ("really", "not", "Tom", "7", "ever", "да", "と")
MARKS = (".", ",", "?", "!", ";", ":", " —", "…", "\"", "'", ")", "。")


def _random_line(rng):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 22)))


def _honest(rng, text):
    """Capitals and punctuation only."""
    out = []
    for word in text.split():
        if rng.random() < 0.3:
            word = word[:1].upper() + word[1:]
        if rng.random() < 0.3:
            word += rng.choice(MARKS)
        if rng.random() < 0.03:
            word = "\"" + word
        out.append(word)
    return " ".join(out)


def _dishonest(rng, text):
    """A word changed, somewhere in the text."""
    words = text.split()
    i = rng.randrange(len(words))
    kind = rng.choice(("drop", "add", "swap", "respell", "digit", "double", "unaccent", "other", "half"))
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
    else:
        words = words[:len(words) // 2]
    return _honest(rng, " ".join(words))


def _sly(rng, text):
    """Every letter and digit as it was, so the reply as a whole passes; a
    number or a mark is not."""
    text = _honest(rng, text)
    kind = rng.choice(("point", "join", "pound", "percent", "accent", "hyphen", "upper"))
    if kind == "point":
        return re.sub(r"(\d)(\d)", r"\1.\2", text, count=1)
    if kind == "join":
        return re.sub(r"(\d) (\d)", r"\1\2", text, count=1)
    if kind == "pound":
        return re.sub(r"(\d+)", r"£\1", text, count=1)
    if kind == "percent":
        return re.sub(r"(\d+)", r"\1%", text, count=1)
    if kind == "accent":
        at = rng.randrange(len(text))
        return text[:at + 1] + "́" + text[at + 1:]
    if kind == "hyphen":
        return text.replace(" ", "-", rng.randint(1, 3))            # honest, but lines may run together
    return text.upper()                                             # honest too, unless ß became SS


def _random_reply(rng, user):
    roll = rng.random()
    if roll < 0.5:
        return _honest(rng, user)
    if roll < 0.75:
        return _sly(rng, user)
    if roll < 0.95:
        return _dishonest(rng, user)
    return rng.choice(("", None, "Here you go: " + _honest(rng, user), _honest(rng, user) + " Anything else?",
                       "```\n" + _honest(rng, user) + "\n```", "\n".join(_honest(rng, user).split(". "))))


def test_whatever_the_writer_says_the_letters_and_digits_of_every_line_are_the_same():
    rng = random.Random(20261123)
    accepted = refused = 0
    for _ in range(400):
        lines = segs(*[(rng.choice(("mic", "mic", "system")), _random_line(rng)) for _ in range(rng.randint(1, 70))])
        run = Run(lines, lambda user, n: _random_reply(rng, user))
        for seg, after in zip(lines, run.after):
            assert letters_and_digits(after) == letters_and_digits(seg["text"]), (seg["text"], after)
            assert re.findall(r"\d+", after) == re.findall(r"\d+", seg["text"]), (seg["text"], after)
            assert after == " ".join(after.split()) and after
        assert lines == run.before, "nothing but the text is ever touched, and that only through the store"
        assert not run.counts["stopped"]
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

    def __init__(self, tidy=lambda user: f"{A_TIDY} {B_TIDY}"):
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
    assert writer.tidied == [f"{A} {B}"], "one running text, as it was said"
    assert len(writer.notes_from) == 1 and f"Me: {A_TIDY} {B_TIDY}" in writer.notes_from[0]
    assert A not in writer.notes_from[0]
    assert note["title"] == "The day's wrap-up" and "A wrap-up of the day" in note["summary"] and note["error"] == ""
    assert [h["note_id"] for h in service.store.search_segments("Waffler listening")] == [note["id"]]


def test_a_writer_that_changes_words_gets_none_of_them_into_the_meeting(no_keys):
    writer = Writer(lambda user: "This is a wrap-up of my day. I am close, Waffler, if you are listening.")
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
    assert len(writer.tidied) == 2 and writer.tidied[1] == f"{A} {B}"
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
    plus.write_reply = lambda body: f"{A_TIDY} {B_TIDY}" if body["system"] == tidy_lines.SYSTEM else notes(body)
    note = _plus_meeting(Service(store=Store()))
    assert [s["text"] for s in note["segments"]] == [A_TIDY, B_TIDY]
    assert [w["system"] for w in plus.writes] == [tidy_lines.SYSTEM, llm.NOTES_SYSTEM], \
        "POST /api/plus/write, counted like any other writing"
    asked = plus.writes[0]
    assert asked["messages"] == [{"role": "user", "content": f"{A} {B}"}]
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
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=A_TIDY))])


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
    assert all(a["messages"][-1] == {"role": "user", "content": A} for a in client.asked)


def test_the_model_on_this_computer_is_asked_with_no_chance_and_in_the_background(monkeypatch):
    asked = {}

    def chat(system, messages, max_tokens=0, temperature=None, background=False, on_text=None):
        asked.update(system=system, temperature=temperature, background=background)
        return {"text": f"<think>capitals</think>{A_TIDY}"}
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
        return A_TIDY
    monkeypatch.setattr(llm, "resolve_provider", lambda settings: "anthropic")
    monkeypatch.setattr(llm, "_anthropic_complete", claude)
    service = SimpleNamespace(llm_backend=None)
    run = Run(segs(A), lambda user, n: Service._ask_writer(service, {}, tidy_lines.SYSTEM, user))
    assert run.after == [A_TIDY]
    assert asked == [(SPEC_SYSTEM, [{"role": "user", "content": A}], llm.ANTHROPIC_DEFAULT_MODEL)]


@pytest.mark.parametrize("tidied, original, kept", [
    ("I think so, yes.", "i think so yes", "I think so, yes."),
    ("I'm close, all right.", "i'm close all right", "I'm close, all right."),
    ("I\u2019ll have to look.", "i'll have to look", "I\u2019ll have to look."),
    ("It was fine.", "it was fine", "it was fine."),          # not the word "I"
    ("In the morning.", "in the morning", "in the morning."),
])
def test_the_word_i_keeps_its_capital_where_a_sentence_runs_on(tidied, original, kept):
    assert tidy_lines.first_letter(tidied, original, "and then") == kept


# -- a reply with a changed word: the lines it did not touch are still taken --

LINES_SENT = ["this is a wrap up of my day", "so that good and then had a really good gym session",
              "felt good after not going for like five days", "which is good"]


def test_one_changed_word_costs_one_line():
    reply = ("This is a wrap up of my day. So that's good, and then had a really good gym session. "
             "Felt good after not going for, like, five days, which is good.")
    kept, refused = tidy_lines.checked(LINES_SENT, reply)
    assert kept == ["This is a wrap up of my day.", LINES_SENT[1],
                    "Felt good after not going for, like, five days,", "which is good."]
    assert refused == 1


def test_an_added_word_between_two_lines_is_dropped_and_both_lines_stay():
    reply = ("This is a wrap up of my day. Anyway, so that good, and then had a really good gym session. "
             "Felt good after not going for like five days, which is good.")
    kept, refused = tidy_lines.checked(LINES_SENT, reply)
    assert refused == 0
    assert kept[1] == "so that good, and then had a really good gym session."
    assert all(tidy_lines.same_words(a, b) for a, b in zip(LINES_SENT, kept))


def test_a_dropped_line_costs_that_line_only():
    reply = "This is a wrap up of my day. Felt good after not going for like five days, which is good."
    kept, refused = tidy_lines.checked(LINES_SENT, reply)
    assert kept == ["This is a wrap up of my day.", LINES_SENT[1],
                    "Felt good after not going for like five days,", "which is good."]
    assert refused == 1


def test_a_reply_about_something_else_keeps_every_line():
    kept, refused = tidy_lines.checked(LINES_SENT, "Sure! Here is the punctuated transcript you asked for.")
    assert kept == LINES_SENT and refused == len(LINES_SENT)


def test_a_line_said_twice_is_found_in_order():
    lines = ["sort of go through a few things", "sort of go through a few things", "and that was it really yes"]
    reply = "Sort of go through a few things, sort of go through a few things. And that was that, really, yes."
    kept, refused = tidy_lines.checked(lines, reply)
    assert kept == ["Sort of go through a few things,", "sort of go through a few things.", lines[2]]
    assert refused == 1


def test_found_lines_never_hands_back_other_words():
    rng = random.Random(7)
    words = "so yeah we just kind of go through a few things here and there it is good".split()
    for _ in range(300):
        lines = [" ".join(rng.choice(words) for _ in range(rng.randint(1, 9))) for _ in range(rng.randint(1, 8))]
        said = " ".join(lines).split()
        for _ in range(rng.randint(0, 3)):              # the writer changes, adds or drops words
            i = rng.randrange(len(said) + 1)
            said[i:i + rng.randint(0, 1)] = [rng.choice(words)] * rng.randint(0, 2)
        reply = " ".join(w + rng.choice(["", "", ",", "."]) for w in said)
        kept, _ = tidy_lines.checked(lines, reply)
        assert len(kept) == len(lines)
        for before, after in zip(lines, kept):
            assert tidy_lines.words_of(before) == tidy_lines.words_of(after)
