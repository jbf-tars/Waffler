"""Capitals and punctuation for a meeting's transcript lines. Never a word.

The speech model writes fast, running speech with no capitals and no full
stops ("this is yeah so sorry so going back to it then this is a wrap-up").
Once a meeting is written down, and before its notes are written, whatever
writes the notes is asked to add them (Service._enhance). The iPhone app does
the same, to the same rules, so the request and the checks are fixed here.

A stretch of lines goes as one running text, because that is what reads as
sentences: asked line by line, a model ends every line with a full stop. The
reply is cut back into the lines it came from by counting letters and digits
(`resplit`), so a line keeps its own words, times and place.

No reply is trusted. The whole reply must have the letters and digits that
were sent, case folded, and no others; then every line cut from it is compared
with the line it replaces (`same_words`). Only capitals, punctuation, hyphens,
apostrophes and spacing can have changed in a line that is stored. Anything
else, and the line stays exactly as it was. `tidy` never raises: a writer that
fails changes nothing and the meeting goes on to its notes.
"""
from __future__ import annotations

import re
import unicodedata

try:
    from log_util import log as _log            # the app's own copy: src/ is on its path
except ImportError:
    from src.log_util import log as _log

SYSTEM = (
    "You punctuate a transcript of someone speaking, as a careful editor would: full stops, "
    "commas, question marks and capital letters, so that it reads as proper sentences. You never "
    "add, remove, reorder, translate or respell a word, and you never change a number: every word "
    "stays, fillers and repeats included. Reply with the punctuated text and nothing else.")

MIN_LETTERS = 25        # a line of this many letters or fewer is left alone
# In one request. Small, because a reply with one changed word loses all of it.
MAX_LINES = 20
MAX_CHARS = 2500        # of transcript, about

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


def resplit(originals: list[str], tidied: str) -> list[str]:
    """The reply, cut back into the lines it was made from. Each line takes
    as many letters and digits as it had, then the punctuation that closes
    its last word, up to the next space; whatever opens the next word (a
    quote) goes with the next line.

    A line never takes a letter or digit more than it had, so a word the
    writer moved costs the two lines it lies between and no others, and text
    written without spaces (Japanese, Chinese, Thai) is cut at its lines too."""
    out, pos = [], 0
    for text in originals:
        need = sum(ch.isalnum() for ch in text)
        start = pos
        while pos < len(tidied) and need:
            need -= tidied[pos].isalnum()
            pos += 1
        while pos < len(tidied) and not (tidied[pos].isspace() or tidied[pos].isalnum()
                                         or unicodedata.category(tidied[pos]) in ("Ps", "Pi")):
            pos += 1
        out.append(tidied[start:pos].strip())
    return out


# The English word that is a capital in the middle of a sentence too.
_I_WORD = re.compile(r"I(?:['\u2019]\w+)?(?!\w)")


# How far past where a line is expected it is still looked for, in letters
# and digits: room for a few words the writer added.
_SLACK = 200


def found_lines(originals: list[str], tidied: str) -> list[str]:
    """Each line, found in a reply that is not word for word what was sent.

    The lines are looked for in order: a line's letters and digits, case
    folded, as one unbroken run in the reply's, at or after the end of the
    line found before it. A line the writer changed is not there and comes
    back empty (it keeps its original), and the search for the next starts
    from the same place, so one changed word costs one line. What is found is
    cut as `resplit` cuts it: with the punctuation that closes its last word,
    and whatever opens its first. Each line still has to pass `same_words`."""
    keys, at = [], []                       # the reply's letters and digits, and where each is
    for i, ch in enumerate(tidied):
        if ch.isalnum():
            keys.append(ch.casefold())
            at.append(i)
    out, cursor, skipped = [], 0, 0
    for text in originals:
        want = [ch.casefold() for ch in text if ch.isalnum()]
        n = len(want)
        last = min(len(keys) - n, cursor + skipped + _SLACK)
        found = next((p for p in range(cursor, last + 1) if keys[p:p + n] == want), None) if n else None
        if found is None:
            out.append("")
            skipped += n
            continue
        start, end = at[found], at[found + n - 1] + 1
        while start > 0 and unicodedata.category(tidied[start - 1]) in ("Ps", "Pi"):
            start -= 1
        while end < len(tidied) and not (tidied[end].isspace() or tidied[end].isalnum()
                                         or unicodedata.category(tidied[end]) in ("Ps", "Pi")):
            end += 1
        out.append(tidied[start:end].strip())
        cursor, skipped = found + n, 0
    return out


