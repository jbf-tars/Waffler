"""Optional sound cues: a soft tone when recording starts and stops, and a
tick when hands-free locks.

Off by default (settings.json ``sounds``). No audio files ship: each cue is a
few sine tones with a fade in and out, built here as WAV bytes, the same bytes
every time, and kept for reuse. They are short (about 115 to 150 ms) and soft.

Playing never gets in the way of a dictation:

  * It is asynchronous. macOS hands the bytes to ``NSSound``, which plays on
    its own; it opens no output stream of Waffler's, so it cannot compete with
    the microphone's input stream. Windows plays from memory with ``winsound``
    on a short thread (``winsound`` refuses memory with SND_ASYNC).
  * Any failure is swallowed and logged as the error's name, never its text.
  * The microphone must never hear a cue and send it to the speech service:
    Waffler keeps the microphone open (with a half-second of audio from before
    the key goes down). So before a cue starts, the recorder is told to
    silence the moments it will sound in (``AudioRecorder.deafen_for``,
    src/audio.py). If it cannot be told, the cue is not played. The cost is a
    fraction of a second of audio after the cue starts, which is also why
    sounds are off unless asked for.
"""

from __future__ import annotations

import functools
import importlib
import io
import math
import platform as _platform
import struct
import threading
import wave
from typing import Callable, Optional

SAMPLE_RATE = 44100

START, STOP, LOCK = "start", "stop", "lock"
CUES = (START, STOP, LOCK)

# Loudness of the loudest sample, as a share of full scale: audible, soft.
PEAK = 0.20
# Each tone rises from silence and falls back to it, so there is no click.
FADE_IN_S = 0.012
FADE_OUT_S = 0.045

# (pitch in Hz, seconds); a pitch of 0 is a gap. Whole tens of milliseconds, so
# the length is exact at 44.1 kHz and at the microphone's 16 kHz.
CUE_NOTES = {
    START: ((660.0, 0.060), (880.0, 0.070)),                       # up: listening
    STOP: ((880.0, 0.060), (660.0, 0.090)),                        # down: done
    LOCK: ((1175.0, 0.050), (0.0, 0.030), (1175.0, 0.050)),        # tick-tick: locked
}

# The microphone is told to ignore a cue's moments for: how long until the
# sound reaches the speakers (the backend's own latency), the cue itself, and
# a moment for the room to stop ringing.
CUE_TAIL_S = 0.06


def _frames(notes, rate: int) -> int:
    return sum(max(1, round(d * rate)) for _, d in notes)


def cue_seconds(name: str, rate: int = SAMPLE_RATE) -> float:
    """How long the cue lasts."""
    return _frames(CUE_NOTES[name], rate) / rate


def mask_seconds(name: str, latency_s: float) -> float:
    """From the moment play is called, how long the microphone must ignore:
    the sound's trip to the speakers, the cue, and the room's ring."""
    return float(latency_s) + cue_seconds(name) + CUE_TAIL_S


def cue_samples(name: str, rate: int = SAMPLE_RATE) -> list:
    """The cue as 16-bit samples at ``rate``."""
    out = []
    for freq, dur in CUE_NOTES[name]:
        n = max(1, round(dur * rate))
        if freq <= 0:
            out.extend([0] * n)
            continue
        n_in = int(min(FADE_IN_S, dur * 0.25) * rate)
        n_out = int(min(FADE_OUT_S, dur * 0.6) * rate)
        amp = PEAK * 32767.0
        w = 2.0 * math.pi * freq / rate
        for i in range(n):
            env = 1.0
            if i < n_in:
                env = 0.5 - 0.5 * math.cos(math.pi * i / n_in)
            j = n - 1 - i
            if j < n_out:
                env = min(env, 0.5 - 0.5 * math.cos(math.pi * j / n_out))
            out.append(int(round(amp * env * math.sin(w * i))))
    return out


@functools.lru_cache(maxsize=None)
def cue_wav(name: str) -> bytes:
    """The cue as a WAV file in memory (mono, 16-bit, 44.1 kHz). Built once."""
    samples = cue_samples(name, SAMPLE_RATE)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def sounds_enabled(settings) -> bool:
    """The "Sounds" setting: off unless it is exactly True."""
    return isinstance(settings, dict) and settings.get("sounds") is True


