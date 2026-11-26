"""Speech to text for one chunk of audio.

Waffler's one setting decides how (`transcription_mode` in settings.json,
src/local_stt.py): "local" runs the model on this computer, the same one
dictation uses, with no key and no network; otherwise Groq `whisper-large-v3`
first (the pipeline Waffler uses: FLAC upload, verbose segments), OpenAI as the
fallback. And when Waffler Plus is the chosen way and the account has it
(src/account.py), the account service does it, with no key and no model here:
it takes a meeting in parts of PLUS_PART_S, so the recorder hands those over
instead of chunks (`part_seconds`). When the service fails on its own side
(SERVICE_FAULTS) a meeting goes the way it would have without Plus, as a
dictation does, and with nothing else to use its audio waits, to try again
(PLUS_DOWN). The end-of-audio rule is Waffler's: Whisper
invents "Thank you." and channel outros over silence, so a segment that is
only a thanks, a sign-off or an outro is dropped when the audio under it has
no voice or the model itself says nothing was said. Real thanks, said aloud,
stay. Any other line, in any language, goes when the audio shows nobody said
it (`invented`): no voice under it, far more letters than the voice under it
could carry, or one phrase over and over. Whisper writes such lines over
silence, often in another language, and the Waffler Plus answer carries no
confidence numbers to catch them by.
"""
from __future__ import annotations

import re
import time
import zlib

import numpy as np

from . import keys
from .audioproc import RATE, to_flac, voiced_between, voiced_mask, window_rms

try:
    import languages            # the app's own copy: src/ is on its path
    import local_stt
    from log_util import log as _log
except ImportError:
    from src import languages, local_stt
    from src.log_util import log as _log

MIN_VOICED_S = 0.3              # less voice than this in a chunk: not uploaded
_NO_SPEECH_PROB = 0.5
_SEGMENT_MIN_VOICED_S = 0.15
# A line's times can be a little off: its voice is looked for this far either
# side, so a short "Yes." placed late is not taken for silence.
_EDGE_S = 0.3
# More letters than this for each second of voice under a line: no one said
# it. A clear synthetic voice measured 10 to 16 (the voice meter counts only
# the loud 30 ms windows, so pauses between words make the figure higher than
# a speaking rate); very fast speech is roughly 20 to 25 letters a second (an
# estimate); a 24-letter line written over a 0.3 s click is 80. Lines of 20
# letters or fewer are left alone: a short answer under a short voice is normal.
_MAX_LETTERS_PER_VOICED_S = 60.0
_MIN_LETTERS_TO_JUDGE = 20
# Whisper's own bar for a decode that went round in a loop: the text squeezes
# to less than 1/2.4 of its size (openai-whisper compression_ratio_threshold).
_LOOP_RATIO = 2.4

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Waffler Plus holds a request open while it works and stops at ten minutes,
# so a meeting goes up in parts of twelve minutes of its own clock (the
# service's reference asks for 10 to 15), each one request.
PLUS_PART_S = 720.0

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


def _loop_ratio(text: str) -> float:
    """How far the text squeezes: about 1 for what people say, well over 2.4
    for one phrase repeated over and over (Whisper's compression ratio)."""
    raw = text.encode("utf-8")
    return len(raw) / len(zlib.compress(raw)) if raw else 0.0


def invented(text: str, voiced_near: float, check_loop: bool = True) -> bool:
    """Was this line written with nobody speaking? Judged on the audio under it,
    so it holds in any language and for every engine, including the Waffler
    Plus answer, which carries no confidence numbers. `voiced_near` is the
    seconds of voice under the line, _EDGE_S either side included.

    - No voice under it: the model wrote it over silence.
    - Far more letters than that voice could carry: over a cough or a click.
    - One phrase over and over: the model went round in a loop (`check_loop`
      False leaves this one out: a long dictation is many windows of text, and
      long real speech squeezes further than one window's does)."""
    if voiced_near < _SEGMENT_MIN_VOICED_S:
        return True
    letters = sum(ch.isalpha() for ch in text)
    if letters > _MIN_LETTERS_TO_JUDGE and letters / voiced_near > _MAX_LETTERS_PER_VOICED_S:
        return True
    return check_loop and _loop_ratio(text) > _LOOP_RATIO


