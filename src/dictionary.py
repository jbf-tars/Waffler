"""The Vocabulary as a spreadsheet: export, import, and what an import does.

The Vocabulary page already lets a person type a word, and what it sounds
like, one at a time. This is the same list moved in bulk, as a CSV with three
columns:

    word            the entry, as it should be written
    sounds_like     what speech to text writes for it, separated by semicolons
    starred         yes or no (Vocabulary stars, src/vocab_rank.py)

Export writes that, spreadsheet-safe (src/table_io.py) and in the order of the
list. Import reads that, or any file close to it: the columns may be in any
order and named a few ways, or absent (a pasted list of words is one per line,
then what it sounds like), the delimiter may be a comma, a semicolon or a tab.

What an import does is decided here, and only ever adds:

* a word that is not in the list is added, if it passes the rules for a word
  typed on the page (not empty, up to 60 characters, no control or invisible
  characters, at least one letter), the list has room (500), and it is not an
  everyday word with nothing to say what it sounds like (the matcher never
  changes those, so the entry would sit there doing nothing);
* a word that is there (in any capitals) is left exactly as it is, apart from
  the spellings it gains and a star it is given; nothing is ever removed,
  renamed or unstarred;
* a spelling is held to the rules of the page's "sounds like" box: up to 60
  characters, a letter or digit in it, not the word itself, not another word,
  not already how another word sounds, at most 8 for a word. A spelling that
  breaks one is left out and listed, and the word is still added;
* the same word on several rows is one entry that gets all their spellings.

``plan_import`` works on lists and changes none of them. It returns what
the preview shows (counts, who was left out and why) and the lists as they
would be, which the bridge then saves through the ordinary save.
"""

from __future__ import annotations

import unicodedata

try:
    import table_io
    import user_messages
    from transcribe_whisper import (VOCAB_MAX_ENTRIES, VOCAB_MAX_ENTRY_LEN, VOCAB_MAX_SOUNDS,
                                    is_everyday_entry)
except ImportError:  # imported as src.dictionary
    from src import table_io, user_messages
    from src.transcribe_whisper import (VOCAB_MAX_ENTRIES, VOCAB_MAX_ENTRY_LEN, VOCAB_MAX_SOUNDS,
                                        is_everyday_entry)

MAX_WORDS = VOCAB_MAX_ENTRIES
MAX_WORD_LEN = VOCAB_MAX_ENTRY_LEN
MAX_SPELLINGS = VOCAB_MAX_SOUNDS

CSV_COLUMNS = ("word", "sounds_like", "starred")

# The names a column may go by in a file (src/table_io.py column_map).
COLUMN_NAMES = {
    "word": {"word", "words", "term", "terms", "entry", "name", "vocabulary", "correct",
             "correct_spelling", "correction"},
    "sounds_like": {"sounds_like", "soundslike", "sound_like", "sounds", "spellings", "spelling",
                    "misspelling", "misspellings", "misspelt", "misheard", "heard", "alias",
                    "aliases", "variants", "incorrect"},
    "starred": {"starred", "star", "stars", "favorite", "favourite", "pinned"},
}

_TRUE = {"1", "y", "yes", "true", "t", "x", "star", "starred", "★", "⭐", "✓", "✔"}
_FALSE = {"0", "n", "no", "false", "f", "-"}
# Zero-width non-joiner and joiner are part of how some words are written
# (Persian, Hindi, emoji); every other control or invisible character is not.
_ALLOWED_FORMAT = {"‌", "‍"}
_SPLIT_SPELLINGS = (";", ",", "|", "\n", "\r")


# ── One word ─────────────────────────────────────────────────────────────────

def _has_odd_character(text: str) -> bool:
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cs", "Co", "Cn") or (cat == "Cf" and ch not in _ALLOWED_FORMAT):
            return True
    return False


def check_word(raw):
    """(word, reason): the word as it would be kept (spaces tidied) and why it
    can't be, or "" if it can. Reasons: empty, odd (a control or invisible
    character), too_long, no_letters."""
    if not isinstance(raw, str):
        return "", "empty"
    word = " ".join(raw.split())
    if not word:
        return "", "empty"
    if _has_odd_character(word):
        return word, "odd"
    if len(word) > MAX_WORD_LEN:
        return word, "too_long"
    if not any(c.isalpha() for c in word):
        return word, "no_letters"
    return word, ""


