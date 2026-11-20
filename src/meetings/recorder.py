"""Recording: microphone plus the computer's own audio, cut into chunks and
transcribed while the recording is still running.

Two sources are kept apart so the transcript can say who spoke: the
microphone is "Me", system audio (the other side of a call, a video) is
"Them". Both are cut at the same quiet moment every 15-28 seconds; each chunk
is saved as FLAC and sent for transcription on a worker thread, so text
appears during the recording and a crash loses at most the last few seconds.
"""
from __future__ import annotations

import math
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from . import audioproc
from .audioproc import RATE
from .paths import audio_dir
from .transcribe import TranscribeError, transcribe

CHUNK_MIN_S = 15.0
CHUNK_MAX_S = 28.0
_TICK_S = 0.2


# ── sources ─────────────────────────────────────────────────────────────────

class Source:
    """Something that yields 16 kHz mono float32 audio."""
    name = "mic"

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def pull(self) -> np.ndarray:           # samples since the last pull
        return np.zeros(0, dtype=np.float32)


class _Resampler:
    """Streaming resample to 16 kHz: carries filter context between blocks so
    block edges don't click, and a remainder so the ratio never drifts."""

    def __init__(self, src_rate: int):
        self.src = int(src_rate)
        g = math.gcd(self.src, RATE)
        self.step = self.src // g           # input samples per whole output group
        self.ctx = np.zeros(0, dtype=np.float32)
        self.rest = np.zeros(0, dtype=np.float32)
        self._pad = 96

    def feed(self, block: np.ndarray) -> np.ndarray:
        if self.src == RATE:
            return block.astype(np.float32, copy=False)
        data = np.concatenate([self.rest, block])
        usable = (len(data) // self.step) * self.step
        self.rest = data[usable:]
        data = data[:usable]
        if usable == 0:
            return np.zeros(0, dtype=np.float32)
        joined = np.concatenate([self.ctx, data])
        out = audioproc.resample(joined, self.src, RATE)
        drop = int(round(len(self.ctx) * RATE / self.src))
        self.ctx = joined[-(self._pad // self.step + 1) * self.step:]
        return out[drop:]


class MicSource(Source):
    name = "mic"

    def __init__(self, device: str | int | None = None):
        self.device = device if device not in ("", None) else None
        self._blocks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None
        self._resampler: _Resampler | None = None

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        with self._lock:
            self._blocks.append(indata[:, 0].copy())

    def start(self) -> None:
        import sounddevice as sd
        try:
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                          device=self.device, callback=self._callback)
            self._resampler = _Resampler(RATE)
        except Exception:
            info = sd.query_devices(self.device, "input")
            rate = int(info["default_samplerate"])
            self._stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                                          device=self.device, callback=self._callback)
            self._resampler = _Resampler(rate)
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def pull(self) -> np.ndarray:
        with self._lock:
            blocks, self._blocks = self._blocks, []
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        return self._resampler.feed(np.concatenate(blocks))


class SystemSource(Source):
    """What the computer is playing (Windows WASAPI loopback of the default
    output). WASAPI delivers nothing while nothing plays, so gaps are filled
    with silence to keep this source in step with the microphone."""
    name = "system"

    def __init__(self):
        self._blocks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._pa = None
        self._stream = None
        self._rate = 48000
        self._channels = 2
        self._t0 = 0.0
        self._received = 0
        self._resampler: _Resampler | None = None

    @staticmethod
    def supported() -> bool:
        if sys.platform == "darwin":
            return MacSystemSource.supported()
        if sys.platform != "win32":
            return False
        try:
            import pyaudiowpatch  # noqa: F401
            return True
        except Exception:
            return False

    def _callback(self, in_data, frame_count, time_info, status):  # noqa: ARG002
        import pyaudiowpatch as pyaudio
        a = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        if self._channels > 1:
            a = a.reshape(-1, self._channels).mean(axis=1)
        with self._lock:
            self._blocks.append(a)
        return (None, pyaudio.paContinue)

    def start(self) -> None:
        import pyaudiowpatch as pyaudio
        self._pa = pyaudio.PyAudio()
        dev = self._pa.get_default_wasapi_loopback()
        self._rate = int(dev["defaultSampleRate"])
        self._channels = max(1, int(dev["maxInputChannels"]))
        self._resampler = _Resampler(self._rate)
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=self._channels,
                                     rate=self._rate, input=True,
                                     input_device_index=dev["index"],
                                     frames_per_buffer=int(self._rate * 0.05),
                                     stream_callback=self._callback)
        self._t0 = time.monotonic()
        self._received = 0

    def stop(self) -> None:
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        finally:
            self._stream = None
            if self._pa is not None:
                self._pa.terminate()
                self._pa = None

    def pull(self) -> np.ndarray:
        with self._lock:
            blocks, self._blocks = self._blocks, []
        data = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)
        self._received += len(data)
        expected = int((time.monotonic() - self._t0) * self._rate)
        missing = expected - self._received
        if missing > self._rate * 0.3:           # nothing was playing: fill the gap
            pad = missing - int(self._rate * 0.1)
            data = np.concatenate([np.zeros(pad, dtype=np.float32), data]) if len(data) \
                else np.zeros(pad, dtype=np.float32)
            self._received += pad
        return self._resampler.feed(data)


