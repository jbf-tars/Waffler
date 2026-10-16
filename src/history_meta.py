"""What a Journal entry can say about its take, beyond the words.

Entries have always had ``timestamp``, ``text`` (what speech to text heard,
after the word filter), ``styled`` (what was pasted), ``word_count`` and a few
optional extras. These are added when an entry is written, every one optional:
an entry saved by an older Waffler has none of them, and every reader must
treat each as "unknown" when it is missing.

    id           whole number, one more than the last (src/history_ids.py)
    duration_s   how long the recording was, in seconds (one decimal)
    speech_s     seconds of it that were speech (what the transcriber measured)
    lang         the Language setting at the time ("en", "auto", ...)
    provider     {"stt": "groq", "cleanup": "groq"}: who heard it, who tidied it
    paste        what happened to the words: pasted, unverified, failed (the
                 safe paste's outcomes), copied (auto-paste is off), or
                 not_pasted (a newer dictation took over, or Esc kept them)
    app          the app in front when the hotkey went down, by its name
    app_bundle   the same app's identifier on a Mac ("com.apple.mail"), when
                 the system gave one
    category     the kind of app it is: email, work, personal or other
                 (src/app_categories.py)
    audio        the name of the recording kept for this take
                 (src/recent_audio.py), so "Redo this take" can find it
    style        the style the text was finished in (formal, casual,
                 very_casual or excited: src/style_post.py), when the
                 Style section had one for the kind of app and the text was
                 one it styles; left out for "auto"

The app's name, identifier and kind are kept on this computer with the entry
and nowhere else; the window title that helped tell a browser's tab is not kept.
Nothing here reads a file or the window.
"""

from __future__ import annotations

import app_categories
import style_post

# The safe paste's own words (src/pasteboard.py), then the two that say no
# paste was tried.
PASTE_PASTED = "pasted"
PASTE_UNVERIFIED = "unverified"
PASTE_FAILED = "failed"
PASTE_COPIED = "copied"
PASTE_NOT_PASTED = "not_pasted"
PASTE_VALUES = (PASTE_PASTED, PASTE_UNVERIFIED, PASTE_FAILED, PASTE_COPIED, PASTE_NOT_PASTED)

APP_NAME_MAX = 80
APP_BUNDLE_MAX = 120
LANG_MAX = 16

# What stays with a take when it is done again ("Redo this take"): who and
# when it was, and where its recording is. The words and everything about how
# they were made are the new run's.
KEPT_ON_REDO = ("id", "timestamp", "audio", "app", "app_bundle", "category", "duration_s")


def words_of(item: dict) -> str:
    """The words of an entry: what was pasted, else what was heard."""
    return str(item.get("styled") or item.get("text") or "")


def _seconds(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, 1) if v > 0 and v == v and v != float("inf") else None


def _name(value, limit):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def take_fields(*, audio_seconds=None, speech_seconds=None, language=None, stt=None,
                cleanup=None, paste=None, app=None, audio=None, app_bundle=None,
                category=None, style=None) -> dict:
    """The optional fields for an entry, leaving out whatever is unknown.
    ``category`` is kept only for an app that is known, and only if it is one
    of src/app_categories.CATEGORIES; ``style`` only if it is one of
    src/style_post.STYLES."""
    out = {}
    d = _seconds(audio_seconds)
    if d is not None:
        out["duration_s"] = d
    s = _seconds(speech_seconds)
    if s is not None:
        out["speech_s"] = s
    lang = _name(language, LANG_MAX)
    if lang:
        out["lang"] = lang
    who = {k: v for k, v in (("stt", _name(stt, 24)), ("cleanup", _name(cleanup, 24))) if v}
    if who:
        out["provider"] = who
    if paste in PASTE_VALUES:
        out["paste"] = paste
    name = _name(app, APP_NAME_MAX)
    if name:
        out["app"] = name
    bundle = _name(app_bundle, APP_BUNDLE_MAX)
    if bundle:
        out["app_bundle"] = bundle
    if (name or bundle) and category in app_categories.CATEGORIES:
        out["category"] = category
    if isinstance(audio, str) and audio:
        out["audio"] = audio
    if isinstance(style, str) and style in style_post.STYLES:
        out["style"] = style
    return out


def context_of(entry):
    """The ``AppContext`` an entry was dictated in, from the fields above, or
    None when it records no app (an older entry, or one made where the app
    could not be told). The window title is never kept, so ``title`` is empty."""
    if not isinstance(entry, dict):
        return None
    name, bundle = _name(entry.get("app"), APP_NAME_MAX), _name(entry.get("app_bundle"), APP_BUNDLE_MAX)
    if not (name or bundle):
        return None
    import text_context
    category = entry.get("category")
    if category not in app_categories.CATEGORIES:
        category = app_categories.category_for(bundle, name)
    return text_context.AppContext(bundle, name, "", category)


def paste_label(result, pasted: bool) -> str:
    """The ``paste`` value for a paste step. ``result`` is the watchdog's
    CallResult for ``clipboard.paste_text`` (its value the PasteOutcome, or
    nothing when the step did not finish), ``pasted`` what the pipeline
    decided: that the words went in."""
    value = getattr(result, "value", None) if getattr(result, "ok", False) else None
    status = getattr(value, "status", "")
    if status in (PASTE_PASTED, PASTE_UNVERIFIED, PASTE_FAILED):
        return status
    return PASTE_PASTED if pasted else PASTE_FAILED


def redone_entry(old: dict, *, transcript: str, styled: str, vocab_changes=None,
                 redone_at: str = "", **fields) -> dict:
    """The entry that replaces ``old`` after "Redo this take": the same
    take (id, time, recording, app, length), new words. Nothing about how the
    old words were made carries over: its quality flags, "as said" tag and
    paste outcome described a different run."""
    out = {k: old[k] for k in KEPT_ON_REDO if k in old}
    out["text"] = transcript
    out["styled"] = styled
    out["word_count"] = len(styled.split())
    out["text_is"] = "asr_filtered"
    if vocab_changes:
        out["vocab_changes"] = [[h, u] for h, u in vocab_changes]
    if redone_at:
        out["redone_at"] = redone_at
    out.update(take_fields(**fields))
    return out
