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
import math
import sys
import wave
import threading
import time
from collections import deque
from typing import Optional

try:
    import audio_guard
except ImportError:  # imported as src.audio
    from src import audio_guard


# Pre-roll window in milliseconds. When the user presses the hotkey, we splice
# this much audio captured BEFORE the press into the recording, so the first
# 1-2 syllables aren't clipped if they started speaking just before pressing.
_PREROLL_MS = 500

# Post-roll window: how long to keep recording AFTER the user releases the
# hotkey, so the final syllable / word isn't clipped.
_POSTROLL_MS = 150

# Settings' microphone meter: a stream that has delivered nothing for this
# long is not shown as live.
_LEVEL_STALE_S = 0.5

# A microphone with a noise gate (Windows Voice Focus, a headset's own DSP, a
# virtual filter) sends exact zeros whenever nobody is speaking, which looks
# like a dead input. If it is ever seen to send sound after at least this much
# zero (the gate opening), silence on it is a pause, not a failure, and the
# early silence check (src/mic_watch.py) leaves it alone.
_GATE_MIN_ZERO_S = 0.5

# Sound cues (src/sounds.py) play through the speakers while this microphone
# is open, so the recorder can be told to silence the moments a cue sounds in
# (``deafen_for``): the cue is then in neither the recording nor the half-
# second of audio kept from before the next one. A cue cannot be heard in the
# first instant after it is started; and no request is allowed to silence the
# microphone for longer than a cue could ever last.
_CUE_LEAD_S = 0.01
_MAX_DEAF_S = 1.0


def cue_mask_range(frames: int, sample_rate: int, chunk_end: float,
                   deaf_from: float, deaf_until: float):
    """Which samples of a chunk fall in the window ``[deaf_from, deaf_until)``:
    ``(first, end)`` as slice bounds, or None when none do. ``chunk_end`` is
    the time the chunk arrived, taken as the time of its last sample."""
    start = chunk_end - frames / sample_rate
    if chunk_end <= deaf_from or start >= deaf_until:
        return None
    first = max(0, int(math.ceil((deaf_from - start) * sample_rate)))
    end = min(frames, int(math.ceil((deaf_until - start) * sample_rate)))
    return (first, end) if end > first else None


def rms_level(chunk) -> float:
    """A chunk of int16 samples as a level from 0 to 1: the RMS over 800,
    the scale the recording overlay has always used."""
    rms = float(np.sqrt(np.mean(np.asarray(chunk).astype(np.float32) ** 2)))
    return min(1.0, rms / 800.0)


# HAL drain window. After ``stream.stop()`` returns, give CoreAudio's I/O
# thread this long to settle before we ``close()`` the stream and drop the
# Python reference. Empirically 100ms is more than enough — a single HAL
# buffer cycle at 16kHz/1024-frames is ~64ms.
_HAL_DRAIN_S = 0.1


# ── Bounds on every call into PortAudio and CoreAudio (src/audio_guard.py) ──
# Each of these calls takes milliseconds when audio is healthy, and none may
# take more than its bound: a call that does not come back is a CoreAudio
# deadlock (a capture of one showed stream.stop() and CoreAudio's own I/O thread
# waiting on each other), which nothing in the process can end. The call is then
# abandoned on its thread, the locks are let go, and the recorder says so
# (``on_wedge``). Every call that can wedge runs under ``_stream_lock`` and
# ``_STREAM_LOCK``, so a bound is also how long a stop(), an Esc or a quit can
# wait behind it: kept small, but well above what a healthy call takes.
_STOP_TIMEOUT_S = 3.0        # Pa_StopStream: a HAL buffer cycle (~64 ms) when healthy
_CLOSE_TIMEOUT_S = 3.0       # Pa_CloseStream: releases the stream, milliseconds
_TERMINATE_TIMEOUT_S = 3.0   # Pa_Terminate: milliseconds with no stream open
_INITIALIZE_TIMEOUT_S = 5.0  # Pa_Initialize lists every device, ~50 ms (more while one is connecting)
_CREATE_TIMEOUT_S = 5.0      # open and start a stream: 50-300 ms (a Bluetooth mic can take seconds)
_QUERY_TIMEOUT_S = 3.0       # asking PortAudio for its device list: microseconds, it is cached
# How long stop() waits for the recorder's stream lock before reading the
# recording without it: past any bounded hold (stop 3 + drain + close 3), so only
# something that has stopped answering reaches it.
_STOP_LOCK_WAIT_S = 4.0
_SHUTDOWN_LOCK_WAIT_S = 2.0  # quitting never waits longer than this for the lock
# Reading the recorded audio for a rescue: the lock is the callback's, held for
# one list append.
_SALVAGE_LOCK_WAIT_S = 0.5


# A system with no live list of its devices for the device monitor to read (every
# one but a Mac, where src/mac_audio_devices.py asks CoreAudio) learns of a
# microphone plugged in only when PortAudio is started again, which a new stream
# does. There, a press after this much quiet still opens a new stream, as it
# always has, so a device added meanwhile is found. A Mac is told by the monitor
# (``note_devices_changed``) and does not: the CoreAudio deadlock was met in that
# rebuild.
_IDLE_RECYCLE_S = 30.0


def _recycles_when_idle() -> bool:
    return sys.platform != "darwin"


class AudioWedged(RuntimeError):
    """A call into PortAudio or CoreAudio did not come back, so the stream
    could not be made. The message is for the log, never for the screen."""


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