def _mac_helper_path():
    """natter-sysaudio: inside the .app when packaged, mac/build/ in a checkout.
    NATTER_SYSAUDIO overrides it in a checkout only (tests): a built app never
    runs another binary under its Screen Recording permission."""
    import os
    from pathlib import Path
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("natter-sysaudio")
    override = os.environ.get("NATTER_SYSAUDIO")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "mac" / "build" / "natter-sysaudio"


class MacSystemSource(Source):
    """What the Mac is playing, through the ScreenCaptureKit helper
    (mac/sysaudio). The helper writes 16-bit mono PCM to stdout and says
    "ready <rate>" or "error <words>" on stderr."""

    START_TIMEOUT_S = 8.0

    name = "system"

    def __init__(self, command: list[str] | None = None):
        self._blocks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._rate = 48000
        self._t0 = 0.0
        self._received = 0
        self._resampler: _Resampler | None = None
        self._command = command
        self._proc = None
        self._reader: threading.Thread | None = None
        self._last_error = ""
        self.problem = ""                        # set if the helper dies mid-recording; Recorder warns

    @staticmethod
    def supported() -> bool:
        if sys.platform != "darwin":
            return False
        import platform
        try:
            if int(platform.mac_ver()[0].split(".")[0]) < 13:
                return False
        except ValueError:
            return False
        p = _mac_helper_path()
        return p.is_file()

    def start(self) -> None:
        import subprocess
        cmd = self._command or [str(_mac_helper_path())]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, bufsize=0)
        lines: queue.Queue = queue.Queue()

        proc = self._proc

        def read_status():
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("error"):
                    self._last_error = line[len("error"):].strip()
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read_status, daemon=True, name="natter-sysaudio-err").start()
        deadline = time.monotonic() + self.START_TIMEOUT_S
        while True:
            try:
                line = lines.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                self.stop()
                raise RuntimeError("System audio helper did not start in time.")
            if line is None:
                self.stop()
                raise RuntimeError("System audio helper exited before starting.")
            if line.startswith("ready"):
                parts = line.split()
                rate = int(parts[1]) if len(parts) > 1 else 48000
                break
            if line.startswith("error"):
                self.stop()
                raise RuntimeError(line[len("error"):].strip() or "System audio failed.")
        self._rate = rate
        self._resampler = _Resampler(rate)
        self._t0 = time.monotonic()
        self._received = 0
        self._reader = threading.Thread(target=self._read, daemon=True, name="natter-sysaudio")
        self._reader.start()

    def _read(self) -> None:
        carry = b""
        proc = self._proc
        out = proc.stdout
        while True:
            chunk = out.read(4096)
            if not chunk:
                if self._proc is proc:           # not us stopping it: the helper died
                    time.sleep(0.2)              # let its last status line arrive
                    self.problem = "System audio stopped" + (f": {self._last_error}" if self._last_error else ".") \
                        + " Only the microphone is being recorded now."
                return
            chunk = carry + chunk
            usable = len(chunk) - (len(chunk) % 2)
            carry = chunk[usable:]
            a = np.frombuffer(chunk[:usable], dtype="<i2").astype(np.float32) / 32768.0
            with self._lock:
                self._blocks.append(a)

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()               # the helper exits when stdin closes
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        if self._reader is not None:
            self._reader.join(timeout=2)
            self._reader = None
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except Exception:
                pass

    def pull(self) -> np.ndarray:
        """Same gap filling as SystemSource: silence where nothing played, so this
        source stays in step with the microphone."""
        with self._lock:
            blocks, self._blocks = self._blocks, []
        data = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)
        self._received += len(data)
        expected = int((time.monotonic() - self._t0) * self._rate)
        missing = expected - self._received
        if missing > self._rate * 0.3:
            pad = missing - int(self._rate * 0.1)
            data = np.concatenate([np.zeros(pad, dtype=np.float32), data]) if len(data) \
                else np.zeros(pad, dtype=np.float32)
            self._received += pad
        return self._resampler.feed(data)


