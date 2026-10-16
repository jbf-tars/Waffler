"""The Style section of Settings, as the pipeline reads it.

Three choices live in settings.json, all read again for every dictation (so
a change applies to the next one, with no restart):

    cleanup            "on" (default) or "off"
    style_by_category  {"personal", "work", "email", "other"}: each "auto"
                       (default) or a style the kind offers
    email_signature    {"enabled": false, "text": ""}

Clean-up Off
------------
Off means the words as the speech service heard them, after the Vocabulary and
Snippets, and nothing else: no model is asked, so nothing is rewritten and
nothing is spent. It deliberately skips the plain tidy-up the clean-up falls
back to (src/style_openai.py ``_basic_clean``), because that tidy-up removes
filler words and repeated words, and "Off" must never take out a word the
speaker said. The Style rules do not apply to it either (they are formatting,
and Off is "exactly what was heard"). Its provider tag is ``"off"``, so the
Journal's quality chips (src/quality.py), the cost records and the toasts treat
it as a choice and not as a clean-up that failed. The email signature is a
separate, opt-in feature and does still apply (see below).

A style for each kind of app
----------------------------
``auto`` is today's behaviour: the style rules (src/style_post.py) do not run
at all. Personal messages offer Formal, Casual and Very casual; work chat,
email and everything else offer Formal, Casual and Excited. The kind comes
from the app that was in front when the hotkey went down
(src/text_context.py); an unknown kind is "other".

The email signature
-------------------
When it is on, the app in front is an email app, and the dictation looks like
the start of a message (it begins with a greeting, or has ten words or more),
``\\n\\n`` and the signature go on the end, unless the signature is already
before the cursor. To tell, Waffler reads the text before the cursor through
Accessibility, only at that moment and only to look for the signature; it is
not stored or logged. When the app does not show its text (or the field is
empty, which looks the same), the answer comes from a short memory instead: no
second signature in the same app within ten minutes. Never for a snippet, and
never when a selection is being replaced.

This module reads no file and no field. The pipeline hands it the settings and
a function that reads the text before the cursor, so every decision here runs
with fakes in the tests.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass

import app_categories
import style_post
import user_messages

CLEANUP_ON = "on"
CLEANUP_OFF = "off"
CLEANUP_CHOICES = (CLEANUP_ON, CLEANUP_OFF)

# The provider tag a dictation carries when the clean-up was switched off.
PROVIDER_OFF = "off"

AUTO = style_post.AUTO
CATEGORIES = app_categories.CATEGORIES
STYLE_CHOICES = {
    app_categories.PERSONAL: (AUTO, style_post.FORMAL, style_post.CASUAL, style_post.VERY_CASUAL),
    app_categories.WORK: (AUTO, style_post.FORMAL, style_post.CASUAL, style_post.EXCITED),
    app_categories.EMAIL: (AUTO, style_post.FORMAL, style_post.CASUAL, style_post.EXCITED),
    app_categories.OTHER: (AUTO, style_post.FORMAL, style_post.CASUAL, style_post.EXCITED),
}

SIGNATURE_MAX_CHARS = 500
SIGNATURE_MIN_WORDS = 10
SIGNATURE_RECENT_S = 600
# Reading the text before the cursor: what Accessibility is given, and how
# long the pipeline waits for the whole step. A field that does not answer
# costs this much and then counts as unreadable.
FIELD_READ_TIMEOUT_S = 1.2
FIELD_READ_DEADLINE_S = 2.5


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


# ── Clean-up on or off ───────────────────────────────────────────────────────

def cleanup_mode(settings) -> str:
    """``"on"`` unless settings.json says exactly ``"off"``."""
    return CLEANUP_OFF if _dict(settings).get("cleanup") == CLEANUP_OFF else CLEANUP_ON


def cleanup_is_off(settings) -> bool:
    return cleanup_mode(settings) == CLEANUP_OFF


def off_usage() -> dict:
    """What the clean-up step reports when it was switched off: no tokens, no
    model, no failure."""
    return {"input_tokens": 0, "output_tokens": 0, "api_used": False, "provider": PROVIDER_OFF}


# ── A style for each kind of app ─────────────────────────────────────────────

def styles_by_category(settings) -> dict:
    """The style of every kind, ``"auto"`` for anything missing or not offered
    for that kind (so a hand-edited file can never name a style the page does
    not have)."""
    saved = _dict(_dict(settings).get("style_by_category"))
    out = {}
    for kind in CATEGORIES:
        value = saved.get(kind)
        out[kind] = value if isinstance(value, str) and value in STYLE_CHOICES[kind] else AUTO
    return out


def style_for(settings, category) -> str:
    """The style for a dictation in an app of kind ``category`` (an unknown
    kind is ``"other"``)."""
    kind = category if isinstance(category, str) and category in CATEGORIES else app_categories.OTHER
    return styles_by_category(settings)[kind]


def finish_style(text, *, settings, category, language, protected_words=()):
    """``(text, style)``: ``text`` with the style its kind of app has, and the
    style that was applied (``""`` for ``auto``, and when the text or the
    language is not one the rules style)."""
    choice = style_for(settings, category)
    if choice == AUTO or not style_post.eligible(text, language):
        return text, ""
    return (style_post.apply_style(text, choice, category, language=language,
                                   protected_words=protected_words), choice)


# ── The signature settings ───────────────────────────────────────────────────

def tidy_signature(text) -> str:
    """A signature as it is kept: line ends made ``\\n``, control characters
    (other than a new line and a tab) dropped, the ends trimmed."""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(c for c in s if c in "\n\t" or unicodedata.category(c) != "Cc")
    return s.strip()


def signature_settings(settings) -> dict:
    """``{"enabled": bool, "text": str}`` as saved; anything unreadable is the
    default (off, empty)."""
    saved = _dict(_dict(settings).get("email_signature"))
    text = saved.get("text")
    text = tidy_signature(text)[:SIGNATURE_MAX_CHARS] if isinstance(text, str) else ""
    return {"enabled": saved.get("enabled") is True, "text": text}


# ── What save_settings checks, and keeps ─────────────────────────────────────

def settings_problem(payload):
    """A plain sentence when ``payload`` (the dict a save is called with)
    holds a refused value for one of these settings, else None. A refused
    value saves none of the payload."""
    if not isinstance(payload, dict):
        return None
    if "cleanup" in payload and not (isinstance(payload["cleanup"], str)
                                     and payload["cleanup"] in CLEANUP_CHOICES):
        return user_messages.CLEANUP_CHOICE_UNKNOWN
    if "style_by_category" in payload:
        given = payload["style_by_category"]
        if not isinstance(given, dict):
            return user_messages.STYLE_CHOICE_UNKNOWN
        for kind, value in given.items():
            if kind not in CATEGORIES or not isinstance(value, str) or value not in STYLE_CHOICES[kind]:
                return user_messages.STYLE_CHOICE_UNKNOWN
    if "email_signature" in payload:
        given = payload["email_signature"]
        if not isinstance(given, dict) or set(given) - {"enabled", "text"}:
            return user_messages.SIGNATURE_BAD
        if "enabled" in given and not isinstance(given["enabled"], bool):
            return user_messages.SIGNATURE_BAD
        if "text" in given:
            if not isinstance(given["text"], str):
                return user_messages.SIGNATURE_BAD
            if len(tidy_signature(given["text"])) > SIGNATURE_MAX_CHARS:
                return user_messages.SIGNATURE_TOO_LONG
    return None


def merged_styles(stored, payload) -> dict:
    """The styles to save: what is stored, with the kinds in ``payload``
    changed. Call after ``settings_problem`` found nothing."""
    out = styles_by_category(stored)
    out.update(_dict(_dict(payload).get("style_by_category")))
    return out


def merged_signature(stored, payload) -> dict:
    """The signature to save: what is stored, with the fields in ``payload``
    changed and the text tidied. Call after ``settings_problem`` found nothing."""
    out = signature_settings(stored)
    given = _dict(_dict(payload).get("email_signature"))
    if "enabled" in given:
        out["enabled"] = given["enabled"] is True
    if "text" in given:
        out["text"] = tidy_signature(given["text"])
    return out


# ── The signature decision ───────────────────────────────────────────────────

REASON_APPEND = "append"
REASON_DISABLED = "disabled"
REASON_NOT_EMAIL = "not_email"
REASON_SNIPPET = "snippet"
REASON_SELECTION = "selection"
REASON_NOT_START = "not_start"
REASON_ALREADY_THERE = "already_there"
REASON_RECENT = "recent"
REASON_MOVED = "moved"

# What ``read_before`` returns when the app in front is no longer the one the
# words were dictated into: the paste is going somewhere else, so no signature.
FIELD_MOVED = object()

_GREETING_START = re.compile(
    r"^\s*(?:hi|hii|hiya|hey|heya|hello|dear|howdy|greetings|good\s+(?:morning|afternoon|evening|day))\b", re.I)


def looks_like_message_start(text) -> bool:
    """True when ``text`` begins with a greeting or has ten words or more: the
    start of a message, not an answer of a few words in the middle of one."""
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(_GREETING_START.match(text)) or len(text.split()) >= SIGNATURE_MIN_WORDS


def _squash(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def signature_present(before, signature) -> bool:
    """True when ``signature`` is in ``before``, whatever the spacing and case."""
    sig = _squash(signature)
    return bool(sig) and sig in _squash(before)


def read_chars(signature_text) -> int:
    """How much text before the cursor to look at: room for the signature and
    a good deal more."""
    return min(4000, max(600, 2 * len(str(signature_text or "")) + 300))


def with_signature(text: str, signature: str) -> str:
    """``text`` with a blank line and the signature after it."""
    return text.rstrip() + "\n\n" + signature


class SignatureMemory:
    """Which apps a signature was added in lately, for the ten minutes after.
    The fallback for an app that does not show its text; kept in memory only,
    and small."""

    MAX_APPS = 64

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._seen = {}

    def note(self, app_key, now=None):
        if not app_key:
            return
        t = self._clock() if now is None else now
        with self._lock:
            self._seen[app_key] = t
            if len(self._seen) > self.MAX_APPS:
                for key, _t in sorted(self._seen.items(), key=lambda kv: kv[1])[:len(self._seen) - self.MAX_APPS]:
                    del self._seen[key]

    def recent(self, app_key, now=None) -> bool:
        if not app_key:
            return False
        t = self._clock() if now is None else now
        with self._lock:
            then = self._seen.get(app_key)
        return then is not None and 0 <= t - then < SIGNATURE_RECENT_S

    def forget(self):
        with self._lock:
            self._seen.clear()

    def __len__(self):
        with self._lock:
            return len(self._seen)


@dataclass(frozen=True)
class SignatureChoice:
    add: bool
    reason: str


def decide_signature(*, settings, category, text, snippet=False, replaces_selection=False,
                     read_before=None, app_key="", memory=None, now=None) -> SignatureChoice:
    """Whether to put the email signature under ``text``, and why (or why not).

    ``read_before(chars)`` returns the text before the cursor, ``""`` for an
    empty field, None when it could not be read, or ``FIELD_MOVED`` when the
    app in front is no longer the one dictated into; it is called only when
    everything else says yes, so a field is never read for nothing. ``app_key``
    and ``memory`` (a ``SignatureMemory``) are the fallback for a field that
    cannot be read. Never raises."""
    try:
        sig = signature_settings(settings)
        if not sig["enabled"] or not sig["text"]:
            return SignatureChoice(False, REASON_DISABLED)
        if category != app_categories.EMAIL:
            return SignatureChoice(False, REASON_NOT_EMAIL)
        if snippet:
            return SignatureChoice(False, REASON_SNIPPET)
        if replaces_selection:
            return SignatureChoice(False, REASON_SELECTION)
        if not looks_like_message_start(text):
            return SignatureChoice(False, REASON_NOT_START)
        before = None
        if callable(read_before):
            try:
                before = read_before(read_chars(sig["text"]))
            except Exception:       # noqa: BLE001 - an unreadable field is a normal answer
                before = None
        if before is FIELD_MOVED:
            return SignatureChoice(False, REASON_MOVED)
        if isinstance(before, str) and before.strip():
            if signature_present(before, sig["text"]):
                return SignatureChoice(False, REASON_ALREADY_THERE)
            return SignatureChoice(True, REASON_APPEND)
        if memory is not None and memory.recent(app_key, now):
            return SignatureChoice(False, REASON_RECENT)
        return SignatureChoice(True, REASON_APPEND)
    except Exception:       # noqa: BLE001
        return SignatureChoice(False, REASON_DISABLED)
