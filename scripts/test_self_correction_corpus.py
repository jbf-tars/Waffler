"""Self-correction regression harness for Waffler.

When the speaker corrects themselves mid-sentence, the wrong version
must be dropped from the output, not kept alongside the corrected one.

This covers many ways a speaker actually phrases corrections:
  - "X, sorry I mean Y"
  - "X, no wait, Y"
  - "X, actually Y"
  - "X — I meant Y"
  - "X, hmm no, Y"
  - implicit corrections without an explicit marker
  - multi-step corrections (X → Y → Z)
  - corrections inside lists / emails / questions
  - LEAD-IN corrections: the words the correction does NOT replace
    ("Let's meet on", "Send it to") must survive. Whisper often punctuates
    the correction as its own sentence ("Let's meet on Tuesday. No, wait,
    Wednesday."), and the model has been seen deleting the lead-in along
    with the wrong value ("Thursday at two."), which the truncation guard
    then refuses, so the user gets their uncorrected words pasted.

Drives the styler with text only, no Whisper involved.

A case PASSES only on the FINAL pasted output (after the truncation guard
and the deterministic layout passes), because that is what the user gets.
Separately, the harness captures the model's output BEFORE the truncation
guard (by wrapping the instance's _guard_truncation; src/ is untouched) and
reports a "model-correct" rate on it, so a prompt change can be judged even
when the guard hides the model's answer.

Silent damage is counted on its own. A failed correction that pastes the
speaker's own words loses nothing; an answer that drops a word the speaker
meant, or glues a lead-in onto a replacement that has its own verb, or leaves
a stray comma, gets past the truncation guard and is worse. So besides the
per-case checks every run gets:
  LOSS    every word of the transcript must appear in the output, except
          fillers (_FILLER_WORDS) and the words the case says a correct
          output may leave out (DROPPABLE: the retracted values and the
          markers; [] for the negative controls, which keep everything),
          and no answer may delete every correction marker while keeping
          the value it took back (RETRACTED: "£500, £750" reads as both)
  GARBLE  no stray punctuation (a comma after "the", a doubled comma, a
          space before a comma) and none of the case's own broken shapes
          (Case.garble, e.g. "shift it to Monday works better")
Their failure lines start "LOSS:" / "GARBLE:" and the table, totals and JSON
count those runs separately, on the pasted output and on the model's answer.

Run:
  python scripts/test_self_correction_corpus.py
  python scripts/test_self_correction_corpus.py --only "^LEAD" --runs 5
  python scripts/test_self_correction_corpus.py --prompt-file cand.txt --runs 5 \\
      --provider groq --json out.json
  python scripts/test_self_correction_corpus.py --rescore out.json --json new.json

Options:
  --runs N           run every case N times (default 1)
  --prompt-file P    load P (read as UTF-8, like the app reads
                     prompts/normal.txt) into the styler's prompt_template
                     after construction, so a candidate prompt can be
                     measured without editing prompts/normal.txt
  --rescore IN       no model calls: re-score the outputs recorded in IN (a
                     --json file from this harness) with the current checks
  --only REGEX       only cases whose label matches REGEX (case-insensitive)
  --filter TEXT      only cases whose label contains TEXT (legacy substring)
  --json OUT         write per-run detail (model output before the guard,
                     final pasted output, guard reason, pass/fail reasons)
  --provider NAME    pin the styler to one provider with no fallback
  --force-llm        bypass the short-transcript shortcut (_is_simple) so
                     every case reaches the model; default is the app's path
  --delay SECONDS    pause between calls (default 1)
  --max-cost USD     stop before the estimated spend passes this (default 0.50)
"""
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    # utf-8-sig, as the app reads .env: a byte-order mark must not hide a key.
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler  # noqa: E402


@dataclass
class Case:
    label: str
    raw: str
    must_contain: List[str] = field(default_factory=list)
    must_not_contain: List[str] = field(default_factory=list)
    must_match: List[str] = field(default_factory=list)
    must_not_match: List[str] = field(default_factory=list)
    note: str = ""
    # Regexes for a broken answer (a lead-in glued onto a replacement that
    # brings its own verb, a stray comma). A match fails the run as GARBLE.
    garble: List[str] = field(default_factory=list)
    # The whole output must be exactly this (whitespace and apostrophes
    # normalised).
    exact: Optional[str] = None


# Apostrophe-tolerant "let's" / "I'll" for regex checks (the model may emit a
# curly apostrophe; the transcript has a straight one).
_AP = "['’]"

