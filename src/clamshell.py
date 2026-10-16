"""Is the Mac's lid closed, and does that matter to the microphone? (S12)

With the lid shut the built-in microphone is covered and sounds muffled, but a
MacBook in clamshell mode (an external display, a keyboard) still offers it,
and often as the default. Waffler says so once a day (a tip) when a take is
started in that state on the built-in microphone, and offers to choose another.

The lid is read from the power manager's ``AppleClamshellState`` through
IOKit, in this process: a single registry read that takes microseconds, with
no subprocess to start and so none that could hang. It is asked at the key
going down and nowhere else, never on a timer. A Mac without a lid (a Mini, a
Studio, an iMac) has no such property and the answer is None, as is any
failure, and None never produces a hint.
"""

from __future__ import annotations

import sys
from typing import Optional

try:
    import mic_devices as md
except ImportError:  # imported as src.clamshell
    from src import mic_devices as md

_api = None          # (IOKit, CoreFoundation, ctypes) once loaded; False if it cannot be


def _load():
    global _api
    if _api is not None:
        return _api or None
    try:
        import ctypes
        iok = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        iok.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iok.IOServiceMatching.restype = ctypes.c_void_p
        iok.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        iok.IOServiceGetMatchingService.restype = ctypes.c_uint32
        iok.IORegistryEntryCreateCFProperty.argtypes = [ctypes.c_uint32, ctypes.c_void_p,
                                                        ctypes.c_void_p, ctypes.c_uint32]
        iok.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
        iok.IOObjectRelease.argtypes = [ctypes.c_uint32]
        iok.IOObjectRelease.restype = ctypes.c_int32
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFBooleanGetTypeID.argtypes = []
        cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        cf.CFBooleanGetValue.restype = ctypes.c_ubyte
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        _api = (iok, cf, ctypes)
    except Exception:
        _api = False
    return _api or None


def lid_closed() -> Optional[bool]:
    """True if the lid is closed, False if open, None if this Mac has no lid
    or cannot say. Never raises."""
    if sys.platform != "darwin":
        return None
    api = _load()
    if api is None:
        return None
    iok, cf, _ctypes = api
    entry = key = value = None
    try:
        entry = iok.IOServiceGetMatchingService(0, iok.IOServiceMatching(b"IOPMrootDomain"))
        if not entry:
            return None
        key = cf.CFStringCreateWithCString(None, b"AppleClamshellState", 0x08000100)
        if not key:
            return None
        value = iok.IORegistryEntryCreateCFProperty(entry, key, None, 0)
        if not value or cf.CFGetTypeID(value) != cf.CFBooleanGetTypeID():
            return None
        return bool(cf.CFBooleanGetValue(value))
    except Exception:
        return None
    finally:
        try:
            if value:
                cf.CFRelease(value)
            if key:
                cf.CFRelease(key)
            if entry:
                iok.IOObjectRelease(entry)
        except Exception:
            pass


def hint_due(lid_is_closed: Optional[bool], device_name: Optional[str],
             platform: str = sys.platform) -> bool:
    """Whether the closed-lid tip applies: a Mac, the lid known to be closed,
    and the microphone in use is the built-in one. Anything unknown is no."""
    return (platform == "darwin" and lid_is_closed is True
            and isinstance(device_name, str) and md.is_builtin(device_name))
