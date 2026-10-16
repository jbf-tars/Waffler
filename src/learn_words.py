"""Words to suggest for the Vocabulary from an edit made in the Journal.

When someone corrects a dictation in the Journal ("Edit text"), what they
changed can show a word the Vocabulary should know: Waffler written "waffle",
a name spelt another way. ``suggest`` finds those. It only suggests. Nothing
here saves anything; the Journal shows each one with Add and Dismiss, and Add
goes through the Vocabulary page's own add.

Narrow on purpose, because a wrong suggestion is noise and a wrong "sounds
like" rewrites every later dictation:

* Words are compared with punctuation, line breaks and capitals ignored. Only
  one word that became one other word counts (two words into one, or a
  rewritten sentence, is a different kind of edit), and a word that became
  the same word in other capitals when the capitals say something: capitals
  inside the word (PostHog, COBie), or a capital in the middle of a sentence
  (a name). A capital at the start of a sentence is only a sentence.
* A change of mind is not a fix: a day, a month or a number changed for
  another ("Tuesday" to "Thursday") is never suggested. A new word without a
  capital must also look like the old one (72 percent of the letters in
  common: "quil" to "quill"); one with a capital is a name or an acronym, which
  speech to text often writes in another spelling altogether ("shivon" for
  Siobhan), so it is not held to that.
* The new word has at least three letters, fits the Vocabulary's rules for a
  word (src/dictionary.py check_word), is not an everyday English word, and is
  not already a word or a "sounds like" in the Vocabulary.
* What it sounds like is what the speech service wrote: the entry's ``text``
  (the words as heard, before the clean-up; the Vocabulary is applied at that
  point, so that is the spelling it has to match). The pasted word is looked
  up in it, by its place when the clean-up changed it. If it cannot be found
  there, nothing is suggested. That spelling is never an everyday word ("their"
  must never become a rule), and not one already in the Vocabulary.
* At most four, names and acronyms (anything with a capital) first, then in
  the order they appear.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

try:
    import dictionary
    from transcribe_whisper import is_everyday_entry
except ImportError:  # imported as src.learn_words
    from src import dictionary
    from src.transcribe_whisper import is_everyday_entry

MAX_SUGGESTIONS = 4
MIN_LETTERS = 3
MIN_SIMILARITY = 0.72
MAX_HEARD_PER_WORD = 3

# Words people change on purpose, to say something else: the days, the months,
# the numbers. Changing one for another is an edit of what was meant.
_CONTENT_WORDS = frozenset("""
monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november december
zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety
hundred thousand million billion first second third fourth fifth sixth seventh eighth ninth tenth
""".split())

SETTING = "learn_from_edits"

# A word: letters and digits, with apostrophes or hyphens inside (O'Brian,
# Jean-Luc, don't).
_WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*")
_SENTENCE_END = re.compile(r"[.!?…][\"'’”)\]]*\s*$")


def enabled(stored) -> bool:
    """Whether suggestions are made: on unless settings.json says exactly
    false."""
    return not (isinstance(stored, dict) and stored.get(SETTING) is False)


def settings_problem(payload) -> str:
    """A sentence when a save carries a ``learn_from_edits`` that is not a true
    or a false, else ''."""
    if isinstance(payload, dict) and SETTING in payload and not isinstance(payload[SETTING], bool):
        try:
            import user_messages
        except ImportError:
            from src import user_messages
        return user_messages.LEARN_FROM_EDITS_UNKNOWN
    return ""


# ── words ────────────────────────────────────────────────────────────────────

def _tokens(text) -> list:
    """[(word, start, end)] for each word in ``text``."""
    return [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(str(text or ""))]


def _letters(word: str) -> int:
    return sum(1 for c in word if unicodedata.category(c).startswith("L"))


def _fold(word: str) -> str:
    """Lower case, accents off, letters and digits only."""
    base = unicodedata.normalize("NFKD", word.casefold())
    return "".join(c for c in base if c.isalnum() and not unicodedata.combining(c))


def _similar(a: str, b: str) -> bool:
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return False
    return difflib.SequenceMatcher(None, fa, fb, autojunk=False).ratio() >= MIN_SIMILARITY


def _starts_sentence(text: str, start: int) -> bool:
    """Is the word at ``start`` the first of a sentence, line or paragraph?"""
    before = text[:start]
    stripped = before.rstrip(" \t")
    if not stripped or stripped.endswith("\n"):
        return True
    return bool(_SENTENCE_END.search(before))


def _heard_word(index: int, pasted: list, heard: list):
    """The word the speech service wrote where the pasted word at ``index``
    stands, or None when that cannot be told."""
    key = pasted[index][0].casefold()
    same = [h for h in heard if h[0].casefold() == key]
    if same:
        nth = sum(1 for p in pasted[:index] if p[0].casefold() == key)
        return same[min(nth, len(same) - 1)][0]
    matcher = difflib.SequenceMatcher(None, [p[0].casefold() for p in pasted],
                                      [h[0].casefold() for h in heard], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace" and i1 <= index < i2 and i2 - i1 == 1 and j2 - j1 == 1:
            return heard[j1][0]
    return None


# ── the suggestions ──────────────────────────────────────────────────────────

def suggest(heard_text, pasted_text, edited_text, words, sounds_like) -> list:
    """[{"word", "sounds_like": [what the speech service wrote]}], at most
    four. ``heard_text`` is the words as the speech service wrote them,
    ``pasted_text`` what was pasted, ``edited_text`` what the person made of
    it. ``words`` and ``sounds_like`` are the Vocabulary as it stands. Never
    raises."""
    try:
        return _suggest(str(heard_text or ""), str(pasted_text or ""), str(edited_text or ""),
                        words, sounds_like)
    except Exception:
        return []


def _suggest(heard_text, pasted_text, edited_text, words, sounds_like) -> list:
    old, new, heard = _tokens(pasted_text), _tokens(edited_text), _tokens(heard_text)
    if not old or not new:
        return []
    known = {" ".join(w.split()).casefold() for w in (words or []) if isinstance(w, str)}
    for spellings in (sounds_like or {}).values():
        known.update(" ".join(s.split()).casefold() for s in spellings if isinstance(s, str))

    found = {}          # new word (any case) -> [word, [heard...], order]

    def offer(word, spelling, order):
        k = word.casefold()
        if k in found:
            if spelling and spelling not in found[k][1] and len(found[k][1]) < MAX_HEARD_PER_WORD:
                found[k][1].append(spelling)
            return
        found[k] = [word, [spelling] if spelling else [], order]

    def usable(word):
        return (_letters(word) >= MIN_LETTERS and not dictionary.check_word(word)[1]
                and word.casefold() not in known and not is_everyday_entry(word))

    matcher = difflib.SequenceMatcher(None, [t[0].casefold() for t in old],
                                      [t[0].casefold() for t in new], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for off in range(i2 - i1):
                o, n = old[i1 + off][0], new[j1 + off][0]
                if o == n or not usable(n):
                    continue
                interior = any(c.isupper() for c in n[1:])
                name = n[0].isupper() and not _starts_sentence(edited_text, new[j1 + off][1])
                if interior or name:
                    offer(n, "", j1 + off)
        elif tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1:
            o, n = old[i1][0], new[j1][0]
            if not usable(n) or o.casefold() in _CONTENT_WORDS or n.casefold() in _CONTENT_WORDS:
                continue
            if not any(c.isupper() for c in n) and not _similar(o, n):
                continue
            h = _heard_word(i1, old, heard)
            if (not h or len(h) > dictionary.MAX_WORD_LEN or h.casefold() == n.casefold()
                    or h.casefold() in known or is_everyday_entry(h)):
                continue
            offer(n, h, j1)

    ranked = sorted(found.values(), key=lambda f: (not any(c.isupper() for c in f[0]), f[2]))
    return [{"word": w, "sounds_like": list(h)} for w, h, _ in ranked[:MAX_SUGGESTIONS]]


def suggest_for_entry(entry, edited_text, words, sounds_like) -> list:
    """``suggest`` for a Journal entry: its ``text`` is how the speech service
    wrote it and its ``styled`` what was pasted (an entry from before 3.14 with
    only ``text`` uses that for both). A Not sent card, a rewrite or a command
    has no such words and gets none."""
    if not isinstance(entry, dict) or entry.get("failed") or entry.get("kind") in ("transform", "command"):
        return []
    heard = entry.get("text") if isinstance(entry.get("text"), str) else ""
    pasted = entry.get("styled") if isinstance(entry.get("styled"), str) and entry.get("styled") else heard
    return suggest(heard, pasted, edited_text, words, sounds_like)
