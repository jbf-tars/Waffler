"""The microphones macOS lists right now, asked of CoreAudio (S12).

Why this exists: PortAudio makes its list of devices when it starts, and it
starts again only before a new stream is opened (audio.py ``_create_stream``).
It cannot be started again while a stream is open: that ends the stream whose
callback CoreAudio is about to call, and that is the crash the whole teardown
order in audio.py is there to prevent. So between two dictations PortAudio's
list is whatever it was at the last one, and a microphone plugged in a minute
ago is not in it. CoreAudio is asked directly instead.

What is asked is read-only and small: the list of device ids, and for each the
name, the system's own identifier, how many input channels it has and how it
is connected. It opens no device, makes no callback and registers nothing
(a property listener would call into Python from a CoreAudio thread, which is
the other thing audio_device_monitor.py is written to avoid). Each listing
takes under a millisecond.

Every buffer is sized from what CoreAudio says it needs, nothing is written
past it, and a status other than zero leaves that device or that answer out.
Nothing here raises; a Mac that does not answer gives None and the callers
fall back to PortAudio's list.
"""

from __future__ import annotations

import sys
import struct
import threading
from typing import List, Optional

try:
    from mic_devices import Device
except ImportError:  # imported as src.mac_audio_devices
    from src.mic_devices import Device

HOST_API = "Core Audio"

_SYSTEM_OBJECT = 1
_UTF8 = 0x08000100                 # kCFStringEncodingUTF8
_MAX_BLOB = 1 << 20                # no property here is anywhere near 1 MB
_MAX_TEXT = 1024


def _code(text: str) -> int:
    return struct.unpack(">I", text.encode("ascii"))[0]


_GLOBAL, _INPUT = _code("glob"), _code("inpt")
_DEVICES, _DEFAULT_INPUT = _code("dev#"), _code("dIn ")
_UID, _NAME = _code("uid "), _code("lnam")
_STREAM_CONFIG, _TRANSPORT = _code("slay"), _code("tran")


# ── reading the blobs CoreAudio hands back (pure, tested with made-up bytes) ──

def parse_ids(blob: Optional[bytes]) -> List[int]:
    """A list of device ids: native-endian 32-bit integers."""
    if not blob or len(blob) % 4:
        return []
    return list(struct.unpack(f"={len(blob) // 4}I", blob))


def parse_input_channels(blob: Optional[bytes]) -> int:
    """The channels of an input AudioBufferList: a count of buffers, padding
    to the pointer's alignment, then for each buffer its channel count, its
    size in bytes and a pointer (16 bytes on a 64-bit Mac). Short or odd data
    counts as no channels."""
    if not blob or len(blob) < 4:
        return 0
    count = struct.unpack_from("=I", blob, 0)[0]
    channels, offset = 0, 8
    for _ in range(min(count, 64)):
        if offset + 16 > len(blob):
            break
        channels += struct.unpack_from("=I", blob, offset)[0]
        offset += 16
    return channels


def code_text(value: Optional[int]) -> Optional[str]:
    """A four-character code (a transport type) as text: ``'usb '``."""
    if value is None:
        return None
    raw = struct.pack(">I", value & 0xFFFFFFFF)
    return raw.decode("latin-1") if all(32 <= b < 127 for b in raw) else None


# ── the calls ─────────────────────────────────────────────────────────────

_lock = threading.Lock()
_api = None            # (CoreAudio, CoreFoundation, Address) once loaded, False if it cannot be


def _load():
    global _api
    if _api is not None:
        return _api or None
    try:
        import ctypes

        class Address(ctypes.Structure):
            _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                        ("element", ctypes.c_uint32)]

        ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ca.AudioObjectGetPropertyDataSize.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32)]
        ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        ca.AudioObjectGetPropertyData.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                          ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        _api = (ca, cf, Address, ctypes)
    except Exception:
        _api = False
    return _api or None


def _blob(api, obj: int, selector: int, scope: int = _GLOBAL) -> Optional[bytes]:
    ca, _cf, Address, ctypes = api
    addr = Address(selector, scope, 0)
    size = ctypes.c_uint32(0)
    if ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(addr), 0, None, ctypes.byref(size)) != 0:
        return None
    if size.value == 0 or size.value > _MAX_BLOB:
        return None
    buf = ctypes.create_string_buffer(size.value)
    got = ctypes.c_uint32(size.value)
    if ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(got), buf) != 0:
        return None
    return buf.raw[:min(got.value, size.value)]


def _text(api, obj: int, selector: int) -> Optional[str]:
    """A string property. CoreAudio returns a CFString the caller owns, so it
    is released here whatever happens."""
    ca, cf, Address, ctypes = api
    addr = Address(selector, _GLOBAL, 0)
    ref = ctypes.c_void_p()
    size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    if ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size),
                                     ctypes.byref(ref)) != 0 or not ref.value:
        return None
    try:
        room = min(int(cf.CFStringGetMaximumSizeForEncoding(cf.CFStringGetLength(ref), _UTF8)) + 1,
                   _MAX_TEXT)
        buf = ctypes.create_string_buffer(room)
        if not cf.CFStringGetCString(ref, buf, room, _UTF8):
            return None
        return buf.value.decode("utf-8", "replace")
    finally:
        cf.CFRelease(ref)


def _word(blob: Optional[bytes]) -> Optional[int]:
    return struct.unpack("=I", blob)[0] if blob and len(blob) == 4 else None


def list_inputs() -> Optional[List[Device]]:
    """Every input device the Mac lists now, in the system's order, or None if
    CoreAudio cannot be asked (not a Mac, or it did not answer)."""
    if sys.platform != "darwin":
        return None
    api = _load()
    if api is None:
        return None
    try:
        with _lock:
            ids = parse_ids(_blob(api, _SYSTEM_OBJECT, _DEVICES))
            if not ids:
                return None
            default_id = _word(_blob(api, _SYSTEM_OBJECT, _DEFAULT_INPUT))
            out: List[Device] = []
            for obj in ids:
                channels = parse_input_channels(_blob(api, obj, _STREAM_CONFIG, _INPUT))
                if channels < 1:
                    continue
                name = _text(api, obj, _NAME)
                if not name or not name.strip():
                    continue
                out.append(Device(name=name.strip(), host_api=HOST_API, uid=_text(api, obj, _UID),
                                  channels=channels, is_default=(obj == default_id),
                                  transport=code_text(_word(_blob(api, obj, _TRANSPORT)))))
            return out
    except Exception:
        return None
