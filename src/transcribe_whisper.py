"""Whisper transcription — Groq (fastest) → local → OpenAI API (fallback).

Priority order:
  1. Groq Whisper  (GROQ_API_KEY set) → ~100-300ms, needs internet
  2. mlx-whisper   (Mac ARM + LOCAL_WHISPER=1) → ~0.2-0.5s, no internet
  3. faster-whisper (Windows/Intel + LOCAL_WHISPER=1) → ~0.5-2s on CPU
  4. OpenAI Whisper API (always available) → 2-5s, needs internet
"""

import os
import sys
import time
import tempfile
import platform
from pathlib import Path

from openai import OpenAI

# ── Try to load Groq SDK ────────────────────────────────────────────────────
_groq_mod = None
try:
    import groq as _groq_mod
except ImportError:
    pass

_USE_LOCAL = os.getenv("LOCAL_WHISPER", "0") == "1"
_IS_MAC_ARM = sys.platform == "darwin" and platform.machine() == "arm64"
_IS_WINDOWS  = sys.platform == "win32"

# ── Try to load local backend ───────────────────────────────────────────────
_mlx_whisper    = None
_faster_whisper = None

if _USE_LOCAL:
    if _IS_MAC_ARM:
        try:
            import mlx_whisper as _mlx_whisper
            print("🍎 mlx-whisper loaded — local transcription on Apple Silicon")
        except ImportError:
            print("⚠️  LOCAL_WHISPER=1 but mlx-whisper not installed.")
            print("   Run: bash install_local_whisper.sh")
    else:
        # Windows or Intel Mac — use faster-whisper
        try:
            from faster_whisper import WhisperModel as _FasterWhisperModel
            _faster_whisper = _FasterWhisperModel(
                "base",
                device="cpu",
                compute_type="int8"   # fastest CPU mode
            )
            print("⚡ faster-whisper loaded — local transcription (CPU)")
        except ImportError:
            print("⚠️  LOCAL_WHISPER=1 but faster-whisper not installed.")
            print("   Run: pip install faster-whisper")


VOCAB_FILE    = Path.home() / ".waffler-hosted" / "vocab.json"
SETTINGS_FILE = Path.home() / ".waffler-hosted" / "settings.json"


def load_vocab() -> list[str]:
    """Load user's custom vocabulary words."""
    try:
        if VOCAB_FILE.exists():
            import json
            return json.loads(VOCAB_FILE.read_text())
    except Exception:
        pass
    return []


def load_settings() -> dict:
    """Load persisted settings (language, auto_paste, etc.)."""
    try:
        if SETTINGS_FILE.exists():
            import json
            return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}


def vocab_to_prompt(words: list[str]) -> str:
    """Turn vocab list into a Whisper initial_prompt hint.

    Whisper's initial_prompt is conditioning text — it should be a bare
    word list, NOT an instruction sentence.  Sentence-like prompts cause
    Whisper to hallucinate lines containing those words.
    """
    if not words:
        return ""
    return ", ".join(words)