def list_microphones() -> list[dict]:
    try:
        import sounddevice as sd
        default_in = sd.default.device[0]
        hostapis = sd.query_hostapis()
        out = []
        for i, d in enumerate(sd.query_devices()):
            # One entry per physical device: the default host API's list.
            if d["max_input_channels"] > 0 and d["hostapi"] == sd.default.hostapi:
                out.append({"id": i, "name": d["name"], "default": i == default_in,
                            "api": hostapis[d["hostapi"]]["name"]})
        return out
    except Exception:
        return []


# ── echo ────────────────────────────────────────────────────────────────────

_WORD = re.compile(r"[^\W_]+")


def drop_echo(mic: list[dict], system: list[dict], system_audio: np.ndarray | None = None,
              chunk_start_s: float = 0.0) -> list[dict]:
    """Without headphones the microphone also hears the speakers, so the other
    side's words arrive twice, or as a faint mumble the model guesses at.

    A microphone line is dropped when nearly all of its words are in what the
    system audio said around the same time. When the system audio was
    speaking for most of the line (`system_audio`, this chunk's samples), a
    weaker sign is enough: most of its words shared, or the model unsure of it.
    Someone genuinely talking over the other side is clear speech with its
    own words, and stays."""
    if not mic or (not system and system_audio is None):
        return mic
    mask = audioproc.voiced_mask(system_audio) if system_audio is not None else None
    kept = []
    for m in mic:
        words = [w.lower() for w in _WORD.findall(m["text"])]
        near = set()
        for s in system:
            if s["start_s"] <= m["end_s"] + 3 and s["end_s"] >= m["start_s"] - 3:
                near.update(w.lower() for w in _WORD.findall(s["text"]))
        share = (sum(1 for w in words if w in near) / len(words)) if words else 0.0
        if len(words) >= 3 and share >= 0.75:
            continue
        if mask is not None:
            a, b = m["start_s"] - chunk_start_s, m["end_s"] - chunk_start_s
            covered = audioproc.voiced_between(mask, a, b) / max(0.3, b - a)
            if covered >= 0.6 and ((len(words) >= 4 and share >= 0.6)
                                   or m.get("logprob", 0.0) < -0.6):
                continue
        kept.append(m)
    return kept


# ── the recorder ────────────────────────────────────────────────────────────

@dataclass
class _Job:
    note_id: str
    session_n: int
    start_s: float                     # on the note's timeline
    audio: dict                        # source name -> samples
    attempts: int = 0


@dataclass
class RecState:
    recording: bool = False
    note_id: str = ""
    session_n: int = 0
    started: float = 0.0
    offset_s: float = 0.0
    levels: dict = field(default_factory=dict)
    error: str = ""


