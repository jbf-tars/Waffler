"""Styles as code: case, the last full stop, and the line break after a greeting.

The Style section of Settings lets each kind of app (personal messages, work
chat, email, everything else) have a style. The four styles are rules on the
text that is about to be pasted:

    formal       nothing: the text as the clean-up wrote it
    casual       a one-sentence message loses its last full stop
    very_casual  each sentence starts in lower case, the last full stop goes,
                 and a greeting is not put on a line of its own
    excited      the full stop of a short statement becomes an exclamation mark

They are code, not a request to a model, for three reasons. A short dictation
never reaches a model (src/style_openai.py ``_is_simple``), so a prompt line
would miss exactly the messages these styles are for. The same words would
come back with different punctuation from different providers. And a model
asked to "be casual" rewrites words, which a style must never do.

What a style may change
-----------------------
Only the case of the first letter of a sentence, the full stop at the end of
the last sentence (or of short statements, for excited), and the break after a
greeting line. Never a word, never anything inside quotation marks, never a
list item, a line of code, a web address, an email address, an acronym, a
word with a capital inside it (GitHub, McDonald's), a word in the user's
Vocabulary, or the English "I". When there is any doubt a rule does nothing:
under-styling is a missed touch, over-styling is a changed message.

Which text
----------
Languages written in the Latin script (the picker's list, below), and any text
that is mostly Latin letters when the language is Auto-detect. Not Chinese,
Japanese, Thai, Cyrillic, Arabic and the like (their full stops and case are
different things), and not a language code the picker does not know.

The functions are pure. ``apply_style`` is the one the pipeline calls,
``preview`` is what Settings shows as the example, ``eligible`` says whether
a text would be styled at all.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

try:
    from languages import is_unspaced as _is_unspaced
except ImportError:  # imported as src.style_post
    from src.languages import is_unspaced as _is_unspaced

AUTO = "auto"
FORMAL = "formal"
CASUAL = "casual"
VERY_CASUAL = "very_casual"
EXCITED = "excited"
STYLES = (FORMAL, CASUAL, VERY_CASUAL, EXCITED)

OTHER = "other"

# The picker's languages (src/languages.py), decided one way or the other.
LATIN_LANGUAGES = frozenset(
    "en es fr de it pt nl pl tr id ms vi sv no da fi cs sk hu ro hr sl lt lv et ca sw af cy".split())
OTHER_SCRIPT_LANGUAGES = frozenset(
    "ru uk ar he hi bn ur fa ja ko zh th bg el sr ta te".split())

# Excited: a "short" sentence, and how long a message may be before only its
# last sentence gets one.
EXCITED_SHORT_WORDS = 14
EXCITED_MESSAGE_WORDS = 50

# One sentence per kind of app: what Settings shows under each choice. Each
# is a single sentence, so "casual" has a full stop to drop.
SAMPLES = {
    "personal": "Sure, I'll be there at seven.",
    "work": "Thanks, I'll send the report this afternoon.",
    "email": "Thanks for the update, I'll review it today.",
    OTHER: "The new design is ready to look at.",
}


# ── Which text ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=512)
def _is_latin_letter(ch: str) -> bool:
    if not ch.isalpha():
        return False
    if ord(ch) < 0x250:
        return True
    return unicodedata.name(ch, "").startswith("LATIN")


def _language_code(language):
    """The language to style for: a Latin-script code, ``"auto"``, or None for
    one that is not styled (another script, or a code the picker does not
    have). No language at all is English, as everywhere else."""
    if language is None or language == "":
        return "en"
    if not isinstance(language, str):
        return None
    if language == AUTO or language in LATIN_LANGUAGES:
        return language
    return None


def eligible(text, language="en") -> bool:
    """True when ``text`` in ``language`` is styled: a Latin-script language,
    and text that is mostly Latin letters (so Auto-detect, or a language set
    wrongly, never styles Chinese or Russian)."""
    if _language_code(language) is None or not isinstance(text, str):
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters or _is_unspaced(text):
        return False
    return 2 * sum(1 for c in letters if _is_latin_letter(c)) > len(letters)


# ── Reading the text ─────────────────────────────────────────────────────────

BLANK, LIST, CODE, GREETING, PROSE = "blank", "list", "code", "greeting", "prose"

_LIST_RE = re.compile(
    r"^\s*(?:[-*•·‣◦▪–—]\s+\S|\d{1,3}[.)]\s+\S|[a-z]\)\s+\S)")
_GREETING_LINE = re.compile(
    r"^\s*(?:hi|hii|hiya|hey|heya|hello|dear|howdy|greetings|good\s+(?:morning|afternoon|evening|day))"
    r"\b[^,\n]{0,40},\s*$", re.I)

_CODE_START = re.compile(
    r"^\s*(?:\$ |>>> |// |/\*|#!|#include\b|import [\w.]+|from [\w.]+ import |def \w|class \w|"
    r"function \w|const \w|let \w|var \w|return\b|if \(|for \(|while \()")
_CODE_MARKS = re.compile(
    r"=>|->|==|!=|<=|>=|\+=|-=|\*=|::|&&|\|\||</?[A-Za-z][^>]{0,200}>|\$\(|\$\{|\\n|\\t|\{\s*\}|\[\s*\]")
_CODE_ASSIGN = re.compile(r"^\s*[A-Za-z_][\w.\[\]]*\s*[-+*/]?=\s*\S")
# Bounded runs: a long dotted or underscored word (a transcript has none, a model
# gone wrong might) must not make the search re-read it from every start.
_CODE_CALL = re.compile(
    r"\b(?:[A-Za-z_]\w{0,60}\.){1,6}[A-Za-z_]\w{0,60}\(|\b\w{1,60}_\w{1,60}\(|\b[a-z]{1,60}[A-Z]\w{0,60}\(|"
    r"\b[A-Za-z_]\w{0,60}\(\)")
_CODE_SYMBOLS = frozenset("{}[]<>=|\\^~_*#@$%&/")


def _looks_like_code(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if "`" in s or line.startswith(("    ", "\t")):
        return True
    if _CODE_START.match(line) or _CODE_ASSIGN.match(line) or _CODE_MARKS.search(s) or _CODE_CALL.search(s):
        return True
    if s[-1] in ";{}":
        return True
    return len(s) >= 8 and sum(1 for c in s if c in _CODE_SYMBOLS) * 5 > len(s)


# Words whose full stop belongs to them. A sentence does not end after one of
# these, and a last full stop that is one of them is not dropped. Left out on
# purpose: anything that is also a name (Max, Al, Jan, Jun) or a word (min, ref).
_ABBREVIATIONS = frozenset("""
    mr mrs ms mx dr prof sr jr st vs etc inc ltd co corp approx appt dept fig gen gov hon lt col capt sgt
    rev mt ft ave blvd rd misc vol cf feb apr jul aug sep sept oct nov dec
