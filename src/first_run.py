"""The parts of first-run setup that decide things, kept apart from the window.

Two jobs:

1. Say what a new Groq key can do. Checking a key used to mean "the key list
   call worked", which says nothing about the two models Waffler needs.
   Groq's model list is the same request, so setup now also looks for the
   speech-to-text model and the clean-up model in it and shows a line for
   each ("Speech to text: Groq, whisper-large-v3, Working").

2. Finish the practice dictation. Setup used to run only the transcription,
   so the step that should show what Waffler does (turn rambling into tidy
   text) never showed it, and the result was thrown away. Now the practice
   runs the same clean-up as every dictation, returns "You said" and
   "Waffler wrote", and becomes the first Journal entry.

Nothing here touches the network, the clipboard or a file: the caller passes
the clean-up function and saves the entry, so the tests use fakes.
"""
from __future__ import annotations

import os
from datetime import datetime

try:
    from user_messages import cleanup_skipped_message   # app.py puts src/ on sys.path
except ImportError:
    from src.user_messages import cleanup_skipped_message

# The models a Groq key has to reach. The clean-up model follows the same
# override as src/style_openai.py (GROQ_STYLE_MODEL), and speech-to-text is
# the model src/transcribe_whisper.py asks Groq for.
SPEECH_MODEL = "whisper-large-v3"
DEFAULT_CLEANUP_MODEL = "openai/gpt-oss-120b"


def cleanup_model() -> str:
    return os.getenv("GROQ_STYLE_MODEL", "").strip() or DEFAULT_CLEANUP_MODEL


def _short(model: str) -> str:
    """Drop the vendor prefix for display: openai/gpt-oss-120b -> gpt-oss-120b."""
    return model.split("/", 1)[-1]


def model_ids(response) -> set:
    """The model ids in a models.list() response (an SDK object or a dict)."""
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    ids = set()
    for m in data or []:
        mid = getattr(m, "id", None)
        if mid is None and isinstance(m, dict):
            mid = m.get("id")
        if mid:
            ids.add(str(mid))
    return ids


def groq_services(ids) -> list:
    """One line per job the key has to do.

    status is "ok" when Groq lists the model for this key, "missing" when the
    list came back without it, and "unchecked" when the list was empty (the
    key was accepted, but there was nothing to look in).
    """
    ids = set(ids or ())
    out = []
    for name, model in (("Speech to text", SPEECH_MODEL), ("Clean-up", cleanup_model())):
        if not ids:
            status = "unchecked"
        else:
            status = "ok" if model in ids else "missing"
        out.append({"name": name, "provider": "Groq", "model": _short(model), "status": status})
    return out


# ── the practice dictation ────────────────────────────────────────────────────

def finish_practice(transcript: str, style, fallback) -> dict:
    """Clean up the practice dictation like any other.

    style(transcript) -> (text, usage) is the styler; fallback(transcript) is
    the plain tidy used when the clean-up can't run. The words are never lost:
    a failed clean-up still returns them, with a plain note saying why.

    Returns {"said", "wrote", "usage", "cleaned", "note"}.
    """
    said = (transcript or "").strip()
    note = ""
    try:
        wrote, usage = style(said)
        usage = dict(usage or {})
    except Exception as e:  # the styler normally catches its own errors
        wrote = fallback(said)
        usage = {"input_tokens": 0, "output_tokens": 0, "api_used": False,
                 "provider": "basic_clean", "fallback_reason": f"{type(e).__name__}: {e}"}
    reason = usage.get("fallback_reason")
    if reason:
        # The pill's sentences talk about pasting; nothing is pasted during
        # setup. A limit keeps the pill's heading, which says when the
        # clean-up comes back.
        heading, _body = cleanup_skipped_message(str(reason))
        if "RATE_LIMIT|" in str(reason):
            note = f"{heading}. Waffler kept your words as you said them this time."
        else:
            note = "The clean-up didn't run this time, so these are your words as you said them."
    wrote = (wrote or "").strip() or said
    return {"said": said, "wrote": wrote, "usage": usage, "cleaned": not reason, "note": note}


def journal_entry(said: str, wrote: str, now: datetime | None = None) -> dict:
    """The practice dictation as a Journal entry, shaped like the pipeline's."""
    now = now or datetime.now()
    return {
        "timestamp": now.isoformat(timespec="seconds"),
        "text": said,
        "styled": wrote,
        "word_count": len(wrote.split()),
        "text_is": "asr_filtered",
        "source": "setup",
    }
