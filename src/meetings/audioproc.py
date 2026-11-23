"""Small audio helpers on float32 mono arrays: resampling, voice detection,
choosing a quiet place to cut, FLAC encoding."""
from __future__ import annotations

import io

import numpy as np

RATE = 16000          # what the speech models want
_WIN_S = 0.03


def to_mono(frames: np.ndarray) -> np.ndarray:
    a = np.asarray(frames, dtype=np.float32)
    return a if a.ndim == 1 else a.mean(axis=1)


def resample(audio: np.ndarray, src_rate: int, dst_rate: int = RATE) -> np.ndarray:
    """Band-limited enough for speech: a windowed-sinc low-pass, then linear
    interpolation onto the new sample grid."""
    if src_rate == dst_rate or len(audio) == 0:
        return audio.astype(np.float32, copy=False)
    if src_rate > dst_rate:
        cutoff = 0.5 * dst_rate / src_rate * 0.92
        taps = 63
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        audio = np.convolve(audio, (h / h.sum()).astype(np.float32), mode="same")
    out_len = int(round(len(audio) * dst_rate / src_rate))
    x = np.linspace(0, len(audio) - 1, out_len)
    return np.interp(x, np.arange(len(audio)), audio).astype(np.float32)


def window_rms(audio: np.ndarray, rate: int = RATE) -> np.ndarray:
    w = int(rate * _WIN_S)
    n = len(audio) // w
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt((audio[:n * w].reshape(n, w).astype(np.float64) ** 2).mean(axis=1))


# Dictation lifts a quiet take to a normal speech level before it is judged
# (src/transcribe_whisper.py boost_quiet_speech: towards -20 dBFS, by 30 dB at
# most). The same lift is reckoned here, so a quiet microphone's voice is
# measured as voice: on a Windows PC whose speech arrived at -44 to -60 dBFS,
# a fixed -46 dBFS floor counted too little of it, and of 20 real takes heard
# as a meeting, 4 were never sent and 7 more were dropped as written over
# nothing.
_SPEECH_TARGET = 10 ** (-20 / 20)
_MAX_LIFT = 10 ** (30 / 20)
_LIFT_FLOOR = 10 ** (-70 / 20)


# A quiet room is not a quiet microphone. Faint taps, rustling and knocks are
# as far above a silent room's floor as a quiet microphone's voice is, and a
# take lifted for them would count them as voice (measured: 30 s of room with
# five faint taps read as 1 s of voice, lifted). What tells them apart is
# pitch: a voice repeats itself 80 to 400 times a second for a syllable at a
# time, a tap or a rustle does not, and a knock only for an instant. So a take
# is lifted only when enough of its loud sound is pitched, in runs of
# _PITCH_RUN windows or more: 40% of the loud time, never less than one run
# (a one-word answer has little more) and never more than _MAX_PITCHED_S.
# Measured: real dictations made 30 and 40 dB quieter, 16-bit steps and all,
# 0.12 s (one word) to 17 s; on a Windows PC, the two quietest of 20 real
# takes (-60 dBFS) 0.21 s each at a bar of 0.5, where the noise drags the
# repeat down, hence a bar of 0.4. Taps, rustling, shuffling and typing
# measure 0; six desk knocks 0.09 s, against the 0.16 s asked of them.
_PITCH_LO_HZ, _PITCH_HI_HZ = 80.0, 400.0
_PITCH_STRENGTH = 0.4
_PITCH_RUN = 3
_PITCHED_SHARE = 0.4
_MAX_PITCHED_S = 0.21


