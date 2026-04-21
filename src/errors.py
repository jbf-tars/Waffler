"""Shared exception types for Waffler.

Kept in its own module so `local_backend.py` and its callers
(`transcribe_whisper.py`, `style_openai.py`) can import without
circular-dependency risk.
"""


class LocalUnavailableError(RuntimeError):
    """Raised when Private Mode is active but a local resource (Ollama,
    Gemma model, or local Whisper) is unavailable. Must NEVER be silently
    swallowed in favor of a cloud fallback — the caller should surface a
    user-visible error."""
    pass
