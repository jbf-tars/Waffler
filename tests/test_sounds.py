"""Optional sound cues (src/sounds.py): a soft tone when recording starts and
stops, and a tick when hands-free locks.

No audio files ship: each cue is a few sine tones with a fade in and out,
built in code, the same bytes every time. Playing one must never hurt a
dictation: any failure is swallowed and logged without text, and a cue is
only played once the recorder has been told to ignore the moments it sounds
in (tests/test_cue_never_recorded.py proves that part).
"""
import io
import math
import struct
import sys
import threading
import types
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import sounds as S  # noqa: E402


def decode(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
        return (w.getnchannels(), w.getsampwidth(), w.getframerate(), n,
                list(struct.unpack(f"<{n}h", raw)))


def goertzel_power(samples, rate, freq):
    """Energy of ``samples`` at ``freq``, per sample squared."""
    w = 2 * math.pi * freq / rate
    c = 2 * math.cos(w)
    s1 = s2 = 0.0
    for x in samples:
        s0 = x + c * s1 - s2
        s2, s1 = s1, s0
    p = s1 * s1 + s2 * s2 - c * s1 * s2
    return p / (len(samples) ** 2)


# ── the tones ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", S.CUES)
def test_a_cue_is_a_short_mono_16_bit_wav(name):
    ch, width, rate, frames, samples = decode(S.cue_wav(name))
    assert (ch, width, rate) == (1, 2, S.SAMPLE_RATE)
    seconds = frames / rate
    assert 0.10 <= seconds <= 0.20, f"{name} is {seconds * 1000:.0f} ms"
    assert abs(seconds - S.cue_seconds(name)) < 1 / rate


@pytest.mark.parametrize("name", S.CUES)
def test_a_cue_is_soft_and_fades_in_and_out(name):
    _, _, rate, frames, samples = decode(S.cue_wav(name))
    peak = max(abs(x) for x in samples)
    assert 0.10 * 32767 <= peak <= 0.30 * 32767, "soft, but audible"
    assert abs(samples[0]) <= 0.005 * 32767, "starts from silence, no click"
    assert abs(samples[-1]) <= 0.005 * 32767, "ends in silence, no click"
    # The first 3 ms stay under a quarter of the peak: that is the fade-in.
    head = samples[: int(0.003 * rate)]
    assert max(abs(x) for x in head) < 0.25 * peak
    # And the last 8 ms are well on the way down.
    tail = samples[-int(0.008 * rate):]
    assert max(abs(x) for x in tail) < 0.25 * peak


@pytest.mark.parametrize("name", S.CUES)
def test_a_cue_has_the_same_bytes_every_time_and_is_cached(name):
    a = S.cue_wav(name)
    assert S.cue_wav(name) is a, "built once"
    S.cue_wav.cache_clear()
    assert S.cue_wav(name) == a, "and the same bytes if built again"


def test_the_three_cues_sound_different():
    wavs = {n: S.cue_wav(n) for n in S.CUES}
    assert len(set(wavs.values())) == 3
    assert set(S.CUES) == {S.START, S.STOP, S.LOCK} == {"start", "stop", "lock"}


@pytest.mark.parametrize("name", S.CUES)
def test_the_energy_is_at_the_cues_own_pitch(name):
    _, _, rate, frames, samples = decode(S.cue_wav(name))
    freqs = [f for f, _ in S.CUE_NOTES[name] if f > 0]
    at_pitch = max(goertzel_power(samples, rate, f) for f in freqs)
    elsewhere = goertzel_power(samples, rate, 3333.0)
    assert at_pitch > 200 * elsewhere


def test_samples_at_another_rate_are_the_same_cue():
    """The microphone runs at 16 kHz; the tests (and the recorder's mask) use
    the cue as it would be heard there."""
    s16 = S.cue_samples(S.START, 16000)
    assert abs(len(s16) / 16000 - S.cue_seconds(S.START)) < 1 / 16000
    assert max(abs(x) for x in s16) > 2000
    f = [f for f, _ in S.CUE_NOTES[S.START] if f > 0][0]
    assert goertzel_power(s16, 16000, f) > 50 * goertzel_power(s16, 16000, 3333.0)


def test_nothing_ships_as_an_audio_file():
    assets = [p for p in (ROOT / "src").rglob("*") if p.suffix.lower() in (".wav", ".aiff", ".mp3", ".caf")]
    assert assets == []
    assert not [p for p in (ROOT / "branding").rglob("*") if p.suffix.lower() in (".wav", ".aiff", ".mp3", ".caf")]


# ── how long the microphone has to ignore ────────────────────────────────

def test_the_microphone_is_told_to_ignore_the_cue_its_latency_and_its_tail():
    for name in S.CUES:
        t = S.mask_seconds(name, latency_s=0.06)
        assert t == pytest.approx(0.06 + S.cue_seconds(name) + S.CUE_TAIL_S)
        assert t < 0.40, "and not a long stretch of what the person says"


# ── the setting ──────────────────────────────────────────────────────────

def test_sounds_are_off_unless_the_setting_is_true():
    assert S.sounds_enabled({}) is False
    assert S.sounds_enabled(None) is False
    assert S.sounds_enabled({"sounds": True}) is True
    for stray in ("true", 1, "on", None, [], {"a": 1}, False):
        assert S.sounds_enabled({"sounds": stray}) is False, stray


# ── playing ──────────────────────────────────────────────────────────────

class FakeBackend:
    def __init__(self, latency_s=0.06, fail=False):
        self.latency_s = latency_s
        self.fail = fail
        self.calls = []

    def play(self, name, wav):
        self.calls.append((name, wav))
        if self.fail:
            raise RuntimeError("no output device: SECRET WORDS")


def player(settings=None, backend=None, lines=None, **kw):
    backend = backend if backend is not None else FakeBackend()
    log = (lines.append if lines is not None else None)
    settings = {"sounds": True} if settings is None else settings
    return S.CuePlayer(backend=backend, settings=lambda: settings, log=log, **kw), backend


def test_a_cue_plays_its_own_wav():
    p, b = player()
    assert p.play(S.STOP) is True
    assert b.calls == [(S.STOP, S.cue_wav(S.STOP))]


def test_nothing_plays_with_sounds_off():
    p, b = player(settings={})
    deafened = []
    assert p.play(S.START, deafen=deafened.append) is False
    assert b.calls == [] and deafened == []


def test_the_recorder_is_told_to_ignore_before_the_sound_starts():
    order = []
    b = FakeBackend()
    b.play = lambda name, wav: order.append("play")
    p = S.CuePlayer(backend=b, settings=lambda: {"sounds": True})
    p.play(S.START, deafen=lambda seconds: order.append(("deafen", seconds)))
    assert order[0][0] == "deafen" and order[1] == "play"
    assert order[0][1] == pytest.approx(S.mask_seconds(S.START, b.latency_s))


def test_if_the_recorder_cannot_be_told_the_cue_is_not_played():
    """A cue the microphone could hear is worse than no cue."""
    def broken(seconds):
        raise RuntimeError("recorder gone")
    p, b = player()
    assert p.play(S.START, deafen=broken) is False
    assert b.calls == []


def test_a_failure_to_play_is_swallowed_and_logged_without_text():
    lines = []
    p, b = player(backend=FakeBackend(fail=True), lines=lines)
    assert p.play(S.START) is False        # no exception
    assert lines, "it says so"
    assert all("SECRET" not in line and "output device" not in line for line in lines)
    assert any("RuntimeError" in line for line in lines)


def test_an_unknown_cue_is_ignored():
    p, b = player()
    assert p.play("fanfare") is False and b.calls == []


def test_no_backend_means_no_cue_and_no_blind_spot():
    deafened = []
    p = S.CuePlayer(backend=None, settings=lambda: {"sounds": True}, platform="Linux")
    assert p.play(S.START, deafen=deafened.append) is False
    assert deafened == []


def test_settings_that_cannot_be_read_mean_silence():
    def boom():
        raise OSError("settings.json locked")
    b = FakeBackend()
    p = S.CuePlayer(backend=b, settings=boom)
    assert p.play(S.START) is False and b.calls == []


def test_warm_builds_every_cue_without_playing():
    S.cue_wav.cache_clear()
    p, b = player()
    p.warm()
    assert b.calls == []
    assert S.cue_wav.cache_info().currsize == 3


# ── the two platform backends ────────────────────────────────────────────

def test_windows_plays_from_memory_on_its_own_thread():
    """winsound refuses SND_MEMORY with SND_ASYNC, so the blocking call runs
    on a thread and play() returns at once."""
    started, release, played = threading.Event(), threading.Event(), []

    def PlaySound(data, flags):
        played.append((data, flags))
        started.set()
        release.wait(5)

    fake = types.SimpleNamespace(PlaySound=PlaySound, SND_MEMORY=4, SND_ASYNC=1, SND_NODEFAULT=2)
    b = S.WindowsBackend(winsound=fake)
    b.play(S.START, b"RIFFdata")          # returns while PlaySound is still blocked
    assert started.wait(2)
    assert played[0][0] == b"RIFFdata"
    assert played[0][1] & fake.SND_MEMORY and not played[0][1] & fake.SND_ASYNC
    assert played[0][1] & fake.SND_NODEFAULT, "no system beep if the sound cannot play"
    release.set()


class FakeNSSound:
    made = []

    def __init__(self):
        self.log = []
        FakeNSSound.made.append(self)

    @classmethod
    def alloc(cls):
        return cls()

    def initWithData_(self, data):
        self.log.append(("init", bytes(data)))
        return self

    def setVolume_(self, v):
        self.log.append(("volume", v))

    def stop(self):
        self.log.append(("stop",))

    def play(self):
        self.log.append(("play",))
        return True


def test_mac_plays_through_nssound_and_keeps_each_sound_for_reuse():
    FakeNSSound.made.clear()
    appkit = types.SimpleNamespace(NSSound=FakeNSSound)
    foundation = types.SimpleNamespace(NSData=types.SimpleNamespace(
        dataWithBytes_length_=lambda b, n: bytes(b)))
    b = S.MacBackend(appkit=appkit, foundation=foundation)
    b.play(S.START, b"WAVBYTES")
    b.play(S.START, b"WAVBYTES")
    b.play(S.STOP, b"OTHER")
    assert len(FakeNSSound.made) == 2, "one NSSound per cue, made once"
    start = FakeNSSound.made[0].log
    assert start[0] == ("init", b"WAVBYTES")
    assert start.count(("play",)) == 2 and ("stop",) in start, "stopped before a replay"


def test_warm_gets_the_mac_player_ready_only_when_sounds_are_on():
    appkit = types.SimpleNamespace(NSSound=FakeNSSound)
    foundation = types.SimpleNamespace(NSData=types.SimpleNamespace(
        dataWithBytes_length_=lambda b, n: bytes(b)))
    for settings, made in (({}, 0), ({"sounds": True}, 3)):
        FakeNSSound.made.clear()
        backend = S.MacBackend(appkit=appkit, foundation=foundation)
        S.CuePlayer(backend=backend, settings=lambda s=settings: s).warm()
        assert len(FakeNSSound.made) == made
        assert all(("play",) not in n.log for n in FakeNSSound.made), "warming makes no sound"


@pytest.mark.skipif(sys.platform != "darwin", reason="NSSound is macOS only")
def test_the_real_nssound_accepts_our_wav_and_reports_its_length():
    appkit = pytest.importorskip("AppKit")
    foundation = pytest.importorskip("Foundation")
    for name in S.CUES:
        wav = S.cue_wav(name)
        data = foundation.NSData.dataWithBytes_length_(wav, len(wav))
        sound = appkit.NSSound.alloc().initWithData_(data)
        assert sound is not None, f"NSSound could not read the {name} cue"
        assert abs(sound.duration() - S.cue_seconds(name)) < 0.01


def test_the_default_backend_follows_the_platform():
    assert isinstance(S.default_backend("Darwin"), S.MacBackend)
    assert isinstance(S.default_backend("Windows"), S.WindowsBackend)
    assert S.default_backend("Linux") is None
