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
        self._last_rms: float = 0.0   # normalised 0..1 for overlay

    def _callback(self, indata, frames, time, status):
        """Called continuously by sounddevice while recording"""
        if self.is_recording and not self.is_paused:
            with self._lock:
                self._buffer.append(indata.copy())
            # Update live RMS for VU bars
            rms_raw = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
            # Normalise: typical speech ~ 500-3000, silence < 80
            self._last_rms = min(1.0, rms_raw / 2500.0)
        elif self.is_paused:
            # Still capture audio but discard it (or could save for resume)
            self._last_rms = 0.0

    def get_level(self) -> float:
        """Return normalised audio level (0.0–1.0) for the latest chunk."""
        return self._last_rms

    def get_is_paused(self) -> bool:
        """Return paused state."""
        return self.is_paused

    def pause(self):
        """Pause recording (audio continues but is discarded)."""
        self.is_paused = True
        print("⏸️ Recording paused")

    def resume(self):
        """Resume recording from paused state."""
        self.is_paused = False
        print("▶️ Recording resumed")

    def toggle_pause(self):
        """Toggle pause state."""
        if self.is_paused:
            self.resume()
        else:
            self.pause()

    def print_devices(self):
        """Print available audio input devices"""
        print("\n🎙️  Available microphones:")
        default_input = sd.query_devices(kind='input')
        print(f"   ✅ Default input: {default_input['name']}")
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                print(f"   [{i}] {d['name']}")
        print()

    def start(self):
        """Start continuous audio stream"""
        self.is_recording = True
        self._buffer = []

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            callback=self._callback,
            blocksize=1024
        )
        self._stream.start()
        print(f"🎤 Recording started (sample_rate={self.sample_rate}Hz, channels={self.channels})")

    def stop(self) -> bytes:
        """Stop stream and return WAV bytes"""
        self.is_recording = False

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        with self._lock:
            if not self._buffer:
                print("⚠️  No audio recorded")
                return b""

            self.recording = np.concatenate(self._buffer, axis=0)

        duration = len(self.recording) / self.sample_rate
        rms = np.sqrt(np.mean(self.recording.astype(np.float32) ** 2))
        print(f"🎤 Recording stopped ({duration:.2f}s)")
        print(f"📊 Audio level — RMS: {rms:.0f} {'✅ audio detected' if rms > 100 else '❌ SILENCE — mic issue'}")

        return self._to_wav_bytes(self.recording)

    def start_monitoring(self):
        """Start audio stream for level monitoring only (no recording)."""
        if self._stream and self._stream.active:
            return  # Already monitoring/recording

        self.is_recording = False  # Don't save audio
        self._buffer = []

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            callback=self._callback,
            blocksize=1024
        )
        self._stream.start()
        print("🎤 Audio monitoring started (demo mode)")

    def stop_monitoring(self):
        """Stop audio monitoring stream."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._last_rms = 0.0
            print("🎤 Audio monitoring stopped")

    def record_chunk(self, duration: float = 0.1):
        """No-op — continuous stream handles recording automatically"""
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
