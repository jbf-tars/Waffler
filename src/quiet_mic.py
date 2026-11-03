"""A tip when the microphone is set too low (3.16.3 on a Mac, 3.16.5 on Windows).

The owner's Mac had its input volume at 40 of 100. 56 of the owner's last 57 takes
were lifted by the quiet-audio lift (transcribe_whisper.boost_quiet_speech):
their speech arrived at a median -42.5 dBFS, where clear dictation sits near
-20, and was raised by 21.5 dB, up to 30. The lift raises the room's noise
with the speech, and noisy near-silence at the end of a take is what Whisper
turns into "Thank you." Turning the input volume up fixes it at the source,
and only the person should do that: Waffler never changes the volume.

Each take's speech level is kept with its Journal entry (``speech_db``, the
level the lift measures, before it). When the median of the last TAKES of
them is under QUIET_LEVEL_DB, Waffler reads the Mac's input volume (osascript,
READ_TIMEOUT_S, at most once every CHECK_EVERY_S, never raising) and, under
LOW_VOLUME, shows a tip through the notifier (kind TIP_KIND: a tip, so the
Show tips switch, "Don't show again" and a quiet moment all apply, and at most
once a week) with a button that opens Sound settings at Input.

On Windows (3.16.5) the same rules apply to the default input's level: the
owner's PC had "Headset Microphone (Realtek(R) Audio)" at 54%, and its takes
arrived at a median -49 dBFS. The level and the device's name are read from
Core Audio (the default eCapture/eConsole endpoint's IAudioEndpointVolume and
its friendly name) through ctypes, so nothing new is shipped: pycaw and
comtypes are not in requirements_windows.txt, and sounddevice knows names but
not levels. PowerShell is no fallback: it has no command for an endpoint's
level, and compiling one with Add-Type takes longer than READ_TIMEOUT_S. The
reading runs on its own thread and is given READ_TIMEOUT_S. The tip names the
device and its level, and its button opens the microphone's own settings page
(ms-settings:sound-defaultinputproperties), with its Input volume at the top.

The time of the last reading and what it read are kept in STATE_FILE in the
data folder: a number and a time, and on Windows the microphone's short name,
nothing anyone said, deleted with "Delete all my data". Elsewhere nothing is
read and no tip is shown.

Everything the app provides is a hook (Hooks), so the rules are tested
without a Mac, a PC, a notifier or a thread.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, NamedTuple, Optional

try:
    import notifier as _notifier
    import user_messages as _um
    from atomic_json import write_json_atomic
    from data_paths import data_dir as _data_dir
except ImportError:  # imported as src.quiet_mic
    from src import notifier as _notifier
    from src import user_messages as _um
    from src.atomic_json import write_json_atomic
    from src.data_paths import data_dir as _data_dir

TAKES = 10                   # the median is over this many dictations
QUIET_LEVEL_DB = -36.0       # under this median speech level, the microphone is set low
LOW_VOLUME = 70              # the input volume (0 to 100) under this is low
CHECK_EVERY_S = 24 * 3600.0  # the volume is read at most this often
READ_TIMEOUT_S = 2.0
WAIT_S = 30.0                # how long the tip waits for the dictation to be over
POLL_S = 0.5

TIP_KIND = "quiet_mic"
OPEN_SOUND_ACTION = "open_sound_input"       # the tip's button (app.py _on_toast_action)
SOUND_INPUT_URL = "x-apple.systempreferences:com.apple.preference.sound?input"
# The default microphone's own page in Settings: Input volume first, then the
# microphone test (checked on the owner's Windows 11 PC, 2026-11-03).
# ms-settings:sound also has the slider, but at the bottom, below the fold, and
# the Sound control panel's Recording tab (mmsys.cpl,,1) has it two dialogs
# further in, under Properties, Levels.
WINDOWS_SOUND_SETTINGS = "ms-settings:sound-defaultinputproperties"
NAME_CHARS = 32              # the tip names the device in at most this many characters
STATE_FILE = "mic_volume.json"
STATE_VERSION = 1
TIP_SECONDS = 15

_OSASCRIPT = ["/usr/bin/osascript", "-e", "input volume of (get volume settings)"]


# ── the levels ──────────────────────────────────────────────────────────────

def _level(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def levels_of(history, n: int = TAKES) -> list:
    """The ``speech_db`` of the last ``n`` dictations that have one, oldest
    first. A rewrite or a command is not a dictation."""
    if not isinstance(history, list):
        return []
    out = []
    for e in history:
        if isinstance(e, dict) and e.get("kind") not in ("transform", "command"):
            v = _level(e.get("speech_db"))
            if v is not None:
                out.append(v)
    return out[-n:]


def is_quiet(levels) -> bool:
    """TAKES levels, with a median under QUIET_LEVEL_DB."""
    levels = list(levels)
    return len(levels) >= TAKES and statistics.median(levels[-TAKES:]) < QUIET_LEVEL_DB


# ── the Mac ─────────────────────────────────────────────────────────────────

def read_input_volume(runner=subprocess.run, platform: Optional[str] = None) -> Optional[int]:
    """The Mac's input volume, 0 to 100, or None: not a Mac, an input with no
    volume of its own ("missing value"), a failure or READ_TIMEOUT_S passing.
    Never raises."""
    if (platform or sys.platform) != "darwin":
        return None
    try:
        done = runner(_OSASCRIPT, capture_output=True, text=True, timeout=READ_TIMEOUT_S)
        if done.returncode != 0:
            return None
        value = int(str(done.stdout).strip())
        return value if 0 <= value <= 100 else None
    except Exception:
        return None


def open_sound_settings(runner=subprocess.run, platform: Optional[str] = None,
                        starter=None) -> bool:
    """Open Sound settings: on a Mac at Input (``runner``), on Windows the
    Sound page (``starter``, os.startfile). Never raises."""
    try:
        if (platform or sys.platform).startswith("win"):
            (starter or getattr(os, "startfile"))(WINDOWS_SOUND_SETTINGS)
            return True
        runner(["/usr/bin/open", SOUND_INPUT_URL], check=False, capture_output=True, timeout=10)
        return True
    except Exception:
        return False


# ── Windows ─────────────────────────────────────────────────────────────────

class MicInput(NamedTuple):
    """The default input on Windows: its level, 0 to 100, and its name."""
    volume: Optional[int]
    name: Optional[str]


_GENERIC_NAMES = {"microphone", "microphones", "mic"}


def short_device_name(name) -> Optional[str]:
    """The device as the tip names it: Windows' friendly name without the
    driver in brackets ("Headset Microphone (Realtek(R) Audio)" is "Headset
    Microphone"), unless what is left says nothing ("Microphone (2- USB Audio
    Device)" is "USB Audio Device"). At most NAME_CHARS characters; None for
    no name."""
    if not isinstance(name, str):
        return None
    name = " ".join("".join(c if c.isprintable() else " " for c in name).split())
    head, sep, tail = name.partition(" (")
    if sep and head.casefold() in _GENERIC_NAMES:
        inner = re.sub(r"^\d+\s*-\s*", "", tail[:-1] if tail.endswith(")") else tail).strip()
        name = inner or head
    elif sep:
        name = head
    name = name[:NAME_CHARS].strip()
    return name or None


def _whole_volume(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 100 else None


def read_windows_input(reader=None, platform: Optional[str] = None,
                       timeout: float = READ_TIMEOUT_S) -> Optional[MicInput]:
    """The default input's level (0 to 100) and short name on Windows, or
    None: not Windows, no microphone, no level, a failure, or ``timeout``
    passing (the reading has its own thread, left to finish on its own).
    The name may be None. Never raises, never changes the level."""
    if not (platform or sys.platform).startswith("win"):
        return None
    try:
        reader = reader or _read_default_capture
        out = {}

        def run():
            try:
                out["value"] = reader()
            except BaseException as e:      # noqa: B036 - its thread must end quietly
                out["error"] = type(e).__name__

        t = threading.Thread(target=run, daemon=True, name="MicVolumeRead")
        t.start()
        t.join(timeout)
        value = out.get("value")
        if t.is_alive() or not isinstance(value, MicInput):
            return None
        volume = _whole_volume(value.volume)
        return None if volume is None else MicInput(volume, short_device_name(value.name))
    except Exception:
        return None


# Core Audio, through ctypes (mmdeviceapi.h, endpointvolume.h, propsys.h).
# A COM object is a pointer to its vtable; each method takes the object first
# and returns an HRESULT, negative on failure. Slots 0 to 2 are IUnknown's
# (QueryInterface, AddRef, Release).

class _Com(NamedTuple):
    """What ole32 provides, so tests can stand in for it."""
    functype: Callable          # ctypes.WINFUNCTYPE
    co_initialize: Callable     # CoInitializeEx(reserved, flags)
    co_uninitialize: Callable   # CoUninitialize()
    co_create_instance: Callable  # CoCreateInstance(clsid, outer, clsctx, iid, out)
    prop_variant_clear: Callable  # PropVariantClear(pv)


def _guid(text: str):
    import ctypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def _propvariant_type():
    import ctypes

    class PROPVARIANT(ctypes.Structure):
        # vt, three reserved words, then the value (a pointer for VT_LPWSTR),
        # padded to the union's 16 bytes on 64-bit Windows.
        _fields_ = [("vt", ctypes.c_uint16), ("r1", ctypes.c_uint16),
                    ("r2", ctypes.c_uint16), ("r3", ctypes.c_uint16),
                    ("data", ctypes.c_void_p), ("pad", ctypes.c_void_p)]
    return PROPVARIANT


try:
    _PROPVARIANT = _propvariant_type()
except Exception:                    # a Python without ctypes: Windows is never read
    _PROPVARIANT = None

_CLSID_MMDEVICE_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_IMMDEVICE_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_IID_IAUDIO_ENDPOINT_VOLUME = "5CDF2C82-841E-4546-9722-0CF74078229A"
_PKEY_DEVICE_FRIENDLY_NAME = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
_COINIT_MULTITHREADED = 0
_RPC_E_CHANGED_MODE = -2147417850   # 0x80010106: COM already set up otherwise here
_CLSCTX_ALL = 23
_E_CAPTURE, _E_CONSOLE = 1, 0
_STGM_READ = 0
_VT_LPWSTR = 31
_RELEASE = 2
_ENUM_GET_DEFAULT_ENDPOINT = 4      # IMMDeviceEnumerator::GetDefaultAudioEndpoint
_DEVICE_ACTIVATE = 3                # IMMDevice::Activate
_DEVICE_OPEN_PROPERTY_STORE = 4     # IMMDevice::OpenPropertyStore
_STORE_GET_VALUE = 5                # IPropertyStore::GetValue
_VOLUME_GET_SCALAR = 9              # IAudioEndpointVolume::GetMasterVolumeLevelScalar


def _windows_com() -> _Com:
    """ole32's functions, from a private WinDLL so their argtypes are ours
    alone. Raises off Windows (ctypes has no WinDLL there)."""
    import ctypes
    from ctypes import c_int32, c_uint32, c_void_p
    ole32 = ctypes.WinDLL("ole32")
    init, uninit = ole32.CoInitializeEx, ole32.CoUninitialize
    create, clear = ole32.CoCreateInstance, ole32.PropVariantClear
    init.argtypes, init.restype = (c_void_p, c_uint32), c_int32
    uninit.argtypes, uninit.restype = (), None
    create.argtypes = (c_void_p, c_void_p, c_uint32, c_void_p, c_void_p)
    create.restype = c_int32
    clear.argtypes, clear.restype = (c_void_p,), c_int32
    return _Com(ctypes.WINFUNCTYPE, init, uninit, create, clear)


def _call(com: _Com, obj, slot: int, *args, argtypes=()):
    """Call method ``slot`` of the COM object at ``obj``; its HRESULT."""
    import ctypes
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    method = com.functype(ctypes.c_int32, ctypes.c_void_p, *argtypes)(vtbl[slot])
    return method(obj, *args)


def _read_default_capture(com: Optional[_Com] = None) -> MicInput:
    """The default capture device's level (0 to 100) and friendly name, as
    read (either may be None). Only reads: Get, never Set. Every object is
    released and COM is left as it was found. May raise (no ctypes, not
    Windows); read_windows_input catches."""
    from ctypes import byref, c_float, c_int, c_uint32, c_void_p
    com = com or _windows_com()
    hr = com.co_initialize(None, _COINIT_MULTITHREADED)
    if hr < 0 and hr != _RPC_E_CHANGED_MODE:
        return MicInput(None, None)
    held = []
    volume = name = None
    try:
        enumerator = c_void_p()
        if com.co_create_instance(byref(_guid(_CLSID_MMDEVICE_ENUMERATOR)), None, _CLSCTX_ALL,
                                  byref(_guid(_IID_IMMDEVICE_ENUMERATOR)),
                                  byref(enumerator)) < 0 or not enumerator.value:
            return MicInput(None, None)
        held.append(enumerator.value)
        device = c_void_p()
        if _call(com, enumerator.value, _ENUM_GET_DEFAULT_ENDPOINT, _E_CAPTURE, _E_CONSOLE,
                 byref(device), argtypes=(c_int, c_int, c_void_p)) < 0 or not device.value:
            return MicInput(None, None)          # no microphone at all
        held.append(device.value)

        endpoint = c_void_p()
        if _call(com, device.value, _DEVICE_ACTIVATE, byref(_guid(_IID_IAUDIO_ENDPOINT_VOLUME)),
                 _CLSCTX_ALL, None, byref(endpoint),
                 argtypes=(c_void_p, c_uint32, c_void_p, c_void_p)) >= 0 and endpoint.value:
            held.append(endpoint.value)
            scalar = c_float()
            if _call(com, endpoint.value, _VOLUME_GET_SCALAR, byref(scalar),
                     argtypes=(c_void_p,)) >= 0 and math.isfinite(scalar.value):
                volume = _whole_volume(int(math.floor(scalar.value * 100 + 0.5)))

        store = c_void_p()
        if _call(com, device.value, _DEVICE_OPEN_PROPERTY_STORE, _STGM_READ, byref(store),
                 argtypes=(c_uint32, c_void_p)) >= 0 and store.value:
            held.append(store.value)
            name = _friendly_name(com, store.value)
        return MicInput(volume, name)
    finally:
        for obj in reversed(held):
            try:
                _call(com, obj, _RELEASE)
            except Exception:
                pass
        if hr >= 0:
            com.co_uninitialize()


def _friendly_name(com: _Com, store) -> Optional[str]:
    import ctypes
    from ctypes import byref, c_void_p

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", ctypes.c_ubyte * 16), ("pid", ctypes.c_uint32)]
    guid, pid = _PKEY_DEVICE_FRIENDLY_NAME
    key = PROPERTYKEY()
    ctypes.memmove(key.fmtid, ctypes.addressof(_guid(guid)), 16)
    key.pid = pid
    pv = _PROPVARIANT()
    try:
        if (_call(com, store, _STORE_GET_VALUE, byref(key), byref(pv),
                  argtypes=(c_void_p, c_void_p)) >= 0
                and pv.vt == _VT_LPWSTR and pv.data):
            return ctypes.wstring_at(pv.data)
        return None
    finally:
        com.prop_variant_clear(byref(pv))


# ── what is kept ────────────────────────────────────────────────────────────

class VolumeLog:
    """The last reading and its time, in STATE_FILE in the data folder (asked
    for at each call), or at ``path``."""

    def __init__(self, path=None):
        self._path = path

    def path(self) -> Path:
        return Path(self._path) if self._path is not None else _data_dir() / STATE_FILE

    def load(self):
        """``(checked_at, volume, name)``, or None when there is no reading
        that can be used. ``name`` is the Windows microphone's short name."""
        try:
            raw = json.loads(self.path().read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        at = _level(raw.get("checked_at"))
        vol = raw.get("volume")
        vol = _whole_volume(vol)
        name = raw.get("name")
        name = name if isinstance(name, str) and 0 < len(name) <= NAME_CHARS else None
        return None if at is None else (at, vol, name)

    def save(self, checked_at: float, volume: Optional[int], name: Optional[str] = None) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        state = {"version": STATE_VERSION, "checked_at": checked_at, "volume": volume}
        if name:                    # Windows only: a Mac reading has no name
            state["name"] = name
        write_json_atomic(p, state)

    def clear(self) -> None:
        try:
            self.path().unlink()
        except FileNotFoundError:
            pass


# ── the advisor ─────────────────────────────────────────────────────────────

class Hooks(NamedTuple):
    platform: str                                   # sys.platform
    history: Callable[[], list]                     # the Journal's entries
    could_show: Callable[[str], bool]               # notifier.Notifier.could_show
    quiet: Callable[[], bool]                       # no take recording or being turned into text
    toast_allowed: Callable[[str], bool]            # notifier.Notifier.request, as a yes or no
    show: Callable[..., None]                       # (heading, body, buttons, timeout)
    read_volume: Callable[[], Optional[int]] = read_input_volume      # the Mac's
    read_input: Callable[[], Optional[MicInput]] = read_windows_input  # Windows'
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = lambda line: None
    store: object = None                            # a VolumeLog


class QuietMicAdvisor:
    """Told the level of each take (``take_done``), on its own thread; says
    so when the microphone is set low. Never raises."""

    def __init__(self, hooks: Hooks):
        self.h = hooks
        self._lock = threading.Lock()
        self._levels = deque(maxlen=TAKES)
        self._seeded = False
        self._busy = False
        self._reading = None        # (time, volume): the file's, or ours if it could not be written

    def _store(self):
        return self.h.store if self.h.store is not None else VolumeLog()

    def take_done(self, level_db) -> None:
        """A dictation finished with this speech level (dBFS, or None), and
        its Journal entry is saved. On a Mac or Windows, once the last ten
        takes are quiet, read the input volume (at most once a day) and, if it is low,
        show the tip at the next quiet moment. The first call reads the last
        ten levels from the Journal (this take's among them); later ones are
        counted here."""
        try:
            if self.h.platform not in ("darwin", "win32"):
                return
            level = _level(level_db)
            with self._lock:
                if not self._seeded:
                    self._seeded = True
                    try:
                        self._levels.extend(levels_of(self.h.history()))
                    except Exception as e:
                        self.h.log(f"[mic] earlier levels not read ({type(e).__name__})")
                elif level is not None:
                    self._levels.append(level)
                if level is None or not is_quiet(self._levels) or self._busy:
                    return
                self._busy = True
            try:
                self._check_and_tell()
            finally:
                with self._lock:
                    self._busy = False
        except Exception as e:
            try:
                self.h.log(f"[mic] volume tip skipped ({type(e).__name__})")
            except Exception:
                pass

    def _check_and_tell(self) -> None:
        if not self.h.could_show(TIP_KIND):
            return
        volume, name = self._todays_reading()
        if volume is None or volume >= LOW_VOLUME:
            return
        waited = 0.0
        while not self.h.quiet():
            if waited >= WAIT_S:
                return
            self.h.sleep(POLL_S)
            waited += POLL_S
        if not self.h.toast_allowed(TIP_KIND):
            return
        heading, body = _um.quiet_mic_message(volume, platform=self.h.platform, device=name)
        buttons = [{"label": _um.OPEN_SOUND_SETTINGS, "action": OPEN_SOUND_ACTION,
                    "kind": "primary"}] + _notifier.tip_buttons(TIP_KIND)
        self.h.show(heading, body, buttons, TIP_SECONDS)
        self.h.log(f"[mic] told the person the input volume is low ({volume})")

    def _todays_reading(self):
        """Today's ``(volume, name)``, or a new reading when the last is a day
        old (or the clock went back). A failed reading counts as one, so it is
        not tried again until tomorrow. The name is Windows' only."""
        store = self._store()
        now = self.h.clock()
        for last in (store.load(), self._reading):
            if last is not None and 0 <= now - last[0] < CHECK_EVERY_S:
                return last[1], last[2]
        volume = name = None
        if self.h.platform == "win32":
            got = self.h.read_input()
            if isinstance(got, MicInput):
                volume, name = _whole_volume(got.volume), short_device_name(got.name)
        else:
            volume = self.h.read_volume()
        self._reading = (now, volume, name)
        try:
            store.save(now, volume, name)
        except Exception as e:
            self.h.log(f"[mic] reading not kept ({type(e).__name__})")
        self.h.log(f"[mic] dictations are quiet; input volume read: "
                   f"{'unknown' if volume is None else volume}")
        return volume, name

    def forget(self) -> None:
        """Delete my data: the levels, and the reading."""
        with self._lock:
            self._levels.clear()
            self._seeded = False
            self._reading = None
        try:
            self._store().clear()
        except Exception as e:
            self.h.log(f"[mic] reading not deleted ({type(e).__name__})")
