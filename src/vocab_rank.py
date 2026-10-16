"""Starred Vocabulary words, and the Vocabulary as a speech-to-text prompt.

Stars. A star only decides order: the Vocabulary page's "Starred first" sort,
and which words are kept when the prompt has to be cut short. It is not a
property the matcher reads, so a starred word is corrected exactly as before.

They are kept in their own file, ``vocab_stars.json``, a list of the starred
words, and not as a key on the entries in ``vocab.json``. That file is the
plain list of strings Waffler 3.15.1 and 3.14 read, and an entry that is an
object there is dropped by those versions, so a person who went back to an older
Waffler would lose the word. A separate file is simply not read by them. It is
read against the words that are listed now (any capitals, as the spellings in
``vocab_sounds.json`` are), so a word removed by an older version, or by hand,
takes its star out of the way on the next save.

The prompt. OpenAI's transcription and the local model are given the words as a
bare comma list (``transcribe_whisper.vocab_to_prompt``), which they use as a
hint for spelling. They read only the last few hundred characters of it, so a
list of 500 words was mostly not read, and the part that was read was whatever
happened to be last. ``prompt_words`` picks what to send: when the whole list
fits, all of it in the order it has (starred words first, if there are any);
when it does not, the starred words, then the words that corrected the most
dictations, then the list's own order, until the entries or the characters run
out. The same list always gives the same prompt. Groq is given no prompt (a
word list derailed a long clip there) and does not use this.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

try:
    from atomic_json import write_json_atomic
    from data_paths import data_file
except ImportError:  # imported as src.vocab_rank
    from src.atomic_json import write_json_atomic
    from src.data_paths import data_file

STARS_FILE = "vocab_stars.json"

# Held while the Vocabulary is read, changed and saved as a whole (a save from
# the page, an import), so one cannot land in the middle of the other.
LOCK = threading.Lock()

# What is sent as the prompt, at most. Whisper conditions on about the last 224
# tokens; a name is two to four of them, so this is what can matter.
PROMPT_MAX_ENTRIES = 80
PROMPT_MAX_CHARS = 700
_SEPARATOR = 2          # ", "


def _key(word) -> str:
    return " ".join(str(word).split()).casefold()


def _path(path):
    return Path(path) if path is not None else data_file(STARS_FILE)


def clean_stars(raw, words) -> list:
    """The starred words as they are written in ``words``, in the list's order.
    A star for a word that is not listed (any capitals count as the same word),
    a repeat, or anything that is not text is left out."""
    if not isinstance(raw, list) or not isinstance(words, list):
        return []
    stars = {_key(s) for s in raw if isinstance(s, str) and s.strip()}
    out, seen = [], set()
    for w in words:
        if not isinstance(w, str):
            continue
        k = _key(w)
        if k in stars and k not in seen:
            seen.add(k)
            out.append(w)
    return out


def load_stars(words, path=None) -> list:
    """The saved stars for ``words``. A missing or unreadable file is none."""
    try:
        p = _path(path)
        if p.exists():
            return clean_stars(json.loads(p.read_text(encoding="utf-8-sig")), words)
    except (OSError, ValueError):
        pass
    return []


def save_stars(starred, words, path=None) -> list:
    """Write the stars for ``words`` (UTF-8, through a temporary file) and
    return what was kept. Nothing starred and no file yet writes no file.
    Raises OSError if the file cannot be written; the caller decides what that
    costs (the words are saved either way)."""
    kept = clean_stars(list(starred) if isinstance(starred, (list, tuple)) else [], words)
    p = _path(path)
    if kept or p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, kept)
    return kept


def sync_stars(words, starred=None, path=None) -> list:
    """The stars after the Vocabulary was saved as ``words``. ``starred`` is
    the new set of stars; None keeps the saved ones for the words still listed,
    which takes a removed word's star out of the file. The file is written only
    when it changes, and one that cannot be read is left alone when ``starred``
    is None. Returns the stars for ``words``. Raises OSError if a write fails."""
    p = _path(path)
    if starred is not None:
        return save_stars(starred, words, p)
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else []
    except (OSError, ValueError):
        return []
    kept = clean_stars(raw, words)
    if kept != raw and (kept or p.exists()):
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, kept)
    return kept


def sync_quietly(words, starred=None, log=None) -> list:
    """sync_stars for the bridge: a star that cannot be written costs the
    stars, never the words that were saved. ``log`` gets one line, without any
    word in it."""
    try:
        return sync_stars(words, starred)
    except Exception as e:
        if log:
            log(f"[vocab] stars not saved ({type(e).__name__})")
        return load_stars(words)


def prompt_words(words, starred=(), usage=None, max_entries: int = PROMPT_MAX_ENTRIES,
                 max_chars: int = PROMPT_MAX_CHARS) -> list:
    """The words to send as a prompt, in the order to send them.

    ``usage`` is {word: dictations corrected} (any capitals), or a function
    that returns it: it is asked only when the list has to be cut, and a
    usage that cannot be read ranks nobody. Never raises."""
    listed = [" ".join(w.split()) for w in words if isinstance(w, str) and w.strip()] \
        if isinstance(words, list) else []
    if not listed:
        return []
    star = {_key(s) for s in (starred or ()) if isinstance(s, str)}
    order = {w: i for i, w in reversed(list(enumerate(listed)))}

    def total(items):
        return sum(len(w) for w in items) + _SEPARATOR * max(0, len(items) - 1)

    if len(listed) <= max_entries and total(listed) <= max_chars:
        if not star:
            return listed
        return sorted(listed, key=lambda w: (_key(w) not in star, order[w]))

    counts = {}
    try:
        raw = usage() if callable(usage) else usage
        for name, n in (raw or {}).items():
            if isinstance(name, str) and isinstance(n, (int, float)) and not isinstance(n, bool):
                counts[_key(name)] = int(n)
    except Exception:
        counts = {}
    ranked = sorted(listed, key=lambda w: (_key(w) not in star, -counts.get(_key(w), 0), order[w]))
    out, chars = [], 0
    for w in ranked:
        if len(out) >= max_entries:
            break
        add = len(w) + (_SEPARATOR if out else 0)
        if chars + add > max_chars:
            continue
        out.append(w)
        chars += add
    return out