class Recorder:
    def __init__(self, store, source_factory=None, transcribe_backend=None, on_idle=None):
        self.store = store
        self._source_factory = source_factory or self._default_sources
        self._backend = transcribe_backend
        self._on_idle = on_idle           # called with note_id when its last chunk is stored
        self._state = RecState()
        self._sources: list[Source] = []
        self._buffers: dict[str, np.ndarray] = {}
        self._consumed = 0                # samples of this session already cut into chunks
        self._session_id = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._jobs: queue.Queue = queue.Queue()
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._finishing: set[str] = set()
        self._worker = threading.Thread(target=self._work, daemon=True, name="natter-transcribe")
        self._worker.start()
        self.warnings: list[str] = []

    # sources -----------------------------------------------------------------
    def _default_sources(self, settings: dict) -> list[Source]:
        sources: list[Source] = [MicSource(settings.get("mic_device"))]
        if settings.get("capture_system_audio", True) and SystemSource.supported():
            sources.append(MacSystemSource() if sys.platform == "darwin" else SystemSource())
        return sources

    # state -------------------------------------------------------------------
    def state(self) -> dict:
        s = self._state
        elapsed = (time.monotonic() - s.started) if s.recording else 0.0
        with self._pending_lock:
            pending = self._pending
        return {"recording": s.recording, "note_id": s.note_id, "session_n": s.session_n,
                "elapsed_s": round(elapsed, 1), "note_elapsed_s": round(s.offset_s + elapsed, 1),
                "levels": dict(s.levels), "pending_chunks": pending, "error": s.error,
                "sources": [src.name for src in self._sources] if s.recording else [],
                "warnings": list(self.warnings)}

    def busy(self, note_id: str) -> bool:
        with self._pending_lock:
            return self._pending > 0 or note_id in self._finishing

    # start / stop ------------------------------------------------------------
    def start(self, note_id: str) -> dict:
        if self._state.recording:
            raise RuntimeError("Already recording.")
        settings = self.store.settings()
        sources = self._source_factory(settings)
        started, self.warnings = [], []
        for src in sources:
            try:
                src.start()
                started.append(src)
            except Exception as e:  # noqa: BLE001
                label = "microphone" if src.name == "mic" else "system audio"
                detail = str(e).strip()           # the Mac helper says what to allow, in plain words
                self.warnings.append(f"Couldn't open the {label}: {detail}" if detail
                                     else f"Couldn't open the {label} ({type(e).__name__}).")
        if not started:
            raise RuntimeError("No audio input could be opened. Check your microphone.")
        session = self.store.start_session(note_id)
        self._sources = started
        self._buffers = {s.name: np.zeros(0, dtype=np.float32) for s in started}
        self._consumed = 0
        self._session_id = session["id"]
        self._state = RecState(recording=True, note_id=note_id, session_n=session["n"],
                               started=time.monotonic(), offset_s=session["offset_s"])
        self.store.update_note(note_id, status="recording", error="")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="natter-record")
        self._thread.start()
        return self.state()

    def stop(self) -> dict:
        if not self._state.recording:
            return self.state()
        note_id = self._state.note_id
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        for src in self._sources:
            try:
                src.stop()
            except Exception:
                pass
        self._collect()
        length = self._available()
        self._finishing.add(note_id)
        if length > 0:
            self._cut(length)
        duration = self._state.offset_s + self._consumed / RATE
        self.store.end_session(self._session_id, note_id, duration)
        self.store.update_note(note_id, status="processing")
        self._state = RecState()
        self._sources = []
        self._jobs.put(("done", note_id))
        return self.state()

    # capture loop --------------------------------------------------------------
    def _collect(self) -> None:
        for src in self._sources:
            problem = getattr(src, "problem", "")
            if problem and problem not in self.warnings:      # a source that died mid-recording
                self.warnings.append(problem)
            try:
                new = src.pull()
            except Exception:
                new = np.zeros(0, dtype=np.float32)
            if len(new):
                self._buffers[src.name] = np.concatenate([self._buffers[src.name], new])
                self._state.levels[src.name] = audioproc.level(new[-int(RATE * 0.25):])
            else:
                self._state.levels[src.name] = self._state.levels.get(src.name, 0.0) * 0.6

    def _available(self) -> int:
        """Samples every source has. A source that has stalled (a device that
        vanished) must not hold the others back for ever."""
        lengths = [len(b) for b in self._buffers.values()]
        if not lengths:
            return 0
        longest = max(lengths)
        live = [n for n in lengths if longest - n < RATE * 5]
        return min(live)

    def _run(self) -> None:
        while not self._stop.wait(_TICK_S):
            self._collect()
            if self._available() >= CHUNK_MAX_S * RATE:
                mix = sum(b[:int(CHUNK_MAX_S * RATE)] if len(b) >= CHUNK_MAX_S * RATE else
                          np.pad(b, (0, int(CHUNK_MAX_S * RATE) - len(b)))
                          for b in self._buffers.values())
                self._cut(audioproc.quiet_cut(mix, CHUNK_MIN_S, CHUNK_MAX_S))

    def _cut(self, n: int) -> None:
        audio = {}
        for name, buf in self._buffers.items():
            part = buf[:n]
            if len(part) < n:
                part = np.pad(part, (0, n - len(part)))
            audio[name] = part
            self._buffers[name] = buf[n:]
        job = _Job(self._state.note_id, self._state.session_n,
                   self._state.offset_s + self._consumed / RATE, audio)
        self._consumed += n
        with self._pending_lock:
            self._pending += 1
        self._jobs.put(job)

    # transcription worker ------------------------------------------------------
    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if isinstance(job, tuple):               # ("done", note_id)
                note_id = job[1]
                self._finishing.discard(note_id)
                if self._on_idle:
                    try:
                        self._on_idle(note_id)
                    except Exception:
                        pass
                continue
            try:
                self._transcribe_job(job)
            finally:
                with self._pending_lock:
                    self._pending -= 1

    def _transcribe_job(self, job: _Job) -> None:
        settings = self.store.settings()
        keep = settings.get("keep_audio", True)
        per_source: dict[str, list[dict]] = {}
        failed: TranscribeError | None = None
        for name, audio in job.audio.items():
            path = audio_dir(job.note_id) / (
                f"s{job.session_n:02d}_{int(job.start_s * 1000):09d}_{name}.flac")
            has_voice = audioproc.voiced_seconds(audio) >= 0.3
            if has_voice and keep:
                path.write_bytes(audioproc.to_flac(audio))
            if not has_voice:
                continue
            try:
                segs = transcribe(audio, settings, backend=self._backend)
            except TranscribeError as e:
                failed = e
                if not keep:                          # never lose audio that wasn't transcribed
                    path.write_bytes(audioproc.to_flac(audio))
                continue
            per_source[name] = [{"start_s": job.start_s + s["start_s"],
                                 "end_s": job.start_s + s["end_s"], "text": s["text"],
                                 "source": name, "session_n": job.session_n,
                                 "logprob": s.get("logprob", 0.0)} for s in segs]
        if "mic" in per_source and "system" in job.audio:
            per_source["mic"] = drop_echo(per_source["mic"], per_source.get("system", []),
                                          job.audio["system"], job.start_s)
        for segs in per_source.values():
            self.store.add_segments(job.note_id, segs)
        if failed is not None:
            self.store.update_note(job.note_id, touch=False, error=str(failed))