# ── playing ──────────────────────────────────────────────────────────────

class MacBackend:
    """NSSound: one object per cue, made on first use and kept."""

    latency_s = 0.06

    def __init__(self, appkit=None, foundation=None):
        self._appkit = appkit
        self._foundation = foundation
        self._sounds = {}
        self._lock = threading.Lock()

    def _sound(self, name: str, wav: bytes):
        sound = self._sounds.get(name)
        if sound is None:
            appkit = self._appkit or importlib.import_module("AppKit")
            foundation = self._foundation or importlib.import_module("Foundation")
            data = foundation.NSData.dataWithBytes_length_(wav, len(wav))
            sound = appkit.NSSound.alloc().initWithData_(data)
            if sound is None:
                raise RuntimeError("NSSound could not read the cue")
            self._sounds[name] = sound
        return sound

    def prepare(self, name: str, wav: bytes) -> None:
        """Make the NSSound now, so the first cue has no set-up to wait for."""
        with self._lock:
            self._sound(name, wav)

    def play(self, name: str, wav: bytes) -> None:
        with self._lock:
            sound = self._sound(name, wav)
            sound.stop()        # a cue still sounding from a moment ago
            sound.play()


class WindowsBackend:
    """winsound, from memory, on a short thread: PlaySound refuses
    SND_MEMORY together with SND_ASYNC."""

    latency_s = 0.12

    def __init__(self, winsound=None, log: Optional[Callable[[str], None]] = None):
        self._winsound = winsound
        self._log = log

    def play(self, name: str, wav: bytes) -> None:
        ws = self._winsound or importlib.import_module("winsound")
        flags = ws.SND_MEMORY | ws.SND_NODEFAULT    # no system beep if it cannot play
        threading.Thread(target=self._run, args=(ws, name, wav, flags),
                         daemon=True, name="CuePlay").start()

    def _run(self, ws, name, wav, flags) -> None:
        try:
            ws.PlaySound(wav, flags)
        except Exception as e:
            if self._log is not None:
                try:
                    self._log(f"[sounds] {name} cue not played ({type(e).__name__})")
                except Exception:
                    pass


def default_backend(system: str, log: Optional[Callable[[str], None]] = None):
    """The platform's way to play a cue, or None where there is none."""
    if system == "Darwin":
        return MacBackend()
    if system == "Windows":
        return WindowsBackend(log=log)
    return None


_AUTO = object()


class CuePlayer:
    """``play(name)`` makes the cue, when the person has sounds on.

    ``settings`` is a function that reads settings.json now (a change in
    Settings applies to the next cue). ``deafen`` passed to ``play`` is the
    recorder's ``deafen_for``.
    """

    def __init__(self, backend=_AUTO, settings: Optional[Callable[[], dict]] = None,
                 log: Optional[Callable[[str], None]] = None, platform: Optional[str] = None):
        self._log = log
        self._settings = settings or (lambda: {})
        if backend is _AUTO:
            backend = default_backend(platform or _platform.system(), log)
        self._backend = backend

    def warm(self) -> None:
        """Build the cues now, off the hotkey's path (and, with sounds on, get
        the platform's player ready, so the first cue has nothing to set up)."""
        for name in CUES:
            cue_wav(name)
        prepare = getattr(self._backend, "prepare", None)
        if prepare is not None and sounds_enabled(self._settings()):
            for name in CUES:
                prepare(name, cue_wav(name))

    def play(self, name: str, deafen: Optional[Callable[[float], None]] = None) -> bool:
        """True when a cue was handed to the speakers. Never raises."""
        try:
            if name not in CUES or self._backend is None:
                return False
            if not sounds_enabled(self._settings()):
                return False
            wav = cue_wav(name)
            if deafen is not None:
                # First, so the microphone is already deaf when the sound
                # starts. If this fails the cue is skipped.
                deafen(mask_seconds(name, self._backend.latency_s))
            self._backend.play(name, wav)
            return True
        except Exception as e:
            self._say(f"[sounds] {name} cue not played ({type(e).__name__})")
            return False

    def _say(self, line: str) -> None:
        if self._log is not None:
            try:
                self._log(line)
            except Exception:
                pass