def _key(text) -> str:
    return " ".join(str(text).split()).casefold()


# ── Reading a file ───────────────────────────────────────────────────────────

def _star_value(cell):
    v = str(cell or "").strip().casefold()
    if v in _TRUE:
        return True
    if v and v in _FALSE:
        return False
    return None            # an empty cell, or one that says neither: nothing to do


def _spellings(cell) -> list:
    text = str(cell or "")
    for sep in _SPLIT_SPELLINGS[1:]:
        text = text.replace(sep, _SPLIT_SPELLINGS[0])
    return [s for s in (" ".join(p.split()) for p in text.split(";")) if s]


def parse_vocab(text: str) -> dict:
    """The rows of a Vocabulary file: {"header": whether the first row named
    the columns, "rows": [{"row", "word", "sounds_like" (a list), "starred"
    (True, False, or None when the file says nothing)}]}. ``row`` counts the
    file's rows from 1, the header included. Raises table_io.TableError."""
    table = table_io.parse(text)
    if not table:
        return {"header": False, "rows": []}
    columns = table_io.column_map(table[0], COLUMN_NAMES)
    header = columns is not None
    if header:
        if "word" not in columns:
            free = [i for i in range(len(table[0])) if i not in columns.values()]
            if free:
                columns["word"] = free[0]
        body = table[1:]
        first = 2
    else:
        columns = {"word": 0, "sounds_like": 1, "starred": 2}
        body = table
        first = 1

    def cell(row, name):
        i = columns.get(name)
        return table_io.undefuse(row[i]) if i is not None and i < len(row) else ""

    def star(row):
        return _star_value(cell(row, "starred")) if "starred" in columns else None

    rows = []
    for n, row in enumerate(body, first):
        rows.append({
            "row": n,
            "word": cell(row, "word") if "word" in columns else "",
            "sounds_like": _spellings(cell(row, "sounds_like")),
            "starred": star(row),
        })
    return {"header": header, "rows": rows}


# ── Writing a file ───────────────────────────────────────────────────────────

def render_csv(words, sounds, starred) -> str:
    """The Vocabulary as CSV text, in the order of the list."""
    sounds = sounds if isinstance(sounds, dict) else {}
    star = {_key(s) for s in (starred or ())}
    rows = [list(CSV_COLUMNS)]
    for w in words:
        spellings = sounds.get(w) or []
        rows.append([w, "; ".join(" ".join(str(s).replace(";", " ").split()) for s in spellings),
                     "yes" if _key(w) in star else "no"])
    return table_io.render(rows)


def export_bytes(words, sounds, starred) -> bytes:
    """The file: UTF-8 with a byte order mark, so Excel and Numbers show accents."""
    return table_io.encode(render_csv(words, sounds, starred))


# ── What an import does ──────────────────────────────────────────────────────