def retranscribe(store, note_id: str, backend=None) -> int:
    """Rebuild a note's transcript from its kept audio. Returns segments written."""
    files = sorted(audio_dir(note_id).glob("s*_*.flac"))
    if not files:
        return 0
    settings = store.settings()
    chunks: dict[tuple[int, float], dict[str, list[dict]]] = {}
    for f in files:
        m = re.match(r"s(\d+)_(\d+)_(\w+)\.flac$", f.name)
        if not m:
            continue
        session_n, start_s, source = int(m.group(1)), int(m.group(2)) / 1000, m.group(3)
        segs = transcribe(audioproc.read_audio(f), settings, backend=backend)
        chunks.setdefault((session_n, start_s), {})[source] = [
            {"start_s": start_s + s["start_s"], "end_s": start_s + s["end_s"], "text": s["text"],
             "source": source, "session_n": session_n} for s in segs]
    with store._write:
        store.db.execute("DELETE FROM segments WHERE note_id=?", (note_id,))
        store.db.execute("DELETE FROM segments_fts WHERE note_id=?", (note_id,))
    total = 0
    for per_source in chunks.values():
        if "mic" in per_source and "system" in per_source:
            per_source["mic"] = drop_echo(per_source["mic"], per_source["system"])
        for segs in per_source.values():
            total += len(store.add_segments(note_id, segs))
    store.update_note(note_id, error="")
    return total
