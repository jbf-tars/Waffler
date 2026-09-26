"""macOS permissions for setup: ask with the real prompts, and read the Fn key.

Setup used to send people to System Settings for each permission. Waffler
was often missing from the list there, so they had to click +, type their
password, find Waffler.app in a file picker and switch it on, twice. The
microphone was never asked for at all, so the first practice recording came
back silent while macOS showed its prompt mid-hold.

Each Allow button now calls the prompt Apple provides for that permission:

  Microphone      AVCaptureDevice requestAccessForMediaType:completionHandler:
  Keyboard        CGRequestListenEventAccess()            (Input Monitoring)
  Typing for you  AXIsProcessTrustedWithOptions({prompt: true})  (Accessibility)

so Waffler is already in each list and the user only flips one switch. When a
permission was refused before, macOS will not ask again; then the only way is
the settings pane, which is opened instead.

The Fn key: on a Mac, Fn is Waffler's default hotkey, but macOS gives the Fn
(Globe) key its own job too. `defaults read com.apple.HIToolbox
AppleFnUsageType` says which: 0 Do Nothing, 1 Change Input Source, 2 Show
Emoji & Symbols, 3 Start Dictation. Anything but 0 (or no value, which means
the system default) means holding Fn also does that. Waffler says so and
offers the Keyboard settings; it never changes the setting itself.

Everything that touches macOS is looked up at call time and can be passed in,
so the tests run on any computer.
"""
from __future__ import annotations

import subprocess
import sys

PANES = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "input_monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "keyboard": "x-apple.systempreferences:com.apple.preference.keyboard",
}

# AVAuthorizationStatus
_MIC_STATUS = {0: "not_asked", 1: "restricted", 2: "denied", 3: "granted"}

# AppleFnUsageType
FN_DO_NOTHING = 0
_FN_JOBS = {
    1: "switches the input source",
    2: "opens the emoji picker",
    3: "starts Apple's dictation",
}


def is_mac(platform: str | None = None) -> bool:
    return (platform or sys.platform) == "darwin"


def open_pane(name: str, runner=subprocess.run) -> dict:
    """Open one System Settings pane. Returns {"ok": bool}."""
    url = PANES.get(name)
    if not url:
        return {"ok": False}
    try:
        runner(["/usr/bin/open", url], check=False, capture_output=True, timeout=10)
        return {"ok": True}
    except Exception:
        return {"ok": False}


# ── Microphone ───────────────────────────────────────────────────────────────

def microphone_status(avf=None, platform: str | None = None) -> str:
    """'granted', 'denied', 'restricted', 'not_asked', 'unknown', or
    'not_applicable' off a Mac."""
    if not is_mac(platform):
        return "not_applicable"
    try:
        if avf is None:
            import AVFoundation as avf  # noqa: N813
        code = avf.AVCaptureDevice.authorizationStatusForMediaType_(avf.AVMediaTypeAudio)
        return _MIC_STATUS.get(int(code), "unknown")
    except Exception:
        return "unknown"


def request_microphone(avf=None, platform: str | None = None, runner=subprocess.run) -> dict:
    """Show macOS's microphone prompt, or the pane when it was refused before.

    The prompt's answer arrives later; setup's 1-second check picks it up.
    """
    status = microphone_status(avf, platform)
    if status in ("not_applicable", "granted"):
        return {"ok": True, "status": status, "prompted": False}
    if status in ("denied", "restricted"):
        open_pane("microphone", runner)
        return {"ok": True, "status": status, "prompted": False, "opened_settings": True}
    try:
        if avf is None:
            import AVFoundation as avf  # noqa: N813
        avf.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            avf.AVMediaTypeAudio, lambda granted: None)
        return {"ok": True, "status": status, "prompted": True}
    except Exception:
        open_pane("microphone", runner)
        return {"ok": True, "status": status, "prompted": False, "opened_settings": True}


# ── Keyboard (Input Monitoring) ──────────────────────────────────────────────

def _load_coregraphics():
    import ctypes
    cg = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cg.CGPreflightListenEventAccess.restype = ctypes.c_bool
    cg.CGRequestListenEventAccess.restype = ctypes.c_bool
    return cg


def request_input_monitoring(cg=None, platform: str | None = None, runner=subprocess.run,
                             already_asked: bool = False) -> dict:
    """CGRequestListenEventAccess: adds Waffler to Input Monitoring and shows
    macOS's prompt the first time. Once refused, macOS stays quiet, so a
    second press opens the pane."""
    if not is_mac(platform):
        return {"ok": True, "granted": True, "prompted": False}
    try:
        if cg is None:
            cg = _load_coregraphics()
        if cg.CGPreflightListenEventAccess():
            return {"ok": True, "granted": True, "prompted": False}
        granted = bool(cg.CGRequestListenEventAccess())
        if not granted and already_asked:
            open_pane("input_monitoring", runner)
            return {"ok": True, "granted": False, "prompted": True, "opened_settings": True}
        return {"ok": True, "granted": granted, "prompted": True}
    except Exception:
        open_pane("input_monitoring", runner)
        return {"ok": True, "granted": False, "prompted": False, "opened_settings": True}


# ── Typing for you (Accessibility) ───────────────────────────────────────────

def request_accessibility(hiservices=None, platform: str | None = None, runner=subprocess.run,
                          already_asked: bool = False) -> dict:
    """AXIsProcessTrustedWithOptions with the prompt option: adds Waffler to
    the Accessibility list and shows macOS's dialog, whose button opens the
    right pane with Waffler already listed."""
    if not is_mac(platform):
        return {"ok": True, "granted": True, "prompted": False}
    try:
        if hiservices is None:
            import ApplicationServices as hiservices  # noqa: N813
        key = getattr(hiservices, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt")
        granted = bool(hiservices.AXIsProcessTrustedWithOptions({key: True}))
        if not granted and already_asked:
            open_pane("accessibility", runner)
            return {"ok": True, "granted": False, "prompted": True, "opened_settings": True}
        return {"ok": True, "granted": granted, "prompted": not granted}
    except Exception:
        open_pane("accessibility", runner)
        return {"ok": True, "granted": False, "prompted": False, "opened_settings": True}


# ── The Fn (Globe) key ───────────────────────────────────────────────────────

def read_fn_usage(runner=subprocess.run, platform: str | None = None):
    """AppleFnUsageType as an int, None when it isn't set (the system
    default), or FN_DO_NOTHING off a Mac."""
    if not is_mac(platform):
        return FN_DO_NOTHING
    try:
        out = runner(["/usr/bin/defaults", "read", "com.apple.HIToolbox", "AppleFnUsageType"],
                     check=False, capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if getattr(out, "returncode", 1) != 0:
        return None
    try:
        return int(str(out.stdout).strip())
    except ValueError:
        return None


def fn_conflict(usage, hotkey_keys) -> dict:
    """Whether holding the hotkey also sets off the Fn key's own job.

    Only matters when Fn is part of the hotkey. Returns
    {"conflict": bool, "title": str, "detail": str}; the text is empty when
    there is no conflict.
    """
    keys = [str(k).lower() for k in (hotkey_keys or [])]
    if "fn" not in keys or usage == FN_DO_NOTHING:
        return {"conflict": False, "title": "", "detail": ""}
    job = _FN_JOBS.get(usage, "does something else too")
    return {
        "conflict": True,
        "title": f"Your Fn key also {job}.",
        "detail": 'In Keyboard settings, set "Press \U0001F310 key to" to "Do Nothing".',
    }
