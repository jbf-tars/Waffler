"""Watches for default-input-device changes and triggers stream recreation.

Background (v3.14.47)
---------------------
User report: "When Waffler is already open and then I add my wireless
mic, I go to settings and I make sure the sound input is the mic.
Settings can receive it but then Waffler can't, so I have to close the
app and then reopen it." Confirmed on macOS, plausible on Windows too.

Mechanism: ``sd.InputStream`` binds to whatever PortAudio considers the
"default input device" at the moment the stream is created. The
``AudioRecorder`` opens its monitoring stream once at pipeline init and
keeps it alive for the lifetime of the process — so a device change at
the OS layer (user plugs in a new mic, manually switches default in
Settings) doesn't propagate to the running stream.

This monitor polls the default input device every ``POLL_INTERVAL_S``
seconds. When the device's *name* changes from what it was on the last
poll, the registered ``on_change`` callback fires — which in practice
calls ``audio.stop_monitoring()`` + ``audio.start_monitoring()`` on the
``AudioRecorder``, going through its existing
``_STREAM_LOCK``-serialised teardown + creation path. The next
``_create_stream()`` reads PortAudio's *current* default — which now
reflects the new device.

Why polling, not CoreAudio property listeners?
A proper ``AudioObjectAddPropertyListener`` against
``kAudioHardwarePropertyDefaultInputDevice`` would be instant and
event-driven. PyObjC's CoreAudio bindings are awkward, the listener
runs on a non-Python thread, and a buggy implementation that crashes
the listener thread takes down the whole app. A 2 s poll is mid-single-
digit microseconds of CPU per tick, completely bulletproof, and works
identically on Windows. The 1–2 s of latency between "user plugged in
mic" and "Waffler sees it" is well under any meaningful user-perceived
delay — they're still in System Settings clicking around when it
catches up.

S12 (microphone ranking and hot-plug)
-------------------------------------
The same poll now also carries the list of inputs to whoever registered
``on_devices`` (src/mic_supervisor.py), so a microphone that is plugged in or
unplugged is noticed here and not by a second thread. On a Mac the list is
CoreAudio's own (src/mac_audio_devices.py), which is live: PortAudio's list is
only as new as the last time it was started, and it cannot be started while a
stream is open. That also makes the default-input name read here live, so a
change of the system's microphone is seen when it happens and not at the next
time something else restarts PortAudio. Elsewhere it is PortAudio's list, read
under the stream lock, which changes whenever a new stream is made. One source
is used for the whole run: a Mac that answers once and then fails is skipped
for that tick rather than mixed with PortAudio's older list, which would make
every microphone look new.

Limitations:
- Polling has a 1–2 s window. Acceptable.
- We deliberately don't restart the stream if a recording is in flight
  — that would lose the audio captured so far. The new default takes
  effect on the next press.
- Renaming a device (rare; e.g. Bluetooth profile switch on the same
  hardware) looks identical to a real device swap and will trigger a
  reset. Restarting the monitoring stream during silence is harmless,
  so this is fine.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

try:
    import sounddevice as sd
    _HAS_SD = True
except ImportError:
    _HAS_SD = False

try:
    import mic_devices as _md
except ImportError:  # imported as src.audio_device_monitor
    from src import mic_devices as _md


class AudioDeviceMonitor:
    """Background poll for default-input-device changes."""

    POLL_INTERVAL_S = 2.0
    QUERY_WAIT_S = 0.5          # the most a PortAudio query waits for a stream being made

    def __init__(
        self,
        on_change: Callable[[str, str], None],
        log_fn: Optional[Callable[[str], None]] = None,
        on_devices: Optional[Callable[[list], None]] = None,
        reader: Optional[Callable[[], Optional[list]]] = None,
    ) -> None:
        """
        Args:
            on_change: Called when the default input device changes.
                Receives ``(old_name, new_name)``.
            log_fn: Optional logger callable; defaults to ``print``.
            on_devices: Called every poll with the inputs the system lists
                now (``mic_devices.Device`` objects). May also be set later.
            reader: How to list the inputs: a function that returns the
                devices, or None when it cannot this time. Defaults to
                CoreAudio's own list on a Mac and PortAudio's elsewhere.
        """
        self._on_change = on_change
        self._log = log_fn or print
        self.on_devices = on_devices
        self._reader = reader
        self._source: Optional[str] = None      # "live" or "portaudio", once known
        self._baselined = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_default_name: Optional[str] = None

    def start(self) -> None:
        if not _HAS_SD:
            self._log("[audio-monitor] sounddevice unavailable, monitor disabled")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._poll, daemon=True, name="AudioDeviceMonitor"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _current_default_name(self) -> Optional[str]:
        try:
            # Under the stream lock, like every other query of PortAudio: a
            # stream being made starts PortAudio again, and a query in the
            # middle of that is a query of a library being torn down. "Not now"
            # (None) when the lock cannot be had in time; the next poll tries.
            try:
                from audio import snapshot_devices
            except ImportError:  # imported as src.audio_device_monitor
                from src.audio import snapshot_devices
            snap = snapshot_devices(timeout=self.QUERY_WAIT_S)
            if not snap:
                return None
            infos, _hostapis, default_index, _primary = snap
            if default_index is None or not (0 <= default_index < len(infos)):
                return None
            name = infos[default_index].get("name")
            return name if isinstance(name, str) and name else None
        except Exception:
            # No default input device available right now (e.g. all
            # mics unplugged). Return None — we'll keep polling until
            # one appears.
            return None

    def _read_devices(self) -> Optional[list]:
        """The inputs now, from one source for the whole run (see the module
        docstring), or None when this poll cannot say."""
        if self._reader is not None:
            return self._reader()
        if self._source in (None, "live"):
            live = _md.live_inputs()
            if live is not None:
                self._source = "live"
                return live
            if self._source == "live":
                return None
            self._source = "portaudio"
        return _md.pa_inputs() or None

    def _tick(self) -> None:
        """One poll: the default input's name, and the list of inputs. The
        first one only learns the baseline (so nothing "changes" at startup)."""
        devices = None
        try:
            devices = self._read_devices()
        except Exception as e:
            self._log(f"[audio-monitor] device list not read: {type(e).__name__}")
        default = _md.default_of(devices) if devices else None
        name = default.name if default is not None else self._current_default_name()
        if not self._baselined:
            self._baselined = True
            self._last_default_name = name
            self._log(
                f"[audio-monitor] started; baseline default input = "
                f"{self._last_default_name!r}"
            )
        elif name and name != self._last_default_name:
            old = self._last_default_name
            self._last_default_name = name
            self._log(
                f"[audio-monitor] default input changed: "
                f"{old!r} → {name!r}"
            )
            try:
                self._on_change(old or "", name)
            except Exception as e:
                self._log(f"[audio-monitor] on_change failed: {e}")
        # An empty list is "none are plugged in" only where the system itself
        # answered (CoreAudio, "live"); PortAudio says [] when it cannot say.
        # Without this the last microphone leaving was never reported.
        if (devices or (devices is not None and self._source == "live")) and self.on_devices is not None:
            try:
                self.on_devices(devices)
            except Exception as e:
                self._log(f"[audio-monitor] on_devices failed: {type(e).__name__}")

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                self._log(f"[audio-monitor] poll error: {e}")
            self._stop.wait(self.POLL_INTERVAL_S)