CORPUS: List[Case] = [
    # ─── Day-of-week corrections (user's exact ask) ─────────────────────────
    Case("DAY-1 user's exact example",
         "Can we set up a meeting for Tuesday, sorry I mean Monday.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"],
         note="user reported this case isn't being corrected"),
    Case("DAY-2 'no wait' marker",
         "Let's do the standup at Tuesday, no wait, Monday.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"]),
    Case("DAY-3 'actually' marker (less explicit)",
         "Can we shift it to Tuesday, actually Monday works better.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"],
         garble=[r"(?i)\bto\s+monday\s+works\b"],
         note="the replacement brings its own verb: 'Can we shift it to Monday works "
              "better.' (round 1, 2 of 3 runs) passes the guard and is broken"),
    Case("DAY-4 em-dash + 'I meant'",
         "Send the doc on Tuesday — I meant Monday — before the standup.",
         must_contain=["Monday", "before the standup"],
         must_not_contain=["Tuesday"]),
    Case("DAY-5 'hmm no'",
         "Let's schedule it for Tuesday, hmm no, Monday morning.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"]),
    Case("DAY-6 abandoned without marker",
         "Tuesday — Monday at three would be better actually.",
         must_contain=["Monday", "three"],
         must_not_contain=["Tuesday"]),

    # ─── Name corrections ──────────────────────────────────────────────────
    Case("NAME-1 'sorry I mean'",
         "Send the report to John, sorry I mean Jane, by end of day.",
         must_contain=["Jane"],
         must_not_contain=["John"]),
    Case("NAME-2 multi-name correction",
         "Loop in Rohan and Sarah, no wait, Rohan and James.",
         must_contain=["Rohan", "James"],
         must_not_contain=["Sarah"]),
    Case("NAME-3 surname correction",
         "Ping James Smith, sorry, James Farrelly about the migration.",
         must_contain=["James Farrelly"],
         must_not_contain=["Smith"]),

    # ─── Number / time corrections ─────────────────────────────────────────
    Case("NUM-1 time correction",
         "The call is at three, no four, in the afternoon.",
         must_contain=["four"],
         must_not_contain=["at three"],
         note="'three' should be dropped, 'four' kept"),
    Case("NUM-2 price correction",
         "The quote was £500, sorry, £750 for the whole package.",
         must_contain=["£750"],
         must_not_contain=["£500"]),
    Case("NUM-3 count correction",
         "We've got about twenty users, no actually fifty users on the beta.",
         must_contain=["fifty"],
         must_not_contain=["twenty"]),

    # ─── Place / location corrections ──────────────────────────────────────
    Case("PLACE-1 location correction",
         "We're meeting in the boardroom, sorry I mean the cafeteria, at twelve.",
         must_contain=["cafeteria", "twelve"],
         must_not_contain=["boardroom"]),

    # ─── Multi-step corrections ────────────────────────────────────────────
    Case("MULTI-1 two corrections in one sentence",
         "Send it to John, sorry James, by Tuesday, no wait, Wednesday at three.",
         must_contain=["James", "Wednesday"],
         must_not_contain=["John", "Tuesday"],
         must_match=[r"(?i)\bby\b"],
         garble=[r"(?i)\bto\s+James,\s*by\b"],
         note="both the name AND day corrections must apply, and the frame word 'by' "
              "stays: round 1 pasted 'Send it to James, Wednesday at three.' and "
              "'Send it to James.\\n\\nWednesday at three.' as passes"),
    Case("MULTI-2 chain X→Y→Z",
         "Let's meet on Tuesday, no Wednesday, actually Thursday at two.",
         must_contain=["Thursday"],
         must_not_contain=["Tuesday", "on Wednesday"],
         note="only the final option in the chain survives"),

    # ─── Corrections inside structure ──────────────────────────────────────
    Case("STRUCT-1 correction inside numbered list",
         "Number one, ping John — sorry, I mean Jane — about the contract. Number two, book the room for Tuesday, no Monday.",
         must_contain=["Jane", "Monday"],
         must_not_contain=["John", "Tuesday"],
         must_match=[r"(?m)^\s*1\.\s*", r"(?m)^\s*2\.\s*"]),
    Case("STRUCT-2 correction inside email body",
         "Hi Rohan, can you sign off the budget by Tuesday, sorry I mean Monday. Cheers, James.",
         must_contain=["Monday", "Rohan"],
         must_not_contain=["Tuesday"],
         must_match=[r"^Hi Rohan,[ \t]*\n[ \t]*\n",
                     r"\n[ \t]*\n[ \t]*Cheers,[ \t]*\n[ \t]*James\b"]),
    Case("STRUCT-3 correction inside a question",
         "Are we meeting at three, sorry I mean four, this afternoon?",
         must_contain=["four", "?"],
         must_not_contain=["at three"]),

    # ─── Abandoned-phrase corrections (no explicit marker) ─────────────────
    Case("ABAND-1 abandoned start with em-dash",
         "We should ship — actually let me start over. The plan is to ship the dashboard refactor first.",
         must_contain=["dashboard refactor"],
         must_not_contain=["let me start over"]),
    Case("ABAND-2 partial then restart",
         "The thing about — what I'm trying to say is the migration is risky.",
         # Acceptable outputs: drop the abandoned start, OR fold the two halves
         # into smooth English. gpt-4.1-mini tends to preserve more text
         # (which is generally GOOD), so the assertion is just that the
         # meaning is preserved — migration + risky present in output.
         must_contain=["migration", "risky"],
         note="abandoned start; both 'drop entirely' and 'fold smoothly' are acceptable. "
              "The critical thing is content preservation."),

    # ─── NEGATIVE — 'I mean' as clarification, NOT a correction ────────────
    Case("NEG-1 'I mean' adds detail, doesn't replace",
         "Make sure the test is comprehensive — I mean cover the edge cases, the happy path, and the error states.",
         must_contain=["edge cases", "happy path", "error states", "comprehensive"],
         note="'I mean' here introduces a clarification, not a correction; nothing should be dropped"),
    Case("NEG-2 'actually' as emphasis not correction",
         "I actually really enjoyed working on this project, the whole team was great.",
         # 'actually' here is emphasis, not a correction. The prompt's
         # WHAT TO KEEP rule explicitly preserves "actually" when emphasising,
         # so the styler may legitimately keep it. The critical check is that
         # NOTHING gets dropped — "enjoyed" and "whole team" must survive.
         must_contain=["enjoyed", "whole team"],
         note="'actually' as emphasis is keep-able per prompt rule; the test is that no "
              "content gets dropped, not that 'actually' is removed"),
    Case("NEG-3 'sorry' as apology not correction",
         "Sorry for the late reply, the call ran over by twenty minutes.",
         must_contain=["late reply", "twenty minutes"],
         note="'Sorry' starting a sentence is an apology, not a correction marker — must not drop anything"),
    Case("NEG-4 'no wait' as suspense not correction",
         "I thought the deploy would take an hour, but no, wait until you see this — it took six.",
         must_contain=["six"],
         must_match=[r"(?i)\bno,?\s+wait\s+until\s+you\s+see\s+this\b"],
         note="'no wait' as a rhetorical device, not a correction; round 1 pasted "
              "'...but until you see this, it took six.' and it passed"),

    # ─── LEAD-IN corrections (the bug class, 2026-09) ──────────────────────
    # The correction replaces ONE slot (a day, a name, a number); everything
    # the speaker said before that slot is a shared lead-in and must stay.
    Case("LEAD-1 cross-sentence chain (exact bug transcript)",
         "Let's meet on Tuesday. No, wait, Wednesday. Actually, Thursday at two.",
         must_contain=["meet on", "Thursday at two"],
         must_not_contain=["Tuesday", "Wednesday"],
         must_match=[rf"(?i)\blet{_AP}s\s+meet\s+on\s+thursday\s+at\s+two\b"],
         note="live 5/5: model returned 'Thursday at two.', guard pasted raw"),
    Case("LEAD-2 one-sentence chain (exact bug transcript)",
         "Let's meet on Tuesday, no wait Wednesday, actually Thursday at 2.",
         must_contain=["meet on", "Thursday at 2"],
         must_not_contain=["Tuesday", "Wednesday"],
         must_match=[rf"(?i)\blet{_AP}s\s+meet\s+on\s+thursday\s+at\s+2\b"],
         must_not_match=[r"(?i)\bactually\s+thursday"],
         note="live 4/5: model returned 'Thursday at 2.' / 'actually Thursday at 2.'"),
    Case("LEAD-3 cross-sentence single correction",
         "Can you send it on Monday. Sorry, Tuesday.",
         must_contain=["send it on", "Tuesday"],
         must_not_contain=["Monday", "sorry"],
         must_match=[r"(?i)\bcan\s+you\s+send\s+it\s+on\s+tuesday\b"],
         note="8 words and no marker the _is_simple gate recognises, so the app "
              "may never ask the model; use --force-llm to measure the prompt alone"),
    Case("LEAD-4 chain inside a longer sentence",
         "I'll book the table for six, no wait, seven, actually eight people on Friday.",
         must_contain=["book the table for", "eight people on Friday"],
         must_not_contain=["six", "seven"],
         must_match=[rf"(?i)\bi{_AP}ll\s+book\s+the\s+table\s+for\s+eight\s+people\s+on\s+friday\b"]),
    Case("LEAD-5 cross-sentence number chain",
         "The budget is five thousand. No, six. Actually, let's say seven thousand.",
         must_contain=["budget is", "seven thousand"],
         must_not_contain=["five", "six"],
         note="lead-in 'The budget is' must survive; 'let's say' may stay or go"),
    Case("LEAD-6 name correction split by Whisper punctuation",
         "Please forward the invoice to Sarah. Sorry, Sophie.",
         must_contain=["forward the invoice to", "Sophie"],
         must_not_contain=["Sarah", "sorry"],
         must_match=[r"(?i)\bforward\s+the\s+invoice\s+to\s+sophie\b"],
         note="8 words and no marker the _is_simple gate recognises; see LEAD-3"),
    Case("LEAD-7 whole-clause lead-in",
         "We need to ship the dashboard by Monday. No, wait, by Wednesday.",
         must_contain=["We need to ship the dashboard", "Wednesday"],
         must_not_contain=["Monday"],
         must_match=[r"(?i)\bship\s+the\s+dashboard\s+by\s+wednesday\b"]),
    Case("LEAD-8 in-sentence control (works today)",
         "Send it to John, sorry, James, by Wednesday at three.",
         must_contain=["Send it to James", "Wednesday at three"],
         must_not_contain=["John", "sorry"],
         exact="Send it to James by Wednesday at three.",
         garble=[r"(?i)\bto\s+James,\s*by\b"],
         note="positive control: in-sentence corrections were 5/5 live; round 1 left "
              "a stray comma ('James, by') in 13 of 13 runs"),
    Case("LEAD-9 cross-sentence correction then more content",
         "Book the flight for the 14th. Sorry, the 15th. And get a hotel near the office.",
         must_contain=["hotel near the office"],
         must_not_contain=["14th", "sorry"],
         must_match=[r"(?i)\bbook\s+the\s+flight\s+for\s+the\s+15th\b"],
         note="content AFTER the correction must survive too"),

    # ─── NEGATIVE controls for the lead-in fix: keep EVERYTHING ────────────
    Case("NEG-LEAD-1 'Actually' adds an option, not a correction",
         "Let's meet on Tuesday. Actually, Thursday works too.",
         must_contain=["Tuesday", "Thursday works too"],
         garble=[r"(?i)\bon\s+thursday\s+works\b"],
         note="new information; short enough that the app may skip the model"),
    Case("NEG-LEAD-1b 'Actually' adds an option (reaches the model)",
         "Let's meet on Tuesday to go through the numbers. Actually, Thursday works too if you're busy.",
         must_contain=["Tuesday", "go through the numbers", "Thursday works too", "busy"],
         garble=[r"(?i)\bon\s+thursday\s+works\b"]),
    Case("NEG-LEAD-2 rhetorical 'No, wait'",
         "No, wait until you see the numbers.",
         must_contain=["wait until you see the numbers"],
         must_match=[r"(?i)^\s*no\b"],
         note="rhetorical, not a correction: 'No' stays"),
    Case("NEG-LEAD-3 'Sorry' apology then a plan",
         "Sorry, I'm running late. Let's meet at two.",
         must_contain=["sorry", "running late", "meet at two"],
         note="apology; short enough that the app may skip the model"),
    Case("NEG-LEAD-3b 'Sorry' apology then a plan (reaches the model)",
         "Sorry, I'm running late this morning. Let's meet at two in the usual room.",
         must_contain=["sorry", "running late", "meet at two", "usual room"]),

    # ─── Round-1 review (2026-09-25): shapes that lost or garbled words ────
    # Each got past the truncation guard (the answer kept at least half the
    # words), so the user got a wrong or broken sentence with no warning,
    # where before they got their own words back.
    Case("NEG-LEAD-4 'actually' adds an option after a comma",
         "Let's meet on Tuesday, actually Thursday works too.",
         must_contain=["Tuesday", "Thursday", "works too"],
         garble=[r"(?i)\bon\s+thursday\s+works\b"],
         note="8 words, but ', actually' sends it to the model; round 1 pasted "
              "\"Let's meet on Thursday works too.\" 2 of 2 (6 of 8 words passes the guard)"),
    Case("NEG-LEAD-5 'Actually' adds an option, 11 words",
         "Let's meet on Tuesday at noon then. Actually, Thursday works too.",
         must_contain=["Tuesday", "noon", "Thursday works too"],
         garble=[r"(?i)\bon\s+thursday\s+works\b"],
         note="round 1 pasted \"Let's meet on Thursday at noon then.\" 2 of 2 "
              "(7 of 11 words passes the guard)"),
    Case("NEG-LEAD-6 'Actually' adds an option, 14 words",
         "We could do the review on Tuesday at noon then. Actually, Thursday works too.",
         must_contain=["Tuesday", "noon", "Thursday works too"],
         garble=[r"(?i)\bon\s+thursday\s+works\b"],
         note="round 1 measured nothing between 8 and 16 words"),
    Case("NEG-4b rhetorical 'no, wait' mid-sentence (comma)",
         "I thought the deploy would take an hour, but no, wait until you see this, it took six.",
         must_contain=["six", "an hour"],
         must_match=[r"(?i)\bno,?\s+wait\s+until\s+you\s+see\s+this\b"],
         note="NEG-4 as Whisper usually punctuates it, with a comma for the dash"),
    Case("VERB-1 replacement brings its own verb (cross-sentence)",
         "Email the draft to Priya. No, wait, just Slack it to her instead.",
         must_contain=["Slack it to"],
         must_not_contain=["Email the draft"],
         must_not_match=[r"(?i)\bno,?\s+wait\b"],
         garble=[r"(?i)\bto\s+(?:just\s+)?slack\b"],
         note="keeping the lead-in is wrong here ('Email the draft to just Slack it'). "
              "A correct answer is 5 or 6 words of 13, so the guard pastes the raw "
              "words: here the best final result is a fail that loses nothing"),
    Case("VERB-2 'scratch that' replaces the whole plan",
         "Let's meet on Tuesday. Actually, scratch that, let's just do a quick call.",
         must_contain=["quick call"],
         must_not_contain=["Tuesday", "scratch that"],
         garble=[r"(?i)\bon\s+(?:let['’]s|just\s+do)\b"],
         note="keeping the lead-in is wrong here ('Let's meet on let's just do...'). "
              "A correct answer is 6 words of 13, so the guard pastes the raw words: "
              "here the best final result is a fail that loses nothing"),
    Case("EMAIL-1 fillers inside an email (no correction)",
         "Hi Sam. Um, so, yeah, for the review, can we do Thursday at two? "
         "I'll bring the, uh, updated numbers. Cheers, James.",
         must_contain=["Thursday at two?", "updated numbers", "review"],
         must_match=[r"^Hi Sam,[ \t]*\n[ \t]*\n",
                     r"\n[ \t]*\n[ \t]*Cheers,[ \t]*\n[ \t]*James\b"],
         garble=[r"(?i)\bthe,\s*updated\b"],
         note="round 1 pasted \"I'll bring the, updated numbers.\" in 5 of 10 runs "
              "(0 of 10 on the old prompt)"),
]


