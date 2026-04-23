"""Audio recording module using sounddevice - continuous stream"""

import sounddevice as sd
import numpy as np
import io
import wave
import threading
from typing import Optional


class AudioRecorder:
    """Records audio using a continuous sounddevice stream"""

    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.recording: Optional[np.ndarray] = None
        self.is_recording = False
        self.is_paused = False
        self._buffer = []
        self._paused_buffer = []  # Audio captured while paused (discarded)
        self._stream = None
        self._lock = threading.Lock()
        self._stream_lock = threading.Lock()  # Protect stream start/stop
        self._last_rms: float = 0.0   # normalised 0..1 for overlay
        self._callback_active = False  # Guard against callback during teardown

    def _callback(self, indata, frames, time, status):
        """Called continuously by sounddevice while recording"""
        if not self._callback_active:
            return
        if self.is_recording and not self.is_paused:
            try:
                with self._lock:
                    self._buffer.append(indata.copy())
                # Update live RMS for VU bars
                rms_raw = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
                # Normalise: whisper ~ 80-200, typical speech ~ 500-3000, silence < 30
                self._last_rms = min(1.0, rms_raw / 800.0)
            except Exception:
                pass  # Never crash in callback
        elif self.is_paused:
            self._last_rms = 0.0

    def get_level(self) -> float:
        """Return normalised audio level (0.0-1.0) for the latest chunk."""
        return self._last_rms

    def get_is_paused(self) -> bool:
        """Return paused state."""
        return self.is_paused

    def pause(self):
        """Pause recording (audio continues but is discarded)."""
        self.is_paused = True
        print("Recording paused")

    def resume(self):
        """Resume recording from paused state."""
        self.is_paused = False
        print("Recording resumed")

    def toggle_pause(self):
        """Toggle pause state."""
        if self.is_paused:
            self.resume()
        else:
            self.pause()

    def print_devices(self):
        """Print available audio input devices"""
        print("\nAvailable microphones:")
        default_input = sd.query_devices(kind='input')
        print(f"  Default input: {default_input['name']}")
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                print(f"  [{i}] {d['name']}")
        print()

    def start(self):
        """Start continuous audio stream"""
        with self._stream_lock:
            # Stop any existing stream first to prevent overlapping
            if self._stream:
                self._callback_active = False
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

            self._buffer = []
            self.is_recording = True
            self._callback_active = True

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                callback=self._callback,
                blocksize=1024
            )
            self._stream.start()

    def stop(self) -> bytes:
        """Stop stream and return WAV bytes.

        sounddevice's `_stream.stop()` calls into CoreAudio (PortAudio on
        Windows). Both can wedge for tens of seconds on long recordings or
        after a device hot-swap. When that happens we'd hold `_stream_lock`
        forever, blocking every subsequent recording at `start()` / `stop()`
        and producing the "stuck on processing" symptom (pipeline pile-up,
        all threads waiting on the same kernel call).

        We work around it: snapshot the buffer first (cheap, all the data
        we actually need lives in `_buffer`), then run the blocking
        `_stream.stop()` / `.close()` in a daemon thread with a 1.5 s
        watchdog. If it doesn't return in time we abandon the stream
        reference — the OS will reclaim it eventually — and proceed with
        whatever we captured. We never get stuck.
        """
        with self._stream_lock:
            # Disable callback FIRST to prevent CFFI race condition.
            self._callback_active = False
            self.is_recording = False
            stream = self._stream
            self._stream = None

        # Snapshot the buffer outside the close attempt — we want to be
        # able to return what we've captured even if the close hangs.
        with self._lock:
            buf_snapshot = list(self._buffer)

        if stream is not None:
            def _close():
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            t = threading.Thread(target=_close, daemon=True, name="AudioStreamClose")
            t.start()
            t.join(timeout=1.5)
            if t.is_alive():
                # CoreAudio / PortAudio is wedged. Don't wait — abandon the
                # stream object and continue. The thread will eventually
                # finish (or leak), but the pipeline isn't blocked.
                print("audio.stop: stream.stop() did not return in 1.5s — abandoning")

        if not buf_snapshot:
            return b""

        self.recording = np.concatenate(buf_snapshot, axis=0)
        duration = len(self.recording) / self.sample_rate
        rms = np.sqrt(np.mean(self.recording.astype(np.float32) ** 2))
        print(f"Recording stopped ({duration:.2f}s, RMS: {rms:.0f})")

        return self._to_wav_bytes(self.recording)

    def start_monitoring(self):
        """Start audio stream for level monitoring only (no recording)."""
        with self._stream_lock:
            if self._stream and self._stream.active:
                return  # Already monitoring/recording

            self.is_recording = False  # Don't save audio
            self._buffer = []
            self._callback_active = True

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                callback=self._callback,
                blocksize=1024
            )
            self._stream.start()

    def stop_monitoring(self):
        """Stop audio monitoring stream."""
        with self._stream_lock:
            self._callback_active = False
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
                self._last_rms = 0.0

    def record_chunk(self, duration: float = 0.1):
        """No-op - continuous stream handles recording automatically"""
        pass

    def _to_wav_bytes(self, audio_data: np.ndarray) -> bytes:
        byte_io = io.BytesIO()
        with wave.open(byte_io, 'wb') as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(audio_data.tobytes())
        return byte_io.getvalue()

    def get_duration(self) -> float:
        if self.recording is None:
            return 0.0
        return len(self.recording) / self.sample_rate
