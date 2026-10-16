"""Which microphone is which: names, identity and the list of inputs (S12).

Waffler used to remember a microphone as a PortAudio *index*. An index is the
position in a list that PortAudio builds when it starts, and it is built
again before every new stream (audio.py ``_create_stream``), so after a
headset is plugged in "device 3" can be another microphone. This module
describes a microphone the way a person would, by name, with the host API (or
the system's own identifier, on a Mac) to tell two of the same name apart,
and turns that back into an index only at the moment a stream is opened.

It is pure: lists come in as plain data and nothing here opens a device or
starts PortAudio. The two readers at the bottom (``pa_inputs`` and
``live_inputs``) are the only places that ask the system, and both import
lazily and never raise.

Where a list comes from
-----------------------
``pa_inputs``    PortAudio's own list. It is only as fresh as the last time
                 PortAudio was started, which is every new stream, so it is
                 right when a stream is being opened and can be old otherwise.
``live_inputs``  The operating system's list right now (macOS, through
                 CoreAudio: src/mac_audio_devices.py), with the system's own
                 identifier and how the device is connected. ``None`` where
                 there is no such reader, and the callers use PortAudio's.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence

# How CoreAudio says a device is connected (four-character codes).
BLUETOOTH_TRANSPORTS = frozenset({"blue", "blea"})
VIRTUAL_TRANSPORTS = frozenset({"virt", "grup"})      # virtual, aggregate
BUILTIN_TRANSPORT = "bltn"

# The Windows multimedia API cuts a device's name at 31 characters, so the
# same microphone is "Microphone (Realtek High Defini" in one list and
# "Microphone (Realtek High Definition Audio)" in another.
MME_NAME_LIMIT = 31

# Microphones that are not a microphone: software that makes a device, or one
# that listens to what the computer plays. Hidden from "Add a microphone"
# unless "Show other devices" is open. By name, since PortAudio gives nothing
# else; CoreAudio's transport type adds to it where there is one.
_VIRTUAL_PATTERNS = tuple(re.compile(p) for p in (
    r"virtual", r"aggregate", r"loopback", r"blackhole", r"soundflower", r"vb-audio",
    r"vb audio", r"vb-?cable", r"voicemeeter", r"\bcable (?:input|output)\b", r"multi-?output",
    r"stereo mix", r"what u hear", r"wave out mix", r"teams audio", r"zoom ?audio",
    r"background music", r"\bndi\b", r"\bobs\b", r"\beqmac\b", r"\bkrisp\b",
    r"rogue amoeba", r"audio hijack", r"\bsoundsource\b", r"\bairfoil\b",
    r"\bsteam streaming\b", r"monitor of ",
))
_BUILTIN_PATTERNS = tuple(re.compile(p) for p in (
    r"\bmacbook\b", r"built-?in", r"\bimac\b", r"\bmac (?:mini|studio|pro)\b",
    r"\binternal\b",
))

_COUNTER = re.compile(r"\s*\(\d+\)\s*$")


# ── names ─────────────────────────────────────────────────────────────────

def norm_name(name) -> str:
    """A microphone's name for comparing: any capitals, spacing and accents
    form, and without the "(2)" the system adds to a second device of the
    same name, so "USB Mic (2)" and "USB Mic" are one microphone."""
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKC", name).casefold()
    text = " ".join(text.split())
    return _COUNTER.sub("", text).strip()


def same_name(a, b) -> bool:
    """Whether two names are one microphone. Equal after ``norm_name``, or one
    is the other cut at the Windows limit."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(short) == MME_NAME_LIMIT and long_.startswith(short)


def is_bluetooth(name) -> bool:
    """Whether Waffler treats this as a Bluetooth headset microphone: the same
    test that decides which microphones it avoids (audio.py), kept in one
    place. False if the recorder module cannot be loaded."""
    try:
        try:
            from audio import _name_is_bluetooth
        except ImportError:
            from src.audio import _name_is_bluetooth
        return bool(_name_is_bluetooth(name if isinstance(name, str) else ""))
    except Exception:
        return False


