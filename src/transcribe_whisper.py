"""Whisper transcription — Groq (fastest) → local → OpenAI API (fallback).

Priority order:
  1. Groq Whisper  (GROQ_API_KEY set) → ~100-300ms, needs internet
  2. mlx-whisper   (Mac ARM + LOCAL_WHISPER=1) → ~0.2-0.5s, no internet
  3. faster-whisper (Windows/Intel + LOCAL_WHISPER=1) → ~0.5-2s on CPU
  4. OpenAI Whisper API (always available) → 2-5s, needs internet
"""

import functools
import os
import sys
import time
import tempfile
import platform
import re

from openai import OpenAI

# ── Try to load Groq SDK ────────────────────────────────────────────────────
_groq_mod = None
try:
    import groq as _groq_mod
except ImportError:
    pass

# Shared file logger so transcription diagnostics reach ~/.waffler-hosted/app.log.
# (Plain print() only goes to stdout, which the windowed bundle doesn't capture —
# that's why the chunking logs were invisible when diagnosing truncation.)
try:
    from log_util import log as _wlog
except ImportError:
    try:
        from src.log_util import log as _wlog
    except ImportError:
        _wlog = print

_USE_LOCAL = os.getenv("LOCAL_WHISPER", "0") == "1"
_IS_MAC_ARM = sys.platform == "darwin" and platform.machine() == "arm64"
_IS_WINDOWS  = sys.platform == "win32"

# ── Try to load local backend ───────────────────────────────────────────────
_mlx_whisper    = None
_faster_whisper = None

if _USE_LOCAL:
    if _IS_MAC_ARM:
        try:
            import mlx_whisper as _mlx_whisper
            print("🍎 mlx-whisper loaded — local transcription on Apple Silicon")
        except ImportError:
            print("⚠️  LOCAL_WHISPER=1 but mlx-whisper not installed.")
            print("   Run: bash install_local_whisper.sh")
    else:
        # Windows or Intel Mac — use faster-whisper
        try:
            from faster_whisper import WhisperModel as _FasterWhisperModel
            _faster_whisper = _FasterWhisperModel(
                "base",
                device="cpu",
                compute_type="int8"   # fastest CPU mode
            )
            print("⚡ faster-whisper loaded — local transcription (CPU)")
        except ImportError:
            print("⚠️  LOCAL_WHISPER=1 but faster-whisper not installed.")
            print("   Run: pip install faster-whisper")


try:
    from data_paths import data_dir as _data_dir
except ImportError:  # imported as src.transcribe_whisper
    from src.data_paths import data_dir as _data_dir

VOCAB_FILE    = _data_dir() / "vocab.json"
SETTINGS_FILE = _data_dir() / "settings.json"


# Limits on the Vocabulary list. Each dictation compares every word heard
# with every entry, so the list is capped where that stays well under the
# time a dictation takes. Measured with 500 entries on a 300-word dictation:
# about 0.13 s of everyday words, 0.36 s if every word were unusual.
# An entry is a name, a word or a short phrase, never a paragraph.
VOCAB_MAX_ENTRIES = 500
VOCAB_MAX_ENTRY_LEN = 60


def clean_vocab(words) -> list[str]:
    """The Vocabulary list as the app uses it: strings only, spaces trimmed
    and runs of spaces made one, empty entries dropped, and an entry that
    differs from an earlier one only in case or spacing dropped (the first
    spelling wins). Anything that is not a list gives an empty list."""
    if not isinstance(words, list):
        return []
    out, seen = [], set()
    for w in words:
        if not isinstance(w, str):
            continue
        w = " ".join(w.split())
        if not w or w.casefold() in seen:
            continue
        seen.add(w.casefold())
        out.append(w)
    return out


def load_vocab() -> list[str]:
    """Load the user's custom vocabulary words (see clean_vocab). Read on
    every dictation, so a change in the Vocabulary page applies to the next
    dictation without a restart."""
    try:
        if VOCAB_FILE.exists():
            import json
            return clean_vocab(json.loads(VOCAB_FILE.read_text(encoding="utf-8-sig")))
    except Exception:
        pass
    return []


def save_vocab(words) -> dict:
    """Save the Vocabulary list to vocab.json: tidied (clean_vocab), checked
    against the limits, written as UTF-8 through a temporary file so a crash
    mid-write cannot leave half a list.

    A vocab.json that cannot be read (hand-edited, or cut short) loads as an
    empty list, and the next save would have replaced it with just the new
    word. It is now kept beside it as vocab.unreadable-<time>.json first.

    Returns {"ok": True, "count", "words"} or {"ok": False, "error"} with a
    sentence for the page; "log" carries a line for app.log when there is
    one."""
    import json
    if not isinstance(words, list):
        return {"ok": False, "error": "That list couldn't be read."}
    cleaned = clean_vocab(words)
    if any(len(w) > VOCAB_MAX_ENTRY_LEN for w in cleaned):
        return {"ok": False, "error": f"Keep each entry to {VOCAB_MAX_ENTRY_LEN} characters "
                                      "or fewer: a name, a word or a short phrase."}
    if len(cleaned) > VOCAB_MAX_ENTRIES:
        return {"ok": False, "error": f"Your list is full ({VOCAB_MAX_ENTRIES} words). "
                                      "Remove one you no longer need first."}
    log = ""
    try:
        VOCAB_FILE.parent.mkdir(parents=True, exist_ok=True)
        if VOCAB_FILE.exists():
            try:
                readable = isinstance(
                    json.loads(VOCAB_FILE.read_text(encoding="utf-8-sig")), list)
            except Exception:
                readable = False
            if not readable:
                kept = VOCAB_FILE.with_name(
                    f"vocab.unreadable-{time.strftime('%Y%m%d-%H%M%S')}.json")
                os.replace(VOCAB_FILE, kept)
                log = f"vocab.json could not be read; kept as {kept.name}"
        try:
            from atomic_json import write_json_atomic
        except ImportError:  # imported as src.transcribe_whisper
            from src.atomic_json import write_json_atomic
        write_json_atomic(VOCAB_FILE, cleaned)
    except Exception as e:
        return {"ok": False, "error": "Couldn't save your list. Try again.",
                "log": f"save failed: {type(e).__name__}: {e}"}
    out = {"ok": True, "count": len(cleaned), "words": cleaned}
    if log:
        out["log"] = log
    return out


def load_settings() -> dict:
    """Load persisted settings (language, auto_paste, etc.)."""
    try:
        if SETTINGS_FILE.exists():
            import json
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return {}


def vocab_to_prompt(words: list[str]) -> str:
    """Turn vocab list into a Whisper initial_prompt hint.

    Whisper's initial_prompt is conditioning text — it should be a bare
    word list, NOT an instruction sentence.  Sentence-like prompts cause
    Whisper to hallucinate lines containing those words.
    """
    if not words:
        return ""
    return ", ".join(words)


