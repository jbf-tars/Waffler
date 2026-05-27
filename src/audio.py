"""Audio recording module using sounddevice — continuous stream.

CFFI lifecycle (v3.14.14)
-------------------------
sounddevice wires the Python callback to PortAudio via a CFFI closure. The
closure's lifetime is tied to the ``InputStream`` Python object. CoreAudio's
HAL I/O thread can still be mid-callback for a brief window after
``stream.stop()`` returns — if the ``InputStream`` is GC'd or the bound
callback method is dropped during that window, the next callback fires into
freed memory and Python's CFFI bridge calls ``_Py_FatalErrorFunc`` →
``abort()`` → ``EXC_CRASH/SIGABRT``. That's the crash signature reported in
``crash.log`` (no Python frame on the faulting thread, ``convert_to_object``
→ ``general_invoke_callback`` → ``ffi_closure_SYSV`` →
``AdaptingInputOnlyProcess`` → CoreAudio HAL).

The fix is a strict teardown sequence that gives the HAL thread time to
drain BEFORE we release the resources it might still touch:

  1. ``_callback_active = False``   — Python-level guard: even if the HAL
                                      thread sneaks in another callback,
                                      it returns immediately.
  2. ``stream.stop()``              — PortAudio: stop dispatching new audio.
  3. ``time.sleep(0.1)``            — paranoia drain — wait for any
                                      in-flight HAL callback to complete.
  4. ``stream.close()``             — PortAudio: release C-level resources
                                      and the CFFI closure.
  5. Drop the Python reference last  — keeps the bound method alive
                                      through steps 2-4.

We also cache the bound ``_callback`` method on ``__init__`` so there is
always a single, stable Python object backing every InputStream's callback.
That makes step 5 deterministic — the bound method survives until the
``AudioRecorder`` itself is destroyed.
"""

import sounddevice as sd
import numpy as np
import io
import wave
import threading
import time
from collections import deque
from typing import Optional


# Pre-roll window in milliseconds. When the user presses the hotkey, we splice
# this much audio captured BEFORE the press into the recording, so the first
# 1-2 syllables aren't clipped if they started speaking just before pressing.
_PREROLL_MS = 500

# Post-roll window: how long to keep recording AFTER the user releases the
# hotkey, so the final syllable / word isn't clipped.
_POSTROLL_MS = 150

# HAL drain window. After ``stream.stop()`` returns, give CoreAudio's I/O
# thread this long to settle before we ``close()`` the stream and drop the
# Python reference. Empirically 100ms is more than enough — a single HAL
# buffer cycle at 16kHz/1024-frames is ~64ms.
_HAL_DRAIN_S = 0.1


# Process-wide lock that serialises InputStream creation and teardown across
# ALL ``AudioRecorder`` instances. The wizard → pipeline handoff bug
# (v3.14.15) was: the wizard's ``AudioRecorder`` was dropped without proper
# teardown, then immediately ``WafflerPipeline.__init__`` created a new
# ``AudioRecorder`` and called ``start_monitoring()`` on it. Both
# InputStreams overlapped briefly; the wizard's CFFI closure was GC'd while
# CoreAudio's HAL thread was still mid-callback for the old stream →
# ``SIGSEGV / EXC_BAD_ACCESS at 0x400`` in ``pythonify_c_value``.
#
# Holding ``_STREAM_LOCK`` for the entire stop → drain → close → drop-ref
# sequence makes the wait-for-drain part transitive across instances: the
# next stream creation simply blocks until the previous one is fully torn
# down. The lock window is ~100ms — invisible to users in normal use, and
# exactly what we need at the wizard→pipeline transition.
_STREAM_LOCK = threading.Lock()