# A sign-off the model writes at the start or the end of a line that has real
# words beside it ("Thank you for watching So that the ... stuff I need to
# sort out"). Only phrases nobody says in a meeting; the short ones that open
# or close real sentences ("Thank you.", "Bye.", "you", "Okay") are NOT here:
# those are judged on the audio under a whole line (is_outro, invented).
_SIGNOFF = (
    r"(?:(?:okay|ok|so|and|well|alright)[\s,]+)?(?:thank\s+you|thanks)(?:\s+(?:so|very)\s+much)?"
    r"\s+for\s+(?:watching|listening|viewing)(?:\s+(?:this\s+video|everyone|everybody|guys|all))?"
    r"|(?:please\s+|don'?t\s+forget\s+to\s+|remember\s+to\s+)?like\s+and\s+subscribe"
    r"(?:\s+to\s+(?:my|the|our)\s+channel)?"
    r"|(?:please\s+)?(?:hit|smash)\s+(?:the|that)\s+like\s+button"
    r"|see\s+you\s+in\s+the\s+next\s+(?:one|video)")
_SIGNOFF_AT_START = re.compile(r"^\s*(?:" + _SIGNOFF + r")(?P<gap>\s*[.!?,;:\-]+\s*|\s+)(?=\S)", re.IGNORECASE)
_SIGNOFF_AT_END = re.compile(r"(?P<lead>^|[.!?,;]\s*|\s)(?P<phrase>(?:" + _SIGNOFF + r"))[\s.!?]*$", re.IGNORECASE)
# With no punctuation after it, a sign-off is a join (two segments run
# together) only when a sentence begins straight after: one of these,
# capitalised. "Thanks for watching Sam's kids" is a sentence, not a join.
_SENTENCE_STARTERS = frozenset(
    "so and but the i i'm i'll i've it it's that that's this there we we're we'll "
    "you your they he she okay ok now well yeah yes no right alright anyway um uh "
    "let's let what why how when if then also just my our in on at for to".split())


def strip_signoffs(text: str) -> str:
    """`text` without a made-up sign-off at its start or its end, when real
    words are beside it. A line that is only a sign-off is left as it is (the
    audio rule decides it: is_outro / invented). At the start it goes when
    punctuation follows it or a new sentence begins straight after; at the
    end when it follows punctuation or is capitalised after a lower-case
    word (a join). Never raises, never returns less than the input unless it
    stripped one of the phrases above."""
    if not isinstance(text, str):
        return text
    out = " ".join(text.split())
    while True:
        m = _SIGNOFF_AT_START.match(out)
        if not m:
            break
        rest = out[m.end():]
        first = re.match(r"[\w']+", rest)
        punct = bool(re.search(r"[.!?,;:\-]", m.group("gap")))
        if not first or not any(ch.isalpha() for ch in rest):
            break
        if not punct and not (first.group(0)[0].isupper() and first.group(0).lower() in _SENTENCE_STARTERS):
            break
        out = rest[:1].upper() + rest[1:]
    while True:
        m = _SIGNOFF_AT_END.search(out)
        if not m:
            break
        head = out[:m.start("phrase")].rstrip()
        if not any(ch.isalpha() for ch in head):
            break
        joined = m.group("lead") and not re.search(r"[.!?,;]", m.group("lead")) and m.group("lead").strip() == ""
        if joined and not m.group("phrase")[0].isupper():
            break
        if joined and head[-1] in ".!?,;":
            joined = False
        out = head.rstrip(",;: ").rstrip()
    only = re.sub(r"\s*,\s*", " ", re.sub(r"[.!?]+", " ", out)).split()
    if not only or re.fullmatch(r"(?:\s*(?:" + _SIGNOFF + r")\s*)+", " ".join(only), re.IGNORECASE):
        return text                  # nothing but sign-offs: the audio rule decides, as before
    return out