def _levenshtein_distance(a: str, b: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(a) < len(b):
        return _levenshtein_distance(b, a)
    if len(b) == 0:
        return len(a)
    
    previous_row = range(len(b) + 1)
    for i, ca in enumerate(a):
        current_row = [i + 1]
        for j, cb in enumerate(b):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (ca != cb)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


# Ordinary English words a vocabulary entry may never overwrite. A custom
# vocab is a list of names and jargon; when one of them lands within an edit
# or two of a everyday word, the everyday word is what the speaker said.
# Live failure (2026-09-11): vocab entry "Nour" rewrote "your" in
# "really appreciate your time today", because at four characters a
# single-edit neighbour scores exactly the 0.75 threshold. Exact matches are
# unaffected — a speaker who genuinely says "Nour" still gets it.
_VOCAB_PROTECTED_WORDS = frozenset("""
about after again all also and any are because been before being both but
came come could day did does down each even every first for four from get
give going good got had has have her here him his how hour hours into its
just keep know like little long look made make many may might more morning
most much must name need new next now off often once only other our out
over own part people place put right said same see seem she should since
some soon still such sure take team than thank thanks that the their them
then there these they thing think this those though thought three through
time times today too took tour tours two under until use used very want
was way week well went were what when where which while who why will with
within without word work would year years yes yet you your yours
""".split())

# Below this length a vocabulary entry carries too little signal to correct
# on: at four characters, every word one edit away scores 0.75, which is the
# default threshold. Short entries still match EXACTLY.
_MIN_FUZZY_VOCAB_LEN = 5

# ── Joining two words into one vocabulary entry (pass 2) ────────────────────
# Whisper sometimes splits an unfamiliar name into two words ("Ashkan" ->
# "Nash can", "Postgres" -> "post grass", "Clubcard" -> "club card"), so pass
# 2 joins each adjacent pair and compares the join with each entry. It used a
# looser bar than single words (0.70) and no common-word guard, so ordinary
# pairs of words were rewritten as names. Found on real speech (2026-09-25):
# "add an", "and an" and "said and" became Aidan (17 times in one user's
# history), "is hotel" and "is model" became Isobel, "colour card" and "blue
# card" became Clubcard, and "the sign had" became "the Sinéad".
#
# The rule now, for words a and b and entry v:
#   * a join that spells v exactly is always taken ("club card");
#   * otherwise a word under 3 letters rules the pair out: two-letter words
#     are almost all glue ("an", "is", "on", "me") and caused most of the
#     damage ("add an", "is hotel", "month on", "me thank"). Since 3.15 a
#     two-letter word that is not everyday English (an initialism such as
#     "qc") may join a word of 3+ letters, which brings back "kobi qc" ->
#     COBieQC; the rules below still apply to it;
#   * so does a join more than one letter longer or shorter than v: a split
#     name keeps its length ("said Dan" is not "Aidan");
#   * when both words are everyday English (common_words.py), the pair is
#     what the speaker said unless the join differs from v only in its
#     vowels and doubled letters (same consonant skeleton, both words 4+
#     letters, similarity >= the single-word bar): "post grass" -> Postgres
#     passes, "blue card", "colour card", "reach all" do not;
#   * when at least one word is not everyday English ("nash"), the old 0.70
#     bar still applies, but the consonant skeletons may differ by at most
#     one sound: "Nash can" -> Ashkan passes, "chat bot" -> ChatGPT does not.
try:
    from common_words import COMMON_WORDS as _COMMON_WORDS
except ImportError:  # imported as src.transcribe_whisper
    from src.common_words import COMMON_WORDS as _COMMON_WORDS
_BIGRAM_COMMON = _COMMON_WORDS | _VOCAB_PROTECTED_WORDS
_BIGRAM_MIN_WORD_LEN = 3
_BIGRAM_COMMON_MIN_WORD_LEN = 4


def _consonant_skeleton(text: str) -> str:
    """Consonants only, accents folded, doubles collapsed, and letters that
    sound alike merged (hard c/k/q, soft c/s/z, ph/f): what survives when a
    word is misspelled by ear. "postgrass" and "Postgres" both give
    "pstgrs"; "bluecard" gives "blkrd" against "Clubcard"'s "klbkrd"; and
    "facttime" gives "fktm" against "FaceTime"'s "fstm", because the c in
    "face" is soft."""
    import unicodedata
    s = "".join(ch for ch in unicodedata.normalize("NFKD", text.lower())
                if not unicodedata.combining(ch))
    s = s.replace("ck", "k").replace("ph", "f").replace("q", "k")
    s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k")
    s = s.replace("z", "s")
    out = []
    for ch in s:
        if not ch.isalpha() or ch in "aeiouy":
            continue
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _bigram_join_matches(a: str, b: str, vword: str, similarity: float,
                         bigram_threshold: float, threshold: float) -> bool:
    """May the adjacent words ``a b`` be replaced by vocab entry ``vword``?
    ``similarity`` is that of the join ``a + b`` to ``vword``. See the rule
    above."""
    glued = a + b
    if glued == vword:
        return True
    short_word = min(a, b, key=len)
    if len(short_word) < _BIGRAM_MIN_WORD_LEN and (
            len(short_word) < 2 or short_word in _BIGRAM_COMMON
            or max(len(a), len(b)) < _BIGRAM_MIN_WORD_LEN):
        return False
    # A split name keeps the name's length, give or take a letter ("nashcan"
    # 7 for "Ashkan" 6). "said dan" (7) for "Aidan" (5) is two words, not one.
    if abs(len(glued) - len(vword)) > 1:
        return False
    skel_glued = _consonant_skeleton(glued)
    skel_vword = _consonant_skeleton(vword)
    if a in _BIGRAM_COMMON and b in _BIGRAM_COMMON:
        return (min(len(a), len(b)) >= _BIGRAM_COMMON_MIN_WORD_LEN
                and similarity >= threshold
                and skel_glued == skel_vword)
    return (similarity >= bigram_threshold
            and _levenshtein_distance(skel_glued, skel_vword) <= 1)


# ── One word for one vocabulary entry (pass 1) ──────────────────────────────
# Pass 1 compared each word heard with each entry and took the first within
# an edit or two of it. On one user's real dictations (225 "Vocabulary
# corrections applied" lines, checked 2026-10-05) that rewrote ordinary words:
# "waffle" (16 times) and "waffled" became Waffler ("I'll waffle on" pasted
# as "I'll Waffler on"), "mortar" (15) became Morta and "BIM" (9) became XBim;
# and in testing "Phillips" became Phillip, "Matthews" Mathew and "linked"
# LinkedIn. None of those is a mishearing: each is a real word, or the entry
# with letters added to or taken off one end. The rule now, for a word w
# heard and an entry v:
#   * an everyday English word (common_words.py), or one made from one with
#     a common ending ("posters", "hailed"), is never changed, not even its
#     capitals: with "Will" in the list, "will" stays "will";
#   * w spelt as v apart from accents is taken, at any length ("Sinead" ->
#     Sinéad, "zoe" -> Zoë), and so is w spelt as v apart from doubled
#     letters ("postgress" -> Postgres, "Matthew" -> Mathew);
#   * w that is v with letters added at or taken off its start or end
#     ("waffle" for Waffler, "mortar" for Morta, "Phillips" for Phillip,
#     "bim" for XBim, "linked" for LinkedIn), or that shares v's first four
#     letters and differs only in an English ending ("waffled" for Waffler:
#     -d against -r), is left alone;
#   * otherwise the similarity bar applies as before, and the entry closest
#     to w wins ("cobiec" -> COBieQC, not COBie, whichever is listed first).
_ENGLISH_ENDINGS = frozenset(["", "s", "es", "d", "ed", "r", "er", "rs", "ers",
                              "ing", "ings", "y", "ly"])
_FAMILY_MIN_STEM = 4

# A word, as the corrector sees one: a run of letters in any alphabet.
# "[a-zA-Z]+" (before 3.15) split "Sinéad" into "sin" and "ad".
_WORD_RE = re.compile(r"[^\W\d_]+")
# An entry the corrector can match: words of letters joined by spaces,
# hyphens or apostrophes ("Sinéad", "James Farrelly", "Jean-Luc",
# "O'Brien"). Entries with digits or symbols ("GPT-4", "C++") are still sent
# to the speech step as hints but are not matched here: their letters alone
# would turn every "gpt" into "GPT-4".
_ENTRY_RE = re.compile(r"[^\W\d_]+(?:[\s'’\-]+[^\W\d_]+)*")
_PHRASE_SEP = r"[\s'’\-]+"


@functools.lru_cache(maxsize=8192)
def _fold_accents(text: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", text.lower())
                   if not unicodedata.combining(ch))


@functools.lru_cache(maxsize=8192)
def _collapse_doubles(text: str) -> str:
    return re.sub(r"(.)\1+", r"\1", text)


def _is_word_family(word: str, vword: str) -> bool:
    """Is ``word`` the entry ``vword`` with letters added or taken off at one
    end, or the same stem with a different English ending? Such a word is
    a real word in its own right ("waffle", "mortar", "linked"), not a
    mishearing of the entry. See the rule above."""
    a = _collapse_doubles(_fold_accents(word))
    b = _collapse_doubles(_fold_accents(vword))
    if a == b:
        return False
    short, long_ = sorted((a, b), key=len)
    if long_.startswith(short) or long_.endswith(short):
        return True
    p = len(os.path.commonprefix([a, b]))
    return (p >= _FAMILY_MIN_STEM
            and a[p:] in _ENGLISH_ENDINGS and b[p:] in _ENGLISH_ENDINGS)


# Endings that make another everyday word from one: "posters" is "post" +
# "ers", "hailed" is "hail" + "ed". common_words.py lists the frequent forms
# only, and "posters" was rewritten to Postgres.
_EVERYDAY_ENDINGS = ("ings", "ing", "ers", "er", "ies", "es", "ed", "ly", "s", "d")


def _is_everyday(word: str) -> bool:
    """An everyday English word, or one made from one with a common ending
    ("posters", "aiding", "hailed", "parties"). Lower-case input."""
    if word in _BIGRAM_COMMON:
        return True
    for end in _EVERYDAY_ENDINGS:
        if len(word) - len(end) >= 3 and word.endswith(end):
            stem = word[:-len(end)]
            if (stem in _BIGRAM_COMMON or stem + "e" in _BIGRAM_COMMON
                    or (end == "ies" and stem + "y" in _BIGRAM_COMMON)
                    or (len(stem) >= 4 and stem[-1] == stem[-2] and stem[:-1] in _BIGRAM_COMMON)):
                return True
    return False


def _levenshtein_within(a: str, b: str, limit: int) -> int:
    """Levenshtein distance of ``a`` and ``b`` if it is at most ``limit``,
    else ``limit + 1``. Stops as soon as every path is over the limit, so a
    long Vocabulary list costs little per word heard."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return min(previous[-1], limit + 1)


def _single_word_score(word: str, vword: str, threshold: float):
    """How well the lower-case word ``word`` matches the lower-case entry
    ``vword`` if it may be replaced by it, else None. The caller has already
    refused everyday words and handled an exact match."""
    if _fold_accents(word) == _fold_accents(vword):
        return 1.0
    if len(word) < 3 or len(vword) < _MIN_FUZZY_VOCAB_LEN:
        return None
    if _collapse_doubles(_fold_accents(word)) == _collapse_doubles(_fold_accents(vword)):
        return 1.0
    max_len = max(len(word), len(vword))
    # The edit distance is at least the difference in length: skip entries
    # that cannot reach the bar before paying for the full comparison.
    if abs(len(word) - len(vword)) > (1 - threshold) * max_len:
        return None
    if _is_word_family(word, vword):
        return None
    limit = int((1 - threshold) * max_len + 1e-9)
    distance = _levenshtein_within(word, vword, limit)
    if distance > limit:
        return None
    similarity = 1 - distance / max_len
    return similarity if similarity >= threshold else None


def _matchable_entries(vocab):
    """(lower-case words, entry) for each entry the corrector can match."""
    out = []
    for entry in vocab[:VOCAB_MAX_ENTRIES]:
        if not isinstance(entry, str):
            continue
        entry = " ".join(entry.split())
        if not entry or len(entry) > VOCAB_MAX_ENTRY_LEN or not _ENTRY_RE.fullmatch(entry):
            continue
        out.append((tuple(_WORD_RE.findall(entry.lower())), entry))
    return out


def fuzzy_match_word(transcribed: str, vocab: list[str], threshold: float = 0.75) -> list[tuple[str, str]]:
    """
    Find vocabulary words that are similar to transcribed words.
    Returns list of (transcribed_phrase, vocab_word) pairs to substitute,
    the phrase in lower case with its words joined by single spaces.

    Three passes:
      0. Entries of two or more words ("James Farrelly"): the same number
         of words heard in a row, each spelt as the entry's word or passing
         the single-word rule, and at least one spelt exactly. Before 3.15
         these entries were never matched at all.
      1. Single words (rule above ``_ENGLISH_ENDINGS``).
      2. **Bigram collapse** match: when Whisper splits a compound name into
         two words ("Ashkan" → "Nash can", "Ashcan", "Ash can"), pass 1
         can't find it. We glue every adjacent bigram together
         ("nashcan", "ashcan") and fuzzy-match that against single-word
         vocab entries (rule above ``_bigram_join_matches``).
    An everyday English word is never changed by passes 0 and 1, so an
    entry that is itself an everyday word ("Will", "IT") never rewrites it.
    """
    if not vocab:
        return []

    entries = _matchable_entries(vocab)
    if not entries:
        return []
    words = _WORD_RE.findall(transcribed.lower())

    corrections: list[tuple[str, str]] = []
    seen_phrases: set[str] = set()
    used: set[int] = set()          # positions already corrected or claimed

    def emit(phrase: str, entry: str, positions) -> None:
        used.update(positions)
        if phrase not in seen_phrases:
            seen_phrases.add(phrase)
            corrections.append((phrase, entry))

    # Pass 0: entries of several words, longest first.
    phrases = sorted((e for e in entries if len(e[0]) > 1), key=lambda e: -len(e[0]))
    for etoks, entry in phrases:
        n = len(etoks)
        for i in range(len(words) - n + 1):
            span = range(i, i + n)
            if any(j in used for j in span):
                continue
            window = words[i:i + n]
            if all(_is_everyday(t) for t in window):
                continue        # "the office" stays as said, even for "The Office"
            if not any(t == e for t, e in zip(window, etoks)):
                continue
            if all(t == e or (not _is_everyday(t)
                              and _single_word_score(t, e, threshold) is not None)
                   for t, e in zip(window, etoks)):
                emit(" ".join(window), entry, span)

    singles = [(etoks[0], entry) for etoks, entry in entries if len(etoks) == 1]
    exact = {}
    for vword, entry in singles:
        exact.setdefault(vword, entry)

    # Pass 1: single words.
    for i, word in enumerate(words):
        if i in used:
            continue
        if _is_everyday(word):
            # An everyday word is what the speaker said, not a near-miss of
            # a name; and its capitals are left alone too ("will", not
            # "Will"). Claimed, so pass 2 does not join it either.
            if word in exact:
                used.add(i)
            continue
        if word in exact:
            # Exact (case-insensitive) match: the entry's spelling and
            # capitals ("cobie" -> COBie). apply_vocab_corrections only
            # reports it when the text actually changes.
            emit(word, exact[word], (i,))
            continue
        best, best_score = None, None
        for vword, entry in singles:
            score = _single_word_score(word, vword, threshold)
            if score is not None and (best_score is None or score > best_score):
                best, best_score = entry, score
        if best is not None:
            emit(word, best, (i,))

    # Pass 2 — bigram collapse against single-word vocab entries.
    # We only target vocab terms that are themselves single words (no spaces),
    # because the failure mode is "Whisper split a compound into two words".
    # The 0.70 bar catches "Nash can" ↔ "Ashkan" (similarity 0.71), but on its
    # own it also admitted ordinary pairs ("add an" -> Aidan), so every
    # candidate must also pass _bigram_join_matches (rule above it).
    bigram_threshold = max(0.65, threshold - 0.05)
    single_vocab_words = [(v, e) for v, e in singles if len(v) >= 4]
    for i in range(len(words) - 1):
        if i in used or i + 1 in used:
            continue
        a, b = words[i], words[i + 1]
        glued = a + b
        for vword, entry in single_vocab_words:
            max_len = max(len(glued), len(vword))
            if max_len < 4:
                continue
            # _bigram_join_matches takes only an exact join or one within a
            # letter of the entry's length: skip the rest before paying for
            # the edit distance (most of the time on a long list).
            if glued != vword and abs(len(glued) - len(vword)) > 1:
                continue
            distance = _levenshtein_distance(glued, vword)
            similarity = 1 - (distance / max_len)
            if similarity >= bigram_threshold and _bigram_join_matches(
                    a, b, vword, similarity, bigram_threshold, threshold):
                # Substitute the literal "a b" two-word sequence (with the
                # space) so apply_vocab_corrections can replace it as a phrase.
                emit(f"{a} {b}", entry, (i, i + 1))
                break

    return corrections


def apply_vocab_changes(transcribed: str, vocab: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """Apply vocabulary corrections to transcribed text.

    Returns (corrected_text, changes), where each change is (heard, used):
    the words as they stood in the text and the entry that replaced them,
    once per distinct change and only where the text actually changed. A
    transcript that already reads "Morta" is no longer reported as
    "'morta' → 'Morta'": before 3.15 every exact match was logged as a
    correction, so the log could not tell a fix from no change.

    One pass over the text: a replacement is never matched again by a later
    correction. A multi-word mishearing may be written with spaces, hyphens
    or an apostrophe between its words: Whisper wrote "post-grass" for
    spoken "Postgres" when tested on real audio.
    """
    if not vocab or not transcribed:
        return transcribed, []

    corrections = fuzzy_match_word(transcribed, vocab)
    if not corrections:
        return transcribed, []

    target = {}
    for misheard, correct in corrections:
        target.setdefault(tuple(misheard.split()), correct)
    keys = sorted(target, key=lambda k: (-len(k), -sum(map(len, k))))
    alternation = "|".join(_PHRASE_SEP.join(re.escape(w) for w in k) for k in keys)
    pattern = re.compile(r"(?<![^\W\d_])(?:" + alternation + r")(?![^\W\d_])", re.IGNORECASE)

    changes: list[tuple[str, str]] = []

    def repl(m):
        heard = m.group(0)
        correct = target.get(tuple(_WORD_RE.findall(heard.lower())))
        if correct is None or heard == correct:
            return heard
        if (heard, correct) not in changes:
            changes.append((heard, correct))
        return correct

    return pattern.sub(repl, transcribed), changes


def apply_vocab_corrections(transcribed: str, vocab: list[str]) -> tuple[str, list[str]]:
    """
    Apply vocabulary corrections to transcribed text.
    Returns tuple of (corrected_text, list_of_corrections), each correction
    written "'heard' → 'used'" for the log. See apply_vocab_changes.
    """
    corrected, changes = apply_vocab_changes(transcribed, vocab)
    return corrected, [f"'{heard}' → '{used}'" for heard, used in changes]


# Whisper's most common silence-hallucinations. When the audio is empty or
# near-empty, the model's training corpus (heavy on YouTube transcripts) leaks
# through as canned closing-line phrases. We strip these when they're the
# entire output — never substring-match, since a real recording can mention
# them in passing ("did you see that 'thanks for watching' ad?").
_WHISPER_HALLUCINATIONS = frozenset(s.strip().lower() for s in [
    "thanks for watching",
    "thanks for watching!",
    "thanks for watching.",
    "thank you for watching",
    "thank you for watching!",
    "thank you for watching.",
    "thanks for watching, and i'll see you in the next video.",
    "see you in the next video",
    "see you next time",
    "please subscribe",
    "please like and subscribe",
    "don't forget to like and subscribe",
    "like and subscribe",
    "subscribe to my channel",
    "[music]",
    "[applause]",
    # ── v3.14.97 — decoder derailments seen on real clips (see
    # _DERAIL_TAIL_RE): list completions and the Amara credit line.
    "and the rest of the team",
    "the rest of the team",
    "and the likes",
    "and so on",
    "subtitles by the amara.org community",
    "you",  # Whisper's most common 1-token hallucination on noise
    ".",
    # ── v3.14.39 — short-clip Whisper hallucinations ──────────────────
    # Complementary to v3.14.38's styling-prompt fix. v3.14.38 stops the
    # LLM styler from generating filler-tails like "and many more"; this
    # catches the upstream case where Whisper ITSELF hallucinates these
    # phrases on <1 s near-silent audio (so the styling LLM never even
    # runs — local pass-through emits the Whisper output verbatim).
    # User log on 14 May: 28 instances of literal "Done: and more." with
    # `styling (local): 0ms` — every one was a Whisper-layer hallucination.
    "and more",
    "and more.",
    "and more!",
    "and more...",
    # Sign-offs that show up on very short clips of room noise.
    "bye",
    "bye.",
    "bye!",
    "bye-bye",
    "bye bye",
    "goodbye",
    "goodbye.",
    "thank you",
    "thank you.",
    "thank you!",
    "thanks",
    "thanks.",
    "okay",
    "okay.",
    "ok",
    "ok.",
    "yeah",
    "yeah.",
    "uh",
    "um",
    "hmm",
    "mhm",
])


def _is_whisper_hallucination(text: str) -> bool:
    """True if the entire transcript is a known Whisper boilerplate hallucination.

    Whisper produces YouTube outro lines ("Thanks for watching!", "Please
    subscribe", etc.) when fed silence or very low-energy audio. The vocab
    echo filter doesn't catch these because they don't overlap with vocab.
    Only triggers when the cleaned text is *exactly* one of the canned
    phrases — a real utterance that mentions one of them in context is left
    alone.
    """
    if not text:
        return False
    cleaned = text.strip().lower().rstrip(".,!?")
    if not cleaned:
        return True
    # Compare against both raw and trailing-punct-stripped forms.
    return text.strip().lower() in _WHISPER_HALLUCINATIONS or cleaned in _WHISPER_HALLUCINATIONS


def _is_vocab_echo(text: str, vocab: list) -> bool:
    """Detect when Whisper echoed the vocab prompt instead of transcribing.

    Whisper sometimes regurgitates the `prompt` argument verbatim when given
    silence or low-quality audio. Catches that specific failure so the vocab
    list doesn't get pasted as output when the user records nothing.
    """
    if not vocab or not text:
        return False

    text_tokens = set(re.findall(r"\w+", text.lower()))
    if not text_tokens:
        return False

    vocab_tokens = set()
    for word in vocab:
        for tok in re.findall(r"\w+", str(word).lower()):
            vocab_tokens.add(tok)
    if not vocab_tokens:
        return False

    # Exact match of the comma-joined prompt form (with/without trailing punct)
    prompt_form = ", ".join(str(w) for w in vocab).lower().strip().rstrip(".,!?")
    if text.lower().strip().rstrip(".,!?") == prompt_form:
        return True

    overlap = text_tokens & vocab_tokens
    ratio = len(overlap) / len(text_tokens)

    # Every distinct token in the output is a vocab token — no real words at
    # all, so whatever the length this is just regurgitation of the prompt.
    if ratio >= 1.0:
        return True

    # Short transcript (<= 10 distinct words) dominated by vocab tokens.
    # Real speech of that length almost never hits 50%+ vocab density unless
    # the user was literally reading their vocab list aloud.
    if len(text_tokens) <= 10 and ratio >= 0.5:
        return True

    # Original heuristic: output length is close to vocab length AND vocab
    # dominates. Catches the classic case where Whisper spits out the whole
    # vocab list with one or two extra filler tokens.
    if ratio >= 0.7 and len(text_tokens) <= len(vocab_tokens) + 2:
        return True

    return False


def _pad_audio_with_silence(audio_bytes: bytes, padding_ms: int = 300) -> bytes:
    """Add silence padding to the start and end of a WAV clip.

    Whisper mis-transcribes short clips when the first or last syllable is
    partially clipped (common with hotkey-triggered recording, where PyAudio
    takes ~50-200ms to spin up the input stream). Padding gives Whisper's
    attention mechanism clean silence boundaries and prevents the language
    model from "guessing" at half-heard words.
    """
    import io
    import wave
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        silence_frames = int(params.framerate * padding_ms / 1000)
        silence_bytes = b"\x00" * (silence_frames * params.sampwidth * params.nchannels)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setparams(params)
            w.writeframes(silence_bytes + frames + silence_bytes)
        return out.getvalue()
    except Exception:
        return audio_bytes


def _split_audio_on_silence(
    audio_bytes: bytes,
    target_chunk_s: float = 120.0,
    hard_max_s: float = 150.0,
    window_ms: int = 30,
    min_silence_run_ms: int = 300,
    max_single_shot_bytes: int = 18 * 1024 * 1024,
) -> list:
    """Split a VERY long WAV clip into <= ~hard_max_s chunks at quiet points.

    v3.14.79 — threshold raised from 30 s to 150 s after the 30 s version made
    things WORSE, not better. The original premise (Whisper truncates clips
    over ~30 s) turned out to be false for Groq Whisper: live tests transcribed
    51 s, 64 s and 111 s real recordings FULLY and correctly in a single call.
    Meanwhile the 30 s chunking actively caused truncation — it split one
    reliable Groq call into 3+ separate calls, and on a rate-limited free tier
    (or when a silence boundary left a chunk mostly quiet) the later chunks came
    back nearly empty (observed live: a 55.5 s clip -> chunks of 70 / 9 / 0
    words). The inconsistency the user saw (lost content at the start, middle,
    or end) was exactly this: whichever chunk degraded.

    v3.14.80 made the trigger FILE SIZE, not duration (24 MB). v3.14.85
    lowered it to 18 MB after live evidence that near-max single uploads are
    UNRELIABLE on real networks: the same 23.2 MB WAV failed with a bare
    "Connection error." on Groq, succeeded on OpenAI, then failed on OpenAI
    twenty minutes later — a ~23 MB POST against a busy uplink is a coin
    flip, and the user experiences it as "half my recording is missing".
    Clips over 18 MB (~9.4 min; rarer than 1 in 200 recordings) now split at
    silence into ~120 s chunks whose ~4 MB uploads are reliably small (a
    4-min chunk was proven live to transcribe 100% complete). Everything
    under 18 MB — every normal dictation — still goes single-shot, which
    remains the path that reliably transcribes the whole thing.

    Returns a list of WAV-byte chunks. For clips short enough (the overwhelming
    common case), unusual formats, or any error, returns ``[audio_bytes]``
    unchanged — the single-shot path.
    """
    import io
    import wave
    try:
        import numpy as np
        with wave.open(io.BytesIO(audio_bytes), "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        framerate = params.framerate
        sampwidth = params.sampwidth
        nchannels = params.nchannels
        nframes = params.nframes

        # Single-shot unless the FILE itself is large enough to risk the
        # provider upload limit. Whisper transcribes multi-minute clips fully in
        # one call, so chunking now exists ONLY as a last-resort guard against
        # the ~25 MB file-size cap — not as a duration limit. At 16 kHz mono
        # 16-bit (32 KB/s) the 24 MB threshold is ~12.5 min, and Waffler
        # auto-stops recording at 12 min, so in practice NOTHING here ever
        # splits: every real dictation goes single-shot. (Splitting by
        # *duration* used to cause the very truncation it was meant to prevent —
        # see the module history above. This is the "chunking removed" change.)
        if (
            len(audio_bytes) <= max_single_shot_bytes
            or sampwidth != 2
            or nchannels != 1
            or nframes == 0
        ):
            return [audio_bytes]

        samples = np.frombuffer(frames, dtype=np.int16)
        win = max(1, int(framerate * window_ms / 1000))
        n_win = len(samples) // win
        if n_win < 2:
            return [audio_bytes]

        # Per-window RMS energy.
        block = samples[: n_win * win].astype(np.float32).reshape(n_win, win)
        rms = np.sqrt(np.mean(block * block, axis=1))

        # Silence threshold: a small fraction of the median energy, floored at
        # an absolute value so a near-silent recording doesn't flag everything.
        # 0.15 * median sits well below speech but above room tone / breaths.
        sil_thresh = max(150.0, float(np.median(rms)) * 0.15)
        is_sil = rms < sil_thresh

        target_win = max(1, int(target_chunk_s * 1000 / window_ms))
        hard_win = max(target_win + 1, int(hard_max_s * 1000 / window_ms))
        min_run = max(1, int(min_silence_run_ms / window_ms))

        # Greedy cut points (window indices): once a chunk passes the target
        # length, cut at the next silence run; if none appears by the hard max,
        # force-cut so a continuous loud monologue is still bounded.
        cuts = []
        start = 0
        i = 0
        while i < n_win:
            length = i - start
            if length >= hard_win:
                cuts.append(i)
                start = i
                i += 1
                continue
            if length >= target_win and is_sil[i]:
                run_end = min(i + min_run, n_win)
                if bool(np.all(is_sil[i:run_end])):
                    cuts.append(i)
                    start = i
                    while i < n_win and is_sil[i]:
                        i += 1
                    continue
            i += 1

        if not cuts:
            return [audio_bytes]

        boundaries = [0] + [c * win for c in cuts] + [len(samples)]
        chunks = []
        for a, b in zip(boundaries[:-1], boundaries[1:]):
            if b - a < win:  # skip empty / sub-window slivers
                continue
            seg = samples[a:b].tobytes()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as ww:
                ww.setnchannels(nchannels)
                ww.setsampwidth(sampwidth)
                ww.setframerate(framerate)
                ww.writeframes(seg)
            chunks.append(buf.getvalue())

        return chunks if len(chunks) >= 2 else [audio_bytes]
    except Exception as e:
        _wlog(f"[whisper] chunk-split failed, using single-shot: {e}")
        return [audio_bytes]


# Below this many measured seconds of speech a clip is treated as effectively
# silent, which is the only situation where discarding transcript text is
# justified. Measured from the audio, never inferred from the text.
_NEAR_SILENCE_S = 1.5


def _strip_hallucinations(text: str, speech_seconds: float = None) -> str:
    """Remove stock Whisper outros without eating the speaker's own words.

    Whisper's training data skews to YouTube, so on silence or poor audio it
    invents channel-end outros. Those are APPENDED AFTER a finished clause.
    Legitimate speech that happens to end with the same words is
    GRAMMATICALLY INTEGRATED into the sentence:

        hallucination : "That is the plan. Thanks for watching!"
        real speech   : "The tutorial ends by saying thanks for watching."

    Before v3.14.86 the patterns were anchored only to end-of-string, so both
    were truncated. Reproduced offline: "Please send the deck to Priya and
    thank you." -> "...to Priya and"; "Over to you." -> "Over to";
    "...benefits and more." -> "...benefits"; "Thank you." -> "". That is
    silent, unrecoverable loss of the user's words, and the dangling function
    word ("Over to") is the signature of a phrase that was never an outro.

    Three guards, in order of strength:

    1. BOUNDARY. A phrase is only a candidate when it starts its own sentence.
       Unambiguous YouTube outros also accept a comma boundary ("...Monday,
       please subscribe!"); short high-risk phrases ("thank you", bare "you",
       "and more") require a full stop, because they are everywhere in
       ordinary speech.
    2. NO DANGLING WORD. If removing the phrase would leave a trailing
       conjunction/preposition, it was integrated speech — the strip is
       rejected.
    3. AUDIO EVIDENCE. ``speech_seconds`` (measured, not guessed) decides the
       genuinely ambiguous cases. A transcript is never blanked when the
       recording actually contained speech; "Thank you." with four seconds of
       speech is the user talking, the same text on a silent clip is not.

    ``speech_seconds=None`` means "unknown" and keeps the conservative
    behaviour: guards 1 and 2 still apply, so integrated speech is safe.
    """
    # Unambiguous channel outros: accept a sentence end OR a comma before them.
    _STRONG_TAIL_PATTERNS = [
        r"thanks for watching[.!?]*",
        r"thanks for listening[.!?]*",
        r"(?:please|remember to|don'?t forget to|and|like and|so please)\s+subscribe[.!?]*",
        r"subscribe to (?:my|the|our) channel[.!?]*",
        r"subscribe[.!?]*",
        r"see you (?:in the next one|next time|later|in the next video)[.!?]*",
        r"hit the like button[.!?]*",
        r"smash that like button[.!?]*",
        r"subtitles by .*",
        r"translated by .*",
        r"captioned by .*",
        r"closed\s+caption(?:s|ing)?\s+(?:by|provided\s+by)\s+.*",
        r"caption(?:s|ing)?\s+provided\s+by\s+.*",
    ]
    # Short phrases that are common in real speech: own-sentence only.
    _WEAK_TAIL_PATTERNS = [
        r"thank you[.!?]*",
        r"thanks[.!?]*",
        r"you[.!?]*",
        r"(?:and|with|plus)\s+(?:many\s+|much\s+|lots\s+)?more[.!?]*",
        # List completions the decoder falls into after a weak window (see
        # _DERAIL_TAIL_RE). Own-sentence only: "...to Malak and the rest of
        # the team." inside a sentence is real speech and is left alone.
        r"(?:and\s+)?the rest of the team[.!?]*",
        r"and the likes[.!?]*",
        r"thank you for watching[.!?]*",
    ]
    # A trailing one of these proves the removed phrase was part of the clause.
    _DANGLING = {
        "and", "to", "for", "of", "with", "or", "plus", "by", "saying", "the",
        "a", "an", "but", "so", "that", "is", "are", "was", "were", "than",
        "from", "into", "about", "at", "on", "in",
    }

    if not text or not text.strip():
        return ""

    stripped = text.strip()
    original = stripped
    had_speech = speech_seconds is not None and speech_seconds >= _NEAR_SILENCE_S
    # Evidence of silence licenses aggressive filtering; absence of evidence
    # does not. On a clip measured as effectively silent the ENTIRE transcript
    # is model invention, so the boundary and dangling-word guards (which exist
    # to protect real speech) are stood down and the original end-anchored
    # patterns apply. This is the case v3.14.39 was written for: Whisper
    # emitting "and more." on a sub-second clip.
    near_silent = speech_seconds is not None and speech_seconds < _NEAR_SILENCE_S

    def _tidy(s: str) -> str:
        # Drop a comma/semicolon left dangling by a strip, but keep terminators.
        return re.sub(r"[,;\s]+$", "", s).strip()

    def _try(patterns, boundary):
        nonlocal stripped
        for pat in patterns:
            # Near-silence: match the bare pattern anywhere at the end.
            expr = (r"\s*(?:" + pat + r")\s*$") if near_silent else (
                boundary + r"\s*(?:" + pat + r")\s*$")
            m = re.search(expr, stripped, flags=re.IGNORECASE)
            if not m:
                continue
            candidate = _tidy(stripped[: m.start()])
            if not near_silent:
                # Guard 2: never leave a dangling function word.
                words = candidate.rstrip(".!?,").split()
                if words and words[-1].lower() in _DANGLING:
                    continue
                # Guard 3: never blank real speech.
                if not candidate and had_speech:
                    continue
            stripped = candidate

    _try(_STRONG_TAIL_PATTERNS, r"(?:^|(?<=[.!?])|(?<=,))")
    _try(_WEAK_TAIL_PATTERNS, r"(?:^|(?<=[.!?]))")

    if not stripped or stripped in (".", ",", "!"):
        # Everything was boilerplate. Only honour that when the audio agrees
        # (or is unknown); with measured speech, keep the words.
        return original if had_speech else ""

    # A near-empty remainder after a strip used to be discarded unconditionally,
    # which deleted real short dictations. It is now an audio-evidenced call:
    # only discard when the clip was effectively silent.
    if (len(stripped) < len(original)
            and len(stripped.split()) <= 2
            and speech_seconds is not None
            and speech_seconds < _NEAR_SILENCE_S):
        return ""

    return stripped

# Per-request timeout (seconds) for a single transcription call. A chunked
# clip is <= ~30 s of audio, which Groq/OpenAI Whisper turn around in 1-5 s;
# 60 s is generous headroom but still abandons a wedged provider fast so we
# fail over to the next instead of hanging the dictation.
_TRANSCRIBE_TIMEOUT_S = 60.0

# Groq's upload path stalls and dies with a connection error on near-max
# files: proven live 2026-07-29 — a 23.2MB WAV failed ("Connection error.")
# while a 7.7MB clip from the same audio transcribed fine. Above this gate we
# skip Groq entirely and go straight to OpenAI, saving a doomed multi-second
# upload on exactly the recordings that are already the slowest. (With the
# 18MB chunk gate in _split_audio_on_silence no chunk should ever exceed
# this; it remains as an independent safety net.)
_GROQ_MAX_UPLOAD_BYTES = 18 * 1024 * 1024

# ── Transient-error retries and the Groq circuit-breaker ────────────────────
# Both SDK clients run with max_retries=0, so one dropped connection used to
# fail the call outright. With another provider configured that is fine: the
# next one takes the clip at once. But the recommended free setup is a Groq
# key alone, and there one blip also put Groq on a 30 s cooldown (1 h for an
# auth-looking error), during which every dictation failed with "no
# transcription backend available" and was saved as an unsent WAV. Real use
# logged 7 Groq connection errors and 5 lost dictations.
#
# Now: the LAST provider left to try gets up to _TRANSIENT_RETRIES more
# attempts on a timeout, a 5xx or a connection error, with jittered
# exponential backoff, and only while the time already spent on that provider
# plus the next wait stays inside _TRANSIENT_RETRY_BUDGET_S. A request that
# has already run into its 60 s timeout is therefore not repeated, so a
# retry can never double a long wait. Earlier providers are not retried:
# falling over to the next one is faster than waiting.
_TRANSIENT_RETRIES = 2
_TRANSIENT_BACKOFF_S = 0.6
_TRANSIENT_RETRY_BUDGET_S = 20.0
# Cooldowns only apply when another speech provider can take the clip. When
# Groq is the only one, it is never skipped: a skipped sole provider is a
# guaranteed failure, while a retried one usually works.
_GROQ_AUTH_COOLDOWN_S = 60.0   # was 3600: a VPN switch should not cost an hour
_GROQ_ERROR_COOLDOWN_S = 30.0

# Patched by tests so retries do not really wait.
_retry_sleep = time.sleep


def _classify_asr_error(exc: BaseException) -> str:
    """Sort a transcription failure into 'auth', 'transient' or 'other'.

    Uses the HTTP status and exception type the OpenAI and Groq SDKs attach
    (both name their classes the same way), then falls back to the message
    text the old code matched on, so behaviour is unchanged for anything the
    SDKs do not type.
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__
    text = str(exc)
    lower = text.lower()

    if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
        return "auth"
    if isinstance(status, int):
        if status >= 500 or status == 408:
            return "transient"
        return "other"
    if name in ("APIConnectionError", "APITimeoutError", "InternalServerError",
                "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
                "PoolTimeout", "ReadError", "WriteError", "RemoteProtocolError"):
        return "transient"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "transient"
    if any(s in text for s in ("403", "401")) or any(
        s in lower for s in ("access denied", "unauthorized", "permission")
    ):
        return "auth"
    if any(s in lower for s in ("connection error", "timed out", "timeout",
                                "connection reset", "connection aborted")):
        return "transient"
    return "other"


def _upload_timeout_s(nbytes: int) -> float:
    """Per-request timeout scaled to upload size.

    The flat 60s client timeout strangled big uploads: 23MB needs a sustained
    ~3.1 Mbps uplink to fit inside 60s, so on a busy connection the POST dies
    as a bare "Connection error" and the dictation loses its transcript.
    Base 60s + ~6s per MB above 4MB, capped at 240s: 4MB -> 60s, 18MB -> 144s.
    """
    mb = nbytes / (1024.0 * 1024.0)
    return min(240.0, max(_TRANSCRIBE_TIMEOUT_S, 60.0 + (mb - 4.0) * 6.0))

# Incomplete-transcript detector. Real dictation is never sustained below
# ~1.5 words per second OF SPEECH. Recalibrated for the noise-floor-relative
# ``_speech_seconds`` (v3.14.97): across 160 real recordings measured that way
# the healthy distribution was p5 = 1.74, p10 = 2.0, median 3.0 w/s, and the
# confirmed-broken ones sat at 0.47, 0.73 and 1.31 (the 08:47 clip that lost
# half its words to a "Thank you for watching!" derailment). The old 1.0
# floor was tuned against a speech measure that undercounted ~2.5x, which is
# why it never fired on a real loss. Below this, with enough speech to trust
# the measurement, the transcript is near-certainly missing content and is
# worth a retry on the other provider.
_MIN_WORDS_PER_SPEECH_SEC = 1.5
_MIN_SPEECH_S_FOR_RETRY = 10.0
# The alternate provider's transcript replaces the original only when it is
# meaningfully fuller — not for noise-level differences.
_RETRY_IMPROVEMENT_FACTOR = 1.25
# When the transcript ENDS on a known decoder-derailment phrase the loss is
# already evidenced, so a smaller gain is enough to accept the retry. A
# 6-window clip that loses its last window recovers only ~1.2x, which the
# rate-triggered factor above would refuse.
_TAIL_RETRY_IMPROVEMENT_FACTOR = 1.10

# Whisper decoder derailments. When a window of a long clip is uncertain the
# decoder falls into a stock continuation instead of the audio: the YouTube
# outro family, and list completions. "and the rest of the team" was
# produced 27 times between June and September 2026 for one user because the
# custom-vocabulary prompt (a comma list of colleagues' names) read to the
# model like a roll-call, so any weak window became "...and the rest of the
# team." Proven on retained audio: 80 s clip -> 159 words ending in that
# phrase with the prompt, 214 correct words without it. A transcript that
# ends on one of these, after real speech, is treated as suspect and retried.
_DERAIL_TAIL_RE = re.compile(
    r"(?:^|(?<=[.!?,]))\s*(?:"
    r"(?:and\s+)?the\s+rest\s+of\s+the\s+team"
    r"|and\s+the\s+likes"
    r"|thanks?\s+(?:you\s+)?for\s+(?:watching|listening)"
    r"|see\s+you\s+in\s+the\s+next\s+(?:one|video)"
    r"|subtitles\s+by\s+.*"
    r")[.!?]*\s*$",
    re.IGNORECASE,
)


def _ends_on_derailment(text: str) -> bool:
    """True when the transcript's final clause is a known derailment phrase
    standing as its own sentence (or the whole output). A phrase used inside a
    real sentence ("send it to Malak and the rest of the team") is not
    preceded by punctuation and does not match."""
    return bool(text) and _DERAIL_TAIL_RE.search(text.strip()) is not None


# Words whose loss inverts an instruction. A retry that drops one of these has
# not recovered the sentence, it has replaced it with the opposite sentence.
_NEGATIONS = {
    "not", "no", "never", "none", "nothing", "nobody", "nowhere", "cannot",
    "nor", "neither", "without", "dont", "doesnt", "didnt", "wont", "cant",
    "couldnt", "shouldnt", "wouldnt", "isnt", "arent", "wasnt", "werent",
    "hasnt", "havent", "hadnt",
}

# How much of the original must survive in the alternate for the two to be
# plausibly the same utterance rather than a different one.
_MIN_TOKEN_OVERLAP = 0.5


def _normalise_tokens(text: str):
    """Lowercase word tokens with punctuation stripped, so "don't", "dont" and
    "Don't." all compare equal.

    Apostrophes are removed BEFORE splitting: otherwise "can't" tokenises as
    "can" plus "t" and the negation disappears, which is exactly the case the
    negation guard exists to catch.
    """
    cleaned = (text or "").lower().replace("'", "").replace("’", "")
    return [t for t in re.findall(r"[a-z0-9]+", cleaned) if t]


def _alternate_is_safe_replacement(original: str, alternate: str):
    """Decide whether a retry's transcript may REPLACE the first one.

    Returns ``(safe, reason)``.

    The retry fires when a transcript is impossibly short for the measured
    speech, so a real recovery is *the same speech, more completely
    transcribed*. Word count alone cannot tell that apart from a different or
    invented utterance, and acting on count alone allowed a longer transcript
    to silently reverse an instruction:

        "Do not transfer the money to that account."
        -> "Please transfer the money to that account right now..."

    Three checks, each of which can only ever REFUSE a replacement, so the
    worst outcome is keeping the transcript we already had:

    1. Overlap. Most of the original's words should still be present, since
       the alternate is meant to contain the same speech and more.
    2. Negation. A negation in the original that is absent from the alternate
       means the sentence now says the opposite. Disqualifying.
    3. Numbers. A figure in the original that is absent from the alternate has
       been changed or dropped, which is precisely the detail a user cannot
       afford to have quietly rewritten.
    """
    alt_tokens = _normalise_tokens(alternate)
    if not alt_tokens:
        return False, "alternate is empty"

    orig_tokens = _normalise_tokens(original)
    if not orig_tokens:
        return True, "original was empty"

    orig_set, alt_set = set(orig_tokens), set(alt_tokens)

    missing_negations = {t for t in orig_set if t in _NEGATIONS} - alt_set
    if missing_negations:
        return False, f"alternate drops negation {sorted(missing_negations)}"

    orig_numbers = {t for t in orig_set if any(c.isdigit() for c in t)}
    missing_numbers = orig_numbers - alt_set
    if missing_numbers:
        return False, f"alternate changes/drops number {sorted(missing_numbers)}"

    overlap = len(orig_set & alt_set) / len(orig_set)
    if overlap < _MIN_TOKEN_OVERLAP:
        return False, (f"only {overlap:.0%} token overlap - the two look like "
                       f"different utterances")

    return True, f"{overlap:.0%} token overlap, no contradictions"


# Speech-window threshold for ``_speech_seconds``: see its docstring.
_SPEECH_RMS_FLOOR = 12.0
_SPEECH_NOISE_MULTIPLIER = 3.0


def _speech_seconds(audio_bytes: bytes) -> float:
    """Estimate seconds of actual speech in a WAV via per-window RMS.

    30 ms windows; a window counts as speech when its RMS clears
    ``max(_SPEECH_RMS_FLOOR, 3 x noise floor)`` where the noise floor is the
    10th-percentile window RMS of the clip itself.

    Until v3.14.97 the threshold was ``max(150, 15% of median RMS)``, i.e. a
    fixed 150 floor. That is a fine cut point for *splitting* audio at
    silence, but as a speech measure it undercounted a normally gained
    laptop mic (median window RMS ~50) by ~2.5x: a 79 s clip with 64 s of
    audible speech measured 23 s. Every consumer of this number (the
    incomplete-transcript retry, the quality signals, the near-silence
    licence in the hallucination filter, the short-tap guard) was therefore
    reasoning from a third of the real speech. The floor of 12 is the same
    one the capture diagnostics have used since v3.14.5 (room tone on a
    well-gained mic is ~3-8 RMS); the 3x-noise-floor term keeps a noisy mic
    from counting hiss as speech. Returns 0.0 for malformed input — callers
    treat that as "cannot judge, don't retry".
    """
    import io
    import wave
    try:
        import numpy as np
        with wave.open(io.BytesIO(audio_bytes), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                return 0.0
            framerate = w.getframerate()
            frames = w.readframes(w.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16)
        win = max(1, int(framerate * 30 / 1000))
        n_win = len(samples) // win
        if n_win < 1:
            return 0.0
        block = samples[: n_win * win].astype(np.float32).reshape(n_win, win)
        rms = np.sqrt(np.mean(block * block, axis=1))
        noise_floor = float(np.percentile(rms, 10))
        # The old split-oriented threshold is kept as a CEILING: a clip that
        # is speech from end to end (a 0.3 s "Yes") has no quiet windows, so
        # its 10th percentile is speech and 3x that would exclude everything.
        # Capping at the old value means this measure can only ever count
        # more speech than before, never less.
        legacy = max(150.0, float(np.median(rms)) * 0.15)
        thresh = max(_SPEECH_RMS_FLOOR,
                     min(noise_floor * _SPEECH_NOISE_MULTIPLIER, legacy))
        return float((rms >= thresh).sum()) * (win / float(framerate))
    except Exception:
        return 0.0


def _normalize_transcriber_order(order) -> list:
    """Return a clean provider permutation from user input for transcription.

    Mirrors the styler's normalizer: dedupes, lowercases, drops unknowns, and
    appends any missing canonical providers. Cerebras is kept here (so the
    relative order is preserved) but the caller filters it out because it has
    no speech-to-text endpoint. Bad input -> canonical default.
    """
    valid = ["groq", "cerebras", "openai"]
    default = ["groq", "cerebras", "openai"]
    out = []
    try:
        for p in (order or []):
            p = str(p).strip().lower()
            if p in valid and p not in out:
                out.append(p)
    except Exception:
        return list(default)
    for p in default:
        if p not in out:
            out.append(p)
    return out


class WhisperTranscriber:
    """Transcribes audio via a configurable cloud provider order + local fallbacks.

    Cloud transcribers (Groq Whisper, OpenAI Whisper) are tried in the user's
    configured ``provider_order`` (Cerebras is skipped — it has no speech-to-text
    API). On-device backends (mlx / faster-whisper, gated by LOCAL_WHISPER=1)
    take precedence when enabled. Each cloud call has a 60 s timeout.
    """

    def __init__(self, api_key: str = "", model: str = "",
                 groq_api_key: str = "", provider_order=None):
        self.api_key = api_key
        # Default to the newer, cheaper, better gpt-4o-mini-transcribe ($0.003/min
        # vs whisper-1's $0.006/min). Users can override via the OPENAI_WHISPER_MODEL
        # env var — e.g. "gpt-4o-transcribe" for max quality at the old whisper-1
        # price, or "whisper-1" to force the legacy model.
        if not model:
            model = os.getenv("OPENAI_WHISPER_MODEL", "gpt-4o-mini-transcribe")
        self.model   = model
        self.groq_api_key = groq_api_key
        # Cloud transcriber order, filtered to providers that can do
        # speech-to-text (groq, openai). Cerebras has no Whisper endpoint so
        # it's dropped here; the relative order of groq vs openai is honoured.
        self._cloud_order = [
            p for p in _normalize_transcriber_order(provider_order)
            if p in ("groq", "openai")
        ]
        self.client  = (
            OpenAI(api_key=api_key, timeout=_TRANSCRIBE_TIMEOUT_S, max_retries=0)
            if api_key else None
        )
        self._groq_client = None
        # Monotonic-clock deadline: until this timestamp, skip Groq entirely
        # and call OpenAI directly. Set when Groq returns a 403/401/auth error
        # (typically a VPN exit-IP block — Groq hard-rejects many VPN nodes
        # before authentication). Mirror of the same flag on OpenAIStyler.
        # Without this every recording wastes ~150-300 ms on a dead Groq
        # round-trip before fallback. Reset on process restart. Only set, and
        # only honoured, when OpenAI is configured too: with Groq as the sole
        # speech provider a cooldown would fail every dictation inside it.
        self._groq_skip_until = 0.0

        # Try Groq first (fastest cloud option)
        if groq_api_key and _groq_mod:
            self._groq_client = _groq_mod.Groq(
                api_key=groq_api_key, timeout=_TRANSCRIBE_TIMEOUT_S, max_retries=0
            )
            self._backend = "groq"
            print("⚡ Transcription: Groq Whisper (fastest)")
        elif _USE_LOCAL and _mlx_whisper:
            self._backend = "mlx"
            print("⚡ Transcription: local mlx-whisper (no API calls)")
        elif _USE_LOCAL and _faster_whisper:
            self._backend = "faster"
            print("⚡ Transcription: local faster-whisper (no API calls)")
        elif api_key:
            self._backend = "api"
            if _USE_LOCAL:
                print("⚠️  Falling back to OpenAI API (local model not loaded)")
        else:
            self._backend = "api"
            print("⚠️  No transcription backend available")

    def _call_with_transient_retries(self, prov: str, fn, audio_bytes: bytes,
                                     retry: bool) -> str:
        """Call ``fn(audio_bytes)``, retrying timeouts, 5xx and connection
        errors when ``retry`` is set. See _TRANSIENT_RETRIES for the rules."""
        import random as _random
        import time as _time
        started = _time.monotonic()
        attempt = 0
        while True:
            try:
                return fn(audio_bytes)
            except Exception as e:
                if not retry or attempt >= _TRANSIENT_RETRIES:
                    raise
                kind = _classify_asr_error(e)
                if kind != "transient":
                    raise
                delay = _TRANSIENT_BACKOFF_S * (2 ** attempt) * _random.uniform(0.5, 1.5)
                spent = _time.monotonic() - started
                if spent + delay > _TRANSIENT_RETRY_BUDGET_S:
                    _wlog(f"[whisper] {prov} {type(e).__name__} after {spent:.1f}s: "
                          f"no retry, the {_TRANSIENT_RETRY_BUDGET_S:.0f}s budget is spent")
                    raise
                attempt += 1
                _wlog(f"[whisper] {prov} {type(e).__name__} ({str(e)[:60]}); "
                      f"retry {attempt}/{_TRANSIENT_RETRIES} in {delay:.1f}s")
                _retry_sleep(delay)

    def _dispatch_one(self, audio_bytes: bytes, exclude: str = None) -> str:
        """Transcribe ONE already-padded/chunked WAV blob.

        On-device backends (mlx / faster-whisper) take precedence when enabled.
        Otherwise cloud transcribers (Groq Whisper, OpenAI Whisper) are tried in
        the user's configured order, with the Groq 403/auth circuit-breaker.

        ``exclude`` skips one named cloud provider — used by the
        incomplete-transcript retry to force the ALTERNATE provider.
        Sets ``self._last_cloud_provider`` to whichever provider produced the
        returned text (None for local backends).

        Extracted from ``transcribe_sync`` so the long-recording chunk loop can
        call it per chunk without duplicating the fallback logic.
        """
        # On-device backends are an explicit local choice — order doesn't apply.
        if self._backend == "mlx":
            return self._transcribe_mlx(audio_bytes)
        if self._backend == "faster":
            return self._transcribe_faster(audio_bytes)

        # Cloud path: walk the configured cloud order (groq / openai), honouring
        # the Groq cooldown, and fall through to the next available.
        import time as _time
        order = getattr(self, "_cloud_order", None) or ["groq", "openai"]
        if exclude:
            order = [p for p in order if p != exclude]
        # Groq is the only speech provider when no OpenAI client exists. Then
        # it is never skipped, for a cooldown or for the upload-size gate:
        # skipping the sole provider is a certain failure.
        groq_only = getattr(self, "client", None) is None
        candidates = []
        for prov in order:
            if prov == "groq":
                if getattr(self, "_groq_client", None) is None:
                    continue
                if not groq_only:
                    # Circuit-breaker: skip Groq during its cooldown.
                    if _time.monotonic() < getattr(self, "_groq_skip_until", 0.0):
                        continue
                    # Near-max uploads make Groq stall and die with a connection
                    # error (proven live at 23.2MB; fine at 7.7MB). Don't waste a
                    # doomed upload — let OpenAI take it directly.
                    if len(audio_bytes) > _GROQ_MAX_UPLOAD_BYTES:
                        _wlog(f"[whisper] clip {len(audio_bytes)/1e6:.1f}MB > Groq "
                              f"upload gate — going straight to OpenAI")
                        continue
                candidates.append(prov)
            elif prov == "openai":
                if getattr(self, "client", None) is None:
                    continue
                candidates.append(prov)

        last_err = None
        for i, prov in enumerate(candidates):
            # Only the last provider left is retried; before that, moving on
            # to the next provider is the faster recovery.
            retry = i == len(candidates) - 1
            if prov == "groq":
                try:
                    _result = self._call_with_transient_retries(
                        "groq", self._transcribe_groq, audio_bytes, retry)
                    self._last_cloud_provider = "groq"
                    return _result
                except Exception as e:
                    last_err = e
                    err = str(e)
                    kind = _classify_asr_error(e)
                    if groq_only:
                        print(f"⚠️  Groq transcription failed ({err[:80]}); Groq is the "
                              f"only speech provider, so it is not paused")
                    elif kind == "auth":
                        self._groq_skip_until = _time.monotonic() + _GROQ_AUTH_COOLDOWN_S
                        print(f"⚠️  Groq auth/network blocked: skipping Groq transcription "
                              f"for {_GROQ_AUTH_COOLDOWN_S:.0f}s ({err[:80]})")
                    else:
                        self._groq_skip_until = _time.monotonic() + _GROQ_ERROR_COOLDOWN_S
                        print(f"⚠️  Groq transcription failed ({err[:80]}), trying next provider")
                    continue
            elif prov == "openai":
                try:
                    _result = self._call_with_transient_retries(
                        "openai", self._transcribe_api, audio_bytes, retry)
                    self._last_cloud_provider = "openai"
                    return _result
                except Exception as e:
                    last_err = e
                    print(f"⚠️  OpenAI transcription failed ({str(e)[:80]}), trying next provider")
                    continue

        # Nothing in the order worked. Re-raise the last real error, or fall
        # back to whatever single backend was configured at init.
        if last_err is not None:
            raise last_err
        if self.client is not None:
            _result = self._transcribe_api(audio_bytes)
            self._last_cloud_provider = "openai"
            return _result
        raise RuntimeError("no transcription backend available")

    def _retry_if_incomplete(self, audio_bytes: bytes, transcript: str) -> str:
        """Detect a transcript that is impossibly short for the measured speech
        and retry on the alternate cloud provider.

        The live failure this exists for: 57 seconds of MEASURED speech came
        back as 18 words (0.32 words/speech-second) — the Whisper layer
        silently dropped ~85% of a dictation. Real speech never sustains below
        ~1 word/sec, so that ratio is a reliable broken-transcript signal
        (across 205 real recordings every healthy one measured >= 1.17).

        The alternate provider's transcript replaces the original only when
        meaningfully fuller (>= 1.25x the words). Any error in the retry path
        keeps the original — this can only ever improve the result.
        """
        try:
            if self._backend in ("mlx", "faster"):
                return transcript  # no alternate provider to retry on
            words = len((transcript or "").split())
            speech_s = _speech_seconds(audio_bytes)
            if speech_s < _MIN_SPEECH_S_FOR_RETRY:
                return transcript
            wps = words / speech_s
            # Two independent triggers. A low word rate catches a transcript
            # that lost most of the clip; a derailment tail catches the case
            # where only the final window was replaced (the rate then looks
            # healthy: 159 words for 57 s is 2.8 w/s, but the last 20 s of
            # speech had become "and the rest of the team.").
            tail_derailed = _ends_on_derailment(transcript)
            if wps >= _MIN_WORDS_PER_SPEECH_SEC and not tail_derailed:
                return transcript
            factor = _RETRY_IMPROVEMENT_FACTOR
            if tail_derailed:
                factor = _TAIL_RETRY_IMPROVEMENT_FACTOR
                why_suspect = "ends on a known decoder-derailment phrase"
            else:
                why_suspect = f"{wps:.2f} w/s is below {_MIN_WORDS_PER_SPEECH_SEC}"
            first_provider = getattr(self, "_last_cloud_provider", None)
            _wlog(f"[whisper] SUSPICIOUS transcript: {words} words for "
                  f"{speech_s:.1f}s of speech ({why_suspect}) via "
                  f"{first_provider or '?'} — retrying on alternate provider")
            self.last_retry_fired = True
            alt = self._dispatch_one(audio_bytes, exclude=first_provider)
            alt_words = len((alt or "").split())
            if alt_words < max(1, words) * factor:
                _wlog(f"[whisper] retry no better ({alt_words} words) — keeping original")
                return transcript
            # Being longer is necessary but nowhere near sufficient. Replacing
            # the user's transcript is destructive, so the alternate has to look
            # like MORE OF THE SAME SPEECH rather than a different utterance.
            safe, why = _alternate_is_safe_replacement(transcript, alt)
            if not safe:
                self.last_retry_rejected = True
                _wlog(f"[whisper] retry produced {alt_words} words (was {words}) "
                      f"but REJECTED: {why}. Keeping the original transcript; "
                      f"this recording is flagged as possibly incomplete.")
                return transcript
            _wlog(f"[whisper] retry recovered {alt_words} words "
                  f"(was {words}) — using alternate transcript ({why})")
            return alt
        except Exception as e:
            _wlog(f"[whisper] incomplete-transcript retry failed: {e}")
            return transcript

    def transcribe_sync(self, audio_bytes: bytes):
        # Provenance, reset per recording so a stale response from an earlier
        # dictation can never be attributed to this one. These are the
        # UNTOUCHED provider words: everything below (hallucination filter,
        # vocab-echo discard, boilerplate discard) edits a copy, and the
        # caller stores both so a filtering mistake stays recoverable.
        self.last_asr_response = ""
        self.last_asr_filtered = False
        # Measured speech and whether a cross-provider retry fired. Surfaced so
        # the quality signals are computed from evidence, not guesses.
        self.last_speech_seconds = 0.0
        self.last_retry_fired = False
        # Set when a retry produced a longer transcript that was refused as
        # unsafe. The recording is then known to be suspect AND unrecovered,
        # which is worth telling the user about.
        self.last_retry_rejected = False
        audio_bytes = _pad_audio_with_silence(audio_bytes)

        # Long-recording fix: split clips over ~30 s into <= 25-30 s chunks on
        # silence so Whisper's decoder doesn't terminate early and drop the
        # tail (the "speaks for a minute, only the first 20 s survives + a
        # 'Thank you for watching!' hallucination" bug). Short clips -- the
        # common case -- come back as a single chunk and take the unchanged
        # single-shot path.
        chunks = _split_audio_on_silence(audio_bytes)
        # Measure speech ONCE here: the hallucination filter and the
        # incomplete-transcript check both need audio evidence rather than
        # guesses about the text, and measuring twice on a 20 MB clip is
        # wasted work.
        _clip_speech_s = _speech_seconds(audio_bytes)
        self.last_speech_seconds = _clip_speech_s
        # Diagnostic: how long was the clip and did we split it? Via _wlog so it
        # actually lands in app.log (unlike the old print()s).
        try:
            import io as _io, wave as _wave
            with _wave.open(_io.BytesIO(audio_bytes), "rb") as _w:
                _clip_s = _w.getnframes() / float(_w.getframerate() or 1)
            _wlog(f"[whisper] clip={_clip_s:.1f}s -> {len(chunks)} chunk(s)")
        except Exception:
            pass
        if len(chunks) > 1:
            parts = []
            for idx, ch in enumerate(chunks):
                part = self._dispatch_one(ch)
                # Strip a hallucinated outro PER CHUNK so a fake ending Whisper
                # tacks onto one chunk doesn't land in the middle of the joined
                # transcript. (The trailing strip below still covers chunk N.)
                part = _strip_hallucinations(
                    part, speech_seconds=_speech_seconds(ch)
                ).strip()
                _wlog(f"[whisper] chunk {idx+1}/{len(chunks)} -> {len(part.split())} words")
                if part:
                    parts.append(part)
            raw = " ".join(parts)
        else:
            raw = self._dispatch_one(chunks[0])
            _wlog(f"[whisper] single-shot -> {len(raw.split())} words")

        # Incomplete-transcript safety net: if the word count is impossibly low
        # for the measured seconds of speech, retry on the alternate provider
        # and keep whichever transcript is fuller. (The live failure: 57s of
        # speech -> 18 words, ~85% of a dictation silently gone.)
        raw = self._retry_if_incomplete(audio_bytes, raw)

        # Snapshot the provider's words BEFORE any filtering runs.
        self.last_asr_response = raw

        # Pass MEASURED speech duration so the filter decides ambiguous
        # cases on audio evidence instead of guessing from the text.
        cleaned = _strip_hallucinations(raw, speech_seconds=_clip_speech_s)
        if cleaned != raw:
            # Metadata only — don't print the transcript text (PII; app.log
            # ships in the Download Logs bundle).
            print(f"[whisper] Stripped hallucination ({len(raw)}→{len(cleaned)} chars)")

        # Whisper sometimes echoes the vocab prompt verbatim on silence —
        # discard rather than pasting the user's vocabulary list as output.
        try:
            vocab = load_vocab()
        except Exception:
            vocab = []
        if _is_vocab_echo(cleaned, vocab):
            print(f"[whisper] Discarded vocab-echo hallucination: '{cleaned}'")
            self.last_asr_filtered = True
            return ""

        # Discard known boilerplate Whisper produces on silence / near-silence
        # ("Thanks for watching!", "Please subscribe", etc.) — but ONLY when the
        # audio agrees it was silence. A user who genuinely says just "Thank
        # you." or "Thanks." produces text identical to the classic
        # hallucination, and blanking that is silent loss of their words.
        if _is_whisper_hallucination(cleaned):
            if _clip_speech_s >= _NEAR_SILENCE_S:
                _wlog(f"[whisper] boilerplate-shaped text kept: "
                      f"{_clip_speech_s:.1f}s of measured speech says it is real")
            else:
                print(f"[whisper] Discarded boilerplate hallucination: '{cleaned}'")
                self.last_asr_filtered = True
                return ""

        self.last_asr_filtered = cleaned != raw
        return cleaned

    def get_duration_seconds(self) -> float:
        """Return the duration of the last transcription in seconds (API only)."""
        return getattr(self, '_last_duration', 0.0)

    # ── Local backends ───────────────────────────────────────────────────────

    def _transcribe_mlx(self, audio_bytes: bytes) -> str:
        """Apple Silicon — mlx-whisper via Neural Engine."""
        t0 = time.time()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            vocab    = load_vocab()
            hint     = vocab_to_prompt(vocab)
            settings = load_settings()
            lang     = settings.get("language", "en")
            kwargs   = dict(path_or_hf_repo="mlx-community/whisper-base-mlx")
            if hint:
                kwargs["initial_prompt"] = hint
            if lang and lang != "auto":
                kwargs["language"] = lang
            result = _mlx_whisper.transcribe(tmp, **kwargs)
            text = result["text"].strip()
            print(f"⚡ mlx-whisper ({(time.time()-t0)*1000:.0f}ms, {len(text)} chars)")
            return text
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _transcribe_faster(self, audio_bytes: bytes) -> str:
        """Windows / Intel Mac — faster-whisper on CPU."""
        t0 = time.time()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            settings = load_settings()
            lang     = settings.get("language", "en")
            fw_lang  = lang if lang != "auto" else None
            segments, _ = _faster_whisper.transcribe(tmp, beam_size=1, language=fw_lang)
            text = " ".join(seg.text for seg in segments).strip()
            print(f"⚡ faster-whisper ({(time.time()-t0)*1000:.0f}ms, {len(text)} chars)")
            return text
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── Groq (fastest cloud) ─────────────────────────────────────────────────

    def _transcribe_groq(self, audio_bytes: bytes) -> str:
        """Groq Whisper — same model, ~10-50x faster than OpenAI."""
        t0 = time.time()
        print("⚡ Groq Whisper API...")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            settings = load_settings()
            lang     = settings.get("language", "en")
            # The custom vocabulary is deliberately NOT sent as a prompt to
            # whisper-large-v3. Proven on retained audio (v3.14.97): with the
            # name list as the prompt, an 80 s clip came back as 159 words
            # ending "...and the rest of the team." (the whole final window
            # replaced by a list completion); every list-shaped variant
            # derailed the same way ("Thank you for watching!", "Subtitles by
            # the Amara.org community", "and so on."); with no prompt the same
            # model returned all 214 words, twice. On the same day's clips the
            # prompt gave no spelling benefit either ("craic" -> "Craig" both
            # ways); ``apply_vocab_corrections`` downstream fixes those.
            # ``WAFFLER_WHISPER_PROMPT=1`` restores the old behaviour.
            hint = ""
            if os.environ.get("WAFFLER_WHISPER_PROMPT") == "1":
                hint = vocab_to_prompt(load_vocab())
            with open(tmp, "rb") as af:
                kwargs = dict(
                    model="whisper-large-v3",
                    file=af,
                    response_format="text",
                )
                if hint:
                    kwargs["prompt"] = hint
                if lang and lang != "auto":
                    kwargs["language"] = lang
                # Per-request timeout scaled to the upload size — the flat 60s
                # client default kills large uploads on slow uplinks.
                kwargs["timeout"] = _upload_timeout_s(len(audio_bytes))
                response = self._groq_client.audio.transcriptions.create(**kwargs)
            text = response.strip()
            duration = time.time() - t0
            self._last_duration = duration
            print(f"⚡ Groq Whisper ({duration*1000:.0f}ms, {len(text)} chars)")
            return text
        except Exception as e:
            print(f"❌ Groq Whisper error: {e}")
            raise
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── API fallback ─────────────────────────────────────────────────────────

    def _transcribe_api(self, audio_bytes: bytes) -> str:
        """OpenAI Whisper API — always works, needs internet."""
        t0 = time.time()
        print(f"📡 OpenAI Whisper API...")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            vocab    = load_vocab()
            hint     = vocab_to_prompt(vocab)
            settings = load_settings()
            lang     = settings.get("language", "en")
            with open(tmp, "rb") as af:
                kwargs = dict(model=self.model, file=af, response_format="text")
                if hint:
                    kwargs["prompt"] = hint
                if lang and lang != "auto":
                    kwargs["language"] = lang
                # Per-request timeout scaled to the upload size — the flat 60s
                # client default kills large uploads on slow uplinks.
                kwargs["timeout"] = _upload_timeout_s(len(audio_bytes))
                response = self.client.audio.transcriptions.create(**kwargs)
            text = response.strip()
            duration = time.time() - t0
            self._last_duration = duration  # Store for usage tracking
            print(f"✅ API Whisper ({duration*1000:.0f}ms, {len(text)} chars)")
            return text
        except Exception as e:
            print(f"❌ Whisper API error: {e}")
            raise
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
