"""Capitals and punctuation for a meeting's transcript lines. Never a word.

The speech model writes fast, running speech with no capitals and no full
stops ("this is yeah so sorry so going back to it then this is a wrap-up").
Once a meeting is written down, and before its notes are written, whatever
writes the notes is asked to add them (Service._enhance). The iPhone app does
the same, to the same rules, so the request and the check are fixed here.

No reply is trusted. Every returned line is compared with the line it was made
from (`same_words`): the letters and digits, case folded, must be identical.
Then only capitals, punctuation, hyphens, apostrophes and spacing can have
changed, and the line is stored. Anything else, and the line stays exactly as
it was: a missing number, an extra line, lines merged or split, a reply that
is not numbered lines at all. `tidy` never raises: a writer that fails changes
nothing and the meeting goes on to its notes.
"""
from __future__ import annotations

import re
import unicodedata

try:
    from log_util import log as _log            # the app's own copy: src/ is on its path
except ImportError:
    from src.log_util import log as _log

SYSTEM = (
    "You add capital letters and punctuation to a transcript. You never add, remove, reorder, "
    "translate or respell a word, and you never change a number. Keep every line: the same lines, "
    "in the same order, with the same numbers. A sentence may run on from one line to the next; "
    "then the first line does not end with a full stop and the next does not start with a capital. "
    "Reply with the numbered lines and nothing else.")
# The line before a request's first, for a sentence that runs on. Not numbered,
# not to be returned.
CONTEXT = "Earlier, for context only: "

MIN_LETTERS = 25        # a line of this many letters or fewer is left alone
MAX_LINES = 40          # in one request
MAX_CHARS = 4000        # of transcript in one request, about

_NUMBERED = re.compile(r"^\s*(\d{1,4})\s*:\s?(.*)$")
# A number as it is written: its digits, with the marks inside it (1,500.50,
# 5:30, 14/01). A full stop or comma after it belongs to the sentence.
_NUMBER = re.compile(r"\d+(?:[.,:/]\d+)*")
# Punctuation that is said aloud or is part of a name, not of a sentence.
_NOT_SENTENCE_MARKS = "%‰&@#*_/\\"


def needs_tidying(text: str) -> bool:
    """More than MIN_LETTERS letters, and either none of . , ? ! or a small
    letter to start with."""
    if sum(ch.isalpha() for ch in text) <= MIN_LETTERS:
        return False
    return not any(ch in ".,?!" for ch in text) or text[:1].islower()


def words_of(text: str) -> str:
    """A line's letters and digits and nothing else, case folded: what may
    never change."""
    return "".join(ch for ch in text if ch.isalnum()).casefold()


def _all_but_marks(text: str) -> str:
    """Everything in a line but its spaces and sentence punctuation. Besides
    the letters and digits that is: an accent written as its own character
    (e + U+0301), the vowel signs of Hindi, Arabic or Thai (marks, not
    letters, and they make the word), and signs such as £ and %."""
    return "".join(ch for ch in text if not ch.isspace() and (
        unicodedata.category(ch)[0] != "P" or ch in _NOT_SENTENCE_MARKS)).casefold()


def same_words(before: str, after: str) -> bool:
    """Did only capitals, punctuation, hyphens, apostrophes and spacing change?

    The rule is the first line: the same letters and digits. The other two
    only ever refuse more. Without them "की" could become "कि" (one vowel
    sign), "cafe" could gain an accent written as a separate mark, and "15"
    could become "1.5", "£15", "15%" or, from "1 5", "15"."""
    return (words_of(before) == words_of(after)
            and _all_but_marks(before) == _all_but_marks(after)
            and _NUMBER.findall(before) == _NUMBER.findall(after))