# ─── LOSS check: the words a correct output may leave out ─────────────────
# Keyed by case id (the label's first word). Every OTHER word of the
# transcript, fillers aside, must appear somewhere in the output or the run
# fails with "LOSS:". Only the retracted values and the correction markers
# are listed; [] means keep every word (the negative controls). Comparison is
# by word, case-insensitive, with contractions reduced to their first word
# ("I'll" -> "I") and number words matched to digits ("two" == "2").
DROPPABLE: Dict[str, List[str]] = {
    "DAY-1": ["Tuesday", "sorry"],
    "DAY-2": ["Tuesday", "no", "wait", "at"],     # "at Monday" -> "on Monday" is fine
    "DAY-3": ["Tuesday"],
    "DAY-4": ["Tuesday", "I", "meant"],
    "DAY-5": ["Tuesday", "no"],
    "DAY-6": ["Tuesday"],
    "NAME-1": ["John", "sorry"],
    "NAME-2": ["Sarah", "no", "wait"],
    "NAME-3": ["Smith", "sorry"],
    "NUM-1": ["three", "no"],
    "NUM-2": ["£500", "sorry"],
    "NUM-3": ["twenty", "no", "about"],           # "about" hedged the retracted number
    "PLACE-1": ["boardroom", "sorry"],
    "MULTI-1": ["John", "sorry", "Tuesday", "no", "wait"],
    "MULTI-2": ["Tuesday", "no", "Wednesday"],
    "STRUCT-1": ["number", "one", "two", "John", "sorry", "Tuesday", "no"],
    "STRUCT-2": ["Tuesday", "sorry"],
    "STRUCT-3": ["three", "sorry"],
    "ABAND-1": ["we", "should", "let", "me", "start", "over"],
    "ABAND-2": ["thing", "about", "what", "I", "trying", "to", "say"],
    "NEG-1": [], "NEG-2": [], "NEG-3": [], "NEG-4": [],
    "LEAD-1": ["Tuesday", "no", "wait", "Wednesday"],
    "LEAD-2": ["Tuesday", "no", "wait", "Wednesday"],
    "LEAD-3": ["Monday", "sorry"],
    "LEAD-4": ["six", "no", "wait", "seven"],
    "LEAD-5": ["five", "no", "six", "let's", "say"],
    "LEAD-6": ["Sarah", "sorry"],
    "LEAD-7": ["Monday", "no", "wait"],
    "LEAD-8": ["John", "sorry"],
    "LEAD-9": ["14th", "sorry"],
    "NEG-LEAD-1": [], "NEG-LEAD-1b": [], "NEG-LEAD-2": [],
    "NEG-LEAD-3": [], "NEG-LEAD-3b": [],
    "NEG-LEAD-4": [], "NEG-LEAD-5": [], "NEG-LEAD-6": [], "NEG-4b": [],
    # A correct answer here rewrites the frame, so only the new plan's words
    # are required; GARBLE and must_not_contain catch the broken merges.
    "VERB-1": ["email", "the", "draft", "Priya", "her", "no", "wait", "just", "instead"],
    "VERB-2": ["meet", "on", "Tuesday", "scratch", "that"],
    "EMAIL-1": [],
}

