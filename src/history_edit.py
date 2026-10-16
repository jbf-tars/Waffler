"""Editing a dictation's words in the Journal.

"Edit text" on a Journal card saves what the person made of a dictation as
``edited_text`` on the entry. Nothing else about the entry changes: ``styled``
stays what Waffler pasted, ``text`` stays how the speech service wrote it (the
Vocabulary suggestions are worked out from those two, src/learn_words.py), and
the word count stays the words that were said, so the Journal's numbers keep
measuring dictation, not typing.

Everything that shows or reuses an entry's words reads them with
``history_meta.final_text``: the card, Copy, "Paste last", the Journal search,
and the CSV and text exports. The JSON export, deleting and the retention
setting treat the entry as they always did. Redo this take replaces the words
and so drops the edit (it is not carried over, src/history_meta.py
KEPT_ON_REDO); the old entry, edit included, is what the Undo puts back.

This module holds the rules. app.py finds the entry and writes the file under
its history lock.
"""

from __future__ import annotations

try:
    from history_meta import NON_DICTATION_KINDS, final_text, words_of
except ImportError:  # imported as src.history_edit
    from src.history_meta import NON_DICTATION_KINDS, final_text, words_of

KEY = "edited_text"
# Far more than anyone dictates in one go (a recording is capped near 20
# minutes, about 3,000 words), and far less than a file.
MAX_EDIT_CHARS = 20000

EDITED = "edited"
CLEARED = "cleared"
UNCHANGED = "unchanged"


def is_editable(entry) -> bool:
    """A dictation with words: not a Not sent card, a rewrite or a command."""
    return (isinstance(entry, dict) and not entry.get("failed")
            and entry.get("kind") not in NON_DICTATION_KINDS
            and bool(words_of(entry).strip()))


def clean(new_text):
    """(text, reason): what was typed as it would be kept, or '' and why not
    ('empty', 'too_long'). Line ends become one kind, the outside is trimmed,
    control characters other than the tab and the line break are dropped; what
    is inside is left as typed."""
    if not isinstance(new_text, str):
        return "", "empty"
    text = new_text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\t\n" or c >= " " and c != "\x7f")
    text = text.strip()
    if not text:
        return "", "empty"
    if len(text) > MAX_EDIT_CHARS:
        return "", "too_long"
    return text, ""


def apply(entry: dict, new_text: str):
    """(entry, state): a copy of ``entry`` with the edit in it. ``new_text``
    must already be clean. The same words as were pasted take the edit off
    (state 'cleared', or 'unchanged' if there was none); otherwise the state
    is 'edited', or 'unchanged' when it is the edit already there."""
    out = dict(entry)
    if new_text == words_of(entry).strip():
        had = KEY in out
        out.pop(KEY, None)
        return out, CLEARED if had else UNCHANGED
    if out.get(KEY) == new_text:
        return out, UNCHANGED
    out[KEY] = new_text
    return out, EDITED


__all__ = ["KEY", "MAX_EDIT_CHARS", "EDITED", "CLEARED", "UNCHANGED", "is_editable", "clean", "apply",
           "final_text"]