# Names that say Bluetooth for sure. audio.py's list, which decides which
# microphones Waffler avoids, also has "headset", "jabra" and "bose", which
# match wired headsets too: right for what to avoid, too loose for telling
# someone their wired headset is Bluetooth.
_STRONG_BLUETOOTH = ("airpods", "bluetooth", "beats", "buds")


def is_known_bluetooth(name) -> bool:
    n = name.lower() if isinstance(name, str) else ""
    return any(m in n for m in _STRONG_BLUETOOTH)


def is_virtual(name) -> bool:
    n = norm_name(name)
    return bool(n) and any(p.search(n) for p in _VIRTUAL_PATTERNS)


def is_builtin(name) -> bool:
    n = norm_name(name)
    return bool(n) and any(p.search(n) for p in _BUILTIN_PATTERNS)


# ── one microphone ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Device:
    name: str
    index: Optional[int] = None       # PortAudio's index when the list was made; never kept
    host_api: str = ""
    uid: Optional[str] = None         # the system's own identifier (macOS)
    channels: int = 1
    is_default: bool = False
    transport: Optional[str] = None   # CoreAudio's connection type, when known
    primary_api: bool = True          # listed by PortAudio's default host API

    @property
    def key(self) -> str:
        return norm_name(self.name)

    @property
    def bluetooth(self) -> bool:
        return self.transport in BLUETOOTH_TRANSPORTS or is_bluetooth(self.name)

    @property
    def known_bluetooth(self) -> bool:
        """Whether it is certainly Bluetooth: how the system says it is
        connected when it says, else a name that says so. What the window
        calls Bluetooth. ``bluetooth`` (above) is the wider test that decides
        what Waffler avoids."""
        if self.transport:
            return self.transport in BLUETOOTH_TRANSPORTS
        return is_known_bluetooth(self.name)

    @property
    def virtual(self) -> bool:
        return self.transport in VIRTUAL_TRANSPORTS or is_virtual(self.name)

    @property
    def builtin(self) -> bool:
        return self.transport == BUILTIN_TRANSPORT or is_builtin(self.name)

    def ident(self) -> Optional[str]:
        """The ``id`` kept beside the name in a ranking: the system's own
        identifier where there is one, else the host API."""
        if self.uid:
            return "uid:" + self.uid
        if self.host_api:
            return "api:" + self.host_api
        return None

    def entry(self) -> Dict[str, str]:
        out = {"name": self.name}
        ident = self.ident()
        if ident:
            out["id"] = ident
        return out


def devices_from_portaudio(infos: Iterable[dict], hostapis: Sequence[dict] = (),
                           default_index: Optional[int] = None,
                           primary_api: Optional[int] = None) -> List[Device]:
    """PortAudio's answers (``query_devices()``, ``query_hostapis()``) as the
    inputs, in index order. Anything that is not an input with a name is left
    out; the index is the position in ``infos``."""
    out: List[Device] = []
    apis = list(hostapis or ())
    for i, info in enumerate(list(infos or ())):
        if not isinstance(info, dict):
            continue
        try:
            channels = int(info.get("max_input_channels", 0))
        except (TypeError, ValueError):
            channels = 0
        name = info.get("name")
        if channels < 1 or not isinstance(name, str) or not name.strip():
            continue
        api = info.get("hostapi")
        api_name = ""
        if isinstance(api, int) and 0 <= api < len(apis) and isinstance(apis[api], dict):
            api_name = str(apis[api].get("name") or "")
        out.append(Device(name=name.strip(), index=i, host_api=api_name, channels=channels,
                          is_default=(default_index is not None and i == default_index),
                          primary_api=(primary_api is None or api == primary_api)))
    return out