# ─── LOSS check: a retraction deleted, the retracted value kept ──────────
# An answer that deletes every correction marker but keeps a value the
# speaker took back reads as if both stand ("The quote was £500, £750 for
# the whole package.", "Let's meet on Tuesday. Let's just do a quick call.").
# The words as spoken were clearer, so that run fails as LOSS too. Listed:
# the values each case takes back. Not listed: the negative controls, and
# cases with no spoken marker (DAY-6, ABAND-1/2).
RETRACTED: Dict[str, List[str]] = {
    "DAY-1": ["Tuesday"], "DAY-2": ["Tuesday"], "DAY-3": ["Tuesday"],
    "DAY-4": ["Tuesday"], "DAY-5": ["Tuesday"],
    "NAME-1": ["John"], "NAME-2": ["Sarah"], "NAME-3": ["Smith"],
    "NUM-1": ["three"], "NUM-2": ["£500"], "NUM-3": ["twenty"],
    "PLACE-1": ["boardroom"],
    "MULTI-1": ["John", "Tuesday"], "MULTI-2": ["Tuesday", "Wednesday"],
    "STRUCT-1": ["John", "Tuesday"], "STRUCT-2": ["Tuesday"], "STRUCT-3": ["three"],
    "LEAD-1": ["Tuesday", "Wednesday"], "LEAD-2": ["Tuesday", "Wednesday"],
    "LEAD-3": ["Monday"], "LEAD-4": ["six", "seven"], "LEAD-5": ["five", "six"],
    "LEAD-6": ["Sarah"], "LEAD-7": ["Monday"], "LEAD-8": ["John"], "LEAD-9": ["14th"],
    "VERB-1": ["email"], "VERB-2": ["Tuesday"],
}
# Anything that still tells the reader a value was taken back.
_MARKERS = (r"\bsorry\b", r"\bno\b", r"\bwait\b", r"\bactually\b", r"\bhmm\b",
            r"\bi\s+mean", r"\bscratch\s+that\b", r"\binstead\b", r"\bstart\s+over\b")