def batches(lines: list[dict]) -> list[list[list[dict]]]:
    """The lines as they are asked about: for each source in turn (a sentence
    runs on only within one: the microphone, or the computer's sound), its
    lines in order, MAX_LINES and about MAX_CHARS to a request. A line is
    never cut: one longer than MAX_CHARS goes alone."""
    by_source: dict[str, list[dict]] = {}
    for line in lines:
        by_source.setdefault(line.get("source") or "mic", []).append(line)
    out = []
    for source_lines in by_source.values():
        group, batch, size = [], [], 0
        for line in source_lines:
            n = len(line["text"])
            if batch and (len(batch) >= MAX_LINES or size + n > MAX_CHARS):
                group.append(batch)
                batch, size = [], 0
            batch.append(line)
            size += n
        if batch:
            group.append(batch)
        out.append(group)
    return out


def request_text(texts: list[str], context: str = "") -> str:
    """What the writer is sent: the lines, numbered from 1, under the line
    before them when there is one."""
    numbered = [f"{i}: {' '.join(text.split())}" for i, text in enumerate(texts, 1)]
    return "\n".join(([CONTEXT + context] if context else []) + numbered)


def read_reply(reply) -> dict[int, list[str]]:
    """The numbered lines of a reply: {number: every text given that number}.
    Whatever else is in it (a greeting, a code fence, a line with no number)
    is not a line."""
    found: dict[int, list[str]] = {}
    if isinstance(reply, str):
        for raw in reply.splitlines():
            m = _NUMBERED.match(raw)
            if m:
                found.setdefault(int(m.group(1)), []).append(" ".join(m.group(2).split()))
    return found


def checked(texts: list[str], reply) -> tuple[list[str], int]:
    """For each line sent, the line to keep: the writer's, when it is that
    line with nothing but capitals and punctuation changed; the original
    otherwise, exactly. And how many were refused (not given, given twice,
    or not the same words)."""
    found = read_reply(reply)
    out, refused = [], 0
    for i, before in enumerate(texts, 1):
        given = found.get(i, [])
        after = given[0] if len(given) == 1 else ""       # given twice: not guessed at
        ok = bool(after) and same_words(before, after)
        out.append(after if ok else before)
        refused += not ok
    return out, refused


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def tidy(lines: list[dict], write, save) -> dict:
    """Ask for capitals and punctuation on a meeting's lines and keep what
    passes the check.

    `lines` are the meeting's segments in transcript order ({"id", "source",
    "text"}); `write(system, user)` is one reply from the notes' writer;
    `save([(id, text before, text after)])` stores a request's accepted lines.
    When no line needs it, nobody is asked. The first failure, of the writer
    or of the store, ends the pass: what was stored stays, the rest is left
    as it was. Never raises. Returns the counts, which is also all the log
    gets: never a word of the transcript."""
    counts = {"lines": 0, "requests": 0, "changed": 0, "refused": 0, "stopped": ""}
    try:
        lines = [line for line in lines if isinstance(line.get("text"), str) and line["text"].strip()]
        counts["lines"] = len(lines)
        if not any(needs_tidying(line["text"]) for line in lines):
            return counts
        for group in batches(lines):
            context = ""
            for batch in group:
                texts = [line["text"] for line in batch]
                counts["requests"] += 1
                kept, refused = checked(texts, write(SYSTEM, request_text(texts, context)))
                changes = [(line["id"], before, after)
                           for line, before, after in zip(batch, texts, kept) if after != before]
                if changes:
                    save(changes)
                counts["changed"] += len(changes)
                counts["refused"] += refused
                context = kept[-1]
    except Exception as e:  # noqa: BLE001 - nothing here may fail a meeting
        counts["stopped"] = type(e).__name__
    if counts["requests"]:
        try:
            _log(f"[meetings] lines tidied: {_n(counts['lines'], 'line')}, "
                 f"{_n(counts['requests'], 'request')}, {counts['changed']} changed, "
                 f"{counts['refused']} refused"
                 + (f", stopped ({counts['stopped']})" if counts["stopped"] else ""))
        except Exception:  # noqa: BLE001
            pass
    return counts

