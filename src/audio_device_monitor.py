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


class AudioDeviceMonitor:
    """Background poll for default-input-device changes."""

    POLL_INTERVAL_S = 2.0

    def __init__(
        self,
        on_change: Callable[[str, str], None],
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Args:
            on_change: Called when the default input device changes.
                Receives ``(old_name, new_name)``.
            log_fn: Optional logger callable; defaults to ``print``.
        """
        self._on_change = on_change
        self._log = log_fn or print
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
            default = sd.query_devices(kind="input")
            return default.get("name") if isinstance(default, dict) else None
        except Exception:
            # No default input device available right now (e.g. all
            # mics unplugged). Return None — we'll keep polling until
            # one appears.
            return None

    def _poll(self) -> None:
        # Baseline: read the current default on first tick so we don't
        # immediately fire a spurious "changed" event on startup.
        self._last_default_name = self._current_default_name()
        self._log(
            f"[audio-monitor] started; baseline default input = "
            f"{self._last_default_name!r}"
        )

        while not self._stop.is_set():
            try:
                name = self._current_default_name()
                if name and name != self._last_default_name:
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
            except Exception as e:
                self._log(f"[audio-monitor] poll error: {e}")
            self._stop.wait(self.POLL_INTERVAL_S)
