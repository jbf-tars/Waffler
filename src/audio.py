"""Audio recording module using sounddevice - continuous stream"""

import sounddevice as sd
import numpy as np
import io
import wave
import threading
from collections import deque
from typing import Optional


# Pre-roll window in milliseconds. When the user presses the hotkey, we splice
# this much audio captured BEFORE the press into the recording, so the first
# 1-2 syllables aren't clipped if they started speaking just before pressing.
# 500ms is plenty for typical reaction-time clipping.
_PREROLL_MS = 500


class AudioRecorder:
    """Records audio using a continuous sounddevice stream.

    The stream stays alive for the lifetime of the recorder (created once
    on first start(), reused for every subsequent recording). This avoids
    the 50-300ms stream-creation latency on Windows that was clipping the
    first 1-2 syllables of speech every time the hotkey was pressed.

    A ring buffer captures the last 500ms of audio continuously, so even if
    the speaker started talking BEFORE pressing the hotkey, those samples
    are spliced into the recording.
    """

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

        # Pre-roll ring buffer — every audio chunk delivered by the stream is
        # appended here regardless of whether we're recording. On start(), we
        # splice this into the recording buffer so the moment-before-press is
        # captured. blocksize is 1024 frames @ 16kHz = 64ms per chunk, so 8
        # chunks = ~512ms of pre-roll headroom.
        chunks_per_preroll = max(1, int(_PREROLL_MS / 1000 * sample_rate / 1024) + 1)
        self._preroll = deque(maxlen=chunks_per_preroll)

    def _callback(self, indata, frames, time, status):
        """Called continuously by sounddevice while the stream is alive."""
        if not self._callback_active:
            return
        try:
            # Always capture into pre-roll, even when not recording.
            chunk = indata.copy()
            self._preroll.append(chunk)

            if self.is_recording and not self.is_paused:
                with self._lock:
                    self._buffer.append(chunk)
                # Update live RMS for VU bars
                rms_raw = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
                # Normalise: whisper ~ 80-200, typical speech ~ 500-3000, silence < 30
                self._last_rms = min(1.0, rms_raw / 800.0)
            elif self.is_paused:
                self._last_rms = 0.0
        except Exception:
            pass  # Never crash in callback

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
        """Begin recording.

        Uses the long-lived monitor stream when available. If the stream
        isn't running yet (first call after init or after an explicit
        stop_monitoring()), creates and starts it now and waits briefly
        for it to begin producing samples before flipping is_recording on.

        The 500ms pre-roll is spliced into the recording buffer so the
        first 1-2 syllables aren't lost to stream-startup latency.
        """
        with self._stream_lock:
            self._buffer = []

            stream_was_running = (
                self._stream is not None
                and getattr(self._stream, 'active', False)
                and self._callback_active
            )

            if not stream_was_running:
                # No live stream — create one. This is the slow path (~50-300ms
                # on Windows). We close any stale stream first.
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
                self._preroll.clear()
                self._callback_active = True
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype='int16',
                    callback=self._callback,
                    blocksize=1024,
                )
                self._stream.start()
                # Wait briefly for the stream to actually start producing
                # samples — Windows InputStream.start() returns before audio
                # begins flowing. Without this, even the pre-roll is empty.
                import time as _t
                deadline = _t.time() + 0.5
                while not self._preroll and _t.time() < deadline:
                    _t.sleep(0.01)

            # Splice the pre-roll into the recording buffer FIRST so the
            # first syllable isn't lost. Snapshot via list() to avoid
            # iterating a deque that the callback is appending to.
            with self._lock:
                self._buffer = list(self._preroll)

            # Now flip the flag — subsequent callback invocations will
            # append to _buffer.
            self.is_recording = True

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes.

        Does NOT close the underlying stream — keeps it alive so the next
        start() is instant (no 50-300ms stream-creation latency on
        Windows). The stream continues filling the pre-roll buffer in the
        background. If we did need to fully release the stream (e.g.
        app shutdown), call shutdown() explicitly.
        """
        # Flip recording off, snapshot the captured buffer.
        with self._stream_lock:
            self.is_recording = False

        with self._lock:
            buf_snapshot = list(self._buffer)
            self._buffer = []

        if not buf_snapshot:
            return b""

        self.recording = np.concatenate(buf_snapshot, axis=0)
        duration = len(self.recording) / self.sample_rate
        rms = np.sqrt(np.mean(self.recording.astype(np.float32) ** 2))
        print(f"Recording stopped ({duration:.2f}s, RMS: {rms:.0f})")

        return self._to_wav_bytes(self.recording)

    def shutdown(self):
        """Fully tear down the audio stream. Called on app exit only.

        Has the watchdog the old stop() had: sounddevice's _stream.stop()
        can wedge for tens of seconds on long recordings or after device
        hot-swap; we abandon the stream after 1.5s rather than block the
        shutdown path.
        """
        with self._stream_lock:
            self._callback_active = False
            self.is_recording = False
            stream = self._stream
            self._stream = None

        if stream is None:
            return

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
            print("audio.shutdown: stream.stop() did not return in 1.5s — abandoning")

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
