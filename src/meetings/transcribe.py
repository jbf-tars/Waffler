"""Speech to text for one chunk of audio.

Waffler's one setting decides how (`transcription_mode` in settings.json,
src/local_stt.py): "local" runs the model on this computer, the same one
dictation uses, with no key and no network; otherwise Groq `whisper-large-v3`
first (the pipeline Waffler uses: FLAC upload, verbose segments), OpenAI as the
fallback. The end-of-audio rule is Waffler's: Whisper
invents "Thank you." and channel outros over silence, so a segment that is
only a thanks, a sign-off or an outro is dropped when the audio under it has
no voice or the model itself says nothing was said. Real thanks, said aloud,
stay.
"""
from __future__ import annotations

import re
import time

import numpy as np

from . import keys
from .audioproc import RATE, to_flac, voiced_between, voiced_mask

try:
    import local_stt            # the app's own copy: src/ is on its path
except ImportError:
    from src import local_stt

MIN_VOICED_S = 0.3              # less voice than this in a chunk: not uploaded
_NO_SPEECH_PROB = 0.5
_SEGMENT_MIN_VOICED_S = 0.15

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_OUTRO_CREDIT_RE = re.compile(
    r"^(?:closed\s+)?(?:subtitles|captions?|captioned|captioning|transcription|transcribed"
    r"|translated|translation)\s+(?:by|provided\s+by)\s+.+$", re.IGNORECASE)
_OUTRO_SENTENCE_RE = re.compile(r"""^(?:
      (?:(?:okay|ok|so|and|well|alright|all\s+right)\s+)?(?:thank\s+you|thanks)(?:\s+\S+){0,4}
    | (?:bye(?:[\s-]+bye)?|good\s*bye|bye\s+now)(?:\s+(?:everyone|everybody|all|guys))?
    | see\s+you(?:\s+(?:next\s+time|later|soon|tomorrow|around|in\s+the\s+next\s+(?:one|video)))?
    | take\s+care
    | have\s+a\s+(?:good|nice|great|lovely)\s+(?:day|one|night|evening|weekend)
    | (?:please\s+|don'?t\s+forget\s+to\s+|remember\s+to\s+)?(?:like\s+and\s+)?subscribe
      (?:\s+to\s+(?:my|the|our)\s+channel)?
    | (?:please\s+)?(?:hit|smash)\s+(?:the|that)\s+like\s+button
    | you
    | \[?(?:music|applause|laughter|silence|blank_audio)\]?
)$""", re.IGNORECASE | re.VERBOSE)


def is_outro(text) -> bool:
    """Only a thanks, a sign-off or a known outro (up to three of them)?"""
    if not isinstance(text, str):
        return False
    t = " ".join(text.replace("’", "'").split())
    if not t or len(t.split()) > 12:
        return False
    if _OUTRO_CREDIT_RE.match(t.rstrip(".!?")):
        return True
    parts = [re.sub(r"\s*,\s*", " ", p).strip(" ,;:-") for p in re.split(r"[.!?]+", t)]
    parts = [p for p in parts if p]
    return bool(parts) and len(parts) <= 3 and all(_OUTRO_SENTENCE_RE.match(p) for p in parts)


def _get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def clean_segments(raw_segments, audio: np.ndarray) -> list[dict]:
    """Model segments → [{start_s, end_s, text}], hallucinations removed."""
    mask = voiced_mask(audio)
    out: list[dict] = []
    repeats = 0
    for seg in raw_segments or []:
        text = " ".join(str(_get(seg, "text", "") or "").split())
        if not text:
            continue
        start = float(_get(seg, "start", 0.0) or 0.0)
        end = float(_get(seg, "end", start) or start)
        no_speech = float(_get(seg, "no_speech_prob", 0.0) or 0.0)
        voiced = voiced_between(mask, start, end)
        logprob = float(_get(seg, "avg_logprob", 0.0) or 0.0)
        # Whisper's own measures of a guess: very unsure of the words, or
        # fairly unsure and it thinks nobody spoke (its standard thresholds).
        if logprob < -1.0 or (no_speech > 0.6 and logprob < -0.5):
            continue
        if is_outro(text) and (voiced < _SEGMENT_MIN_VOICED_S or no_speech > _NO_SPEECH_PROB):
            continue
        if voiced < _SEGMENT_MIN_VOICED_S and no_speech > 0.8:
            continue
        # A model stuck in a loop repeats one line; keep the first two.
        if out and out[-1]["text"].casefold() == text.casefold():
            repeats += 1
            if repeats >= 2:
                continue
        else:
            repeats = 0
        out.append({"start_s": start, "end_s": max(end, start), "text": text, "logprob": logprob})
    return out


class TranscribeError(Exception):
    """`kind` is 'no_key', 'no_local', 'auth', 'rate', 'network' or 'other'."""

    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


