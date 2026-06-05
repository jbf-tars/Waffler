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
import re
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
    Returns list of (transcribed_phrase, vocab_word) pairs to substitute.

    Two passes:
      1. Single-token fuzzy match (Levenshtein-similarity ≥ threshold).
      2. **Bigram collapse** match — when Whisper splits a compound name into
         two words ("Ashkan" → "Nash can", "Ashcan", "Ash can"), pass 1
         can't find it. We glue every adjacent bigram together
         ("nashcan", "ashcan") and fuzzy-match that against single-word
         vocab entries. This is the fix for the real-world "Nash can" →
         "Ashkan" miss seen in transcript history.
    """
    if not vocab:
        return []

    vocab_lower = {w.lower(): w for w in vocab}
    transcribed_lower = transcribed.lower()
    words = re.findall(r"[a-zA-Z]+", transcribed_lower)

    corrections = []
    vocab_words = list(vocab_lower.keys())

    # Track which input tokens we've matched so we don't double-correct
    # (e.g., bigram pass shouldn't fire on tokens already matched as unigrams).
    matched_tokens: set[str] = set()

    # Pass 1 — single-word fuzzy match.
    for word in words:
        if word in vocab_lower:
            # Exact (case-insensitive) match. If the user spelled it in
            # canonical form already, no correction needed. If the case
            # differs (e.g. transcribed "cobie" but vocab has "COBie"),
            # emit a correction so the canonical form replaces it.
            canonical = vocab_lower[word]
            matched_tokens.add(word)
            if word != canonical:
                corrections.append((word, canonical))
            continue
        for vword in vocab_words:
            if len(word) < 3 or len(vword) < 3:
                continue
            max_len = max(len(word), len(vword))
            if max_len == 0:
                continue
            distance = _levenshtein_distance(word, vword)
            similarity = 1 - (distance / max_len)
            if similarity >= threshold:
                corrections.append((word, vocab_lower[vword]))
                matched_tokens.add(word)
                break

    # Pass 2 — bigram collapse against single-word vocab entries.
    # We only target vocab terms that are themselves single words (no spaces),
    # because the failure mode is "Whisper split a compound into two words".
    # Threshold is intentionally a touch lower than the unigram pass: gluing
    # two words always adds 1 char vs the original (the implicit space), so
    # a perfect distortion still scores ~0.85 instead of 1.0. 0.70 catches
    # "Nash can" ↔ "Ashkan" (similarity 0.71) without admitting unrelated
    # bigrams.
    bigram_threshold = max(0.65, threshold - 0.05)
    single_vocab_words = [v for v in vocab_words if " " not in v and len(v) >= 4]
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        if a in matched_tokens or b in matched_tokens:
            continue
        glued = a + b
        for vword in single_vocab_words:
            max_len = max(len(glued), len(vword))
            if max_len < 4:
                continue
            distance = _levenshtein_distance(glued, vword)
            similarity = 1 - (distance / max_len)
            if similarity >= bigram_threshold:
                # Substitute the literal "a b" two-word sequence (with the
                # space) so apply_vocab_corrections can replace it as a phrase.
                corrections.append((f"{a} {b}", vocab_lower[vword]))
                matched_tokens.add(a)
                matched_tokens.add(b)
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
        pattern = r'\b' + re.escape(misheard) + r'\b'
        if re.search(pattern, corrected, re.IGNORECASE):
            corrected = re.sub(pattern, correct, corrected, flags=re.IGNORECASE)
            applied.append(f"'{misheard}' → '{correct}'")
    
    return corrected, applied


# Whisper's most common silence-hallucinations. When the audio is empty or
# near-empty, the model's training corpus (heavy on YouTube transcripts) leaks
# through as canned closing-line phrases. We strip these when they're the
# entire output — never substring-match, since a real recording can mention
# them in passing ("did you see that 'thanks for watching' ad?").
_WHISPER_HALLUCINATIONS = frozenset(s.strip().lower() for s in [
    "thanks for watching",
    "thanks for watching!",
    "thanks for watching.",
    "thank you for watching",
    "thank you for watching!",
    "thank you for watching.",
    "thanks for watching, and i'll see you in the next video.",
    "see you in the next video",
    "see you next time",
    "please subscribe",
    "please like and subscribe",
    "don't forget to like and subscribe",
    "like and subscribe",
    "subscribe to my channel",
    "[music]",
    "[applause]",
    "you",  # Whisper's most common 1-token hallucination on noise
    ".",
    # ── v3.14.39 — short-clip Whisper hallucinations ──────────────────
    # Complementary to v3.14.38's styling-prompt fix. v3.14.38 stops the
    # LLM styler from generating filler-tails like "and many more"; this
    # catches the upstream case where Whisper ITSELF hallucinates these
    # phrases on <1 s near-silent audio (so the styling LLM never even
    # runs — local pass-through emits the Whisper output verbatim).
    # User log on 14 May: 28 instances of literal "Done: and more." with
    # `styling (local): 0ms` — every one was a Whisper-layer hallucination.
    "and more",
    "and more.",
    "and more!",
    "and more...",
    # Sign-offs that show up on very short clips of room noise.
    "bye",
    "bye.",
    "bye!",
    "bye-bye",
    "bye bye",
    "goodbye",
    "goodbye.",
    "thank you",
    "thank you.",
    "thank you!",
    "thanks",
    "thanks.",
    "okay",
    "okay.",
    "ok",
    "ok.",
    "yeah",
    "yeah.",
    "uh",
    "um",
    "hmm",
    "mhm",
])


def _is_whisper_hallucination(text: str) -> bool:
    """True if the entire transcript is a known Whisper boilerplate hallucination.

    Whisper produces YouTube outro lines ("Thanks for watching!", "Please
    subscribe", etc.) when fed silence or very low-energy audio. The vocab
    echo filter doesn't catch these because they don't overlap with vocab.
    Only triggers when the cleaned text is *exactly* one of the canned
    phrases — a real utterance that mentions one of them in context is left
    alone.
    """
    if not text:
        return False
    cleaned = text.strip().lower().rstrip(".,!?")
    if not cleaned:
        return True
    # Compare against both raw and trailing-punct-stripped forms.
    return text.strip().lower() in _WHISPER_HALLUCINATIONS or cleaned in _WHISPER_HALLUCINATIONS


def _is_vocab_echo(text: str, vocab: list) -> bool:
    """Detect when Whisper echoed the vocab prompt instead of transcribing.

    Whisper sometimes regurgitates the `prompt` argument verbatim when given
    silence or low-quality audio. Catches that specific failure so the vocab
    list doesn't get pasted as output when the user records nothing.
    """
    if not vocab or not text:
        return False

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

    overlap = text_tokens & vocab_tokens
    ratio = len(overlap) / len(text_tokens)

    # Every distinct token in the output is a vocab token — no real words at
    # all, so whatever the length this is just regurgitation of the prompt.
    if ratio >= 1.0:
        return True

    # Short transcript (<= 10 distinct words) dominated by vocab tokens.
    # Real speech of that length almost never hits 50%+ vocab density unless
    # the user was literally reading their vocab list aloud.
    if len(text_tokens) <= 10 and ratio >= 0.5:
        return True

    # Original heuristic: output length is close to vocab length AND vocab
    # dominates. Catches the classic case where Whisper spits out the whole
    # vocab list with one or two extra filler tokens.
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


def _split_audio_on_silence(
    audio_bytes: bytes,
    target_chunk_s: float = 25.0,
    hard_max_s: float = 30.0,
    window_ms: int = 30,
    min_silence_run_ms: int = 300,
) -> list:
    """Split a long WAV clip into <= ~hard_max_s chunks, cutting at quiet points.

    THE LONG-RECORDING FIX. Whisper's decoder terminates early on long audio
    (>~30 s): it transcribes the first ~20-30 s, emits an end-of-transcript
    token that surfaces as a hallucinated outro ("Thank you for watching!",
    "and so on", "and others"), and silently drops the remaining audio. The
    user speaks for 90 s, the clip is fully captured (2-3 MB on disk), but the
    transcript comes back with only the first third plus a fake ending.

    Splitting the clip into <= 25-30 s pieces sidesteps the failure entirely:
    each chunk is short enough that the decoder runs to completion. Cutting on
    *silence* (a quiet run >= ``min_silence_run_ms``) rather than a fixed
    offset avoids slicing through the middle of a word.

    Returns a list of WAV-byte chunks (each independently transcribable). For
    clips already short enough, unusual formats, or any error, returns
    ``[audio_bytes]`` unchanged -- i.e. the current single-shot path, so short
    recordings (the common case) are completely unaffected.
    """
    import io
    import wave
    try:
        import numpy as np
        with wave.open(io.BytesIO(audio_bytes), "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        framerate = params.framerate
        sampwidth = params.sampwidth
        nchannels = params.nchannels
        nframes = params.nframes
        duration = nframes / float(framerate) if framerate else 0.0

        # Short clip or format we don't handle -> leave it alone.
        if (
            duration <= hard_max_s
            or sampwidth != 2
            or nchannels != 1
            or nframes == 0
        ):
            return [audio_bytes]

        samples = np.frombuffer(frames, dtype=np.int16)
        win = max(1, int(framerate * window_ms / 1000))
        n_win = len(samples) // win
        if n_win < 2:
            return [audio_bytes]

        # Per-window RMS energy.
        block = samples[: n_win * win].astype(np.float32).reshape(n_win, win)
        rms = np.sqrt(np.mean(block * block, axis=1))

        # Silence threshold: a small fraction of the median energy, floored at
        # an absolute value so a near-silent recording doesn't flag everything.
        # 0.15 * median sits well below speech but above room tone / breaths.
        sil_thresh = max(150.0, float(np.median(rms)) * 0.15)
        is_sil = rms < sil_thresh

        target_win = max(1, int(target_chunk_s * 1000 / window_ms))
        hard_win = max(target_win + 1, int(hard_max_s * 1000 / window_ms))
        min_run = max(1, int(min_silence_run_ms / window_ms))

        # Greedy cut points (window indices): once a chunk passes the target
        # length, cut at the next silence run; if none appears by the hard max,
        # force-cut so a continuous loud monologue is still bounded.
        cuts = []
        start = 0
        i = 0
        while i < n_win:
            length = i - start
            if length >= hard_win:
                cuts.append(i)
                start = i
                i += 1
                continue
            if length >= target_win and is_sil[i]:
                run_end = min(i + min_run, n_win)
                if bool(np.all(is_sil[i:run_end])):
                    cuts.append(i)
                    start = i
                    while i < n_win and is_sil[i]:
                        i += 1
                    continue
            i += 1

        if not cuts:
            return [audio_bytes]

        boundaries = [0] + [c * win for c in cuts] + [len(samples)]
        chunks = []
        for a, b in zip(boundaries[:-1], boundaries[1:]):
            if b - a < win:  # skip empty / sub-window slivers
                continue
            seg = samples[a:b].tobytes()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as ww:
                ww.setnchannels(nchannels)
                ww.setsampwidth(sampwidth)
                ww.setframerate(framerate)
                ww.writeframes(seg)
            chunks.append(buf.getvalue())

        return chunks if len(chunks) >= 2 else [audio_bytes]
    except Exception as e:
        print(f"[whisper] chunk-split failed, using single-shot: {e}")
        return [audio_bytes]


def _strip_hallucinations(text: str) -> str:
    """Remove common Whisper hallucinations from transcribed text.

    Whisper often hallucinates stock phrases when it encounters silence
    or low-quality audio, especially at the end of a recording. The
    training data skews heavily toward YouTube transcripts, so the
    failure modes cluster around channel-end outros.
    """
    # Trailing-only patterns (anchored to end of string). Every entry tolerates
    # optional punctuation/whitespace so we catch "Thanks for watching!",
    # "Thanks for watching." etc.
    _HALLUCINATION_PATTERNS = [
        r"thank you[\.\!\?]*$",
        r"thanks for watching[\.\!\?]*$",
        r"thanks for listening[\.\!\?]*$",
        # YouTube-style subscribe outros in all the usual prefixes.
        r"(?:please|remember to|don'?t forget to|and|like and|so please)\s+subscribe[\.\!\?]*$",
        r"subscribe to (?:my|the|our) channel[\.\!\?]*$",
        r"subscribe[\.\!\?]*$",
        # Channel sign-offs.
        r"see you (?:in the next one|next time|later|in the next video)[\.\!\?]*$",
        r"hit the like button[\.\!\?]*$",
        r"smash that like button[\.\!\?]*$",
        # Auto-caption credits — the WKNO-MEMPHIS / station-attribution shape
        # (real instance from history: "CLOSED CAPTION PROVIDED BY WKNO-MEMPHIS.").
        r"subtitles by .*$",
        r"translated by .*$",
        r"captioned by .*$",
        r"closed\s+caption(?:s|ing)?\s+(?:by|provided\s+by)\s+.*$",
        r"caption(?:s|ing)?\s+provided\s+by\s+.*$",
        # Stock single-word hallucinations on silence.
        r"\byou\b[\.\!\?]*$",
        # v3.14.39 — trailing "and more" / "and many more" / "with much more".
        # YouTube ad-segment tails are common Whisper training data; on short
        # clips it can append the phrase to whatever else it imagined. Pattern
        # (not full-match) so we strip the tail off real content too.
        # This is the transcription-layer complement to v3.14.38's
        # styling-prompt fix: that prevents the LLM styler from generating
        # filler-tails; this catches the case where Whisper itself emits one
        # and local pass-through styling never gets a chance to fix it.
        r"(?:and|with|plus)\s+(?:many\s+|much\s+|lots\s+)?more[\.\!\?]*$",
    ]

    stripped = text.strip()
    original_len = len(stripped)
    for pattern in _HALLUCINATION_PATTERNS:
        stripped = re.sub(pattern, "", stripped, flags=re.IGNORECASE).strip()
        # Clean up any trailing comma/semicolon left dangling after a strip,
        # e.g. "web outfits, remember to subscribe!" -> "web outfits," -> "web outfits".
        stripped = re.sub(r"[,;\s]+$", "", stripped)

    # If the entire transcription was a hallucination, return empty.
    if not stripped or stripped in (".", ",", "!"):
        return ""

    # If stripping removed content AND what remains is just a tiny word or
    # two with no real shape, the leading fragment was almost certainly
    # Whisper-on-silence babble too (e.g. "web outfits" left over after the
    # subscribe tail was removed). Discard the remainder rather than pasting
    # garbage into the user's clipboard.
    if len(stripped) < original_len and len(stripped.split()) <= 2:
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

    def __init__(self, api_key: str = "", model: str = "",
                 groq_api_key: str = ""):
        self.api_key = api_key
        # Default to the newer, cheaper, better gpt-4o-mini-transcribe ($0.003/min
        # vs whisper-1's $0.006/min). Users can override via the OPENAI_WHISPER_MODEL
        # env var — e.g. "gpt-4o-transcribe" for max quality at the old whisper-1
        # price, or "whisper-1" to force the legacy model.
        if not model:
            model = os.getenv("OPENAI_WHISPER_MODEL", "gpt-4o-mini-transcribe")
        self.model   = model
        self.groq_api_key = groq_api_key
        self.client  = OpenAI(api_key=api_key) if api_key else None
        self._groq_client = None
        # Monotonic-clock deadline: until this timestamp, skip Groq entirely
        # and call OpenAI directly. Set when Groq returns a 403/401/auth error
        # (typically a VPN exit-IP block — Groq hard-rejects many VPN nodes
        # before authentication). Mirror of the same flag on OpenAIStyler.
        # Without this every recording wastes ~150-300 ms on a dead Groq
        # round-trip before fallback. Reset on process restart.
        self._groq_skip_until = 0.0

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

    def _dispatch_one(self, audio_bytes: bytes) -> str:
        """Transcribe ONE already-padded/chunked WAV blob via the configured
        backend, with the Groq->OpenAI fallback + 403/auth circuit-breaker.

        Extracted from ``transcribe_sync`` so the long-recording chunk loop can
        call it per chunk without duplicating the fallback logic.
        """
        if self._backend == "groq":
            import time as _time
            # Circuit-breaker: if a recent Groq call returned 403/auth, skip
            # straight to OpenAI for the rest of the cool-down window.
            # Saves the 150-300 ms wasted round-trip per recording when a
            # VPN exit IP is blocking Groq at the network layer.
            if _time.monotonic() < self._groq_skip_until and self.client:
                return self._transcribe_api(audio_bytes)
            try:
                return self._transcribe_groq(audio_bytes)
            except Exception as e:
                err = str(e)
                # Auth/network block — cool down for an hour. Anything
                # else (transient 5xx, rate-limit, connection blip) gets
                # a shorter 30 s skip so we recover quickly.
                if any(s in err for s in ("403", "401")) or any(
                    s in err.lower() for s in ("access denied", "unauthorized", "permission")
                ):
                    self._groq_skip_until = _time.monotonic() + 3600.0
                    print(f"⚠️  Groq auth/network blocked — skipping Groq transcription for 1h ({err[:80]})")
                else:
                    self._groq_skip_until = _time.monotonic() + 30.0
                    print(f"⚠️  Groq transcription failed ({err[:80]}), falling back to OpenAI")
                if self.client:
                    return self._transcribe_api(audio_bytes)
                raise
        elif self._backend == "mlx":
            return self._transcribe_mlx(audio_bytes)
        elif self._backend == "faster":
            return self._transcribe_faster(audio_bytes)
        else:
            return self._transcribe_api(audio_bytes)

    def transcribe_sync(self, audio_bytes: bytes):
        audio_bytes = _pad_audio_with_silence(audio_bytes)

        # Long-recording fix: split clips over ~30 s into <= 25-30 s chunks on
        # silence so Whisper's decoder doesn't terminate early and drop the
        # tail (the "speaks for a minute, only the first 20 s survives + a
        # 'Thank you for watching!' hallucination" bug). Short clips -- the
        # common case -- come back as a single chunk and take the unchanged
        # single-shot path.
        chunks = _split_audio_on_silence(audio_bytes)
        if len(chunks) > 1:
            print(f"[whisper] long clip split into {len(chunks)} chunks for transcription")
            parts = []
            for idx, ch in enumerate(chunks):
                part = self._dispatch_one(ch)
                # Strip a hallucinated outro PER CHUNK so a fake ending Whisper
                # tacks onto one chunk doesn't land in the middle of the joined
                # transcript. (The trailing strip below still covers chunk N.)
                part = _strip_hallucinations(part).strip()
                if part:
                    parts.append(part)
            raw = " ".join(parts)
        else:
            raw = self._dispatch_one(chunks[0])

        cleaned = _strip_hallucinations(raw)
        if cleaned != raw:
            # Metadata only — don't print the transcript text (PII; app.log
            # ships in the Download Logs bundle).
            print(f"[whisper] Stripped hallucination ({len(raw)}→{len(cleaned)} chars)")

        # Whisper sometimes echoes the vocab prompt verbatim on silence —
        # discard rather than pasting the user's vocabulary list as output.
        try:
            vocab = load_vocab()
        except Exception:
            vocab = []
        if _is_vocab_echo(cleaned, vocab):
            print(f"[whisper] Discarded vocab-echo hallucination: '{cleaned}'")
            return ""

        # Discard known boilerplate Whisper produces on silence / near-silence
        # ("Thanks for watching!", "Please subscribe", etc.).
        if _is_whisper_hallucination(cleaned):
            print(f"[whisper] Discarded boilerplate hallucination: '{cleaned}'")
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
            print(f"⚡ mlx-whisper ({(time.time()-t0)*1000:.0f}ms, {len(text)} chars)")
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
            print(f"⚡ faster-whisper ({(time.time()-t0)*1000:.0f}ms, {len(text)} chars)")
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
            print(f"⚡ Groq Whisper ({duration*1000:.0f}ms, {len(text)} chars)")
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
            print(f"✅ API Whisper ({duration*1000:.0f}ms, {len(text)} chars)")
            return text
        except Exception as e:
            print(f"❌ Whisper API error: {e}")
            raise
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
