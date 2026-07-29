#!/usr/bin/env python3
"""Tests for long-audio handling in transcribe_whisper._split_audio_on_silence.

History of this code:
  - Originally split clips > ~30 s, on the (wrong) belief that Whisper truncates
    long audio. Live tests disproved it: Groq Whisper transcribes 51 s / 64 s /
    111 s real recordings FULLY in one call, and the 30 s chunking ACTIVELY
    caused truncation (later chunks came back near-empty on the free tier).
  - v3.14.79 raised the duration threshold to 150 s.
  - v3.14.80 went further: the trigger is now FILE SIZE, not duration. Chunking
    is reserved purely as a safety net against oversized uploads.
  - v3.14.85 lowered the default gate 24 MB -> 18 MB after live evidence that
    near-max single uploads are network-unreliable (the same 23.2 MB WAV
    failed on Groq, passed on OpenAI, then failed on OpenAI 20 minutes later).
    >18 MB (~9.4 min at 16 kHz mono 16-bit) splits into ~120 s chunks whose
    ~4 MB uploads are reliably small. Everything under 18 MB — every normal
    dictation — still goes single-shot.

These tests prove:
  1. normal dictations go single-shot (the only path that matters in practice),
  2. the gate is byte-size based (a file just over the threshold splits, just
     under does not),
  3. when a genuinely oversized file IS split, chunks stay bounded and preserve
     (near) all the audio, cutting at silence and never crashing,
  4. malformed input degrades to single-shot.
"""

import io
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcribe_whisper import _split_audio_on_silence  # noqa: E402

SR = 16000
BYTES_PER_SEC = SR * 2  # mono, 16-bit


def _wav(samples_int16: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples_int16.astype(np.int16).tobytes())
    return out.getvalue()


def _tone(seconds: float, amp: int = 6000, freq: float = 180.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.int16)


def _wav_duration(b: bytes) -> float:
    with wave.open(io.BytesIO(b), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ── The common case: normal dictations never split ───────────────────────────

def test_normal_dictation_not_chunked():
    """A 64 s clip — the length that was being WRONGLY chunked into empty
    pieces — must go single-shot under the default (byte-size) threshold."""
    clip = _wav(_tone(64.0))
    assert _split_audio_on_silence(clip) == [clip]


def test_short_clip_untouched():
    clip = _wav(_tone(10.0))
    assert _split_audio_on_silence(clip) == [clip]


def test_multi_minute_clip_single_shot_by_default():
    """Even a 4-minute clip (well over the old 150 s duration threshold) goes
    single-shot now, because it's far under the 18 MB file-size cap. This is the
    'chunking removed for all real recordings' guarantee."""
    clip = _wav(_tone(240.0))  # ~7.7 MB, << 18 MB
    assert _split_audio_on_silence(clip) == [clip]


def test_default_gate_splits_near_max_recording():
    """A ~10.4-min clip (>18 MB) must split under the DEFAULT gate — near-max
    single uploads are network-unreliable (proven live: the same 23 MB WAV
    flip-flopped between success and 'Connection error.' on both providers)."""
    audio = np.concatenate([_tone(310.0), _silence(1.0), _tone(313.0)])
    clip = _wav(audio)  # ~19.9 MB > 18 MB
    assert len(clip) > 18 * 1024 * 1024
    chunks = _split_audio_on_silence(clip)
    assert len(chunks) >= 2, "near-max recording must chunk by default"
    for c in chunks:
        assert len(c) <= 18 * 1024 * 1024


def test_upload_timeout_scales_with_size():
    from transcribe_whisper import _upload_timeout_s
    assert _upload_timeout_s(1 * 1024 * 1024) == 60.0          # small: base
    assert _upload_timeout_s(4 * 1024 * 1024) == 60.0          # knee point
    t18 = _upload_timeout_s(18 * 1024 * 1024)
    assert 140.0 <= t18 <= 148.0, t18                          # 18 MB ≈ 144s
    assert _upload_timeout_s(100 * 1024 * 1024) == 240.0       # capped


# ── The gate is byte-size based ──────────────────────────────────────────────

def test_just_under_byte_threshold_single_shot():
    """A file whose size is just UNDER the passed threshold is not split."""
    clip = _wav(_tone(60.0))  # ~1.92 MB
    thresh = len(clip) + 1000
    assert _split_audio_on_silence(clip, max_single_shot_bytes=thresh) == [clip]


def test_just_over_byte_threshold_splits():
    """A file whose size is just OVER the threshold IS split."""
    audio = np.concatenate([_tone(120.0), _silence(1.0), _tone(120.0)])
    clip = _wav(audio)  # ~7.7 MB
    thresh = len(clip) // 2  # force it over the limit
    chunks = _split_audio_on_silence(clip, max_single_shot_bytes=thresh)
    assert len(chunks) >= 2, f"expected split, got {len(chunks)} chunk(s)"
    for c in chunks:
        assert _wav_duration(c) <= 151.5, f"chunk too long: {_wav_duration(c):.1f}s"


# ── When an oversized file IS split, it behaves ──────────────────────────────

def test_oversized_split_preserves_audio_length():
    audio = np.concatenate(
        [_tone(110.0), _silence(0.6), _tone(110.0), _silence(0.6), _tone(40.0)]
    )
    clip = _wav(audio)
    orig = _wav_duration(clip)
    chunks = _split_audio_on_silence(clip, max_single_shot_bytes=len(clip) // 2)
    total = sum(_wav_duration(c) for c in chunks)
    assert abs(total - orig) <= 2.0, f"lost {orig - total:.1f}s of audio"
    assert len(chunks) >= 2


def test_continuous_loud_oversized_force_cut_no_crash():
    """A continuous tone with NO silence still gets bounded chunks via the hard
    force-cut, and never raises."""
    clip = _wav(_tone(320.0))
    chunks = _split_audio_on_silence(clip, max_single_shot_bytes=len(clip) // 4)
    assert len(chunks) >= 2
    for c in chunks:
        assert _wav_duration(c) <= 151.5


def test_malformed_input_degrades_to_single_shot():
    junk = b"not a wav file at all"
    assert _split_audio_on_silence(junk) == [junk]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
