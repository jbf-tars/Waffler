"""A Mac imports an .m4a (an iPhone voice memo) with macOS's own converter,
so it needs no ffmpeg (src/meetings/audio_decode.py; tests/test_audio_decode.py
has the rest)."""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not Path("/usr/bin/afconvert").exists(),
                                reason="needs macOS's afconvert")


def _m4a(tmp_path) -> Path:
    wav = tmp_path / "tone.wav"
    t = np.arange(16000 * 2) / 16000.0
    pcm = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype("<i2")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    out = tmp_path / "memo.m4a"
    subprocess.run(["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", str(wav), str(out)], check=True,
                   capture_output=True)
    return out


def test_a_voice_memo_is_read_with_no_ffmpeg_anywhere(tmp_path, monkeypatch):
    from src.meetings import audio_decode, service
    monkeypatch.setattr(audio_decode.shutil, "which", lambda name: None)  # no ffmpeg on PATH
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)              # and none from pip
    audio = service.load_any_audio(_m4a(tmp_path))
    assert 1.8 * 16000 < len(audio) < 2.3 * 16000                       # two seconds, 16 kHz mono
    assert float(np.abs(audio).max()) > 0.05                            # and it is the tone, not silence


def test_a_file_that_is_not_audio_still_says_so_plainly(tmp_path, monkeypatch):
    from src.meetings import audio_decode, service
    monkeypatch.setattr(audio_decode.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    bad = tmp_path / "notes.m4a"
    bad.write_bytes(b"this is not audio at all")
    with pytest.raises(RuntimeError, match="isn't audio Waffler can read, or it is damaged"):
        service.load_any_audio(bad)