""".split())
_DOTTED_ABBREVIATION = re.compile(r"[a-z]{1,2}(?:\.[a-z]{1,2})+")


def _is_abbreviation(word: str) -> bool:
    w = word.lower().lstrip("(\"'“‘[")
    return w in _ABBREVIATIONS or bool(_DOTTED_ABBREVIATION.fullmatch(w))


_OPEN_TO_CLOSE = {'"': '"', "“": "”“", "„": "“”", "«": "»",
                  "‹": "›"}
_QUOTE_CLOSERS = frozenset("\"”“»›")
_CLOSERS = frozenset("\"'”’»›)]“")


def _quote_mask(text: str) -> list:
    """For each character, True when it is a quotation mark or inside a quoted
    span. A quote that is never closed covers the rest of the text. Apostrophes
    and single quotes are not read as quotation marks."""
    mask = [False] * len(text)
    i, n = 0, len(text)
    while i < n:
        closers = _OPEN_TO_CLOSE.get(text[i])
        if closers is None:
            i += 1
            continue
        j = i + 1
        while j < n and text[j] not in closers:
            j += 1
        end = min(j + 1, n)
        for k in range(i, end):
            mask[k] = True
        i = end
    return mask


@dataclass
class _Sentence:
    start: int              # first character
    end: int                # just past the last, closing marks included
    run: tuple = None       # (a, b) of the ./!/? run that ends it, or None
    last_start: int = 0     # first character of its last word
    last_boundary: bool = False   # a sentence follows on the same line


@dataclass
class _Line:
    start: int
    end: int                # just past the last non-space character
    kind: str
    cont: bool = False      # follows a line that ends in a comma


_TERMINATORS = re.compile(r"[.!?…]+")


def _word_start(text: str, a: int, lo: int) -> int:
    k = a
    while k > lo and not text[k - 1].isspace():
        k -= 1
    return k


def _sentences(text: str, line: _Line, mask: list) -> list:
    """The sentences of one line. A sentence ends at ``.``, ``!`` or ``?``
    followed by a space and the next sentence; not at an abbreviation, an
    initial, an ellipsis, a stop before a lower-case word, or one inside a
    quotation."""
    out = []
    s = line.start
    while s < line.end and text[s].isspace():
        s += 1
    if s >= line.end:
        return out
    pos = s
    for m in _TERMINATORS.finditer(text, line.start, line.end):
        a, b = m.span()
        if a < pos:
            continue
        if mask[a]:
            # Inside a quotation: it ends a sentence only when the quotation
            # closes right after it ("Stop." Then ...).
            closes = (b < line.end and text[b] in _QUOTE_CLOSERS and mask[b]
                      and (b + 1 >= len(mask) or not mask[b + 1]))
            if not closes:
                continue
        j = b
        while j < line.end and text[j] in _CLOSERS:
            j += 1
        if j >= line.end:
            out.append(_Sentence(s, j, (a, b), _word_start(text, a, s)))
            pos = line.end
            s = line.end
            break
        if not text[j].isspace():
            continue
        k = j
        while k < line.end and text[k].isspace():
            k += 1
        run = text[a:b]
        if "…" in run or run.count(".") >= 2:
            continue
        if run[-1] == ".":
            word = text[_word_start(text, a, s):a]
            if _is_abbreviation(word):
                continue
            if re.fullmatch(r"[A-Z]", word.lstrip("(\"'“")) and text[k].isupper():
                continue            # an initial: "J. K. Rowling"
            if text[k].islower():
                continue
            if text[k].isdigit() and word.strip("(\"'\u201c\u2018[").isalpha() and len(word.strip("(\"'\u201c\u2018[")) <= 4:
                continue            # "Mon. 5th", "Jan. 5": a short word, then a number
        out.append(_Sentence(s, j, (a, b), _word_start(text, a, s), True))
        s = pos = k
    else:
        out.append(_Sentence(s, line.end, None, _word_start(text, line.end, s)))
        return out
    return out


def _lines(text: str) -> list:
    lines, pos = [], 0
    for raw in text.split("\n"):
        end = pos + len(raw.rstrip())
        if end <= pos or not text[pos:end].strip():
            kind = BLANK
        elif _LIST_RE.match(text[pos:end]):
            kind = LIST
        elif _looks_like_code(text[pos:end]):
            kind = CODE
        else:
            kind = PROSE
        lines.append(_Line(pos, end, kind))
        pos += len(raw) + 1
    first = next((ln for ln in lines if ln.kind != BLANK), None)
    if first is not None and first.kind == PROSE and _GREETING_LINE.match(text[first.start:first.end]):
        first.kind = GREETING
    for prev, ln in zip(lines, lines[1:]):
        if ln.kind == PROSE and prev.kind == PROSE and text[prev.start:prev.end].endswith(","):
            ln.cont = True
    return lines


# ── What a sentence starts with ──────────────────────────────────────────────

_PREFIX_SKIP = frozenset("¿¡([")
_TRAILING = ".,;:!?)]}\"”»…"
_I_FORM = re.compile(r"I(?:['’](?:m|ll|ve|d|mma))?")
_INITIALS = re.compile(r"[A-Z]\.(?:[A-Z]\.)*")
_CODEISH_CHARS = frozenset("_/\\@#=+~^|<>*&$%")
_DOT_BETWEEN = re.compile(r"[A-Za-z0-9]\.[A-Za-z0-9]")


def _lower_char(ch: str, language: str) -> str:
    if language == "tr":
        if ch == "I":
            return "ı"
        if ch == "İ":
            return "i"
    if ch == "İ":
        return "i"
    low = ch.lower()
    return low if len(low) == 1 else ch


def _vocabulary_index(words) -> dict:
    """The Vocabulary entries by their first word, longest entry first."""
    index = {}
    for w in words or ():
        if not isinstance(w, str):
            continue
        w = " ".join(w.split())
        if not w:
            continue
        index.setdefault(w.split(" ", 1)[0].casefold(), []).append(w)
    for entries in index.values():
        entries.sort(key=len, reverse=True)
    return index


def _in_vocabulary(text: str, i: int, index: dict) -> bool:
    if not index:
        return False
    m = re.match(r"\S+", text[i:i + 80])
    if not m:
        return False
    token = m.group(0).rstrip(_TRAILING)
    keys = {m.group(0).casefold(), token.casefold(), re.split(r"['\u2019]", token)[0].casefold()}
    tail = text[i:]
    for entry in sorted((e for k in keys for e in index.get(k, ())), key=len, reverse=True):
        if tail[:len(entry)].casefold() == entry.casefold():
            after = tail[len(entry):len(entry) + 1]
            if not (after.isalnum() or after == "_"):
                return True
    return False


def _keeps_its_capital(text: str, i: int, language: str, index: dict, alone: bool = False) -> bool:
    """True when the word at ``i`` is not to be lower-cased. ``alone``: it is the
    whole sentence, so a letter and a stop is not an initial (the stop is the
    sentence's own, and may be dropped)."""
    m = re.match(r"\S+", text[i:])
    token = m.group(0) if m else ""
    core = token.rstrip(_TRAILING)
    if not core:
        return True
    if language in ("en", AUTO) and _I_FORM.fullmatch(core):
        return True
    if not alone and _INITIALS.match(token):
        return True
    letters = [c for c in core if c.isalpha()]
    if len(letters) >= 2 and all(c.isupper() for c in letters):
        return True                                  # NASA, OK, API
    if letters and any(c.isdigit() for c in core):
        return True                                  # B2B, A4, H2O
    if any(c.isupper() for c in letters[1:]):
        return True                                  # GitHub, McDonald's, Wi-Fi
    if any(c in _CODEISH_CHARS for c in core) or _DOT_BETWEEN.search(core) or "::" in core:
        return True                                  # file names, addresses, code
    return _in_vocabulary(text, i, index)


def _lowering_edit(text: str, sentence: _Sentence, mask: list, language: str, index: dict):
    i = sentence.start
    while i < sentence.end and text[i] in _PREFIX_SKIP:
        i += 1
    if i >= sentence.end or mask[i] or not text[i].isalpha() or not text[i].isupper():
        return None
    alone = len(text[sentence.start:sentence.end].split()) == 1
    if _keeps_its_capital(text, i, language, index, alone):
        return None
    low = _lower_char(text[i], language)
    return (i, 1, low) if low != text[i] else None


# ── The last full stop, and the exclamation mark ─────────────────────────────

def _single_stop(text: str, sentence: _Sentence, mask: list):
    """The index of the sentence's full stop when it ends in exactly one,
    outside any quotation, and with nothing after it, else None."""
    if sentence.run is None:
        return None
    a, b = sentence.run
    if b - a != 1 or text[a] != "." or sentence.end != b or mask[a] or a == 0 or text[a - 1].isspace():
        return None
    return a


_CUES = re.compile(
    r"\b(?:sorry|apolog\w*|unfortunately|sadly|regret\w*|can't|can’t|cannot|won't|won’t|"
    r"wouldn't|wouldn’t|couldn't|couldn’t|shouldn't|don't|don’t|doesn't|doesn’t|"
    r"didn't|didn’t|isn't|isn’t|aren't|wasn't|weren't|haven't|hasn't|hadn't|not|never|nothing|"
    r"nobody|problem\w*|issue\w*|error\w*|fail\w*|broke\w*|broken|bug\w*|delay\w*|late|cancel\w*|"
    r"down|crash\w*|stuck|unable|lost|missed|outage|"
    r"postpon\w*|pass(?:ed)? away|died|death|funeral|sick|ill|hurt|worried|worry|concern\w*|trouble|bad|"
    r"wrong|difficult|angry|upset|disappoint\w*)\b", re.I)


def _is_cheerful_statement(text: str, sentence: _Sentence) -> bool:
    body = text[sentence.start:sentence.run[0]]
    if len(body.split()) > EXCITED_SHORT_WORDS or not body.strip():
        return False
    if body.strip().lower() == "no" or _CUES.search(body):
        return False
    if _is_abbreviation(text[sentence.last_start:sentence.run[0]]):
        return False
    last = text[sentence.last_start:sentence.run[0]].strip(_TRAILING + "'’")
    if re.fullmatch(r"\d+(?:[.,]\d+)*", last):
        return True
    return not ("://" in last or "@" in last or any(c in _CODEISH_CHARS for c in last)
                or re.search(r"[A-Za-z]\.[A-Za-z0-9]|[A-Za-z0-9]\.[A-Za-z]", last))


# ── Putting it together ──────────────────────────────────────────────────────

def apply_style(text, style, category=OTHER, *, language="en", protected_words=()):
    """``text`` in ``style`` (``"formal"``, ``"casual"``, ``"very_casual"`` or
    ``"excited"``); anything else, and anything that is not text, comes back
    as it was.

    ``category`` is the kind of app the text is for (src/app_categories.py).
    The rules are the same for every kind; which style a kind gets is the
    user's choice (src/style_layer.py), made before this is called.
    ``language`` is the dictation language, ``protected_words`` the
    Vocabulary: a sentence that starts with one of them keeps its capital.
    """
    if not isinstance(text, str) or style not in STYLES or style == FORMAL or not text.strip():
        return text
    code = _language_code(language)
    if code is None or not eligible(text, code):
        return text
    if style == EXCITED and (code == "es" or "¡" in text or "¿" in text):
        return text         # Spanish wants the opening mark too; leave it alone
    mask = _quote_mask(text)
    lines = _lines(text)
    prose = [ln for ln in lines if ln.kind in (PROSE, GREETING)]
    sentences = {id(ln): _sentences(text, ln, mask) for ln in prose}
    edits = []

    last = next((ln for ln in reversed(lines) if ln.kind != BLANK), None)
    last_sentences = sentences.get(id(last)) if last is not None and last.kind == PROSE else None

    if style == VERY_CASUAL:
        index = _vocabulary_index(protected_words)
        for ln in prose:
            for n, s in enumerate(sentences[id(ln)]):
                if n == 0 and ln.cont:
                    continue
                edit = _lowering_edit(text, s, mask, code, index)
                if edit:
                    edits.append(edit)
        first = next((ln for ln in lines if ln.kind != BLANK), None)
        if first is not None and first.kind == GREETING:
            nxt = next((ln for ln in lines if ln.start > first.start and ln.kind != BLANK), None)
            if nxt is not None and nxt.kind == PROSE:
                edits.append((first.end, nxt.start - first.end, " "))
    if style in (CASUAL, VERY_CASUAL) and last_sentences:
        count = sum(len(sentences[id(ln)]) for ln in prose if ln.kind == PROSE)
        if style == VERY_CASUAL or count == 1:
            stop = _single_stop(text, last_sentences[-1], mask)
            if stop is not None and not _is_abbreviation(text[last_sentences[-1].last_start:stop]):
                edits.append((stop, 1, ""))
    if style == EXCITED:
        every = [(ln, s) for ln in prose if ln.kind == PROSE for s in sentences[id(ln)]]
        words = sum(len(text[s.start:s.end].split()) for _ln, s in every)
        chosen = every if words <= EXCITED_MESSAGE_WORDS else every[-1:]
        for _ln, s in chosen:
            stop = _single_stop(text, s, mask)
            if stop is not None and _is_cheerful_statement(text, s):
                edits.append((stop, 1, "!"))

    if not edits:
        return text
    out = text
    for pos, cut, put in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:pos] + put + out[pos + cut:]
    return out


def preview(style, category=OTHER) -> str:
    """The example Settings shows for ``style`` in a kind of app: the kind's
    sample sentence, run through the real rules."""
    kind = category if isinstance(category, str) and category in SAMPLES else OTHER
    return apply_style(SAMPLES[kind], style, kind)