class AudioRecorder:
    """Records audio using a continuous sounddevice stream.

    The stream stays alive for the lifetime of the recorder (created once
    on first ``start()``, reused for every subsequent recording) so we don't
    pay the 50-300ms stream-creation latency on every hotkey press.
    """

    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.recording: Optional[np.ndarray] = None
        self.is_recording = False
        self.is_paused = False
        self._buffer = []
        self._paused_buffer = []
        self._stream = None
        self._lock = threading.Lock()
        self._stream_lock = threading.RLock()  # reentrant — teardown may be
                                               # called while we already hold
                                               # the lock for start/stop.
        self._last_rms: float = 0.0
        self._callback_active = False

        # CRITICAL: cache the bound callback method ONCE. Every ``self._callback``
        # access creates a new bound-method object; we want exactly one to
        # exist so PortAudio's CFFI closure always points at the same Python
        # object for the recorder's lifetime. Without this caching, the
        # bound method handed to ``sd.InputStream`` could be GC'd if the
        # InputStream itself were GC'd, leaving a dangling CFFI closure.
        self._callback_bound = self._callback

        # Pre-roll ring buffer.
        chunks_per_preroll = max(1, int(_PREROLL_MS / 1000 * sample_rate / 1024) + 1)
        self._preroll = deque(maxlen=chunks_per_preroll)

    # ── Callback (called on CoreAudio HAL thread) ───────────────────────

    def _callback(self, indata, frames, time_info, status):
        """Called continuously by PortAudio while the stream is alive.

        ``_callback_active`` is the FIRST gate — it lets us drop callbacks
        immediately during teardown without touching numpy / deque / locks
        that another thread might be tearing down too.
        """
        if not self._callback_active:
            return
        try:
            chunk = indata.copy()
            self._preroll.append(chunk)

            if self.is_recording and not self.is_paused:
                with self._lock:
                    self._buffer.append(chunk)
                rms_raw = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
                self._last_rms = min(1.0, rms_raw / 800.0)
            elif self.is_paused:
                self._last_rms = 0.0
        except Exception:
            pass  # Never crash inside the audio callback.

    # ── Public state inspection ─────────────────────────────────────────

    def get_level(self) -> float:
        return self._last_rms

    def get_is_paused(self) -> bool:
        return self.is_paused

    def pause(self):
        self.is_paused = True
        print("Recording paused")

    def resume(self):
        self.is_paused = False
        print("Recording resumed")

    def toggle_pause(self):
        if self.is_paused:
            self.resume()
        else:
            self.pause()

    def print_devices(self):
        print("\nAvailable microphones:")
        default_input = sd.query_devices(kind='input')
        print(f"  Default input: {default_input['name']}")
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                print(f"  [{i}] {d['name']}")
        print()

    # ── Stream lifecycle ────────────────────────────────────────────────

    def _create_stream(self) -> None:
        """Create a fresh InputStream using the cached bound callback.

        Serialised against any concurrent teardown (e.g. wizard → pipeline
        handoff) via ``_STREAM_LOCK`` so we never have two streams' HAL
        threads live in the same C-runtime moment.

        Mic hot-swap fix (v3.14.50)
        ---------------------------
        We force a PortAudio reinitialisation (``sd._terminate(); sd._initialize()``)
        right before creating the InputStream. Without this, PortAudio
        keeps a process-lifetime cache of the default-input-device index
        — so when a user plugs in a wireless mic and switches the
        system default in Settings, ``InputStream(...)`` with no explicit
        ``device=`` still binds to the old (cached) default. The previous
        ``AudioDeviceMonitor`` (v3.14.47) read PortAudio's *same* cached
        view, so it usually missed the change. The reinit costs ~50 ms
        per stream creation, which is invisible to the user, and means
        every press of the hotkey resolves the *current* OS default —
        no app restart needed when the user changes their mic. We do
        this inside ``_STREAM_LOCK`` so it can't race with another
        thread's stream teardown.
        """
        with _STREAM_LOCK:
            # Force PortAudio to re-read the OS-level default input device.
            # Safe to call inside the lock — by definition there's no live
            # stream right now (this function exists to create one).
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                # Reinit failure (extremely rare — would mean PortAudio is
                # in a corrupted state). Fall through to InputStream
                # creation against the cached default and hope it works.
                print(f"[audio] PortAudio reinit failed before stream create: {e}")
            # ``self._callback_active`` is set true BEFORE start() so the
            # very first callback that fires isn't dropped.
            self._callback_active = True
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                callback=self._callback_bound,  # stable bound method
                blocksize=1024,
            )
            self._stream.start()

    def _teardown_stream(self, stream) -> None:
        """Safely tear down an InputStream.

        Holds the Python reference (via the local ``stream`` argument) all
        the way through the stop → drain → close sequence so the bound
        callback can't be GC'd while CoreAudio's HAL thread might still
        invoke it. See the file docstring for the rationale.

        Must be called with ``self._stream`` already reassigned away from
        the stream being torn down (or None) so a concurrent thread won't
        see a half-closed stream.

        The whole stop → drain → close sequence runs inside ``_STREAM_LOCK``
        so any concurrent ``_create_stream`` call (in this instance or a
        different ``AudioRecorder``) blocks until the HAL thread of the
        outgoing stream has fully drained. This is what fixes the
        wizard → pipeline handoff segfault.
        """
        if stream is None:
            return
        with _STREAM_LOCK:
            # 1. Tell our Python-level callback to bail immediately.
            self._callback_active = False
            # 2. Stop dispatching new audio.
            try:
                stream.stop()
            except Exception as e:
                print(f"audio._teardown_stream: stream.stop() failed: {e}")
            # 3. Drain — let any in-flight HAL callback complete. We hold
            # `stream` in this local scope for the entire sleep so the
            # InputStream (and its bound-method callback) can't be GC'd.
            try:
                time.sleep(_HAL_DRAIN_S)
            except Exception:
                pass
            # 4. Release C-level resources and the CFFI closure.
            try:
                stream.close()
            except Exception as e:
                print(f"audio._teardown_stream: stream.close() failed: {e}")
            # 5. ``stream`` goes out of scope when this function returns —
            # the InputStream is GC-eligible only after the drain window
            # AND after close() has released the CFFI closure.

    def start(self):
        """Begin recording.

        Reuses the long-lived monitor stream when it's still healthy; if
        the stream isn't running (first call, or after ``stop_monitoring``),
        spins up a fresh one via the safe lifecycle helpers.

        Mic hot-swap belt-and-suspenders (v3.14.50)
        -------------------------------------------
        If it's been more than 30 s since the last press, we recycle the
        stream — i.e. force the slow path with its fresh PortAudio reinit.
        This is the moment a user is most likely to have switched mics
        (plugged in a wireless headset, gone to System Settings, etc.),
        and PortAudio's cached default doesn't refresh on its own.
        Recycling once-per-session-burst loses only ~50 ms of pre-roll
        for that single press — invisible — and means the user no longer
        has to restart the app to pick up a new default device.
        """
        with self._stream_lock:
            self._buffer = []

            now = time.time()
            time_since_last_press = now - getattr(self, "_last_press_time", 0.0)
            self._last_press_time = now
            force_recycle = time_since_last_press > 30.0

            stream_was_running = (
                self._stream is not None
                and getattr(self._stream, 'active', False)
                and self._callback_active
                and not force_recycle
            )

            if force_recycle and self._stream is not None:
                print(f"[audio] recycling stream after {time_since_last_press:.0f}s idle to pick up any mic change")

            if not stream_was_running:
                # Slow path. If there's a stale stream hanging around,
                # tear it down properly before creating the new one. We
                # detach it from ``self._stream`` FIRST so concurrent
                # readers don't see a half-closed object.
                stale = self._stream
                self._stream = None
                if stale is not None:
                    self._teardown_stream(stale)
                self._preroll.clear()
                self._create_stream()
                # Cold-start warm-up. A freshly (re)built stream can hand back
                # zero-filled buffers for a while before real audio flows —
                # most painfully on Bluetooth mics: AirPods negotiate their
                # HFP input link over ~1-2s. The old code only waited for the
                # pre-roll to be NON-EMPTY, which a silent/dead stream satisfies
                # instantly — so the first press right after popping in AirPods
                # recorded pure silence, got flagged as a dead stream, and the
                # dictation was lost (repeating for 2-3 presses until the link
                # settled — see the 10:20 AirPods burst in app.log). Now we wait
                # for LIVE audio (non-zero RMS) before proceeding, up to ~2s.
                # A normal warm built-in mic still returns in ~100 ms because
                # its pre-roll fills with live samples immediately, so this only
                # adds latency on the rare cold start that actually needs it.
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    if self._preroll:
                        try:
                            recent = np.concatenate(list(self._preroll), axis=0)
                            rms = float(np.sqrt(np.mean(recent.astype(np.float32) ** 2)))
                            if rms > 1.0:
                                break  # real audio is flowing — safe to record
                        except Exception:
                            break
                    time.sleep(0.02)

            # Splice pre-roll into the recording buffer FIRST so the
            # first syllable isn't lost.
            with self._lock:
                self._buffer = list(self._preroll)
            self.is_recording = True

    def stop(self) -> bytes:
        """Stop the *recording* (not the stream) and return WAV bytes.

        The stream keeps running so the next ``start()`` is instant. The
        post-roll trick: we sleep BEFORE flipping ``is_recording=False``
        so the callback continues to append trailing audio chunks during
        the wait, capturing the last word.
        """
        time.sleep(_POSTROLL_MS / 1000.0)

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

        sounddevice's ``stream.stop()`` can wedge for tens of seconds on a
        long recording or after a device hot-swap; we abandon the stream
        after 1.5s rather than block the shutdown path.
        """
        with self._stream_lock:
            self.is_recording = False
            stream = self._stream
            self._stream = None

        if stream is None:
            return

        def _close():
            self._teardown_stream(stream)

        t = threading.Thread(target=_close, daemon=True, name="AudioStreamClose")
        t.start()
        t.join(timeout=2.0)  # 2.0 = 1.5 watchdog + 0.1 drain headroom + slack
        if t.is_alive():
            print("audio.shutdown: teardown did not return in 2s — abandoning")

    def start_monitoring(self):
        """Start audio stream for level monitoring only (no recording)."""
        with self._stream_lock:
            if self._stream and self._stream.active and self._callback_active:
                return  # Already monitoring/recording
            stale = self._stream
            self._stream = None
            if stale is not None:
                self._teardown_stream(stale)
            self.is_recording = False
            self._buffer = []
            self._create_stream()

    def stop_monitoring(self):
        """Stop audio monitoring stream — used when switching devices etc."""
        with self._stream_lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            self._teardown_stream(stream)
        self._last_rms = 0.0

    def force_rebuild(self):
        """Hard-tear-down the current stream so the next start() builds fresh.

        Called when the pipeline detects a *dead* stream — one delivering
        zero-filled buffers (overall RMS ≈ 0). This happens after the Mac
        sleeps/wakes or a mic is hot-swapped: PortAudio's stream stays
        ``.active`` and keeps firing the callback, but CoreAudio hands back
        pure silence. The 30 s idle-recycle and PortAudio reinit don't
        always recover it, so we make the detection *reactive*: once we've
        captured a recording that's pure digital silence, treat the stream
        as poisoned and drop it. The next press takes the slow path in
        start() → _create_stream() with a fresh sd._terminate()/_initialize()
        and a brand-new InputStream, which reliably re-acquires the device.
        """
        with self._stream_lock:
            stream = self._stream
            self._stream = None
            # Force start()'s slow path next time, regardless of timing.
            self._last_press_time = 0.0
        if stream is not None:
            self._teardown_stream(stream)
        self._last_rms = 0.0

    def record_chunk(self, duration: float = 0.1):
        """No-op — continuous stream handles recording automatically."""
        pass

    # ── Encoding ───────────────────────────────────────────────────────

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