# ── Bluetooth-mic avoidance (v3.14.64) ──────────────────────────────────────
# Opening an AirPods/Bluetooth *microphone* forces the headset out of high-
# quality stereo (A2DP) into call-quality mono (HFP), which wrecks any music
# the user is playing — for as long as the mic stays open. Waffler holds a
# continuous monitor stream, so on Bluetooth that degradation was permanent.
# Per the user's choice, when the OS default input is a Bluetooth mic we record
# from a non-Bluetooth mic (the built-in one) instead. Waffler then never opens
# the AirPods mic, so they stay output-only in A2DP and music quality is kept.
_BT_INPUT_MARKERS = (
    "airpods", "bluetooth", "beats", "buds", "headset",
    "jabra", "bose", "sony wh", "sony wf", "galaxy buds",
)
_BUILTIN_INPUT_MARKERS = ("macbook", "built-in", "imac", "mac mini", "mac studio")


def _name_is_bluetooth(name: str) -> bool:
    n = (name or "").lower()
    return any(m in n for m in _BT_INPUT_MARKERS)


def snapshot_devices(timeout: float = 1.0, locked: bool = False):
    """PortAudio's list of devices as it is now: ``(devices, host APIs, the
    default input's index, the default host API's index)``, or None when it
    could not be read in time.

    Taken under ``_STREAM_LOCK``. Making a stream starts PortAudio again
    (``_create_stream``), and a query that runs in the middle of that is a
    query of a library that is being torn down. The wait is bounded: a stream
    being made or closed holds the lock for a fraction of a second, and a
    caller that cannot wait (the Settings page, the device monitor) is better
    served by "not now" than by a hang.

    ``locked=True`` is for the one caller that already holds the lock: the
    recorder's own choice of microphone, made inside ``_create_stream`` right
    after PortAudio was started again. The lock is not reentrant, so asking
    for it there would only wait out the timeout.
    """
    if not locked and not _STREAM_LOCK.acquire(timeout=timeout):
        return None
    try:
        # One bounded call for all the queries: they ask PortAudio, and a
        # PortAudio that has stopped answering must not hold the lock (or the
        # device monitor's thread) for ever.
        done, value = audio_guard.run_bounded(_read_device_lists, _QUERY_TIMEOUT_S, "query_devices")
        if done and not isinstance(value, BaseException):
            return value
        if not done and value is audio_guard.WEDGED:
            audio_guard.report_wedge("query_devices", _QUERY_TIMEOUT_S)
        return None
    finally:
        if not locked:
            _STREAM_LOCK.release()


def _read_device_lists():
    """What snapshot_devices reads from PortAudio. Runs on a bounded thread."""
    infos = list(sd.query_devices())
    try:
        hostapis = list(sd.query_hostapis())
    except Exception:
        hostapis = []
    default_index = None
    try:
        default = sd.query_devices(kind="input")
        default_index = default.get("index")
        if default_index is None:
            name = default.get("name")
            default_index = next((i for i, d in enumerate(infos) if d.get("name") == name), None)
    except Exception:
        pass
    try:
        primary = int(sd.default.hostapi)
    except Exception:
        primary = None
    return infos, hostapis, default_index, primary


def _resolve_input_device(selected_index=None):
    """Return the input device index to record from, or None to use PortAudio's
    current default.

    ``selected_index`` is the microphone the user explicitly chose in Settings.
    It wins outright: if someone has picked a device, recording from a different
    one is a silent failure of the worst kind, because the app reports success
    while capturing the wrong source (or silence). Before v3.14.91 the selection
    was stored but never reached stream creation, so the picker did nothing at
    all.

    With no explicit selection the normal case returns None (use the default).
    Only when the default input is a Bluetooth mic do we override: we pick a
    non-Bluetooth input (preferring the built-in mic) so opening the stream
    doesn't drag the user's AirPods into call-quality HFP mode. Fully
    best-effort — any failure returns None and the default is used, so this can
    never block recording.
    """
    if selected_index is not None:
        try:
            info = sd.query_devices(selected_index)
            if info.get("max_input_channels", 0) > 0:
                return int(selected_index)
            print(f"[audio] selected device {selected_index} has no input "
                  f"channels — falling back to the default")
        except Exception as e:
            print(f"[audio] selected device {selected_index} unavailable "
                  f"({e}) — falling back to the default")
    try:
        default_in = sd.query_devices(kind="input")
        if not _name_is_bluetooth(default_in.get("name", "")):
            return None  # built-in / wired default — nothing to avoid

        devices = sd.query_devices()
        builtin_idx = None
        first_nonbt_idx = None
        for idx, d in enumerate(devices):
            if d.get("max_input_channels", 0) <= 0:
                continue
            dn = (d.get("name") or "").lower()
            if _name_is_bluetooth(dn):
                continue
            if first_nonbt_idx is None:
                first_nonbt_idx = idx
            if any(m in dn for m in _BUILTIN_INPUT_MARKERS):
                builtin_idx = idx
                break

        chosen = builtin_idx if builtin_idx is not None else first_nonbt_idx
        if chosen is not None:
            print(
                f"[audio] default input '{default_in.get('name')}' is Bluetooth — "
                f"recording from '{sd.query_devices(chosen)['name']}' instead so "
                f"AirPods stay in high-quality (A2DP) mode"
            )
        return chosen
    except Exception as e:
        print(f"[audio] input-device resolve failed (using default): {e}")
        return None


