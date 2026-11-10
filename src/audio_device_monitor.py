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

Waking up
---------
A Mac that has slept is the other time a stream most often goes dead, and the
poll is how that is noticed: it runs every two seconds, so a tick that arrives
more than ``WAKE_GAP_S`` after the one before it means the computer was
asleep. ``on_wake`` is called then, and Waffler flags the stream stale, so the
next press opens a new one (once) instead of recording on a dead one. On a
Mac this replaces the old rule "a press after 30 s idle opens a new stream",
which put a stop and a restart of CoreAudio on the hotkey's path every time.
Where there is no live device list (Windows) the poll keeps a form of that rule
off the hotkey's path: ``on_tick`` lets the app open an idle stream again
between presses, at 30 s of quiet, 2 minutes, then every 5 (src/audio.py
``recycle_when_idle``), so the press keeps its pre-roll and PortAudio's list is
new again.

Back at a Mac that slept
------------------------
That press after a wake is a cold start: the stream is taken down and made
again while the key is held, and the half second kept before the press is
thrown away (on one Mac 7 of 80 takes began so, each 0.9 s short at its start,
and 3 of the 7 did not end in a paste). So once the Mac has been awake for
``WAKE_SETTLE_S`` and its keyboard, mouse or trackpad has been used since the
wake was noticed, ``on_awake`` is called, once, and Waffler opens the stream
again there, between presses (``AudioRecorder.reopen_if_stale``). A wake nobody
is at opens nothing: Power Nap wakes a sleeping Mac every quarter of an hour.
Where the time since the last input cannot be read (``seconds_since_input``
answers None: not a Mac, or Quartz will not say) nothing is done, and the next
press opens the stream, as before.

A wake that follows less than ``QUIET_WAKE_AWAKE_S`` awake is one of a run and
is not a line in the log: it is counted, and the count is written once, when
the person is back or the Mac has stayed awake that long. ``on_wake`` is still
told of every one.

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

import sys
import threading
import time
from typing import Callable, Optional

try:
    import sounddevice as sd
    _HAS_SD = sd is not None        # the monitor only asks whether PortAudio is there; audio.py reads it
except ImportError:
    _HAS_SD = False

try:
    import mic_devices as _md
except ImportError:  # imported as src.audio_device_monitor
    from src import mic_devices as _md


def seconds_since_input() -> Optional[float]:
    """Seconds since the keyboard, the mouse or the trackpad was last used on
    this Mac, or None where that cannot be told (not a Mac, no Quartz, a
    failure). One read of a number the window server keeps; it needs no
    permission and watches no key."""
    if sys.platform != "darwin":
        return None
    try:
        import Quartz
        idle = Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType)
        idle = float(idle)
        return idle if idle == idle and idle >= 0.0 else None
    except Exception:
        return None