def clean_segments(raw_segments, audio: np.ndarray) -> list[dict]:
    """Model segments → [{start_s, end_s, text}], hallucinations removed."""
    mask = voiced_mask(audio)
    out: list[dict] = []
    repeats = 0
    for seg in raw_segments or []:
        text = " ".join(str(_get(seg, "text", "") or "").split())
        if not text:
            continue
        text = strip_signoffs(text)
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
        if invented(text, voiced_between(mask, start - _EDGE_S, end + _EDGE_S)):
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


# What a note says when audio came in (an imported file, say) and nothing can
# write it down yet: no key, no model on this computer, no Waffler Plus. The
# audio is kept. This is a state, not a failure: the Meetings page says it in
# its own calm words and Waffler's engine keeps the sentence for everything else.
SPEECH_NEEDS_SETUP = ("The audio is saved. Writing it down needs a speech key, the free model "
                      "or Waffler Plus.")


class TranscribeError(Exception):
    """`kind` is 'no_key', 'no_local', 'auth', 'rate', 'network', 'plus' or 'other'."""

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


def _account():
    """src/account.py, the copy the app uses (one module, so one plan in memory)."""
    try:
        from src import account
    except ImportError:
        import account
    return account


def uses_plus() -> bool:
    """Is this for Waffler Plus? It is the chosen way and there to use: this
    computer is signed in to an account that has it (src/account.py
    plus_ready). Chosen but gone, the engine goes on as it would have before:
    the model on this computer, or a key. Never raises."""
    try:
        return bool(_account().plus_ready())
    except Exception:           # noqa: BLE001
        return False


# ── Waffler Plus failing on its own side ─────────────────────────────────────
#
# The account service's reasons (src/account.py) that are its own fault, not
# the person's: a 5xx (its provider key missing or refused, its server-wide
# spend ceiling, its queues full, a provider failing in passing, a part that
# ran out of time, its disk full) and "plus_burst", its hourly cap on Plus
# requests. On 26 Nov its provider key was refused and every meeting failed.
# A dictation then goes another way (src/transcribe_whisper.py); so does a
# meeting: the person's key, else the model on this computer. The plan, the
# allowance, signing out and no internet are not in this list: those keep
# the service's own sentence, and the audio waits as before.
SERVICE_FAULTS = frozenset({"busy", "transcribe_failed", "transcribe_timeout", "server_full",
                            "write_failed", "plus_burst"})
# After such a fault Plus rests this long for that job (written down, or
# written): what comes in between goes the other way at once instead of
# waiting on Plus first. Then it is tried again by itself. As for a dictation.
PLUS_REST_S = 300.0
_rest_until: dict[str, float] = {}
# What a meeting says when Plus failed on its side and there is nothing else
# to write it down with. The audio waits, and "Try again" is beside it.
PLUS_DOWN = "Waffler Plus isn't working right now. The recording is saved, so try again later."


def rest_plus(job: str) -> None:
    """Plus failed on its side for `job` ("transcribe" or "write"): rest it."""
    _rest_until[job] = time.monotonic() + PLUS_REST_S


def plus_resting(job: str) -> bool:
    return time.monotonic() < _rest_until.get(job, 0.0)


def _local_model_ready() -> bool:
    """Is the speech model on this computer downloaded, in a build that has
    the engine, whatever the setting says? Loads nothing. Never raises."""
    try:
        return bool(local_stt.is_available() and local_stt._is_ready(local_stt.selected_model()))
    except Exception:           # noqa: BLE001
        return False


def fallback_way(settings: dict | None = None) -> str:
    """What writes a meeting down when Waffler Plus cannot: "local" or
    "key", or "" when there is nothing. The way the setting says when that
    can work (the model on this computer, which then never becomes a cloud
    service; or a key); with no key, the model on this computer if it is
    downloaded. The same rule as a dictation's (_leave_plus). Never raises."""
    try:
        if uses_local(settings):
            return "local" if local_ready(settings) else ""
        if any(keys.get_key(p) for p in ("groq", "openai")):
            return "key"
    except Exception:           # noqa: BLE001
        return ""
    return "local" if _local_model_ready() else ""