def _levenshtein_distance(a: str, b: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(a) < len(b):
        return _levenshtein_distance(b, a)
    if len(b) == 0:
        return len(a)
    
    previous_row = range(len(b) + 1)
    for i, ca in enumerate(a):
        current_row = [i + 1]
        for j, cb in enumerate(b):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (ca != cb)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def fuzzy_match_word(transcribed: str, vocab: list[str], threshold: float = 0.75) -> list[tuple[str, str]]:
    """
    Find vocabulary words that are similar to transcribed words.
    Returns list of (transcribed_word, vocab_word) pairs that might be corrections.
    Uses both exact matching and fuzzy matching with Levenshtein distance.
    """
    if not vocab:
        return []
    
    # Normalize vocab for comparison
    vocab_lower = {w.lower(): w for w in vocab}
    transcribed_lower = transcribed.lower()
    
    # Split into words (handle punctuation)
    import re
    words = re.findall(r"[a-zA-Z]+", transcribed_lower)
    
    corrections = []
    vocab_words = list(vocab_lower.keys())
    
    for word in words:
        # First check exact match (case-insensitive)
        if word in vocab_lower:
            continue
        
        # Then check fuzzy match
        for vword in vocab_words:
            if len(word) < 3 or len(vword) < 3:
                continue
            
            # Calculate similarity ratio
            max_len = max(len(word), len(vword))
            if max_len == 0:
                continue
            
            distance = _levenshtein_distance(word, vword)
            similarity = 1 - (distance / max_len)
            
            if similarity >= threshold:
                corrections.append((word, vocab_lower[vword]))
                break
    
    return corrections


def apply_vocab_corrections(transcribed: str, vocab: list[str]) -> tuple[str, list[str]]:
    """
    Apply vocabulary corrections to transcribed text.
    Returns tuple of (corrected_text, list_of_corrections).
    """
    if not vocab:
        return transcribed, []
    
    corrections = fuzzy_match_word(transcribed, vocab)
    
    if not corrections:
        return transcribed, []
    
    corrected = transcribed
    applied = []
    
    for misheard, correct in corrections:
        # Replace word boundaries with proper case
        import re
        pattern = r'\b' + re.escape(misheard) + r'\b'
        if re.search(pattern, corrected, re.IGNORECASE):
            corrected = re.sub(pattern, correct, corrected, flags=re.IGNORECASE)
            applied.append(f"'{misheard}' → '{correct}'")
    
    return corrected, applied


def _is_vocab_echo(text: str, vocab: list) -> bool:
    """Detect when Whisper echoed the vocab prompt instead of transcribing.

    Whisper sometimes regurgitates the `prompt` argument verbatim when given
    silence or low-quality audio. Catches that specific failure so the vocab
    list doesn't get pasted as output when the user records nothing.
    """
    if not vocab or not text:
        return False
    import re

    text_tokens = set(re.findall(r"\w+", text.lower()))
    if not text_tokens:
        return False

    vocab_tokens = set()
    for word in vocab:
        for tok in re.findall(r"\w+", str(word).lower()):
            vocab_tokens.add(tok)
    if not vocab_tokens:
        return False

    # Exact match of the comma-joined prompt form (with/without trailing punct)
    prompt_form = ", ".join(str(w) for w in vocab).lower().strip().rstrip(".,!?")
    if text.lower().strip().rstrip(".,!?") == prompt_form:
        return True

    # Heuristic: if nearly every token in the output is a vocab token,
    # and the output isn't much longer than the vocab itself, it's an echo.
    overlap = text_tokens & vocab_tokens
    ratio = len(overlap) / len(text_tokens)
    if ratio >= 0.7 and len(text_tokens) <= len(vocab_tokens) + 2:
        return True

    return False


def _pad_audio_with_silence(audio_bytes: bytes, padding_ms: int = 300) -> bytes:
    """Add silence padding to the start and end of a WAV clip.

    Whisper mis-transcribes short clips when the first or last syllable is
    partially clipped (common with hotkey-triggered recording, where PyAudio
    takes ~50-200ms to spin up the input stream). Padding gives Whisper's
    attention mechanism clean silence boundaries and prevents the language
    model from "guessing" at half-heard words.
    """
    import io
    import wave
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        silence_frames = int(params.framerate * padding_ms / 1000)
        silence_bytes = b"\x00" * (silence_frames * params.sampwidth * params.nchannels)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setparams(params)
            w.writeframes(silence_bytes + frames + silence_bytes)
        return out.getvalue()
    except Exception:
        return audio_bytes


def _strip_hallucinations(text: str) -> str:
    """Remove common Whisper hallucinations from transcribed text.

    Whisper often hallucinates stock phrases when it encounters silence
    or low-quality audio, especially at the end of a recording.
    """
    import re

    # Phrases that Whisper hallucinates on silence / trailing audio
    _HALLUCINATION_PATTERNS = [
        r"thank you\.?$",
        r"thanks for watching\.?$",
        r"thanks for listening\.?$",
        r"please subscribe\.?$",
        r"subtitles by .*$",
        r"translated by .*$",
        r"captioned by .*$",
        r"you$",  # common single-word hallucination
    ]

    stripped = text.strip()
    for pattern in _HALLUCINATION_PATTERNS:
        stripped = re.sub(pattern, "", stripped, flags=re.IGNORECASE).strip()

    # If the entire transcription was a hallucination, return empty
    if not stripped or stripped in (".", ",", "!"):
        return ""

    return stripped


class WhisperTranscriber:
    """Transcribes audio — Groq (fastest) → local → OpenAI API (fallback).

    Priority:
      1. Groq Whisper  (GROQ_API_KEY set + groq SDK installed)
      2. mlx-whisper   (Mac Apple Silicon + LOCAL_WHISPER=1)
      3. faster-whisper (Windows/Intel + LOCAL_WHISPER=1)
      4. OpenAI Whisper API (always available, needs internet)
    """

    def __init__(self, api_key: str = "", model: str = "whisper-1",
                 groq_api_key: str = ""):
        self.api_key = api_key
        self.model   = model
        self.groq_api_key = groq_api_key
        self.client  = OpenAI(api_key=api_key) if api_key else None
        self._groq_client = None

        # Try Groq first (fastest cloud option)
        if groq_api_key and _groq_mod:
            self._groq_client = _groq_mod.Groq(api_key=groq_api_key)
            self._backend = "groq"
            print("⚡ Transcription: Groq Whisper (fastest)")
        elif _USE_LOCAL and _mlx_whisper:
            self._backend = "mlx"
            print("⚡ Transcription: local mlx-whisper (no API calls)")
        elif _USE_LOCAL and _faster_whisper:
            self._backend = "faster"
            print("⚡ Transcription: local faster-whisper (no API calls)")
        elif api_key:
            self._backend = "api"
            if _USE_LOCAL:
                print("⚠️  Falling back to OpenAI API (local model not loaded)")
        else:
            self._backend = "api"
            print("⚠️  No transcription backend available")

    def transcribe_sync(self, audio_bytes: bytes):
        audio_bytes = _pad_audio_with_silence(audio_bytes)

        if self._backend == "groq":
            try:
                raw = self._transcribe_groq(audio_bytes)
            except Exception as e:
                print(f"⚠️  Groq transcription failed ({e}), falling back to OpenAI")
                if self.client:
                    raw = self._transcribe_api(audio_bytes)
                else:
                    raise
        elif self._backend == "mlx":
            raw = self._transcribe_mlx(audio_bytes)
        elif self._backend == "faster":
            raw = self._transcribe_faster(audio_bytes)
        else:
            raw = self._transcribe_api(audio_bytes)

        cleaned = _strip_hallucinations(raw)
        if cleaned != raw:
            print(f"[whisper] Stripped hallucination: '{raw}' → '{cleaned}'")

        # Whisper sometimes echoes the vocab prompt verbatim on silence —
        # discard rather than pasting the user's vocabulary list as output.
        try:
            vocab = load_vocab()
        except Exception:
            vocab = []
        if _is_vocab_echo(cleaned, vocab):
            print(f"[whisper] Discarded vocab-echo hallucination: '{cleaned}'")
            return ""

        return cleaned

    def get_duration_seconds(self) -> float:
        """Return the duration of the last transcription in seconds (API only)."""
        return getattr(self, '_last_duration', 0.0)

    # ── Local backends ───────────────────────────────────────────────────────

    def _transcribe_mlx(self, audio_bytes: bytes) -> str:
        """Apple Silicon — mlx-whisper via Neural Engine."""
        t0 = time.time()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            vocab    = load_vocab()
            hint     = vocab_to_prompt(vocab)
            settings = load_settings()
            lang     = settings.get("language", "en")
            kwargs   = dict(path_or_hf_repo="mlx-community/whisper-base-mlx")
            if hint:
                kwargs["initial_prompt"] = hint
            if lang and lang != "auto":
                kwargs["language"] = lang
            result = _mlx_whisper.transcribe(tmp, **kwargs)
            text = result["text"].strip()
            print(f"⚡ mlx-whisper ({(time.time()-t0)*1000:.0f}ms): {text[:80]}")
            return text
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _transcribe_faster(self, audio_bytes: bytes) -> str:
        """Windows / Intel Mac — faster-whisper on CPU."""
        t0 = time.time()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            settings = load_settings()
            lang     = settings.get("language", "en")
            fw_lang  = lang if lang != "auto" else None
            segments, _ = _faster_whisper.transcribe(tmp, beam_size=1, language=fw_lang)
            text = " ".join(seg.text for seg in segments).strip()
            print(f"⚡ faster-whisper ({(time.time()-t0)*1000:.0f}ms): {text[:80]}")
            return text
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── Groq (fastest cloud) ─────────────────────────────────────────────────

    def _transcribe_groq(self, audio_bytes: bytes) -> str:
        """Groq Whisper — same model, ~10-50x faster than OpenAI."""
        t0 = time.time()
        print("⚡ Groq Whisper API...")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            vocab    = load_vocab()
            hint     = vocab_to_prompt(vocab)
            settings = load_settings()
            lang     = settings.get("language", "en")
            with open(tmp, "rb") as af:
                kwargs = dict(
                    model="whisper-large-v3",
                    file=af,
                    response_format="text",
                )
                if hint:
                    kwargs["prompt"] = hint
                if lang and lang != "auto":
                    kwargs["language"] = lang
                response = self._groq_client.audio.transcriptions.create(**kwargs)
            text = response.strip()
            duration = time.time() - t0
            self._last_duration = duration
            print(f"⚡ Groq Whisper ({duration*1000:.0f}ms): {text[:80]}")
            return text
        except Exception as e:
            print(f"❌ Groq Whisper error: {e}")
            raise
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── API fallback ─────────────────────────────────────────────────────────

    def _transcribe_api(self, audio_bytes: bytes) -> str:
        """OpenAI Whisper API — always works, needs internet."""
        t0 = time.time()
        print(f"📡 OpenAI Whisper API...")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            vocab    = load_vocab()
            hint     = vocab_to_prompt(vocab)
            settings = load_settings()
            lang     = settings.get("language", "en")
            with open(tmp, "rb") as af:
                kwargs = dict(model=self.model, file=af, response_format="text")
                if hint:
                    kwargs["prompt"] = hint
                if lang and lang != "auto":
                    kwargs["language"] = lang
                response = self.client.audio.transcriptions.create(**kwargs)
            text = response.strip()
            duration = time.time() - t0
            self._last_duration = duration  # Store for usage tracking
            print(f"✅ API Whisper ({duration*1000:.0f}ms): {text[:80]}")
            return text
        except Exception as e:
            print(f"❌ Whisper API error: {e}")
            raise
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