class AudioRecorder:
    """Records audio using a continuous sounddevice stream.

    The stream stays alive for the lifetime of the recorder (created once
    on first ``start()``, reused for every subsequent recording) so we don't
    pay the 50-300ms stream-creation latency on every hotkey press.
    """

    def __init__(self, sample_rate: int = 16000, channels: int = 1,
                 device_index=None, device_resolver=None):
        self.sample_rate = sample_rate
        self.channels = channels
        # Microphone the user picked in Settings. None means "use the OS
        # default". Applied on the NEXT stream creation, which is every hotkey
        # press, so a change takes effect without a restart and never mutates a
        # live stream mid-recording.
        self._device_index = device_index
        # Which microphone to open when nothing is pinned by index: a function
        # asked at the moment a stream is made, right after PortAudio has been
        # started again, so the index it answers is the one that list uses
        # (src/mic_ranking.py). None, or an answer of None, is "follow the
        # system" (the default input, minus the Bluetooth headset).
        self._device_resolver = device_resolver
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
        # The stream the callback is armed for. Tearing down an older stream
        # (one that was detached before a newer one was made) must not switch
        # off the newer stream's callback, and the one stable bound callback
        # cannot say which stream called it, so the stream that is armed is
        # remembered here and only its teardown disarms (see _teardown_stream).
        self._armed = None
        # Starts that have begun and not ended (a cold start waits for live
        # audio outside the lock), so an idle re-open can tell that a take is
        # about to begin on the stream it would tear down.
        self._starting = 0
        # Settings' microphone meter (input_level): when the stream last
        # delivered audio, and which microphone choice it was opened with.
        self._last_chunk_at = 0.0
        self._stream_choice = device_index
        # Capture health (S12, src/mic_watch.py). ``_last_signal_at``: when a
        # chunk last held any sample other than zero. A microphone that is
        # live, however quiet the room, never delivers a block of exact zeros
        # for long, so this is the one number that says "the input is dead"
        # without calling a quiet voice silence. ``_take_started_at``: when the
        # recording began, so the time before it is not counted.
        self._last_signal_at = 0.0
        self._take_started_at = 0.0
        self._reopening = False
        # When the current run of all-zero chunks began (None: the last chunk
        # had sound), and whether this stream has been seen to send sound after
        # a long zero run (a noise-gated microphone: see _GATE_MIN_ZERO_S).
        self._zero_since = None
        self._gated = False
        # The name of the microphone the open stream is on: for the closed-lid
        # tip, and for knowing when that device is the one that left.
        self._opened_device = None
        # A change of microphone preference (Settings, or a device coming or
        # going) bumps ``_pref_version``; a stream made before it is stale, and
        # the next start() makes a new one instead of reusing it.
        self._pref_version = 0
        self._stream_pref_version = 0
        # A change in the devices themselves (the system's default input
        # changed, the microphone the stream is on left, the Mac woke up)
        # bumps ``_devices_version``; like the preference above, a stream made
        # before it is stale. Nothing else makes start() open a new stream: the
        # time since the last press used to, which put every press after half a
        # minute's quiet through a stop and a restart of PortAudio, the calls
        # that wedged CoreAudio.
        self._devices_version = 0
        self._stream_devices_version = 0
        # When a call into PortAudio or CoreAudio does not come back (see
        # src/audio_guard.py): that the recorder is wedged, which call, and when.
        # ``on_wedge(op)`` is told once, on its own thread. ``log`` is where the
        # recorder's own lines go (the app points it at app.log).
        self._wedged = False
        self._wedge_op = None
        self._wedge_at = None
        self._wedge_lock = threading.Lock()
        self.on_wedge = None
        self.log = print
        # Recording ownership. is_recording and _buffer are shared, so with
        # overlapping presses one dictation could damage another: an old stop()
        # sleeping through its post-roll would wake to find a NEW recording in
        # progress and drain its buffer, and a slow cold-start warm-up could
        # finish after a stop and switch capture back on. Every start() takes a
        # session number and only finalises if it still holds it; every stop()
        # only touches shared state if it still owns the session it began with.
        self._session = 0
        # Capture health for THIS recording. PortAudio reports overruns via the
        # callback's `status`, and the callback swallows every exception so it
        # can never crash the audio thread. Both were invisible, so a recording
        # that lost chunks looked identical to a clean one and went on to be
        # transcribed as if nothing had happened.
        self._capture_overflows = 0
        self._capture_errors = 0
        # Windows (start, end) on ``_clock`` that sound cues are playing in;
        # audio arriving in one is replaced by silence (see ``deafen_for``).
        self._clock = time.monotonic
        self._deaf = ()

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
        if status:
            # Input overflow means PortAudio dropped samples before we saw
            # them: that audio is gone, and the recording is incomplete.
            self._capture_overflows += 1
        try:
            chunk = self._silence_cue(indata.copy())
            self._preroll.append(chunk)
            now_m = time.monotonic()
            self._last_chunk_at = now_m
            # On what the device sent, before a sound cue was blanked out of
            # it: a cue's silence is ours, not the microphone's.
            if indata.any():
                self._last_signal_at = now_m
                if self._zero_since is not None:
                    if now_m - self._zero_since >= _GATE_MIN_ZERO_S:
                        self._gated = True
                    self._zero_since = None
            elif self._zero_since is None:
                self._zero_since = now_m

            # Test and append under the SAME lock stop() uses, so an admitted
            # callback either makes it into the returned WAV or is dropped. It
            # can no longer append to a buffer that has just been cleared.
            with self._lock:
                _captured = self.is_recording and not self.is_paused
                if _captured:
                    self._buffer.append(chunk)
            # RMS is computed outside the lock to keep the held region to a
            # single list append on the real-time audio thread.
            if _captured:
                rms_raw = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                self._last_rms = min(1.0, rms_raw / 800.0)
            elif self.is_paused:
                self._last_rms = 0.0
        except Exception:
            # Never crash inside the audio callback, but do not pretend it did
            # not happen: a failed append is a missing chunk.
            self._capture_errors += 1

    def _silence_cue(self, chunk):
        """``chunk`` with any part that falls in a cue's window set to silence.
        Runs for every chunk, so it does nothing when no cue is armed."""
        windows = self._deaf
        if not windows:
            return chunk
        end = self._clock()
        live = False
        for a, b in windows:
            if b > end - len(chunk) / self.sample_rate:
                live = True
            span = cue_mask_range(len(chunk), self.sample_rate, end, a, b)
            if span is not None:
                chunk[span[0]:span[1]] = 0
        if not live and self._deaf is windows:
            # Only when nothing was added since: deafen_for() runs on another
            # thread, and a cue armed while this chunk was read must stay armed.
            self._deaf = ()
        return chunk

    def deafen_for(self, seconds: float, lead_s: float = _CUE_LEAD_S) -> None:
        """Replace the audio that arrives over the next ``seconds`` with
        silence, before it reaches the recording or the pre-roll. Called just
        before a sound cue starts, so the speakers' sound is never in what is
        sent to speech to text. Overlapping requests add up. Never raises."""
        try:
            seconds = min(float(seconds), _MAX_DEAF_S)
            if not seconds > 0:
                return
            now = self._clock()
            window = (now + lead_s, now + seconds)
            if window[1] <= window[0]:
                return
            self._deaf = tuple(w for w in self._deaf if w[1] > now) + (window,)
        except (TypeError, ValueError):
            return

    # ── Public state inspection ─────────────────────────────────────────

    def get_level(self) -> float:
        return self._last_rms

    def input_level(self) -> dict:
        """The microphone's level right now, recording or not: Settings,
        General shows it as a meter by the microphone's name.

        Read from the newest chunk the always-open stream put in the
        pre-roll, on the same scale as get_level(). Nothing is kept or sent.
        ``live`` is False when no stream is running or it has delivered
        nothing for half a second. ``current`` is False when Settings picked
        another microphone since the stream opened: set_device() applies it
        on the next dictation, so this level is still the old microphone's.
        """
        stream = self._stream
        live = bool(stream is not None and getattr(stream, "active", False)
                    and self._callback_active
                    and time.monotonic() - self._last_chunk_at < _LEVEL_STALE_S)
        level = 0.0
        if live:
            try:
                level = rms_level(self._preroll[-1])
            except IndexError:
                live = False
        return {"live": live, "level": round(level, 3),
                "current": (self._stream_choice == self._device_index
                            and self._stream_pref_version == self._pref_version)}

    def health(self, now=None) -> dict:
        """How the input is doing, for the check that runs while a take is
        recorded (src/mic_watch.py). Cheap, and safe to call from any thread.

        ``quiet_s``   seconds since a chunk last held any sample but zero: the
                      stream may be delivering blocks of digital silence, or
                      nothing at all.
        ``stall_s``   seconds since the last chunk of any kind arrived.
        ``stream_ok`` the stream exists, PortAudio says it is active, and the
                      callback is armed.
        ``reopening`` the stream is being opened again right now; nothing else
                      is meaningful until it is done.
        ``gated``     this stream has sent sound after a long run of exact
                      zeros: a noise gate, so silence on it is a pause.
        Both times count from the start of the take at the earliest.
        """
        now = time.monotonic() if now is None else now
        stream = self._stream
        try:
            ok = bool(stream is not None and getattr(stream, "active", False)
                      and self._callback_active)
        except Exception:
            ok = False
        started = self._take_started_at
        return {
            "recording": bool(self.is_recording),
            "reopening": bool(self._reopening),
            "stream_ok": ok,
            "quiet_s": max(0.0, now - max(self._last_signal_at, started)),
            "stall_s": max(0.0, now - max(self._last_chunk_at, started)),
            "gated": bool(self._gated),
            "device": self._opened_device,
        }

    def opened_device(self) -> Optional[str]:
        """The name of the microphone the stream is on, or None."""
        return self._opened_device

    def note_preference_changed(self) -> None:
        """The choice of microphone may be different now (the ranking was
        edited, a device came or went). The stream that is open is stale: the
        next start() makes a new one. Does not touch a stream, so it is safe
        in the middle of a take."""
        self._pref_version += 1

    def note_devices_changed(self) -> None:
        """The devices changed under the stream (the system's default input,
        the microphone it is on leaving, the Mac waking): it may be on the wrong
        one, or dead. The next start() opens a new stream, once. Does not touch
        a stream, so it is safe in the middle of a take, and it is the only
        thing besides a change of preference that makes start() do so."""
        self._devices_version += 1

    # ── A call into PortAudio or CoreAudio that does not come back ──────

    def wedge_info(self):
        """``(call, wall time)`` of the call that did not come back, or None."""
        if not self._wedged:
            return None
        return self._wedge_op, self._wedge_at

    def is_stuck(self) -> bool:
        """Still wedged: a call into PortAudio or CoreAudio that was given up on
        has not come back (src/audio_guard.py), or the recorder's stream lock is
        held by something that does not let go. False for a recorder that is
        healthy, and for one whose slow call has returned since. Touches no
        stream; the lock is only asked for, briefly, when the recorder was
        flagged for it."""
        if audio_guard.wedged_op() is not None:
            return True
        if not self._wedged:
            return False
        got = self._stream_lock.acquire(timeout=0.25)
        if got:
            self._stream_lock.release()
        return not got

    def clear_wedge(self) -> None:
        """The wedge turned out to be a call that was only slow, and it has
        returned: forget it, so the next one is logged and told again."""
        with self._wedge_lock:
            self._wedged = False
            self._wedge_op = None
            self._wedge_at = None
        audio_guard.forget_report()

    def _say(self, line: str) -> None:
        try:
            self.log(line)
        except Exception:
            pass

    def _note_wedge(self, op: str, timeout_s: float) -> None:
        """CoreAudio did not answer ``op`` within ``timeout_s``. Said once for
        the process (src/audio_guard.py): the one line in the log, and
        ``on_wedge(op)`` told once, on a thread of its own because the caller
        is holding a stream lock."""
        with self._wedge_lock:
            first = not self._wedged
            if first:
                self._wedged = True
                self._wedge_op = op
                self._wedge_at = time.time()
        if not first:
            return
        audio_guard.report_wedge(op, timeout_s, notify=False, log=self.log)
        callback = self.on_wedge
        if callback is not None:
            threading.Thread(target=self._tell_wedge, args=(callback, op), daemon=True,
                             name="AudioWedgeNotify").start()

    def _tell_wedge(self, callback, op: str) -> None:
        try:
            callback(op)
        except Exception as e:
            self._say(f"[audio] the wedge handler failed ({type(e).__name__})")

    def _bounded(self, op: str, fn, timeout_s: float, keep=None):
        """``fn()`` under a bound (src/audio_guard.py): ``(True, result)``, or
        ``(True, exception)`` if it raised, or ``(False, why)`` if it did not
        come back. A call that did not come back (or was not made, because an
        earlier one is still stuck) marks this recorder wedged."""
        done, value = audio_guard.run_bounded(fn, timeout_s, op, keep=keep)
        if not done:
            wedged_op = op if value is audio_guard.WEDGED else (audio_guard.wedged_op() or op)
            self._note_wedge(wedged_op, timeout_s)
        return done, value

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
        every stream resolves the *current* OS default — no app restart
        needed when the user changes their mic. We do this inside
        ``_STREAM_LOCK`` so it can't race with another thread's stream
        teardown.

        A stream is made when none is running, and when something says the
        devices changed (``note_devices_changed``, a change of preference), no
        longer on every press after half a minute: that put the PortAudio
        restart and the stream's stop on the hotkey's path every time, which
        is where a CoreAudio deadlock was met. Each call that can wedge is
        bounded (``_bounded``); the lock is let go and ``AudioWedged`` raised
        when one does not return.
        """
        with _STREAM_LOCK:
            # Force PortAudio to re-read the OS-level default input device.
            # Safe to call inside the lock — by definition there's no live
            # stream right now (this function exists to create one).
            # Two separate steps. After one failed start (coreaudiod restarting
            # after a wake) every later stop raises "not initialised"; with
            # both in one try the start was then never tried again and the
            # microphone stayed dead until a restart of the app.
            # Each call is bounded (src/audio_guard.py): one that does not come
            # back is a CoreAudio deadlock, and the lock is let go (the
            # AudioWedged raised here unwinds through ``with``) instead of
            # being held for as long as it lasts.
            done, value = self._bounded("sd._terminate", lambda: sd._terminate(),
                                        _TERMINATE_TIMEOUT_S)
            if not done:
                raise AudioWedged("PortAudio did not stop")
            if isinstance(value, Exception):
                print(f"[audio] PortAudio stop failed before stream create: {value}")
            done, value = self._bounded("sd._initialize", lambda: sd._initialize(),
                                        _INITIALIZE_TIMEOUT_S)
            if not done:
                raise AudioWedged("PortAudio did not start")
            if isinstance(value, Exception):
                # Reinit failure (coreaudiod restarting, or PortAudio in a bad
                # state). Fall through to InputStream creation against what
                # there is; the next stream tries the restart again.
                print(f"[audio] PortAudio reinit failed before stream create: {value}")
            # ``self._callback_active`` is set true BEFORE start() so the
            # very first callback that fires isn't dropped.
            self._callback_active = True
            # Avoid the AirPods mic when it's the default input (keeps music in
            # A2DP). Resolved AFTER the PortAudio reinit above so it sees the
            # current device list. None ⇒ PortAudio default (the normal case).
            # Both versions are read BEFORE the choice is made: a change that
            # arrives while the resolver runs is then newer than this stream,
            # and the next start() opens it again.
            _pref_before = self._pref_version
            _devices_before = self._devices_version

            def _choose():
                device = self._open_device()
                return device, self._device_name(device)

            done, value = self._bounded("choose the microphone", _choose, _QUERY_TIMEOUT_S)
            if not done:
                self._callback_active = False
                raise AudioWedged("PortAudio did not list the microphones")
            if isinstance(value, BaseException):
                self._callback_active = False
                raise value
            _input_device, _opened_name = value
            self._zero_since, self._gated = None, False       # a new stream is a new device to judge
            self._stream_choice = self._device_index
            self._stream_pref_version = _pref_before
            self._stream_devices_version = _devices_before
            self._armed = None
            made = {"state": "running"}      # running, then done (used) or abandoned (given up on)
            made_lock = threading.Lock()

            def _make():
                stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype='int16',
                    callback=self._callback_bound,  # stable bound method
                    blocksize=1024,
                    device=_input_device,
                )
                made["stream"] = stream
                stream.start()
                with made_lock:
                    late = made["state"] == "abandoned"
                    if not late:
                        made["state"] = "done"
                if late:
                    # The call was only slow, and the recorder has moved on:
                    # nobody owns this stream, and left running it would feed
                    # the one callback a second copy of the audio. End it as a
                    # teardown does (this is the abandoned thread, off to the
                    # side, so nothing waits on it).
                    try:
                        stream.stop()
                        time.sleep(_HAL_DRAIN_S)
                        stream.close()
                    except Exception:
                        pass
                return stream

            # ``made`` is kept for ever if this does not return: the stream it
            # holds (or will hold, if the call finishes late) has a callback
            # CoreAudio may still call.
            done, value = self._bounded("create the stream", _make, _CREATE_TIMEOUT_S, keep=made)
            if not done:
                with made_lock:
                    if made["state"] == "done":
                        done, value = True, made["stream"]   # back in the instant after the bound
                    else:
                        made["state"] = "abandoned"
            if not done:
                self._callback_active = False
                self._stream = None
                raise AudioWedged("the stream did not open")
            if isinstance(value, BaseException):
                self._stream = made.get("stream")
                raise value
            self._stream = value
            self._armed = value
            self._opened_device = _opened_name

    def _open_device(self):
        """The input device index to open, or None for the default. An index
        pinned at construction or by set_device() (the setup practice) wins;
        otherwise the resolver is asked now, with PortAudio just restarted, so
        what it answers is an index of the list this stream is made from."""
        pick = self._device_index
        if pick is None and self._device_resolver is not None:
            try:
                pick = self._device_resolver()
            except Exception as e:
                print(f"[audio] microphone preference not applied ({type(e).__name__}) "
                      f"— following the system")
                pick = None
        return _resolve_input_device(pick)

    @staticmethod
    def _device_name(index):
        """The name of the device a stream is about to be opened on: the index
        given, else the default input. None if PortAudio will not say."""
        try:
            info = sd.query_devices(index) if index is not None else sd.query_devices(kind="input")
            name = info.get("name")
            return name if isinstance(name, str) and name else None
        except Exception:
            return None

    def set_device(self, device_index) -> None:
        """Choose the input device for subsequent recordings.

        Deliberately does not touch the current stream: streams are rebuilt on
        every hotkey press, so the next recording picks this up, and an
        in-flight recording is never disturbed.
        """
        self._device_index = int(device_index) if device_index is not None else None
        print(f"[audio] input device set to {self._device_index if self._device_index is not None else 'system default'}")

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
            # 1. Tell our Python-level callback to bail immediately. Only when
            # this is the stream it is armed for: a stream that was detached
            # earlier and is torn down late must not silence the newer stream
            # that has been armed since (the callback is one shared method).
            if self._armed is None or self._armed is stream:
                self._callback_active = False
                self._armed = None
            # 2. Stop dispatching new audio. Bounded: this is the call a
            # CoreAudio deadlock was caught in (``AudioOutputUnitStop``). One
            # that does not return is abandoned with the stream kept alive for
            # ever (CoreAudio may still call its callback), and nothing more is
            # asked of a CoreAudio that is not answering: not the drain, not
            # the close.
            done, value = self._bounded("stream.stop", stream.stop, _STOP_TIMEOUT_S, keep=stream)
            if not done:
                return
            if isinstance(value, Exception):
                print(f"audio._teardown_stream: stream.stop() failed: {value}")
            # 3. Drain — let any in-flight HAL callback complete. We hold
            # `stream` in this local scope for the entire sleep so the
            # InputStream (and its bound-method callback) can't be GC'd.
            try:
                time.sleep(_HAL_DRAIN_S)
            except Exception:
                pass
            # 4. Release C-level resources and the CFFI closure.
            done, value = self._bounded("stream.close", stream.close, _CLOSE_TIMEOUT_S, keep=stream)
            if done and isinstance(value, Exception):
                print(f"audio._teardown_stream: stream.close() failed: {value}")
            # 5. ``stream`` goes out of scope when this function returns —
            # the InputStream is GC-eligible only after the drain window
            # AND after close() has released the CFFI closure.

    def start(self):
        """Begin recording; see ``_start``. Counted while it runs (its cold-start
        warm-up waits outside the lock), so ``rebuild_if_idle`` can see it."""
        with self._stream_lock:
            self._starting += 1
        try:
            self._start()
        finally:
            with self._stream_lock:
                self._starting -= 1

    def _stale_reason(self, idle_s: float = 0.0):
        """Why the stream that is open is not the one to record on: a sentence
        for the log, or None when it is current. Reads a few numbers and a
        name; touches no stream. ``idle_s`` is how long it has been since the
        last press: it counts only where nothing else can say that the devices
        changed (``_recycles_when_idle``)."""
        if idle_s > _IDLE_RECYCLE_S and _recycles_when_idle():
            return (f"idle for {idle_s:.0f}s, and this system cannot say when a "
                    f"microphone is plugged in")
        if self._pref_version != self._stream_pref_version:
            return "the microphone preference changed"
        if self._stream_choice != self._device_index:
            return "another microphone was chosen"
        if self._devices_version != self._stream_devices_version:
            return "the audio devices changed"
        return None

    def _stream_is_running(self) -> bool:
        stream = self._stream
        if stream is None or not self._callback_active:
            return False
        try:
            return bool(getattr(stream, 'active', False))
        except Exception:
            return False            # a stream PortAudio has already closed

    def _start(self):
        """Begin recording.

        Reuses the long-lived monitor stream when it's still healthy; if
        the stream isn't running (first call, or after ``stop_monitoring``),
        spins up a fresh one via the safe lifecycle helpers.

        On a Mac a healthy stream is reused however long it has been since the
        last press. It used to be that a press after more than 30 s idle took the
        stream down and made a new one "to pick up any mic change" (v3.14.50),
        with the recorder's locks held and PortAudio restarted in the middle:
        the stop of a stream is the call a CoreAudio deadlock was caught in, and
        on a Mac with devices coming and going (a phone's microphone, a meeting
        app's driver, AirPods) every press was a chance of it, with the pill
        and the take waiting behind it. A mic change is now noticed where it
        happens (the device monitor: the system's default input changed, the
        microphone in use left, the Mac woke up) and says so with
        ``note_devices_changed``; that, a change of preference, or a stream
        that is not running are the only reasons to open a new one here. (On
        Windows, which has no such list to read, the timed recycle stays: see
        ``_IDLE_RECYCLE_S``.)
        """
        with self._stream_lock:
            # Claim this recording. Anything older is now superseded.
            self._session += 1
            my_session = self._session
            self._capture_overflows = 0
            self._capture_errors = 0
            self._buffer = []

            now = time.time()
            idle_s = now - getattr(self, "_last_press_time", 0.0)
            self._last_press_time = now
            stale_reason = self._stale_reason(idle_s)

            stream_was_running = self._stream_is_running() and stale_reason is None

            if stale_reason is not None and self._stream is not None:
                self._say(f"[audio] opening the microphone again: {stale_reason}")

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
            else:
                # Warm stream — the pre-roll is already full of live samples,
                # so splice immediately with no warm-up wait and we're done.
                # is_recording flips under _lock so the callback's check and
                # this assignment cannot interleave.
                with self._lock:
                    self._buffer = list(self._preroll)
                    self._take_started_at = time.monotonic()
                    self.is_recording = True
                return

        # ── Cold-start warm-up — deliberately OUTSIDE _stream_lock ──────────
        # A freshly (re)built stream hands back zero-filled buffers for a
        # while before real audio flows — most painfully on Bluetooth mics
        # (AirPods negotiate their HFP input link over ~1-2s). Waiting only
        # for the pre-roll to be NON-EMPTY (a silent/dead stream satisfies it
        # instantly) let the first press after a mic switch record pure
        # silence → flagged dead → lost (the 10:20 AirPods burst in app.log).
        # So we wait for LIVE audio (non-zero RMS), up to ~2s.
        #
        # v3.14.64 — this loop previously ran *while holding _stream_lock*,
        # so Esc-cancel / stop() / shutdown() / a device-switch all blocked
        # on the lock for the full 2s right after a cold start (UI hang).
        # Releasing the lock here lets them interrupt; we re-acquire below to
        # finalise, and bail if the stream was torn down underneath us.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if self._preroll:
                try:
                    recent = np.concatenate(list(self._preroll), axis=0)
                    if float(np.sqrt(np.mean(recent.astype(np.float32) ** 2))) > 1.0:
                        break  # real audio is flowing — safe to record
                except Exception:
                    break
            time.sleep(0.02)

        with self._stream_lock:
            # If a concurrent stop / force_rebuild / device-switch tore the
            # stream down during the warm-up, abort rather than record on a
            # dead stream — the user can simply press again.
            if self._stream is None:
                return
            # Someone stopped, cancelled or started a newer recording while we
            # waited. Finalising now would resurrect a recording the user has
            # already ended, so drop this one instead.
            if self._session != my_session:
                print("[audio] cold-start superseded during warm-up — not starting")
                return
            # Splice pre-roll into the recording buffer FIRST so the first
            # syllable isn't lost.
            with self._lock:
                self._buffer = list(self._preroll)
                self._take_started_at = time.monotonic()
                self.is_recording = True

    def reopen_stream(self) -> bool:
        """Open the input stream again in the middle of a take, keeping what
        has been recorded. For a stream that has gone quiet (src/mic_watch.py):
        the microphone may come back on a fresh stream where the old one stays
        dead. True if a new stream is running.

        Exactly the teardown and creation every other path here uses, in the
        same order and under the same locks: this recorder's ``_stream_lock``
        is held throughout; ``_teardown_stream`` stops, drains and closes the
        old stream under the process-wide ``_STREAM_LOCK`` (callback guard
        first, the Python reference kept to the end); ``_create_stream`` then
        starts PortAudio again, chooses the microphone again (so a device that
        has come or gone since is accounted for) and opens the new stream.
        ``is_recording`` and ``_buffer`` are not touched, so a stop() from the
        key coming up waits its turn on ``_stream_lock`` and returns what
        there is. Audio is lost for as long as this takes (about a third of a
        second), in a take that has heard nothing for seconds. A stream that
        could not be made is left as the other paths leave one (None, or not
        running), which the take's check reads as the microphone having left.
        """
        with self._stream_lock:
            if not self.is_recording or self._reopening:
                return False
            self._reopening = True
            try:
                stale = self._stream
                self._stream = None
                if stale is not None:
                    self._teardown_stream(stale)
                self._create_stream()
                self._last_chunk_at = time.monotonic()
                return self._stream is not None
            except Exception as e:
                print(f"[audio] reopening the stream failed ({type(e).__name__}): {e}")
                return False
            finally:
                self._reopening = False

    def stop(self) -> bytes:
        """Stop the *recording* (not the stream) and return WAV bytes.

        The stream keeps running so the next ``start()`` is instant. The
        post-roll trick: we sleep BEFORE flipping ``is_recording=False``
        so the callback continues to append trailing audio chunks during
        the wait, capturing the last word.

        Never waits for ever on ``_stream_lock``: something stuck inside
        PortAudio or CoreAudio can be holding it (a start that does not
        return), and the recording is already in ``_buffer``, put there by the
        callback with no help from the stream. After ``_STOP_LOCK_WAIT_S`` the
        recording is read without that lock, and the recorder says it is wedged
        (the person's words are kept, and ``on_wedge`` can act on the rest).
        """
        locked = self._stream_lock.acquire(timeout=_STOP_LOCK_WAIT_S)
        try:
            if not locked:
                self._note_wedge("waiting for the stream lock to stop", _STOP_LOCK_WAIT_S)
            my_session = self._session
        finally:
            if locked:
                self._stream_lock.release()

        # The post-roll runs BEFORE we take ownership, so a press landing in
        # this window starts a new recording. That is why the session check
        # below matters: without it this stop would disable the new capture and
        # drain its buffer, losing a dictation the user had only just begun.
        time.sleep(_POSTROLL_MS / 1000.0)

        # One long wait for the lock is enough: if the first did not get it, the
        # second only looks (a start that let go in the meantime).
        locked = self._stream_lock.acquire(timeout=_STOP_LOCK_WAIT_S if locked else 0.2)
        if not locked:
            self._note_wedge("waiting for the stream lock to stop", _STOP_LOCK_WAIT_S)
        try:
            if locked and self._session != my_session:
                print("[audio] stop superseded by a newer recording — leaving it alone")
                return b""
            # Invalidate: no in-flight start() for this session may finalise.
            self._session += 1
            # is_recording and the snapshot flip together under _lock, so a
            # callback either lands in this snapshot or sees recording off.
            # Previously the callback tested is_recording outside the lock and
            # appended inside it, so a late chunk could be appended to the
            # buffer after it had been cleared, and was then thrown away.
            # ``_lock`` is the callback's own: held for one list append, so a
            # wait for it that runs out means something else is wrong.
            if not self._lock.acquire(timeout=_SALVAGE_LOCK_WAIT_S):
                self._say("[audio] the recording could not be read: its lock was not let go")
                self.is_recording = False
                return b""
            try:
                self.is_recording = False
                buf_snapshot = list(self._buffer)
                self._buffer = []
            finally:
                self._lock.release()
        finally:
            if locked:
                self._stream_lock.release()

        if not buf_snapshot:
            return b""

        if self._capture_overflows or self._capture_errors:
            # Reported outside the real-time callback, where logging is safe.
            print(f"[audio] CAPTURE INCOMPLETE: {self._capture_overflows} overflow(s), "
                  f"{self._capture_errors} callback error(s) during this recording")

        self.recording = np.concatenate(buf_snapshot, axis=0)
        duration = len(self.recording) / self.sample_rate
        rms = np.sqrt(np.mean(self.recording.astype(np.float32) ** 2))
        print(f"Recording stopped ({duration:.2f}s, RMS: {rms:.0f})")
        return self._to_wav_bytes(self.recording)

    def salvage_wav(self, timeout: float = None):
        """What this take has recorded so far, as WAV bytes, read from the
        buffer alone: no stream, no PortAudio, no ``_stream_lock``. For the
        moment CoreAudio has stopped answering and the take has to be kept
        before Waffler restarts. The recording goes on; nothing is emptied.

        ``b""`` when nothing has been recorded, and None when the buffer could
        not be read (its lock, the callback's, was not let go in ``timeout``
        seconds): the caller says so rather than pretending it was kept."""
        wait = _SALVAGE_LOCK_WAIT_S if timeout is None else timeout
        if not self._lock.acquire(timeout=wait):
            return None
        try:
            chunks = list(self._buffer)
        finally:
            self._lock.release()
        if not chunks:
            return b""
        try:
            return self._to_wav_bytes(np.concatenate(chunks, axis=0))
        except Exception:
            return None

    def shutdown(self):
        """Fully tear down the audio stream. Called on app exit only.

        sounddevice's ``stream.stop()`` can wedge for tens of seconds on a
        long recording or after a device hot-swap; we abandon the stream
        after 1.5s rather than block the shutdown path.
        """
        # Quitting never waits for ever for the lock: a start stuck inside
        # CoreAudio can be holding it, and the app has to be able to end.
        locked = self._stream_lock.acquire(timeout=_SHUTDOWN_LOCK_WAIT_S)
        try:
            self._session += 1  # nothing in flight may finalise after shutdown
            self.is_recording = False
            stream = self._stream
            self._stream = None
        finally:
            if locked:
                self._stream_lock.release()

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
        """Start audio stream for level monitoring only (no recording).

        A take in progress keeps its audio: only an idle recorder starts from
        an empty buffer (a take that has lost its stream is carried on a new
        one, as ``reopen_stream`` does)."""
        with self._stream_lock:
            if self._stream and self._stream.active and self._callback_active:
                return  # Already monitoring/recording
            stale = self._stream
            self._stream = None
            if stale is not None:
                self._teardown_stream(stale)
            if not self.is_recording:
                self._buffer = []
            self._create_stream()

    def stop_monitoring(self):
        """Stop audio monitoring stream — used when switching devices etc.

        Detached and torn down in ONE hold of ``_stream_lock``: a press landing
        between the two steps would start a new stream (and PortAudio again,
        which frees every open stream) while this one was still open."""
        with self._stream_lock:
            stream = self._stream
            self._stream = None
            if stream is not None:
                self._teardown_stream(stream)
        self._last_rms = 0.0

    def rebuild_if_idle(self) -> bool:
        """Open the always-on stream again if nothing is using it, and say
        whether that was done. For a microphone that came or went, or a new
        ranking: the stream is then on the wrong one.

        The check and the re-open are one step under ``_stream_lock``. Nothing
        is rebuilt while a take is recorded (a Command-mode hold included), or
        while a stop() waits out its post-roll before copying the audio, or
        while a start() warms its stream up, or while the stream is being
        opened again: tearing the stream down then loses the take."""
        with self._stream_lock:
            if self.is_recording or self._starting or self._reopening:
                return False
            stale = self._stream
            self._stream = None
            if stale is not None:
                self._teardown_stream(stale)
            self._buffer = []
            self._create_stream()
            return True

    def force_rebuild(self):
        """Hard-tear-down the current stream so the next start() builds fresh.

        Called when the pipeline detects a *dead* stream — one delivering
        zero-filled buffers (overall RMS ≈ 0). This happens after the Mac
        sleeps/wakes or a mic is hot-swapped: PortAudio's stream stays
        ``.active`` and keeps firing the callback, but CoreAudio hands back
        pure silence. The PortAudio reinit doesn't always recover it (nor
        did the timed recycle that used to be here), so we make the
        detection *reactive*: once we've
        captured a recording that's pure digital silence, treat the stream
        as poisoned and drop it. The next press takes the slow path in
        start() → _create_stream() with a fresh sd._terminate()/_initialize()
        and a brand-new InputStream, which reliably re-acquires the device.
        """
        with self._stream_lock:
            # Invalidate any in-flight start(): the stream it warmed up against
            # is being destroyed, so it must not finalise onto the new one.
            self._session += 1
            stream = self._stream
            self._stream = None
            # With no stream, start()'s slow path is next, whatever the timing.
            # Torn down before the lock is let go (see stop_monitoring).
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