def _classify(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    if status in (401, 403) or "authentication" in name or "permission" in name:
        return "auth"
    if status == 429 or "ratelimit" in name:
        return "rate"
    if "connection" in name or "timeout" in name:
        return "network"
    return "other"


def _call_openai_compatible(audio: np.ndarray, api_key: str, base_url: str | None, model: str,
                            language: str) -> list:
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0, max_retries=1)
    kwargs = {"language": language} if language else {}
    resp = client.audio.transcriptions.create(
        file=("chunk.flac", to_flac(audio), "audio/flac"), model=model,
        response_format="verbose_json", temperature=0.0, **kwargs)
    segments = _get(resp, "segments")
    if segments is None:
        text = _get(resp, "text", "") or ""
        return [{"start": 0.0, "end": len(audio) / RATE, "text": text}]
    return segments


def providers_available() -> list[str]:
    out = [p for p in ("groq", "openai") if keys.get_key(p)]
    if local_stt.is_available():
        out.append("local")
    return out


def uses_local(settings: dict | None = None) -> bool:
    """Is this chunk for the model on this computer? Waffler's setting says so
    (`transcription_mode` "local"), or the engine's own `transcribe_provider`."""
    return (settings or {}).get("transcribe_provider") == "local" or local_stt.mode_is_local()


def local_ready(settings: dict | None = None) -> bool:
    """Can a meeting be written down on this computer now, with no key? Only
    when on-device is the way chosen and its model is downloaded, in a build
    that has the engine. Reads file sizes; loads nothing. Never raises."""
    try:
        return bool(uses_local(settings) and local_stt.is_available()
                    and local_stt._is_ready(local_stt.selected_model()))
    except Exception:           # noqa: BLE001
        return False


def _transcribe_local(audio: np.ndarray, settings: dict, language: str, backend) -> list[dict]:
    """On this machine only, with the model dictation uses (src/local_stt.py).
    A failure here is never retried in the cloud: the user chose on-device so
    that the audio stays on the device. The audio is kept, so the transcript
    can be made later (once the model is downloaded, say)."""
    if backend is not None:
        return clean_segments(backend(audio, "local", local_stt.selected_model(), language), audio)
    try:
        # background: a dictation that arrives while a meeting is being
        # written down goes first.
        raw = local_stt.transcribe_segments(audio, language or None, background=True)
    except local_stt.LocalSTTError as e:
        kind = "no_local" if e.kind in ("unavailable", "not_downloaded") else "other"
        raise TranscribeError(str(e), kind) from e
    except Exception as e:      # noqa: BLE001
        raise TranscribeError(
            f"On-device transcription failed ({type(e).__name__}). The audio is kept and "
            f"nothing was sent to a cloud service.", "other") from e
    return clean_segments(raw, audio)


def transcribe(audio: np.ndarray, settings: dict | None = None, backend=None) -> list[dict]:
    """16 kHz mono float32 → [{start_s, end_s, text}] (times within the chunk).

    Returns [] without calling anyone when the chunk holds no voice.
    With Waffler's `transcription_mode` "local" (or `transcribe_provider`
    "local") the model on this computer does it (src/local_stt.py): no key, no
    network, and no fallback to the cloud.
    `backend(audio, provider, model, language)` replaces the network in tests.
    """
    settings = settings or {}
    if len(audio) == 0 or voiced_mask(audio).sum() * 0.03 < MIN_VOICED_S:
        return []
    language = settings.get("language") or ""
    preferred = settings.get("transcribe_provider") or "groq"
    if uses_local(settings):
        return _transcribe_local(audio, settings, language, backend)
    order = [preferred] + [p for p in ("groq", "openai") if p != preferred]
    if backend is None:
        order = [p for p in order if keys.get_key(p)]
        if not order:
            raise TranscribeError("Add a Groq or OpenAI key in Settings to transcribe.", "no_key")
    last: Exception | None = None
    for provider in order:
        model = (settings.get("transcribe_model") or "whisper-large-v3") if provider == "groq" \
            else "whisper-1"
        for attempt in range(3):
            try:
                if backend is not None:
                    raw = backend(audio, provider, model, language)
                else:
                    raw = _call_openai_compatible(
                        audio, keys.get_key(provider),
                        GROQ_BASE_URL if provider == "groq" else None, model, language)
                return clean_segments(raw, audio)
            except Exception as e:      # noqa: BLE001 - classified below, never logged with a key
                last = e
                kind = _classify(e)
                if kind == "rate" and attempt < 2:
                    time.sleep(4 * (attempt + 1))
                    continue
                if kind == "network" and attempt < 1:
                    time.sleep(1.5)
                    continue
                break
    kind = _classify(last) if last else "other"
    messages = {
        "auth": "The transcription key was refused. Check it in Settings.",
        "rate": "The transcription service is rate-limiting. This part will be retried.",
        "network": "Couldn't reach the transcription service. This part will be retried.",
    }
    raise TranscribeError(messages.get(kind, f"Transcription failed ({type(last).__name__})."), kind)