def first_letter(tidied: str, original: str, earlier: str) -> str:
    """The first letter of a request's first line is ours to decide: the
    writer saw nothing before it and starts every reply with a capital. It is
    a capital when no line comes before it in its source, or the line before
    ends a sentence (. ? !); otherwise it is as the original line had it,
    except the word "I", which the writer is right to capitalise. Only the case of that one letter is ever changed."""
    at = next((i for i, ch in enumerate(tidied) if ch.isalnum()), None)
    if at is None:
        return tidied
    if not earlier or earlier.rstrip()[-1:] in (".", "?", "!"):
        want = tidied[at].upper()
    elif _I_WORD.match(tidied[at:]):
        return tidied            # "I", "I'm", "I'll": a capital wherever it stands
    else:
        want = next((ch for ch in original if ch.isalnum()), "")
    if len(want) == 1 and want.casefold() == tidied[at].casefold():
        return tidied[:at] + want + tidied[at + 1:]
    return tidied


def checked(texts: list[str], reply, earlier: str = "") -> tuple[list[str], int]:
    """For each line sent, the line to keep, and how many were refused.

    The reply is cut back into the lines it was made from (`resplit`). Where
    the writer changed, added or dropped a word the lines no longer fall
    where they did, so each line is looked for in the reply instead
    (`found_lines`) and the ones the change touched are not found. Either
    way a line is the writer's only when it is that line with nothing but
    capitals and punctuation changed; the original otherwise, exactly. `earlier` is the
    line before these in their source, as it is kept."""
    reply = " ".join(reply.split()) if isinstance(reply, str) else ""
    if not reply:
        return list(texts), len(texts)
    if words_of(" ".join(texts)) == words_of(reply):
        cut = resplit(texts, reply)
    else:
        cut = found_lines(texts, reply)     # a word was changed somewhere: keep what was not touched
    if cut[0]:
        cut[0] = first_letter(cut[0], texts[0], earlier)
    out, refused = [], 0
    for before, after in zip(texts, cut):
        ok = bool(after) and same_words(before, after)
        out.append(after if ok else before)
        refused += not ok
    return out, refused


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def tidy(lines: list[dict], write, save) -> dict:
    """Ask for capitals and punctuation on a meeting's lines and keep what
    passes the checks.

    `lines` are the meeting's segments in transcript order ({"id", "source",
    "text"}); `write(system, user)` is one reply from the notes' writer;
    `save([(id, text before, text after)])` stores a request's accepted lines.
    When no line needs it, nobody is asked. A reply that is refused costs its
    own lines and the pass goes on; the first failure, of the writer or of the
    store, ends it: what was stored stays, the rest is left as it was. Never
    raises. Returns the counts, which is also all the log gets: never a word
    of the transcript."""
    counts = {"lines": 0, "requests": 0, "changed": 0, "refused": 0, "stopped": ""}
    try:
        # A line with no letter or digit in it (a "..." or a music note) has
        # nothing to tidy and nothing to be counted by: it is not sent.
        lines = [line for line in lines
                 if isinstance(line.get("text"), str) and any(ch.isalnum() for ch in line["text"])]
        stored = {line["id"]: line["text"] for line in lines}
        lines = [{**line, "text": " ".join(line["text"].split())} for line in lines]
        counts["lines"] = len(lines)
        if not any(needs_tidying(line["text"]) for line in lines):
            return counts
        for group in batches(lines):
            earlier = ""
            for batch in group:
                texts = [line["text"] for line in batch]
                counts["requests"] += 1
                kept, refused = checked(texts, write(SYSTEM, " ".join(texts)), earlier)
                changes = [(line["id"], stored[line["id"]], after)
                           for line, before, after in zip(batch, texts, kept) if after != before]
                if changes:
                    save(changes)
                counts["changed"] += len(changes)
                counts["refused"] += refused
                earlier = kept[-1]
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
