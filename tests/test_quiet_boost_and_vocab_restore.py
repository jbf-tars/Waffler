"""3.15.1: lift quiet recordings before Whisper; keep vocabulary fixes people relied on.

The maker's recordings had speech around -40 to -55 dBFS (clear dictation
sits near -20), and Whisper dropped quiet stretches. And 3.15's stricter
vocabulary matcher stopped three of his most-used fixes (Whisper's "waffle"
for Waffler, "mortar" for Morta, "bim" for XBim).
"""
import io
import json
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import transcribe_whisper as tw  # noqa: E402

SR = 16000


def wav_bytes(a):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
    return b.getvalue()


def samples(b):
    with wave.open(io.BytesIO(b)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def speechlike(level_db, seconds=4.0, pause=1.0):
    t = np.arange(int(SR * seconds)) / SR
    voice = np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    voice *= 10 ** (level_db / 20) / np.sqrt(np.mean(voice ** 2))
    gap = np.random.default_rng(0).normal(0, 10 ** (-85 / 20), int(SR * pause))
    return np.concatenate([gap, voice, gap, voice, gap]).astype(np.float32)


def speech_level_db(a):
    f = int(SR * 0.03); n = a.size // f
    rms = np.sqrt((a[: n * f].reshape(n, f) ** 2).mean(1))
    return 20 * np.log10(np.median(rms[rms > np.percentile(rms, 10) * 4]))


# -- the volume boost -----------------------------------------------------------

def test_quiet_speech_is_lifted_towards_normal_level():
    quiet = speechlike(-48)
    out = samples(tw.boost_quiet_speech(wav_bytes(quiet)))
    assert speech_level_db(out) > -28, speech_level_db(out)
    assert len(out) == len(quiet)


def test_boost_never_clips():
    quiet = speechlike(-48)
    quiet[1000:1010] = 0.5          # a knock far above the speech
    out = samples(tw.boost_quiet_speech(wav_bytes(quiet)))
    assert np.abs(out).max() < 1.0


def test_normal_and_loud_speech_are_left_alone():
    loud = wav_bytes(speechlike(-18))
    assert tw.boost_quiet_speech(loud) == loud


def test_silence_and_non_wav_pass_through():
    silence = wav_bytes(np.zeros(SR, dtype=np.float32))
    assert tw.boost_quiet_speech(silence) == silence
    assert tw.boost_quiet_speech(b"not a wav") == b"not a wav"


def test_boost_is_capped():
    whisper_quiet = speechlike(-80)
    out = samples(tw.boost_quiet_speech(wav_bytes(whisper_quiet)))
    assert speech_level_db(out) - speech_level_db(whisper_quiet) <= tw.SPEECH_MAX_GAIN_DB + 0.5


def test_transcriber_boosts_before_upload():
    src = open(tw.__file__, encoding="utf-8").read()
    i = src.index("audio_bytes = boost_quiet_speech(audio_bytes)")
    assert i < src.index("audio_bytes = _pad_audio_with_silence(audio_bytes)", i)


# -- keeping the fixes people relied on ------------------------------------------

@pytest.fixture
def vocab_home(tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "VOCAB_FILE", tmp_path / "vocab.json")
    monkeypatch.setattr(tw, "VOCAB_SOUNDS_FILE", tmp_path / "vocab_sounds.json")
    (tmp_path / "vocab.json").write_text(json.dumps(["Waffler", "Morta", "Ashkan", "XBim"]), encoding="utf-8")
    return tmp_path


def log_with(tmp_path, pairs):
    lines = [f"12:00:00  Vocabulary corrections applied: '{h}' → '{u}'" for h, u, n in pairs for _ in range(n)]
    p = tmp_path / "app.log"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_lost_frequent_fixes_come_back_as_sounds_like(vocab_home):
    log = log_with(vocab_home, [("waffle", "Waffler", 16), ("mortar", "Morta", 15), ("bim", "XBim", 9)])
    added = tw.restore_lost_corrections([log, vocab_home / "app.log.1"])
    assert added == {"Waffler": ["waffle"], "Morta": ["mortar"], "XBim": ["bim"]}
    fixed, _ = tw.apply_vocab_changes("I built waffle with the mortar team", tw.load_vocab(), tw.load_sounds_like())
    assert fixed == "I built Waffler with the Morta team"


def test_rare_fixes_and_ones_still_made_are_not_added(vocab_home):
    log = log_with(vocab_home, [("waffled", "Waffler", 1), ("ashkahn", "Ashkan", 5)])
    assert tw.restore_lost_corrections([log]) == {}
    assert tw.load_sounds_like() == {}


def test_entries_no_longer_listed_are_ignored(vocab_home):
    log = log_with(vocab_home, [("rowan", "Rohan", 9)])
    assert tw.restore_lost_corrections([log]) == {}


def test_missing_log_is_harmless(vocab_home):
    assert tw.restore_lost_corrections([vocab_home / "nope.log"]) == {}


def test_app_runs_it_once_at_start():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app.py"), encoding="utf-8").read()
    i = src.index("restore_lost_corrections(")
    assert "vocab_corrections_restored" in src[i - 600:i]
    assert i < src.index("_privacy.tidy_logs_at_start(")
