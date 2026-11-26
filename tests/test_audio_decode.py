"""Import audio's decoder for files soundfile cannot read
(src/meetings/audio_decode.py): macOS's own afconvert on a Mac, a decoder
fetched on first use on Windows. No FFmpeg in either build.

The Mac half runs the real afconvert on files made here (skipped elsewhere).
The Windows half runs on every platform: the platform is passed in and the
helper is a small Python stand-in that answers the way FFmpeg does."""
import base64
import re
import subprocess
import sys
import textwrap
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.meetings import audio_decode as ad   # noqa: E402
from src.meetings import service              # noqa: E402

AFCONVERT = Path("/usr/bin/afconvert")
mac_only = pytest.mark.skipif(sys.platform != "darwin" or not AFCONVERT.exists(), reason="needs macOS's afconvert")


# ── files made here ─────────────────────────────────────────────────────────

def _wav(path: Path, seconds=2.0, rate=16000, channels=1, amp=0.3, right=None) -> Path:
    t = np.arange(int(rate * seconds)) / rate
    left = amp * np.sin(2 * np.pi * 440 * t)
    cols = [left] if channels == 1 else [left, left if right is None else right]
    pcm = (np.stack(cols, axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


def _convert(src: Path, dst: Path, fmt: str, data: str) -> Path:
    subprocess.run([str(AFCONVERT), "-f", fmt, "-d", data, str(src), str(dst)], check=True, capture_output=True)
    return dst


def _box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _iso_file(path: Path, tracks) -> Path:
    """A minimal mp4: ftyp, then moov with one trak per (handler, sample entry)."""
    traks = b""
    for handler, entry in tracks:
        hdlr = _box(b"hdlr", b"\0" * 8 + handler + b"\0" * 12)
        stsd = _box(b"stsd", b"\0" * 4 + (1).to_bytes(4, "big") + _box(entry, b"\0" * 28 + (
            _box(b"sinf", _box(b"frma", b"mp4a")) if entry in (b"enca", b"drms") else b"")))
        traks += _box(b"trak", _box(b"mdia", hdlr + _box(b"minf", _box(b"stbl", stsd))))
    path.write_bytes(_box(b"ftyp", b"M4A \0\0\0\0M4A isom") + _box(b"moov", traks) + _box(b"mdat", b"\0" * 64))
    return path


# ── the Mac: afconvert ──────────────────────────────────────────────────────

@mac_only
@pytest.mark.parametrize("fmt,data,name", [
    ("m4af", "aac", "memo.m4a"),            # AAC-LC: an iPhone voice memo
    ("m4af", "aach", "he.m4a"),             # HE-AAC
    ("m4af", "aacp", "hev2.m4a"),           # HE-AACv2
    ("m4af", "alac", "lossless.m4a"),       # Apple Lossless
    ("caff", "aac", "memo.caf"),
    ("mp4f", "aac", "memo.mp4"),
    ("3gpp", "aac", "memo.3gp"),
    ("adts", "aac", "memo.aac"),
])
def test_the_mac_decodes_what_soundfile_cannot(tmp_path, fmt, data, name):
    src = _wav(tmp_path / "tone.wav", rate=44100, channels=2)
    f = _convert(src, tmp_path / name, fmt, data)
    samples, rate = ad.decode(f, platform="darwin")
    assert rate == 16000 and samples.dtype == np.float32 and samples.ndim == 1
    assert 1.9 <= len(samples) / rate <= 2.2
    assert 0.15 < float(np.abs(samples).max()) <= 1.0


@mac_only
def test_stereo_48k_is_averaged_not_one_channel_and_not_summed(tmp_path):
    src = _wav(tmp_path / "lr.wav", seconds=3, rate=48000, channels=2, amp=0.5, right=np.zeros(144000))
    samples, rate = ad.decode(_convert(src, tmp_path / "lr.m4a", "m4af", "aac"), platform="darwin")
    assert abs(len(samples) / rate - 3.0) < 0.05
    assert 0.2 < float(np.abs(samples).max()) < 0.32          # (0.5 + 0) / 2


@mac_only
def test_a_name_with_spaces_and_accents(tmp_path):
    folder = tmp_path / "Mes mémos — été"
    folder.mkdir()
    f = _convert(_wav(tmp_path / "t.wav"), folder / "Mémo vocal ü 2026.m4a", "m4af", "aac")
    samples, _ = ad.decode(f, platform="darwin")
    assert len(samples) > 16000


@mac_only
def test_the_temporary_wav_is_removed(tmp_path, monkeypatch):
    made = []
    real = ad.tempfile.TemporaryDirectory

    def spy(*a, **kw):
        d = real(*a, **kw)
        made.append(Path(d.name))
        return d
    monkeypatch.setattr(ad.tempfile, "TemporaryDirectory", spy)
    ad.decode(_convert(_wav(tmp_path / "t.wav"), tmp_path / "m.m4a", "m4af", "aac"), platform="darwin")
    assert made and not any(p.exists() for p in made)


@mac_only
def test_the_mac_is_asked_with_no_shell_and_a_timeout_that_grows_with_size(tmp_path, monkeypatch):
    seen = {}
    real = subprocess.run

    def spy(argv, **kw):
        seen.update(argv=argv, **kw)
        return real(argv, **kw)
    monkeypatch.setattr(ad.subprocess, "run", spy)
    f = _convert(_wav(tmp_path / "t.wav"), tmp_path / "m.m4a", "m4af", "aac")
    ad.decode(f, platform="darwin")
    assert isinstance(seen["argv"], list) and not seen.get("shell")
    assert seen["argv"][:5] == ["/usr/bin/afconvert", "-f", "RF64", "-d", "LEI16@16000"]
    assert seen["timeout"] == ad._timeout(f)
    big = tmp_path / "big.bin"
    with open(big, "wb") as fh:
        fh.truncate(100 * 1000 * 1000)
    assert ad._timeout(big) == pytest.approx(120 + 200)


@mac_only
def test_a_forty_minute_memo_is_read_whole(tmp_path):
    # 40 minutes at 8 kHz mono, quiet with a tone every few seconds.
    rate, minutes = 8000, 40
    n = rate * 60 * minutes
    t = np.arange(n, dtype=np.float32) / rate
    x = (0.2 * np.sin(2 * np.pi * 300 * t) * (np.sin(2 * np.pi * t / 7) > 0)).astype(np.float32)
    wav = tmp_path / "long.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    del x, t
    f = _convert(wav, tmp_path / "long.m4a", "m4af", "aac")
    wav.unlink()
    audio = service.load_any_audio(f)
    assert abs(len(audio) / 16000 - minutes * 60) < 1.0 and audio.dtype == np.float32


@mac_only
def test_the_mac_failures_are_plain_sentences(tmp_path):
    garbage = tmp_path / "notes.m4a"
    garbage.write_bytes(b"this is not audio at all" * 20)
    with pytest.raises(ad.NotAudio, match="isn't audio"):
        ad.decode(garbage, platform="darwin")
    memo = _convert(_wav(tmp_path / "t.wav"), tmp_path / "m.m4a", "m4af", "aac")
    half = tmp_path / "half.m4a"
    half.write_bytes(memo.read_bytes()[:memo.stat().st_size // 2])
    with pytest.raises(ad.NotAudio, match="damaged"):
        ad.decode(half, platform="darwin")
    webm = tmp_path / "talk.webm"
    webm.write_bytes(bytes.fromhex("1a45dfa3") + b"\0" * 200)
    with pytest.raises(ad.Unsupported) as e:
        ad.decode(webm, platform="darwin")
    assert str(e.value) == ("Waffler on Mac reads m4a, mp3, wav, aiff, flac, ogg and mp4. "
                            "Convert this file first.")
    wma = tmp_path / "old.wma"
    wma.write_bytes(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c") + b"\0" * 200)
    with pytest.raises(ad.Unsupported):
        ad.decode(wma, platform="darwin")


@mac_only
def test_drm_and_video_only_are_named_on_the_mac(tmp_path):
    memo = _convert(_wav(tmp_path / "t.wav"), tmp_path / "m.m4a", "m4af", "aac").read_bytes()
    i = memo.find(b"mp4a")
    for entry in (b"drms", b"enca"):                          # FairPlay; Common Encryption
        f = tmp_path / f"{entry.decode()}.m4a"
        f.write_bytes(memo[:i] + entry + memo[i + 4:])
        with pytest.raises(ad.Protected, match="copy-protected"):
            ad.decode(f, platform="darwin")
    with pytest.raises(ad.NoSound, match="no sound"):
        ad.decode(_iso_file(tmp_path / "clip.mp4", [(b"vide", b"avc1")]), platform="darwin")


@mac_only
def test_load_any_audio_reads_an_m4a_through_the_decoder(tmp_path, monkeypatch):
    calls = []
    real = ad.decode
    monkeypatch.setattr(ad, "decode", lambda p, **kw: calls.append(p) or real(p, **kw))
    audio = service.load_any_audio(_convert(_wav(tmp_path / "t.wav"), tmp_path / "m.m4a", "m4af", "aac"))
    assert len(calls) == 1 and 1.8 * 16000 < len(audio) < 2.3 * 16000


@mac_only
def test_the_mac_is_ready_with_the_system_decoder():
    assert ad.available("darwin") == {"ready": True, "needs_download": False, "how": "system"}


# ── unchanged: what soundfile reads never reaches the decoder ───────────────

@pytest.mark.parametrize("fmt", ["WAV", "FLAC"])
def test_wav_and_flac_never_reach_the_decoder(tmp_path, monkeypatch, fmt):
    import soundfile as sf
    f = tmp_path / f"clip.{fmt.lower()}"
    t = np.arange(44100 * 2) / 44100
    sf.write(str(f), np.stack([0.3 * np.sin(2 * np.pi * 440 * t)] * 2, axis=1), 44100, format=fmt)
    monkeypatch.setattr(ad, "decode", lambda *a, **k: pytest.fail("the decoder was asked"))
    audio = service.load_any_audio(f)
    assert audio.dtype == np.float32 and audio.ndim == 1 and abs(len(audio) - 32000) < 10


# ── Windows: the helper fetched on first use, run as a stand-in here ────────

STAND_IN = textwrap.dedent('''\
    """Answers like FFmpeg: decodes to a 16 kHz mono WAV, or says why not."""
    import json, sys, time, wave
    args = sys.argv[1:]
    src, dst = args[args.index("-i") + 1], args[-1]
    with open(dst + ".argv.json", "w", encoding="utf-8") as f:
        json.dump(args, f)
    head = open(src, "rb").read(16)
    if head.startswith(b"NOTAUDIO"):
        sys.stderr.write(src + ": Invalid data found when processing input\\n"); sys.exit(1)
    if head.startswith(b"VIDEOONLY"):
        sys.stderr.write("Stream map '0:a:0' matches no streams.\\n"); sys.exit(1)
    if head.startswith(b"CRASH"):
        sys.stderr.write("something inside broke\\n"); sys.exit(3)
    if b"moov" in open(src, "rb").read(4096):      # an mp4 it cannot decode (protected, video only)
        sys.stderr.write("Error while opening decoder for input stream #0:0\\n"); sys.exit(1)
    if head.startswith(b"SLOW"):
        time.sleep(30)
    import math, struct
    with wave.open(dst, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / 16000)))
                               for i in range(16000)))
''')


@pytest.fixture()
def windows(tmp_path, monkeypatch):
    """A Windows data folder in tmp_path, and the stand-in as the helper's program."""
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path / "data"))
    script = tmp_path / "stand_in.py"
    script.write_text(STAND_IN, encoding="utf-8")
    monkeypatch.setattr(ad, "_helper_argv", lambda helper: [sys.executable, str(script)])
    return tmp_path


def _fetch_helper(windows) -> Path:
    helper = ad.helper_install_path()
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"MZ")                   # what the Windows session's download will put here
    return helper


def test_windows_needs_the_download_until_the_helper_is_there(windows):
    assert ad.helper_install_path() == windows / "data" / "audio-decoder" / "ffmpeg.exe"
    assert ad.helper_path("win32") is None
    assert ad.available("win32") == {"ready": False, "needs_download": True, "how": "download"}
    memo = windows / "memo.m4a"
    memo.write_bytes(b"any m4a")
    with pytest.raises(ad.NeedsDownload) as e:
        ad.decode(memo, platform="win32")
    assert e.value.reason == "needs_download" and isinstance(e.value, RuntimeError)
    assert ad.bridge_answer(e.value) == {"ok": False, "reason": "needs_download", "error": str(e.value)}
    assert str(e.value).endswith(".") and "\n" not in str(e.value)
    _fetch_helper(windows)
    assert ad.helper_path("win32") == ad.helper_install_path()
    assert ad.available("win32") == {"ready": True, "needs_download": False, "how": "download"}


def test_windows_runs_the_helper_with_the_lgpl_only_command_line(windows):
    _fetch_helper(windows)
    memo = windows / "Mémo vocal.m4a"
    memo.write_bytes(b"an m4a")
    samples, rate = ad.decode(memo, platform="win32")
    assert rate == 16000 and samples.dtype == np.float32 and len(samples) == 16000
    assert 0.2 < float(np.abs(samples).max()) < 0.3


def test_the_helper_command_line_is_a_plain_decode():
    args = ad.helper_args("in put.m4a", "out.wav")
    assert args[args.index("-i") + 1] == "in put.m4a" and args[-1] == "out.wav"
    joined = " ".join(args)
    for want in ("-map 0:a:0", "-vn", "-ac 1", "-ar 16000", "-c:a pcm_s16le", "-f wav", "-nostdin"):
        assert want in joined
    # Nothing that needs a GPL or non-free part of FFmpeg: no filters, no external codecs.
    assert not re.search(r"-(af|vf|filter|filter_complex|lavfi)\b|lib(x26[45]|fdk|mp3lame|rubberband)", joined)


def test_windows_starts_the_helper_hidden_and_with_a_timeout(windows, monkeypatch):
    _fetch_helper(windows)
    seen = {}
    real = subprocess.run

    def spy(argv, **kw):
        seen.update(kw)
        return real(argv, **kw)
    monkeypatch.setattr(ad.subprocess, "run", spy)
    memo = windows / "m.m4a"
    memo.write_bytes(b"x")
    ad.decode(memo, platform="win32")
    assert "creationflags" in seen and seen["timeout"] >= 120 and not seen.get("shell")
    if sys.platform == "win32":
        assert seen["creationflags"] & 0x08000000              # CREATE_NO_WINDOW


@pytest.mark.parametrize("head,error,word", [
    (b"NOTAUDIO", ad.NotAudio, "isn't audio"),
    (b"VIDEOONLY", ad.NoSound, "no sound"),
    (b"CRASH", ad.DecoderFailed, "converter"),
])
def test_windows_failures_are_plain_sentences(windows, head, error, word):
    _fetch_helper(windows)
    f = windows / "file.m4a"
    f.write_bytes(head + b"\0" * 32)
    with pytest.raises(error, match=word) as e:
        ad.decode(f, platform="win32")
    assert e.value.detail                                       # the helper's last words, for the log


def test_windows_names_drm_and_video_only_from_the_file_itself(windows):
    _fetch_helper(windows)
    for tracks, error in (([(b"soun", b"enca")], ad.Protected),
                          ([(b"vide", b"avc1")], ad.NoSound)):
        with pytest.raises(error):
            ad.decode(_iso_file(windows / "f.mp4", tracks), platform="win32")
    with pytest.raises(ad.Protected):                        # an iTunes .m4p, whatever is inside
        ad.decode(_iso_file(windows / "song.m4p", [(b"soun", b"mp4a")]), platform="win32")


def test_windows_gives_up_on_a_helper_that_hangs(windows, monkeypatch):
    _fetch_helper(windows)
    monkeypatch.setattr(ad, "_timeout", lambda path: 1.0)
    f = windows / "slow.m4a"
    f.write_bytes(b"SLOW")
    with pytest.raises(ad.DecoderFailed, match="too long"):
        ad.decode(f, platform="win32")


def test_elsewhere_an_ffmpeg_on_the_path_or_nothing(windows, monkeypatch):
    monkeypatch.setattr(ad, "_path_ffmpeg", lambda: None)
    assert ad.available("linux") == {"ready": False, "needs_download": False, "how": "none"}
    f = windows / "m.m4a"
    f.write_bytes(b"x")
    with pytest.raises(ad.DecoderFailed):
        ad.decode(f, platform="linux")
    monkeypatch.setattr(ad, "_path_ffmpeg", lambda: "/usr/bin/ffmpeg")
    assert ad.available("linux")["ready"] is True
    samples, _ = ad.decode(f, platform="linux")                 # the stand-in, as ffmpeg
    assert len(samples) == 16000


def test_the_mp4_index_is_read_for_kinds_and_protection(tmp_path):
    tracks = ad.mp4_tracks(_iso_file(tmp_path / "a.mp4", [(b"vide", b"avc1"), (b"soun", b"mp4a")]))
    assert [(t["handler"], t["entries"], t["protected"]) for t in tracks] == [
        ("vide", ["avc1"], False), ("soun", ["mp4a"], False)]
    assert ad.mp4_tracks(_iso_file(tmp_path / "p.m4a", [(b"soun", b"drms")]))[0]["protected"] is True
    (tmp_path / "x.bin").write_bytes(b"not an mp4 at all")
    assert ad.mp4_tracks(tmp_path / "x.bin") is None


# ── the page: what the bridge answers when the decoder is not here ──────────

@pytest.fixture()
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(tmp_path / "meetings"))
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from src import meetings_ctl
    monkeypatch.setattr(meetings_ctl, "_share_waffler_keys", lambda: None)
    c = meetings_ctl.MeetingsController()
    yield c
    c.shutdown()


def _import(ctl, name, data):
    from src import meetings_bridge
    return meetings_bridge.handle(ctl, "POST", "/import",
                                  {"file_name": name, "file_base64": base64.b64encode(data).decode()})


def test_the_bridge_answers_needs_download_for_the_windows_session(ctl, monkeypatch):
    monkeypatch.setattr(ad, "_platform", lambda platform=None: platform or "win32")
    r = _import(ctl, "Voice memo.m4a", b"an m4a the decoder has not come down for")
    assert r["status"] == 400
    body = r["body"]
    assert body["ok"] is False and body["reason"] == "needs_download"
    assert body["error"] == body["detail"] == ad.NEEDS_DOWNLOAD     # the frame shows "detail" as it is


@mac_only
def test_the_bridge_gives_the_mac_s_sentence_for_a_file_it_cannot_read(ctl):
    r = _import(ctl, "talk.webm", bytes.fromhex("1a45dfa3") + b"\0" * 64)
    assert r["status"] == 400 and r["body"]["reason"] == "unsupported"
    assert r["body"]["detail"].startswith("Waffler on Mac reads m4a")


def test_any_other_import_failure_still_reads_as_before(ctl, monkeypatch):
    monkeypatch.setattr(service, "load_any_audio", lambda p: (_ for _ in ()).throw(ValueError("x")))
    r = _import(ctl, "a.wav", b"RIFF")
    assert r["status"] == 400 and r["body"] == {"detail": "Couldn't read that audio file (ValueError)."}


# ── no FFmpeg package anywhere in Waffler's own code ────────────────────────

def test_nothing_in_waffler_imports_imageio_ffmpeg():
    found = []
    for path in [ROOT / "app.py", *sorted((ROOT / "src").rglob("*.py"))]:
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(import\s+imageio_ffmpeg|from\s+imageio_ffmpeg\b)|import_module\(\s*['\"]imageio_ffmpeg",
                     text, re.M) or "imageio_ffmpeg" in text:
            found.append(str(path.relative_to(ROOT)))
    assert found == []