def plus_for_now(settings: dict | None = None) -> bool:
    """Does Waffler Plus write this down? It is the chosen way and there to
    use, and not resting after a fault on its side with another way to go."""
    return uses_plus() and not (plus_resting("transcribe") and fallback_way(settings))


def part_seconds(settings: dict | None = None) -> float:
    """How much audio `transcribe` is handed at once. 0: chunk by chunk, as it
    is recorded. With Waffler Plus, PLUS_PART_S: the chunks wait on disk and
    go when the recording has ended, joined into parts (recorder.finish_waiting)."""
    return PLUS_PART_S if plus_for_now(settings) else 0.0


def _transcribe_plus(audio: np.ndarray, language: str, can_fall_back: bool = False) -> list[dict]:
    """One part of a meeting, written down by the account service (POST
    /api/plus/transcribe). No key is read and no model here is asked. Too
    many in a minute: it waits as long as the service says, twice at most
    (so does the service's hourly cap when there is nothing else to use).
    A fault on the service's side (SERVICE_FAULTS) rests Plus and raises
    kind "plus_down" with PLUS_DOWN, for the caller to go another way. Any
    other refusal is its one sentence (src/account.py), kind "plus", and is
    never retried with a key: the audio is kept, to try again."""
    acct = _account()
    flac = to_flac(audio)
    for attempt in range(3):
        answer = acct.transcribe(flac, language=language)
        reason = answer.get("reason")
        waits = reason == "too_many" or (reason == "plus_burst" and not can_fall_back)
        if answer.get("ok") or not waits or attempt == 2:
            break
        time.sleep(min(max(float(answer.get("wait_s") or 20.0), 1.0), 60.0))
    if not answer.get("ok"):
        if answer.get("reason") in SERVICE_FAULTS:
            rest_plus("transcribe")
            raise TranscribeError(PLUS_DOWN, "plus_down")
        raise TranscribeError(str(answer.get("error") or acct.FAILED), "plus")
    return clean_segments([{"start": g["start_s"], "end": g["end_s"], "text": g["text"]}
                           for g in answer["segments"]], audio)


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


def transcribe(audio: np.ndarray, settings: dict | None = None, backend=None,
               fall_back: bool = True) -> list[dict]:
    """16 kHz mono float32 → [{start_s, end_s, text}] (times within the chunk).

    Returns [] without calling anyone when the chunk holds no voice.
    With Waffler Plus the chosen way the account service does it (the audio
    is then a part of the meeting, up to PLUS_PART_S): no key, no model here.
    When it fails on its own side (SERVICE_FAULTS) this audio goes the way
    fallback_way says, or, with nothing there, TranscribeError kind
    "plus_down" says so (PLUS_DOWN) and the audio is kept. `fall_back` False
    raises "plus_down" even when there is a way: recorder.finish_waiting
    then sends the rest chunk by chunk, not a whole part at once.
    With Waffler's `transcription_mode` "local" (or `transcribe_provider`
    "local") the model on this computer does it (src/local_stt.py): no key, no
    network, and no fallback to the cloud.
    `backend(audio, provider, model, language)` replaces the network in tests.
    """
    settings = settings or {}
    if len(audio) == 0 or voiced_mask(audio).sum() * 0.03 < MIN_VOICED_S:
        return []
    # The person's pick; "auto" (Detect automatically) sends none, on purpose;
    # unset or empty sends this computer's own language, never a guess.
    language = languages.resolve(settings.get("language")) or ""
    preferred = settings.get("transcribe_provider") or "groq"
    if uses_plus():
        way = fallback_way(settings)
        if not (plus_resting("transcribe") and way):
            try:
                return _transcribe_plus(audio, language, can_fall_back=bool(way))
            except TranscribeError as e:
                way = fallback_way(settings)
                if e.kind != "plus_down" or not fall_back or not way:
                    raise
                _log(f"[meetings] Waffler Plus failed on its side: written down with "
                     f"{'the model on this computer' if way == 'local' else 'your key'}")
        if way == "local":
            return _transcribe_local(audio, settings, language, backend)
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