def plan_import(parsed: dict, words, sounds, starred) -> dict:
    """What importing ``parsed`` (parse_vocab) into the lists would do. Changes
    none of its arguments.

    Returns {"header", "words", "sounds", "starred" (the lists as they would
    be), "new", "updated", "already" (the words, in file order), "skipped"
    ([{row, word, reason, message}]), "notes" ([{row, word, spelling, reason,
    message}] for spellings left out) and "counts" ({rows, new, updated,
    already, skipped})}."""
    final = list(words)
    spell = {w: list(v) for w, v in (sounds or {}).items() if w in set(words)}
    stars = {_key(s) for s in (starred or ())}
    by_key = {_key(w): w for w in final}
    taken = {}
    for w, v in spell.items():
        for s in v:
            taken[_key(s)] = w
    out = {"new": [], "updated": [], "already": [], "skipped": [], "notes": []}
    seen_existing, changed, added = [], set(), set()

    def skip(row, word, reason, other=""):
        out["skipped"].append({"row": row["row"], "word": word, "reason": reason,
                               "message": user_messages.vocab_skip_message(reason, other)})

    def note(row, word, spelling, reason, other=""):
        out["notes"].append({"row": row["row"], "word": word, "spelling": spelling, "reason": reason,
                             "message": user_messages.spelling_note(reason, spelling, other)})

    def take_spellings(row, entry):
        """Add the row's spellings to ``entry`` by the sounds-like box's rules.
        True if one was added."""
        gained = False
        current = spell.setdefault(entry, [])
        for s in row["sounds_like"]:
            s = " ".join(s.split())
            k = _key(s)
            if len(s) > MAX_WORD_LEN:
                note(row, entry, s, "spelling_long")
            elif _has_odd_character(s):
                note(row, entry, s, "spelling_odd")
            elif not any(c.isalnum() for c in s):
                note(row, entry, s, "spelling_none")
            elif k == _key(entry):
                note(row, entry, s, "spelling_same")
            elif k in by_key:
                note(row, entry, s, "spelling_word")
            elif k in taken and taken[k] != entry:
                note(row, entry, s, "spelling_taken", taken[k])
            elif k in taken:
                continue                                  # already one of this word's
            elif len(current) >= MAX_SPELLINGS:
                note(row, entry, s, "spelling_max")
            else:
                current.append(s)
                taken[k] = entry
                gained = True
        if not current:
            del spell[entry]
        return gained

    for row in parsed.get("rows", []):
        word, reason = check_word(row["word"])
        if reason:
            skip(row, word or row["word"], reason)
            continue
        k = _key(word)
        want_star = row["starred"] is True
        if k in by_key:
            entry = by_key[k]
            gained = take_spellings(row, entry)
            newly_starred = want_star and k not in stars
            if newly_starred:
                stars.add(k)
            if entry in added:
                continue                                  # another row for a word this import added
            if entry not in seen_existing:
                seen_existing.append(entry)
            if gained or newly_starred:
                changed.add(entry)
            continue
        if k in taken:
            skip(row, word, "is_spelling", taken[k])
            continue
        if len(final) >= MAX_WORDS:
            skip(row, word, "full")
            continue
        mark = len(out["notes"])
        final.append(word)
        by_key[k] = word
        gained = take_spellings(row, word)
        if is_everyday_entry(word) and not gained:
            final.pop()
            del by_key[k]
            del out["notes"][mark:]
            skip(row, word, "everyday")
            continue
        if want_star:
            stars.add(k)
        added.add(word)
        out["new"].append(word)

    out["updated"] = [e for e in seen_existing if e in changed]
    out["already"] = [e for e in seen_existing if e not in changed]
    out["words"] = final
    out["sounds"] = {w: spell[w] for w in final if w in spell}
    out["starred"] = [w for w in final if _key(w) in stars]
    out["header"] = bool(parsed.get("header"))
    out["counts"] = {"rows": len(parsed.get("rows", [])), "new": len(out["new"]),
                     "updated": len(out["updated"]), "already": len(out["already"]),
                     "skipped": len(out["skipped"])}
    return out


def plan_from_payload(content, is_base64, words, sounds, starred) -> dict:
    """plan_import for what the page sent (text, or a file's bytes as base64).
    Raises table_io.TableError with a plain sentence for a file that cannot be
    read."""
    return plan_import(parse_vocab(table_io.from_payload(content, bool(is_base64))),
                       words, sounds, starred)


PREVIEW_LIST = 30           # names listed on the preview; the rest are counted
PREVIEW_PROBLEMS = 50       # rows left out, and spellings left out, that are listed


def preview_view(plan: dict) -> dict:
    """What the page's preview shows of a plan (plan_import), kept small for a
    long file: the counts, the first words of each kind, and the first rows
    left out with their reasons, each with how many more there are."""
    def head(items, n):
        return list(items[:n]), max(0, len(items) - n)

    new, new_more = head(plan["new"], PREVIEW_LIST)
    updated, updated_more = head(plan["updated"], PREVIEW_LIST)
    skipped, skipped_more = head(plan["skipped"], PREVIEW_PROBLEMS)
    notes, notes_more = head(plan["notes"], PREVIEW_PROBLEMS)
    return {"header": plan["header"], "counts": dict(plan["counts"]),
            "new": new, "new_more": new_more, "updated": updated, "updated_more": updated_more,
            "skipped": skipped, "skipped_more": skipped_more, "notes": notes, "notes_more": notes_more,
            "stars": len([w for w in plan["starred"] if w in set(plan["new"]) | set(plan["updated"])])}

