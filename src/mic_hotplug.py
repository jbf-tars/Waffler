"""A microphone that appears while Waffler is running (S12, item 3).

When a microphone Waffler has not met is plugged in, it asks once, in a tip:
"<Device> detected. Use it for dictation?" with "Use it" (rank 1) and "Not
now". This module is the memory and the rules; the tip itself is shown by the
supervisor (src/mic_supervisor.py) through the notifier (src/notifier.py), so
the Show tips switch, the one slot and the quiet-moment rules all apply.

The rules, in the order ``decide`` applies them:

  virtual      loopback, aggregate and virtual devices (Teams Audio, BlackHole)
               are not asked about: they come and go with other software.
  bluetooth    a Bluetooth headset Waffler deliberately does not open
               (audio.py ``_resolve_input_device``) is not asked about either.
               Saying "use it?" and then not using it would be worse than
               saying nothing.
  ranked       already in the list: nothing to ask. (A higher-ranked
               microphone coming back is picked up without a word.)
  decided      asked before, whatever the answer, so at most once for each
               device. A prompt that timed out unanswered counts as asked.
  wait         a take is being recorded: not now, ask when it is over.
  prompt       ask.

"New" is relative to the list Waffler saw last: the first list it sees is the
baseline (what was plugged in when it started is not new), and after that a
device is new when it was not in the list before. Names are compared as
``mic_devices.norm_name`` does, so a device that the system numbers "(2)" is
the same one.

What is remembered is in ``mic_devices.json`` in the data folder: for each
device name, what was decided and when. No text from anyone's dictation is in
it. It is deleted by "Delete all my data" and with the folder by a full
reset, and it is not in the logs bundle.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

try:
    import mic_devices as md
    from atomic_json import write_json_atomic
    from data_paths import data_dir as _data_dir
except ImportError:  # imported as src.mic_hotplug
    from src import mic_devices as md
    from src.atomic_json import write_json_atomic
    from src.data_paths import data_dir as _data_dir

STATE_FILE = "mic_devices.json"
STATE_VERSION = 1
MAX_REMEMBERED = 200            # the oldest are forgotten past this

# What is remembered of a device.
SEEN, USE, NOT_NOW = "seen", "use", "not_now"
DECISIONS = (SEEN, USE, NOT_NOW)

# What decide() says.
PROMPT, WAIT = "prompt", "wait"
VIRTUAL, BLUETOOTH, RANKED, DECIDED = "virtual", "bluetooth", "ranked", "decided"


def decide(device: md.Device, *, ranked: bool, decided: Optional[str], recording: bool) -> str:
    """What to do about a microphone that has just appeared. See the module
    docstring for the order."""
    if device.virtual:
        return VIRTUAL
    if device.bluetooth:
        return BLUETOOTH
    if ranked:
        return RANKED
    if decided in DECISIONS:
        return DECIDED
    if recording:
        return WAIT
    return PROMPT


# ── what was decided, kept ────────────────────────────────────────────────

class DeviceMemory:
    """``mic_devices.json``: per device, what was decided. The folder is asked
    for on every use (data_paths.data_dir) so a test that changes it is
    honoured; a path, or a function that gives one, can be passed instead."""

    def __init__(self, path=None, clock: Callable[[], float] = time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._devices: Optional[Dict[str, dict]] = None

    def path(self) -> Path:
        if self._path is None:
            return _data_dir() / STATE_FILE
        return Path(self._path() if callable(self._path) else self._path)

    def _loaded(self) -> Dict[str, dict]:
        if self._devices is None:
            self._devices = {}
            try:
                raw = json.loads(self.path().read_text(encoding="utf-8-sig"))
                devices = raw.get("devices") if isinstance(raw, dict) else None
                for key, rec in (devices.items() if isinstance(devices, dict) else ()):
                    if isinstance(key, str) and isinstance(rec, dict) and rec.get("decision") in DECISIONS:
                        at = rec.get("at")
                        self._devices[key] = {
                            "decision": rec["decision"],
                            "at": float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0}
            except (OSError, ValueError, AttributeError):
                self._devices = {}
        return self._devices

    def decision(self, device_or_name) -> Optional[str]:
        key = device_or_name.key if isinstance(device_or_name, md.Device) else md.norm_name(device_or_name)
        with self._lock:
            rec = self._loaded().get(key)
            return rec["decision"] if rec else None

    def record(self, device_or_name, decision: str) -> None:
        """Remember what was decided. A later answer replaces an earlier one;
        "seen" never replaces a real answer."""
        if decision not in DECISIONS:
            return
        key = device_or_name.key if isinstance(device_or_name, md.Device) else md.norm_name(device_or_name)
        if not key:
            return
        with self._lock:
            devices = self._loaded()
            old = devices.get(key)
            if decision == SEEN and old and old["decision"] != SEEN:
                return
            devices[key] = {"decision": decision, "at": self._clock()}
            if len(devices) > MAX_REMEMBERED:
                for k in sorted(devices, key=lambda k: devices[k]["at"])[:len(devices) - MAX_REMEMBERED]:
                    del devices[k]
            self._save()

    def _save(self) -> None:
        try:
            p = self.path()
            p.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(p, {"version": STATE_VERSION, "devices": self._devices or {}}, indent=1)
        except Exception:
            pass        # a memory that cannot be kept costs, at worst, a second question

    def forget(self) -> None:
        """Delete my data: everything decided, and the file."""
        with self._lock:
            self._devices = {}
            try:
                self.path().unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def count(self) -> int:
        with self._lock:
            return len(self._loaded())


# ── what is new ───────────────────────────────────────────────────────────

class HotplugTracker:
    """Which microphones have appeared since Waffler last looked. The first
    list is the baseline. A new device stays pending until the caller has
    dealt with it (``resolve``) or it is unplugged again, so one that has to
    wait for a take to end is still there afterwards."""

    def __init__(self):
        self._previous: Optional[set] = None
        self._pending: Dict[str, md.Device] = {}
        self._lock = threading.Lock()

    def update(self, devices: Iterable[md.Device]) -> List[md.Device]:
        """Give the list as it is now; get back the devices that are new and
        not yet dealt with."""
        now = {d.key: d for d in md.distinct(devices) if d.key}
        with self._lock:
            if self._previous is None:
                self._previous = set(now)
                return []
            for key, dev in now.items():
                if key not in self._previous:
                    self._pending[key] = dev
            for key in [k for k in self._pending if k not in now]:
                del self._pending[key]
            self._previous = set(now)
            return list(self._pending.values())

    def resolve(self, device_or_key) -> None:
        key = device_or_key.key if isinstance(device_or_key, md.Device) else md.norm_name(device_or_key)
        with self._lock:
            self._pending.pop(key, None)

    def pending(self) -> List[md.Device]:
        with self._lock:
            return list(self._pending.values())

    def reset(self) -> None:
        with self._lock:
            self._previous = None
            self._pending.clear()