def _has_pitch(audio: np.ndarray, rate: int, loud: np.ndarray) -> bool:
    """Do the loud windows carry a voice? True once enough of them, in runs,
    repeat at a voice's pitch (normalised autocorrelation over 60 ms)."""
    w = int(rate * _WIN_S)
    span = 2 * w
    lo, hi = max(1, int(rate / _PITCH_HI_HZ)), int(rate / _PITCH_LO_HZ)
    if hi >= span:
        return False
    lags = np.arange(lo, hi + 1)
    loud_s = float(np.count_nonzero(loud)) * _WIN_S
    need = int(round(min(_MAX_PITCHED_S, max(_PITCH_RUN * _WIN_S, _PITCHED_SHARE * loud_s)) / _WIN_S))
    found = run = 0
    last = -2
    for i in np.flatnonzero(loud):
        a = audio[i * w: i * w + span].astype(np.float64)
        pitched = False
        if len(a) == span:
            a = a - a.mean()
            c = np.cumsum(a * a)
            if c[-1] > 0:
                f = np.fft.rfft(a, 2 * span)
                ac = np.fft.irfft(f * np.conj(f))[lags]
                r = ac / np.sqrt(np.maximum(c[span - 1 - lags] * (c[-1] - c[lags - 1]), 1e-30))
                pitched = float(r.max()) > _PITCH_STRENGTH
        if not pitched:
            run = 0
        else:
            run = run + 1 if i == last + 1 else 1
            if run == _PITCH_RUN:
                found += _PITCH_RUN
            elif run > _PITCH_RUN:
                found += 1
            if found >= need:
                return True
            last = i
    return False


def _lift(rms: np.ndarray, audio: np.ndarray | None = None, rate: int = RATE) -> float:
    """How far below normal speech this take's voice sits: the gain dictation
    would give it (1 for a take at a normal level, or with no voice). With the
    take itself given, a lift is only made for sound that has a voice's pitch."""
    floor = float(np.percentile(rms, 10))
    loud = rms > max(floor * 4.0, _LIFT_FLOOR)
    voiced = rms[loud]
    if voiced.size == 0:
        return 1.0
    lift = float(min(max(_SPEECH_TARGET / float(np.median(voiced)), 1.0), _MAX_LIFT))
    if lift > 1.0 and audio is not None and not _has_pitch(audio, rate, loud):
        return 1.0
    return lift


def voiced_mask(audio: np.ndarray, rate: int = RATE) -> np.ndarray:
    """Per 30 ms window: is someone speaking? Voice is well above the take's
    own noise floor and above an absolute floor (about -46 dBFS once a quiet
    take is lifted as dictation lifts it), so steady room noise and digital
    silence both count as nothing, and a quiet microphone's voice counts."""
    rms = window_rms(audio, rate)
    if len(rms) == 0:
        return np.zeros(0, dtype=bool)
    floor = float(np.percentile(rms, 10))
    return rms > max(floor * 3.0, 0.005 / _lift(rms, audio, rate))


def voiced_seconds(audio: np.ndarray, rate: int = RATE) -> float:
    return float(voiced_mask(audio, rate).sum()) * _WIN_S


def voiced_between(mask: np.ndarray, start_s: float, end_s: float) -> float:
    a, b = max(0, int(start_s / _WIN_S)), max(0, int(np.ceil(end_s / _WIN_S)))
    return float(mask[a:b].sum()) * _WIN_S


def quiet_cut(audio: np.ndarray, earliest_s: float, latest_s: float, rate: int = RATE) -> int:
    """The sample index of the quietest 0.3 s between two times: where to
    cut a chunk so a word is not split."""
    rms = window_rms(audio, rate)
    a, b = int(earliest_s / _WIN_S), min(len(rms), int(latest_s / _WIN_S))
    span = 10
    if b - a <= span:
        return min(len(audio), int(latest_s * rate))
    sums = np.convolve(rms[a:b], np.ones(span), mode="valid")
    best = a + int(np.argmin(sums)) + span // 2
    return int(best * _WIN_S * rate)


def level(audio: np.ndarray) -> float:
    """0..1 loudness for a meter (dBFS mapped -60..0)."""
    if len(audio) == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if rms <= 1e-6:
        return 0.0
    return float(min(1.0, max(0.0, (20 * np.log10(rms) + 60) / 60)))


def to_flac(audio: np.ndarray, rate: int = RATE) -> bytes:
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.clip(audio, -1.0, 1.0), rate, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def read_audio(path) -> np.ndarray:
    """Any file soundfile can read, as 16 kHz mono float32."""
    import soundfile as sf
    data, rate = sf.read(str(path), dtype="float32", always_2d=False)
    return resample(to_mono(data), rate)