# Words a clean-up may delete without losing anything: the prompt's FILLER
# "REMOVE" list, plus "actually" (NEG-2: kept or dropped, both fine).
_FILLER_WORDS = {
    "um", "uh", "uhm", "erm", "er", "ah", "hmm", "mm", "so", "yeah", "well",
    "right", "okay", "ok", "alright", "basically", "literally", "actually",
}
_FILLER_PHRASES = ("you know", "i mean", "sort of", "kind of")
_NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUMBER_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
                      "seventy": "70", "eighty": "80", "ninety": "90"})

# Stray punctuation, checked on every case. Each pattern carries a word of
# context, and a match that the transcript itself (or the guard's lightly
# cleaned paste of it) already contains is not counted: that is the
# speaker's own text, the same for every prompt, not damage done by the model.
_GLOBAL_GARBLE = (
    (r"\b(?:[Tt]he|[Aa]n|a),\s+[\w£$€'’]+", "stray comma after an article"),
    (r"[\w'’]+,\s*[,.;:!?]", "doubled punctuation"),
    (r"[\w'’]+[ \t]+[,.;:](?!\w)", "space before punctuation"),
    (r"(?m)^[ \t]*[,.;:!?]+[ \t]*[\w'’]*", "line starts with punctuation"),
)


def case_id(case: Case) -> str:
    return case.label.split()[0]


def _words(text: str, strip_fillers: bool = False) -> List[str]:
    """Lower-cased words for the LOSS check. Curly apostrophes are straightened,
    a contraction counts as its first word ("I'll" -> "i", "don't" -> "do not")
    and number words become digits, so "I will" or "at 2" is not a loss."""
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    if strip_fillers:
        for ph in _FILLER_PHRASES:
            t = re.sub(rf"\b{ph}\b", " ", t)
    out = []
    for tok in re.findall(r"[£$€]?[a-z0-9]+(?:'[a-z]+)?", t):
        if tok.endswith("n't"):
            stem = tok[:-3]
            out += [{"ca": "can", "wo": "will", "sha": "shall"}.get(stem, stem), "not"]
            continue
        base = tok.split("'")[0]
        out.append(_NUMBER_WORDS.get(base, base))
    if strip_fillers:
        out = [w for w in out if w not in _FILLER_WORDS]
    return out


def _has_marker(text: str) -> bool:
    t = (text or "").lower().replace("’", "'")
    return any(re.search(p, t) for p in _MARKERS)


def loss_failures(case: Case, styled: str) -> List[str]:
    """Words of the transcript missing from the output, fillers and the case's
    DROPPABLE words aside (a case with no DROPPABLE entry is not checked), and
    a retraction deleted while the value it took back stays (RETRACTED)."""
    fails = []
    have = set(_words(styled))
    droppable = DROPPABLE.get(case_id(case))
    if droppable is not None:
        allowed = set(_words(" ".join(droppable)))
        need = [w for w in dict.fromkeys(_words(case.raw, strip_fillers=True))
                if w not in allowed]
        missing = [w for w in need if w not in have]
        if missing:
            fails.append("LOSS: dropped " + ", ".join(repr(w) for w in missing))
    kept = [v for v in RETRACTED.get(case_id(case), [])
            if all(w in have for w in _words(v))]
    if kept and _has_marker(case.raw) and not _has_marker(styled):
        fails.append("LOSS: dropped every correction marker but kept "
                     + ", ".join(repr(v) for v in kept) + ", the value it took back")
    return fails


def _guard_paste(raw: str) -> str:
    """What the truncation guard pastes: OpenAIStyler._basic_clean(raw). It
    reads nothing from the instance, so no styler (or key) is needed."""
    return OpenAIStyler._basic_clean(None, raw)


def garble_failures(case: Case, styled: str) -> List[str]:
    spoken = (case.raw, _guard_paste(case.raw))
    fails = []
    for pat, why in _GLOBAL_GARBLE:
        for m in re.finditer(pat, styled):
            if not any(m.group(0) in s for s in spoken):
                fails.append(f"GARBLE: {why} ({m.group(0)!r})")
                break
    fails += [f"GARBLE: matched {pat!r}" for pat in case.garble
              if re.search(pat, styled, re.MULTILINE)]
    return fails


def self_check(cases: List[Case]) -> List[str]:
    """The damage checks must never fire on the speaker's own words: pasting
    the transcript, or the guard's lightly cleaned copy of it, loses and
    garbles nothing. A LOSS or GARBLE here is a bug in the check itself."""
    problems = []
    for c in cases:
        for name, text in (("transcript", c.raw), ("guard paste", _guard_paste(c.raw))):
            bad = [f for f in loss_failures(c, text) + garble_failures(c, text)]
            if bad:
                problems.append(f"{case_id(c)}: the {name} itself fails: {bad}")
    return problems