def distinct(devices: Iterable[Device]) -> List[Device]:
    """One entry for each microphone. Windows lists a device once for each
    host API, and the system can number a second one of the same name; the
    one PortAudio's default host API lists (else the lowest index) stands for
    them, and is the default if any of them is. The order is the one the
    list came in."""
    devices = list(devices)
    order = sorted(range(len(devices)),
                   key=lambda n: (not devices[n].primary_api,
                                  devices[n].index if devices[n].index is not None else 10 ** 9, n))
    groups: List[List[int]] = []          # positions in `devices`, the one that stands for them first
    for n in order:
        for g in groups:
            if same_name(devices[n].name, devices[g[0]].name):
                g.append(n)
                break
        else:
            groups.append([n])
    return [replace(devices[g[0]], is_default=any(devices[n].is_default for n in g))
            for g in sorted(groups, key=min)]


# ── choosing ──────────────────────────────────────────────────────────────

def find(entry: dict, devices: Iterable[Device]) -> Optional[Device]:
    """The device a ranking entry means, or None when it is not here. The
    system's identifier wins when both have one (a renamed device is still the
    same device); otherwise the name, and between devices of one name the same
    host API as the entry was made with, then PortAudio's default host API,
    then the lowest index. Never by index."""
    if not isinstance(entry, dict):
        return None
    devices = list(devices)
    ident = entry.get("id") if isinstance(entry.get("id"), str) else ""
    if ident.startswith("uid:") and len(ident) > 4:
        for d in devices:
            if d.uid and d.uid == ident[4:]:
                return d
    same = [d for d in devices if same_name(entry.get("name"), d.name)]
    if not same:
        return None
    want_api = ident[4:] if ident.startswith("api:") else ""
    same.sort(key=lambda d: (bool(want_api) and d.host_api != want_api,
                             not d.primary_api,
                             d.index if d.index is not None else 10 ** 9))
    return same[0]


def first_present(ranking: Iterable[dict], devices: Iterable[Device]) -> Optional[Device]:
    """The highest-ranked entry that is here now."""
    devices = list(devices)
    for entry in ranking or ():
        found = find(entry, devices)
        if found is not None:
            return found
    return None


def index_of(device: Optional[Device], pa_devices: Iterable[Device]) -> Optional[int]:
    """PortAudio's index for ``device``, from a list made just now (by name:
    a list from the system carries no PortAudio index). None when PortAudio
    does not list it."""
    if device is None:
        return None
    if device.index is not None and not device.uid:
        # Came from PortAudio's own list: that index is the one.
        return device.index
    found = find({"name": device.name}, pa_devices)
    return found.index if found is not None else None


def default_of(devices: Iterable[Device]) -> Optional[Device]:
    for d in devices:
        if d.is_default:
            return d
    return None


# ── the two readers (the only calls into the system) ──────────────────────

def pa_inputs(locked: bool = False) -> List[Device]:
    """PortAudio's inputs now. Taken under the stream lock (audio.py), so a
    stream being made, which starts PortAudio again, is never raced. [] if it
    cannot be read. ``locked=True`` only for a caller that already holds that
    lock (the recorder's choice of microphone, made while it opens a stream)."""
    try:
        try:
            from audio import snapshot_devices
        except ImportError:
            from src.audio import snapshot_devices
        snap = snapshot_devices(locked=locked)
        if not snap:
            return []
        infos, hostapis, default_index, primary = snap
        return devices_from_portaudio(infos, hostapis, default_index, primary)
    except Exception:
        return []


def live_inputs() -> Optional[List[Device]]:
    """The operating system's inputs right now (macOS), or None where there is
    no way to ask without starting PortAudio."""
    try:
        try:
            import mac_audio_devices
        except ImportError:
            from src import mac_audio_devices
        return mac_audio_devices.list_inputs()
    except Exception:
        return None


def current_inputs() -> List[Device]:
    """The best list there is: the system's own where it can be asked, else
    PortAudio's."""
    live = live_inputs()
    return live if live is not None else pa_inputs()
