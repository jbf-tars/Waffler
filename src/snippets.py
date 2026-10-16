"""Snippets: say a short phrase, get longer text.

A snippet is a trigger and what it expands to ("my address" and the three
lines of it). They live in ``snippets.json`` as a list of
``{"trigger": ..., "expansion": ...}`` and are edited on the Vocabulary page.

Two rules decide what a dictation does with them, both pure and tested on
their own:

* ``whole_utterance``: when the whole dictation is a trigger (ignoring case,
  punctuation and surrounding space) the result is exactly the expansion. The
  pipeline then pastes it without asking a model to clean it up: there is
  nothing to clean, and a model has been seen to add a full stop or to
  rephrase a signature.
* ``apply``: otherwise each trigger inside the text is replaced, whole words
  only, ignoring case, the longest trigger first. One pass over the text, so
  what one snippet expands to is never read for triggers again: a snippet can
  never cause another.

``clean`` is what the page's list goes through before it is saved.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

try:
    from languages import is_unspaced as _unspaced
except ImportError:  # imported as src.snippets
    from src.languages import is_unspaced as _unspaced

# Limits. A trigger is a phrase you say, an expansion is text you paste.
# ui/logic.js SNIPPET_MAX_* must match (a test compares them).
MAX_TRIGGER = 60
MAX_EXPANSION = 4000
MAX_SNIPPETS = 200

ERR_READ = "Waffler couldn't read those snippets. Try again."
ERR_TRIGGER = f"A trigger can be up to {MAX_TRIGGER} characters."
ERR_EXPANSION = f"A snippet's text can be up to {MAX_EXPANSION:,} characters."
ERR_COUNT = f"You can keep up to {MAX_SNIPPETS} snippets."


def _collapse(text) -> str:
    """Outer space removed, every run of space (and line breaks) one space."""
    return " ".join(str(text).split())


def trigger_key(trigger) -> str:
    """What makes two triggers the same: the same words in any case."""
    return _collapse(trigger).casefold()


def spoken_key(text) -> str:
    """A dictation (or a trigger) as it is compared for the whole-utterance
    rule: lower case, with every character that is not a letter, a mark or a
    digit read as a space, and the spaces collapsed. "My e-mail address." and
    "my e mail address" are the same."""
    folded = str(text).casefold()
    return " ".join("".join(c if unicodedata.category(c)[0] in "LMN" else " "
                            for c in folded).split())


def _valid(data) -> list:
    """The entries of ``data`` that are a trigger and its text, as they are."""
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        trigger, expansion = item.get("trigger"), item.get("expansion")
        if isinstance(trigger, str) and isinstance(expansion, str) and trigger.strip():
            out.append({"trigger": trigger.strip(), "expansion": expansion})
    return out


def load(path) -> list:
    """The saved snippets, or [] when there are none or the file can't be
    read. A hand-edited file with a byte order mark is read (utf-8-sig)."""
    try:
        return _valid(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        return []


def clean(snippets):
    """The list to save, as ``(snippets, error)``; ``error`` is None or one
    plain sentence, and then nothing is to be saved.

    Outer space is stripped from both parts (inside a trigger, a run of space
    is one space), a snippet with no trigger or no text is dropped, and a
    trigger already in the list in other capitals is dropped (the first
    stays). Too much of anything is refused rather than cut short: a
    snippet's text is never shortened without saying so.
    """
    if not isinstance(snippets, list):
        return [], ERR_READ
    out, seen = [], set()
    for item in snippets:
        if not isinstance(item, dict):
            continue
        trigger, expansion = item.get("trigger"), item.get("expansion")
        if not isinstance(trigger, str) or not isinstance(expansion, str):
            continue
        trigger, expansion = _collapse(trigger), expansion.strip()
        if not trigger or not expansion:
            continue
        if len(trigger) > MAX_TRIGGER:
            return [], ERR_TRIGGER
        if len(expansion) > MAX_EXPANSION:
            return [], ERR_EXPANSION
        key = trigger_key(trigger)
        if key in seen:
            continue
        seen.add(key)
        out.append({"trigger": trigger, "expansion": expansion})
    if len(out) > MAX_SNIPPETS:
        return [], ERR_COUNT
    return out, None


# ── Matching ─────────────────────────────────────────────────────────────

def _freeze(snippets):
    """The snippets as hashable (trigger, expansion) pairs, bad ones dropped."""
    return tuple((s["trigger"], s["expansion"]) for s in _valid(snippets))


@lru_cache(maxsize=8)
def _compiled(pairs):
    """(pattern, expansion by trigger_key, expansion by spoken_key) for a
    snippet list. The pattern is one alternation, longest trigger first, so
    "my address" wins over "my" wherever both could start."""
    by_key, by_spoken, shapes = {}, {}, []
    for trigger, expansion in pairs:
        words = trigger.split()
        key = trigger_key(trigger)
        if not words or key in by_key:        # the first of two equal triggers stays
            continue
        by_key[key] = expansion
        spoken = spoken_key(trigger)
        if spoken and spoken not in by_spoken:
            by_spoken[spoken] = expansion
        shapes.append((len(key), _shape(words)))
    if not shapes:
        return None, by_key, by_spoken
    shapes.sort(key=lambda s: -s[0])          # longest first; ties keep their order
    pattern = re.compile("|".join(p for _n, p in shapes), re.IGNORECASE)
    return pattern, by_key, by_spoken


def _shape(trigger_words) -> str:
    """The pattern for one trigger: its words with any space between them,
    and whole-word edges. Whole word means not glued to a letter or digit on
    either side; a trigger that starts or ends with a symbol ("c++", "@home")
    is held to the same. Chinese, Japanese and Thai have no spaces to
    separate words, so a trigger that begins or ends in those scripts can
    sit inside a sentence."""
    body = r"\s+".join(re.escape(w) for w in trigger_words)
    before = "" if _unspaced(trigger_words[0][0]) else r"(?<!\w)"
    after = "" if _unspaced(trigger_words[-1][-1]) else r"(?!\w)"
    return before + body + after


def whole_utterance(text, snippets):
    """The expansion when ``text`` is nothing but a trigger (ignoring case,
    punctuation and surrounding space), else None."""
    if not text or len(text) > 4 * MAX_TRIGGER:
        return None                            # too long to be a trigger
    spoken = spoken_key(text)
    if not spoken:
        return None
    return _compiled(_freeze(snippets))[2].get(spoken)


def apply(text, snippets):
    """``text`` with each trigger in it replaced by its expansion: whole
    words, any case, longest trigger first, in one pass (an expansion is
    never searched again). Without snippets, or without a match, the text
    comes back as it was."""
    if not text:
        return text
    pattern, by_key, _by_spoken = _compiled(_freeze(snippets))
    if pattern is None:
        return text
    return pattern.sub(lambda m: by_key.get(trigger_key(m.group(0)), m.group(0)), text)