def _norm_exact(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'")).strip()


def is_loss(failures: List[str]) -> bool:
    return any(f.startswith("LOSS:") for f in failures)


def is_garble(failures: List[str]) -> bool:
    return any(f.startswith("GARBLE:") for f in failures)


def evaluate(case: Case, styled: str) -> List[str]:
    failures = []
    s_lower = styled.lower()
    for needle in case.must_contain:
        if needle.lower() not in s_lower:
            failures.append(f"missing: {needle!r}")
    for needle in case.must_not_contain:
        if needle.lower() in s_lower:
            failures.append(f"contains forbidden: {needle!r}")
    for pat in case.must_match:
        if not re.search(pat, styled, re.MULTILINE):
            failures.append(f"missing pattern: {pat!r}")
    for pat in case.must_not_match:
        if re.search(pat, styled, re.MULTILINE):
            failures.append(f"forbidden pattern matched: {pat!r}")
    if case.exact is not None and _norm_exact(styled) != _norm_exact(case.exact):
        failures.append(f"not exactly {case.exact!r}")
    failures += loss_failures(case, styled)
    failures += garble_failures(case, styled)
    return failures


# ── Harness plumbing ─────────────────────────────────────────────────────────

_SECRET_ENV = ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY", "ELEVENLABS_API_KEY")


def _scrub(text) -> str:
    """Belt and braces: never let a key value reach stdout or the JSON."""
    s = "" if text is None else str(text)
    for name in _SECRET_ENV:
        val = os.environ.get(name, "")
        if len(val) >= 8 and val in s:
            s = s.replace(val, f"[{name} REDACTED]")
    return s


def load_prompt_file(path: str) -> str:
    """Read a candidate prompt exactly the way OpenAIStyler._load_prompt_template
    reads prompts/<style>.txt (text mode, UTF-8), so a candidate is decoded
    identically to how the app would decode it once it replaces
    prompts/normal.txt. Before 3.14.100 both used the platform default, which
    on Windows turned every em-dash into mojibake. Fails fast on a template
    .format() would reject."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        text.format(transcript="x", dialect_instruction="y")
    except (KeyError, IndexError, ValueError) as e:
        sys.exit(f"--prompt-file {path}: not a valid template for str.format ({e!r}); "
                 "it needs {transcript} and {dialect_instruction} and no other bare braces")
    for ph in ("{transcript}", "{dialect_instruction}"):
        if ph not in text:
            sys.exit(f"--prompt-file {path}: missing placeholder {ph}")
    return text


def _distinct(values):
    """[(value, count)] in first-seen order."""
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return list(out.items())


def run_once(styler, case: Case, captured: dict, retries: int, log):
    """Style one transcript. Returns a per-run dict. Retries infrastructure
    failures (every provider failed / rate limited), never content failures."""
    attempt = 0
    while True:
        attempt += 1
        captured.clear()
        # A 429 parks the provider for a cooldown; reset so a retry really
        # asks the model again instead of pasting basic_clean.
        styler._groq_skip_until = 0.0
        styler._cerebras_skip_until = 0.0
        buf = io.StringIO()
        t0 = time.time()
        err = None
        final, usage = "", {}
        try:
            with contextlib.redirect_stdout(buf):
                final, usage = styler.style(case.raw)
        except Exception as e:  # pragma: no cover - live harness
            err = f"exception: {e}"
        ms = (time.time() - t0) * 1000
        usage = usage or {}
        if not err and usage.get("fallback_reason"):
            err = f"all providers failed: {usage.get('fallback_reason')}"
        if err and attempt <= retries:
            wait = 10 * attempt
            log(f"      retry {attempt}/{retries} in {wait}s ({_scrub(err)[:90]})")
            time.sleep(wait)
            continue
        break

    model_called = "model_output" in captured
    guard_reason = usage.get("truncation_guard") if model_called else None
    if err:
        path = "error"
    elif not model_called:
        path = "simple"          # _is_simple shortcut: no model call at all
    elif guard_reason:
        path = "guarded"         # model answered, guard refused it, raw pasted
    else:
        path = "model"

    rec = {
        "path": path,
        "provider": (captured.get("model_usage") or {}).get("provider") or usage.get("provider"),
        "final_output": _scrub(final),
        "guard_reason": guard_reason,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "ms": round(ms),
        "attempts": attempt,
    }
    if model_called:
        model_out = captured["model_output"] or ""
        # What would have been pasted had the guard NOT tripped: the same
        # deterministic post-passes style() applies after the guard.
        unguarded = styler._format_email_layout(
            styler._restore_dropped_signoff(model_out, case.raw))
        rec.update({
            "model_output": _scrub(model_out),
            "unguarded_output": _scrub(unguarded),
            "finish_reason": (captured.get("model_usage") or {}).get("finish_reason"),
        })
    else:
        rec.update({"model_output": None, "unguarded_output": None})
    score_record(case, rec, err)
    log_lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    if log_lines:
        rec["styler_log"] = _scrub("\n".join(log_lines))[-600:]
    return rec


def score_record(case: Case, rec: dict, err: Optional[str] = None) -> dict:
    """(Re)score one run from its recorded final_output / unguarded_output, so
    --rescore applies exactly the checks a live run does."""
    fails = [err] if err else evaluate(case, rec.get("final_output") or "")
    rec["pass"] = not fails
    rec["failures"] = [_scrub(f) for f in fails]
    rec["loss"] = is_loss(fails)
    rec["garble"] = is_garble(fails)
    unguarded = rec.get("unguarded_output")
    if unguarded is None:
        rec.update({"model_correct": None, "model_failures": [],
                    "model_loss": None, "model_garble": None})
    else:
        mfails = evaluate(case, unguarded)
        rec.update({"model_correct": not mfails, "model_failures": mfails,
                    "model_loss": is_loss(mfails), "model_garble": is_garble(mfails)})
    return rec


def _case_row(i: int, case: Case, recs: list, width: int) -> str:
    n = len(recs)
    p = sum(1 for x in recs if x["pass"])
    m_runs = [x for x in recs if x["model_correct"] is not None]
    m_ok = sum(1 for x in m_runs if x["model_correct"])
    g = sum(1 for x in recs if x["path"] == "guarded")
    lo = sum(1 for x in recs if x.get("loss"))
    ga = sum(1 for x in recs if x.get("garble"))
    paths = ",".join(f"{k}x{v}" if v > 1 else k for k, v in _distinct([x["path"] for x in recs]))
    mcol = f"{m_ok}/{len(m_runs)}" if m_runs else "n/a"
    ms = sum(x.get("ms") or 0 for x in recs) / n
    return (f"{i:<3} {case.label:<{width}} {f'{p}/{n}':<7} {mcol:<7} {g:<6} {lo:<5} {ga:<6} "
            f"{paths}  ({ms:.0f}ms avg)")


def _header(width: int) -> str:
    return f"{'#':<3} {'LABEL':<{width}} FINAL   MODEL   GUARD  LOSS  GARBLE PATHS"


def _case_summary(recs: list) -> dict:
    model_runs = [x for x in recs if x["model_correct"] is not None]
    return {
        "n_runs": len(recs),
        "runs_pass": sum(1 for x in recs if x["pass"]),
        "model_runs": len(model_runs),
        "model_correct": sum(1 for x in model_runs if x["model_correct"]),
        "guard_trips": sum(1 for x in recs if x["path"] == "guarded"),
        "simple_path_runs": sum(1 for x in recs if x["path"] == "simple"),
        # Silent damage on what was pasted, and in the model's own answer.
        "runs_loss": sum(1 for x in recs if x.get("loss")),
        "runs_garble": sum(1 for x in recs if x.get("garble")),
        "runs_loss_or_garble": sum(1 for x in recs if x.get("loss") or x.get("garble")),
        "model_loss": sum(1 for x in model_runs if x.get("model_loss")),
        "model_garble": sum(1 for x in model_runs if x.get("model_garble")),
    }


def _rescore(path: str, cases: List[Case], log):
    """Re-score a --json file's recorded outputs with the current checks.
    A case whose transcript has changed since the recording is skipped."""
    src = json.loads(Path(path).read_text(encoding="utf-8"))
    by_id = {case_id(c): c for c in cases}
    results = []
    for rc in src.get("cases", []):
        case = by_id.get(rc["label"].split()[0])
        if case is None:
            continue
        if rc.get("raw") != case.raw:
            log(f"skipped {case_id(case)}: its transcript changed since {path}")
            continue
        recs = []
        for r in rc.get("runs", []):
            rec = dict(r)
            err = None
            if rec.get("path") == "error":
                err = (rec.get("failures") or ["error"])[0]
            recs.append(score_record(case, rec, err))
        if recs:
            results.append((case, recs))
    return src.get("meta", {}), results


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Waffler self-correction harness")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between calls (default 1)")
    ap.add_argument("--filter", type=str, default=None,
                    help="only labels containing this substring (case-insensitive)")
    ap.add_argument("--only", type=str, default=None,
                    help="only labels matching this regex (case-insensitive)")
    ap.add_argument("--runs", type=int, default=1, help="runs per case (default 1)")
    ap.add_argument("--prompt-file", type=str, default=None,
                    help="evaluate this prompt template instead of prompts/normal.txt")
    ap.add_argument("--json", type=str, default=None, help="write per-run detail here")
    ap.add_argument("--rescore", type=str, default=None,
                    help="no model calls: re-score the outputs recorded in this --json "
                         "file with the current checks")
    ap.add_argument("--provider", type=str, default=None,
                    choices=["groq", "cerebras", "openai"],
                    help="pin to one provider, no fallback (default: the app's order)")
    ap.add_argument("--force-llm", action="store_true",
                    help="bypass the _is_simple shortcut so every case reaches the model")
    ap.add_argument("--retries", type=int, default=2,
                    help="retries per run when every provider fails (default 2)")
    ap.add_argument("--max-cost", type=float, default=0.50,
                    help="stop before estimated spend passes this many USD (default 0.50)")
    ap.add_argument("--price-in", type=float, default=0.15,
                    help="USD per million input tokens (default 0.15, gpt-oss-120b on Groq)")
    ap.add_argument("--price-out", type=float, default=0.60,
                    help="USD per million output tokens (default 0.60)")
    args = ap.parse_args()

    cases = [c for c in CORPUS
             if (not args.filter or args.filter.lower() in c.label.lower())
             and (not args.only or re.search(args.only, c.label, re.IGNORECASE))]
    if not cases:
        print("no cases matched")
        return
    problems = self_check(cases)
    if problems:
        sys.exit("a damage check fires on the speaker's own words; fix the case first:\n  "
                 + "\n  ".join(problems))

    def log(msg):
        print(msg, flush=True)

    tok_in = tok_out = calls = 0

    def spent():
        return (tok_in * args.price_in + tok_out * args.price_out) / 1_000_000

    results = []
    stopped = None

    if args.rescore:
        src_meta, results = _rescore(args.rescore, cases, log)
        if not results:
            print(f"nothing in {args.rescore} matches the selected cases")
            return
        # Carry the recorded usage over, so a re-scored file still says what
        # the run cost when it was made.
        rescored = [x for _, recs in results for x in recs]
        tok_in = sum(int(x.get("input_tokens") or 0) for x in rescored)
        tok_out = sum(int(x.get("output_tokens") or 0) for x in rescored)
        calls = sum(1 for x in rescored if x.get("input_tokens"))
        width = max(len(c.label) for c, _ in results)
        runs = max(len(recs) for _, recs in results)
        prompt_sha = src_meta.get("prompt_sha256_12")
        prompt_src = src_meta.get("prompt_source")
        force_llm = bool(src_meta.get("force_llm"))
        provider = src_meta.get("provider")
        groq_model = src_meta.get("groq_model")
        log(f"\nRe-scoring {len(results)} cases recorded in {args.rescore} (no model calls)")
        log(f"prompt: {prompt_src}  sha256:{prompt_sha}  provider: {provider}"
            f"{'  FORCE-LLM' if force_llm else ''}")
        log(_header(width))
        log("─" * (width + 66))
        for i, (case, recs) in enumerate(results, 1):
            log(_case_row(i, case, recs, width))
    else:
        styler = OpenAIStyler(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            groq_api_key=os.environ.get("GROQ_API_KEY", ""),
            cerebras_api_key=os.environ.get("CEREBRAS_API_KEY", "") if args.provider == "cerebras" else "",
            provider_order=[args.provider] if args.provider else None,
        )
        if args.provider:
            # _normalize_provider_order appends the missing providers; re-pin.
            styler._provider_order = [args.provider]
        if args.prompt_file:
            styler.prompt_template = load_prompt_file(args.prompt_file)
        if args.force_llm:
            styler._is_simple = lambda _t: False

        # Capture the model's answer BEFORE the truncation guard can replace it.
        captured: dict = {}
        _orig_guard = styler._guard_truncation

        def _spy_guard(styled, usage, transcript):
            captured["model_output"] = styled
            captured["model_usage"] = dict(usage or {})
            return _orig_guard(styled, usage, transcript)

        styler._guard_truncation = _spy_guard

        runs = max(1, args.runs)
        prompt_sha = hashlib.sha256(styler.prompt_template.encode("utf-8")).hexdigest()[:12]
        prompt_src = args.prompt_file or f"prompts/{styler.prompt_style}.txt (default)"
        force_llm = args.force_llm
        provider = args.provider or "app order"
        groq_model = getattr(styler, "_groq_model", None)
        width = max(len(c.label) for c in cases)

        log(f"\nRunning {len(cases)} self-correction cases x {runs} run(s)  delay={args.delay}s")
        log(f"prompt: {prompt_src}  sha256:{prompt_sha}  provider: {provider}"
            f"{'  FORCE-LLM' if force_llm else ''}")
        log(_header(width))
        log("─" * (width + 66))

        for i, case in enumerate(cases, 1):
            recs = []
            for r in range(runs):
                if spent() >= args.max_cost:
                    stopped = f"stopped: estimated spend ${spent():.4f} reached --max-cost ${args.max_cost:.2f}"
                    break
                rec = run_once(styler, case, captured, args.retries, log)
                rec["run"] = r + 1
                recs.append(rec)
                tok_in += rec["input_tokens"]
                tok_out += rec["output_tokens"]
                if rec["input_tokens"]:
                    calls += 1
                if rec["path"] != "simple":
                    time.sleep(args.delay)
            if recs:
                log(_case_row(i, case, recs, width))
                results.append((case, recs))
            if stopped:
                break

    # ── Totals ────────────────────────────────────────────────────────────
    all_recs = [x for _, recs in results for x in recs]
    tot = len(all_recs)
    tot_pass = sum(1 for x in all_recs if x["pass"])
    cases_all_pass = sum(1 for _, recs in results if all(x["pass"] for x in recs))
    m_all = [x for x in all_recs if x["model_correct"] is not None]
    m_ok_all = sum(1 for x in m_all if x["model_correct"])
    guards = sum(1 for x in all_recs if x["path"] == "guarded")
    simple = sum(1 for x in all_recs if x["path"] == "simple")
    errors = sum(1 for x in all_recs if x["path"] == "error")
    f_loss = sum(1 for x in all_recs if x.get("loss"))
    f_garble = sum(1 for x in all_recs if x.get("garble"))
    f_harm = sum(1 for x in all_recs if x.get("loss") or x.get("garble"))
    m_loss = sum(1 for x in m_all if x.get("model_loss"))
    m_garble = sum(1 for x in m_all if x.get("model_garble"))
    non_pinned = sorted({str(x.get("provider")) for x in all_recs
                         if x["path"] in ("model", "guarded") and x.get("provider") != "groq"})

    log(f"\n{'─' * (width + 66)}")
    log(f"PASSED {cases_all_pass}/{len(results)} cases (every run passed on the final pasted output)")
    if tot:
        log(f"final-output pass rate: {tot_pass}/{tot} runs ({tot_pass / tot:.0%})")
        log(f"silent damage pasted: {f_harm}/{tot} runs lost or garbled words "
            f"(LOSS {f_loss}, GARBLE {f_garble})")
    if m_all:
        log(f"model-correct rate (before the guard): {m_ok_all}/{len(m_all)} model runs "
            f"({m_ok_all / len(m_all):.0%});  in the model's answer: LOSS {m_loss}, GARBLE {m_garble}")
    log(f"guard trips: {guards}   simple-path runs (no model call): {simple}   errors: {errors}")
    if non_pinned:
        log(f"NOTE: some runs were answered by {', '.join(non_pinned)}, not groq "
            f"(pass --provider groq to pin)")
    log(f"tokens: {tok_in} in / {tok_out} out over {calls} model calls  "
        f"est. cost ${spent():.4f} (at ${args.price_in}/M in, ${args.price_out}/M out)"
        + ("  [as recorded; re-scoring spent nothing]" if args.rescore else ""))
    if stopped:
        log(f"*** {stopped} (results are partial) ***")

    # ── Failure details ───────────────────────────────────────────────────
    # A case is listed when any pasted output failed OR the model itself got it
    # wrong (even if the guard then saved the final paste).
    failing = [(c, recs) for c, recs in results
               if not all(x["pass"] for x in recs)
               or any(x["model_correct"] is False for x in recs)]
    if failing:
        log("\n=== FAILURE DETAILS ===")
        for case, recs in failing:
            n = len(recs)
            p = sum(1 for x in recs if x["pass"])
            m_runs = [x for x in recs if x["model_correct"] is not None]
            m_ok = sum(1 for x in m_runs if x["model_correct"])
            mtxt = f"  model-correct {m_ok}/{len(m_runs)}" if m_runs else ""
            log(f"\n[{case.label}]  final {p}/{n}{mtxt}  {case.note}")
            log(f"  raw:    {case.raw}")
            fr = {x["final_output"]: x for x in recs}
            for out, cnt in _distinct([x["final_output"] for x in recs]):
                x = fr[out]
                tag = "PASS" if x["pass"] else "FAIL"
                log(f"  pasted x{cnt} [{tag}, {x['path']}]: {out!r}")
                for f in x["failures"]:
                    log(f"      - {f}")
            guarded = [x for x in recs if x["path"] == "guarded"]
            if guarded:
                gr = {x["model_output"]: x for x in guarded}
                for out, cnt in _distinct([x["model_output"] for x in guarded]):
                    x = gr[out]
                    ok = "model-correct" if x["model_correct"] else "model-wrong"
                    log(f"  model [{ok}] (refused by guard, {x['guard_reason']}) x{cnt}: {out!r}")
                    for f in x["model_failures"]:
                        log(f"      - {f}")

    if args.json:
        payload = {
            "meta": {
                "when": datetime.now().isoformat(timespec="seconds"),
                "argv": sys.argv[1:],
                "prompt_source": prompt_src,
                "prompt_sha256_12": prompt_sha,
                "provider": provider,
                "groq_model": groq_model,
                "force_llm": force_llm,
                "runs": runs,
                "stopped": stopped,
                "rescored_from": args.rescore,
            },
            "totals": {
                "cases": len(results),
                "cases_all_runs_pass": cases_all_pass,
                "runs": tot,
                "runs_pass": tot_pass,
                "model_runs": len(m_all),
                "model_correct": m_ok_all,
                "guard_trips": guards,
                "simple_path_runs": simple,
                "errors": errors,
                "runs_loss": f_loss,
                "runs_garble": f_garble,
                "runs_loss_or_garble": f_harm,
                "model_loss": m_loss,
                "model_garble": m_garble,
                "input_tokens": tok_in,
                "output_tokens": tok_out,
                "model_calls": calls,
                "est_cost_usd": round(spent(), 5),
            },
            "cases": [
                dict({
                    "label": c.label,
                    "raw": c.raw,
                    "note": c.note,
                    "checks": {
                        "must_contain": c.must_contain,
                        "must_not_contain": c.must_not_contain,
                        "must_match": c.must_match,
                        "must_not_match": c.must_not_match,
                        "garble": c.garble,
                        "exact": c.exact,
                        "droppable": DROPPABLE.get(case_id(c)),
                    },
                }, **_case_summary(recs), runs=recs)
                for c, recs in results
            ],
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
        log(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