class AudioDeviceMonitor:
    """Background poll for default-input-device changes."""

    POLL_INTERVAL_S = 2.0
    # A restart that could not run (a take was being recorded) is tried again on each
    # poll; five minutes of polls is longer than any take but a 20-minute one, and a
    # take that long ends in a stream that is rebuilt anyway.
    DEFERRED_MAX_POLLS = 150
    QUERY_WAIT_S = 0.5          # the most a PortAudio query waits for a stream being made
    # A poll this much later than the last one means the Mac was asleep: more than
    # a poll (2 s) plus the longest a bounded CoreAudio call may take (3 s), with room.
    WAKE_GAP_S = 20.0
    # After a wake, how long the Mac must have been awake before ``on_awake`` may be
    # called: CoreAudio is busiest as the Mac wakes, and three polls is long enough
    # to tell a Mac that stays awake from one that goes straight back to sleep.
    WAKE_SETTLE_S = 6.0
    # A wake that follows less than this long awake is one of a run (a sleeping Mac
    # wakes itself about every quarter of an hour, for seconds): counted, and the
    # count logged once, where each used to be a line (769 in twelve days on one Mac).
    QUIET_WAKE_AWAKE_S = 300.0

    def __init__(
        self,
        on_change: Callable[[str, str], None],
        log_fn: Optional[Callable[[str], None]] = None,
        on_devices: Optional[Callable[[list], None]] = None,
        reader: Optional[Callable[[], Optional[list]]] = None,
        on_wake: Optional[Callable[[], None]] = None,
        wall_clock: Callable[[], float] = time.time,
        input_idle_s: Callable[[], Optional[float]] = seconds_since_input,
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
            on_wake: Called when a poll comes much later than due (the
                computer was asleep). May also be set later.
            wall_clock: The time of day, for noticing that; a test's.
            input_idle_s: Seconds since the keyboard or mouse was last used,
                or None when that cannot be told; a test's.
        """
        self._on_change = on_change
        self._log = log_fn or print
        self.on_devices = on_devices
        # Called at the start of every poll, before the list is read. Set
        # later; on Windows the app opens an idle stream again from it
        # (AudioRecorder.recycle_when_idle), so the same poll reads the list
        # PortAudio has just made.
        self.on_tick: Optional[Callable[[], None]] = None
        self._reader = reader
        self.on_wake = on_wake
        self._wall = wall_clock
        self._last_tick_at: Optional[float] = None
        # Called once after a wake, when the person is back (see the module
        # docstring). Set later. ``_woke_at``: when the last wake was noticed,
        # while that call is still owed.
        self.on_awake: Optional[Callable[[], None]] = None
        self._input_idle_s = input_idle_s
        self._woke_at: Optional[float] = None
        # The run of wakes being counted: when the last wake was noticed, how
        # many have not been logged, and the seconds asleep before them.
        self._last_wake_at: Optional[float] = None
        self._quiet_wakes = 0
        self._quiet_asleep_s = 0.0
        self._source: Optional[str] = None      # "live" or "portaudio", once known
        # The inputs the poll now running read (None when it could not), for a
        # handler of ``on_change`` that needs to know who is here.
        self.last_devices: Optional[list] = None
        self._baselined = False
        # What to try again on each poll until it works: (function, polls so far).
        self._deferred: Optional[list] = None
        self._deferred_lock = threading.Lock()
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

    def defer_until_done(self, fn: Callable[[], bool]) -> None:
        """Call ``fn`` on every poll until it returns True. A newer request
        replaces an older one. For work that cannot run now and must not be
        forgotten: a change of microphone in the middle of a take used to be
        logged as "deferring stream restart" and never done, which left the
        always-open stream on a microphone that no longer delivered anything."""
        with self._deferred_lock:
            self._deferred = [fn, 0]

    def _run_deferred(self) -> None:
        with self._deferred_lock:
            job = self._deferred
        if job is None:
            return
        fn = job[0]
        job[1] += 1
        try:
            done = bool(fn())
        except Exception as e:
            self._log(f"[audio-monitor] deferred work failed: {type(e).__name__}")
            done = False
        if done:
            with self._deferred_lock:
                if self._deferred is job:
                    self._deferred = None
        elif job[1] >= self.DEFERRED_MAX_POLLS:
            self._log("[audio-monitor] giving up on the deferred restart; the next change of "
                      "microphone or the next take will open the stream again")
            with self._deferred_lock:
                if self._deferred is job:
                    self._deferred = None

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

    def _look_for_wake(self) -> None:
        """A poll that comes WAKE_GAP_S or more after the last one: the computer
        was asleep. Tells ``on_wake`` once; never raises."""
        now = self._wall()
        last, self._last_tick_at = self._last_tick_at, now
        if last is None or now - last < self.WAKE_GAP_S:
            return
        if self._last_wake_at is not None and last - self._last_wake_at < self.QUIET_WAKE_AWAKE_S:
            self._quiet_wakes += 1              # one of a run: counted, logged later
            self._quiet_asleep_s += now - last
        else:
            self._say_quiet_wakes()
            self._log(f"[audio-monitor] the computer woke up after {int(now - last)}s")
        self._last_wake_at = now
        self._woke_at = now
        if self.on_wake is not None:
            try:
                self.on_wake()
            except Exception as e:
                self._log(f"[audio-monitor] on_wake failed: {type(e).__name__}")

    def _say_quiet_wakes(self) -> None:
        """The one line for the wakes that were counted, if there are any."""
        n, asleep = self._quiet_wakes, self._quiet_asleep_s
        if not n:
            return
        self._quiet_wakes, self._quiet_asleep_s = 0, 0.0
        times = "1 more time, after" if n == 1 else f"{n} more times, each after"
        self._log(f"[audio-monitor] the computer woke up {times} less than "
                  f"{int(self.QUIET_WAKE_AWAKE_S // 60)} minutes awake (asleep {int(asleep)}s in all)")

    def _look_for_the_person(self) -> None:
        """After a wake: once the Mac has been awake for WAKE_SETTLE_S and has
        been used since the wake was noticed, tell ``on_awake``, once. Input
        "since the wake" is an idle time shorter than the time since the wake:
        whatever clock the idle time is kept on, it is never shorter than that
        unless a key or the mouse was touched after it. Never raises."""
        if self._quiet_wakes and self._last_wake_at is not None \
                and self._wall() - self._last_wake_at >= self.QUIET_WAKE_AWAKE_S:
            self._say_quiet_wakes()             # it stayed awake: the run is over
        if self._woke_at is None or self.on_awake is None:
            return
        since_wake = self._wall() - self._woke_at
        if since_wake < self.WAKE_SETTLE_S:
            return
        try:
            idle = self._input_idle_s()
        except Exception:
            idle = None
        if idle is None:
            self._woke_at = None        # cannot tell: the next press opens the stream, as before
            return
        if idle >= since_wake:
            return                      # nobody has touched it since it woke
        self._woke_at = None
        self._say_quiet_wakes()
        try:
            self.on_awake()
        except Exception as e:
            self._log(f"[audio-monitor] on_awake failed: {type(e).__name__}")

    def _tick(self) -> None:
        """One poll: the default input's name, and the list of inputs. The
        first one only learns the baseline (so nothing "changes" at startup)."""
        self._look_for_wake()
        self._look_for_the_person()
        self._run_deferred()
        if self.on_tick is not None:
            try:
                self.on_tick()
            except Exception as e:
                self._log(f"[audio-monitor] on_tick failed: {type(e).__name__}")
        devices = None
        try:
            devices = self._read_devices()
        except Exception as e:
            self._log(f"[audio-monitor] device list not read: {type(e).__name__}")
        self.last_devices = devices
        default = _md.default_of(devices) if devices else None
        # A list given to the monitor (a test's) is the whole truth: ask the machine's
        # own default input only when the monitor is reading the machine's list itself.
        if default is not None:
            name = default.name
        else:
            name = None if self._reader is not None else self._current_default_name()
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


def stream_can_stay(audio, devices, ranking) -> bool:
    """The system's input changed: is the open stream still the right one, so
    that it need not be stopped and started? Only when all of this is so: there
    is a ranking; the stream is delivering audio and nothing has flagged it
    stale; and the microphone it is on is, in ``devices`` (the inputs here
    now), the best ranked one that is here. That is the microphone a new
    stream would be opened on, so a restart would change nothing, and every
    restart is a stop and a start inside macOS's audio (where the 3.16.2
    deadlock was caught): AirPods coming and going cost one Mac 33 of them in
    twelve days with the built-in microphone ranked first. Anything else, and
    anything that cannot be asked, is False: restart, as before."""
    try:
        if audio is None or not devices or not ranking:
            return False
        if not audio.is_delivering() or not audio.stream_is_current():
            return False
        opened = audio.opened_device()
        if not isinstance(opened, str) or not opened:
            return False
        best = _md.first_present(ranking, _md.distinct(devices))
        return best is not None and _md.same_name(opened, best.name)
    except Exception:
        return False


def restart_stream_when_idle(audio, monitor, new_name: str, log: Callable[[str], None],
                             busy: Optional[Callable[[], bool]] = None,
                             system_input: bool = True,
                             keep: Optional[Callable[[], bool]] = None) -> None:
    """The system's default input changed: open the always-on stream again so it
    is on the microphone now in use. If a take is being recorded that would lose
    it, so the restart is tried again on each poll of ``monitor`` until the take
    is over. ``busy`` is the app's own "something is using the microphone" check
    (a Command or Transform hold, a take being turned into text), asked as well
    as the recorder's own. Never raises.

    The line in the log names the microphone the stream was opened on. That is
    often not the system's new input (``new_name``): a ranked microphone wins,
    and a Bluetooth headset's is avoided. ``system_input=False`` when
    ``new_name`` is not a device's name (a change of ranking).

    ``keep``: asked first; True means the open stream is still the right one
    (``stream_can_stay``), and then nothing is restarted or flagged. One that
    raises is a restart."""
    if audio is None:
        return                                  # the pipeline is still starting
    if keep is not None:
        try:
            staying = bool(keep())
        except Exception:
            staying = False
        if staying:
            try:
                opened = audio.opened_device()
            except Exception:
                opened = None
            log(f"[audio-monitor] the stream stays on {opened!r}, the best ranked microphone here; "
                f"the system's input is now {new_name!r}")
            return
    # Before anything that can fail or wait: if the restart below cannot run (a
    # take is under way, the stream cannot be opened) the next press opens it.
    flag = getattr(audio, "note_devices_changed", None)
    if flag is not None:
        try:
            flag()
        except Exception as e:
            log(f"[audio-monitor] could not flag the change: {type(e).__name__}")

    def opened_on() -> str:
        try:
            opened = audio.opened_device()
        except Exception:
            opened = None
        if not isinstance(opened, str) or not opened:
            return repr(new_name)
        if system_input and opened != new_name:
            return f"{opened!r}; the system's input is now {new_name!r}"
        return repr(opened)

    def attempt(after_take: bool) -> bool:
        try:
            if busy is not None and busy():
                return False
            if audio.rebuild_if_idle():
                log(f"[audio-monitor] monitoring stream restarted on {opened_on()}"
                    + (" after the take" if after_take else ""))
                return True
        except Exception as e:
            log(f"[audio-monitor] restart failed: {type(e).__name__}")
        return False

    if attempt(False):
        return
    log(f"[audio-monitor] device changed to {new_name!r} but a take is under way; "
        f"the stream will be restarted when the take is over")
    try:
        monitor.defer_until_done(lambda: attempt(True))
    except Exception as e:
        log(f"[audio-monitor] could not schedule the restart: {type(e).__name__}")
