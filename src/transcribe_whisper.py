"""Whisper transcription — local (fast) or API (fallback).

LOCAL_WHISPER=1 in .env enables on-device transcription:
  - Mac (Apple Silicon): uses mlx-whisper  → ~0.2-0.5s, no internet
  - Windows / Intel Mac: uses faster-whisper → ~0.5-2s on CPU, no internet

Both download a small model (~150MB) on first run, then work offline.
The GPT cleanup step still needs internet for longer dictations.
"""

import os
import sys
import time
import tempfile
import platform
from pathlib import Path

from openai import OpenAI

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


VOCAB_FILE    = Path.home() / ".voiceflow" / "vocab.json"
SETTINGS_FILE = Path.home() / ".voiceflow" / "settings.json"


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
    """Turn vocab list into a Whisper initial_prompt hint."""
    if not words:
        return ""
    return "Words to recognise correctly: " + ", ".join(words) + "."


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


class WhisperTranscriber:
    """Transcribes audio using local model or OpenAI API.

    Priority:
      1. mlx-whisper  (Mac Apple Silicon + LOCAL_WHISPER=1)
      2. faster-whisper (Windows/Intel + LOCAL_WHISPER=1)
      3. OpenAI Whisper API (always available, needs internet)
    """

    def __init__(self, api_key: str, model: str = "whisper-1"):
        self.api_key = api_key
        self.model   = model
        self.client  = OpenAI(api_key=api_key)

        if _USE_LOCAL and _mlx_whisper:
            self._backend = "mlx"
            print("⚡ Transcription: local mlx-whisper (no API calls)")
        elif _USE_LOCAL and _faster_whisper:
            self._backend = "faster"
            print("⚡ Transcription: local faster-whisper (no API calls)")
        else:
            self._backend = "api"
            if _USE_LOCAL:
                print("⚠️  Falling back to OpenAI API (local model not loaded)")

    def transcribe_sync(self, audio_bytes: bytes):
        if self._backend == "mlx":
            return self._transcribe_mlx(audio_bytes)
        elif self._backend == "faster":
            return self._transcribe_faster(audio_bytes)
        else:
            return self._transcribe_api(audio_bytes)

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
            lang     = settings.get("language", "auto")
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
            lang     = settings.get("language", "auto")
            fw_lang  = lang if lang != "auto" else None
            segments, _ = _faster_whisper.transcribe(tmp, beam_size=1, language=fw_lang)
            text = " ".join(seg.text for seg in segments).strip()
            print(f"⚡ faster-whisper ({(time.time()-t0)*1000:.0f}ms): {text[:80]}")
            return text
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
            lang     = settings.get("language", "auto")
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
