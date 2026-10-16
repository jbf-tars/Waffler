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

The same module answers a quick question each time the hotkey goes down, before
recording starts: is the microphone, or Accessibility, switched off? And, from
time to time while the app runs, is the keyboard listener still alive? A
permission taken away while Waffler runs leaves a tap that delivers no key at
all, so the press that would tell us never arrives (PermissionPrecheck).
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

try:
    import notifier as _notifier
    import user_messages as _msg
except ImportError:  # imported as src.mac_permissions
    import src.notifier as _notifier
    import src.user_messages as _msg

PANES = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "input_monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "keyboard": "x-apple.systempreferences:com.apple.preference.keyboard",
}

# The toast buttons that open one of the panes above (app.py _on_toast_action).
PANE_ACTIONS = {
    "open_mic_settings": "microphone",
    "open_ax_settings": "accessibility",
    "open_input_settings": "input_monitoring",
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


# ── Accessibility, read live ─────────────────────────────────────────────────

_app_services = None


def _load_application_services():
    global _app_services
    if _app_services is None:
        _app_services = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    return _app_services


def accessibility_trusted(lib=None, platform: str | None = None) -> Optional[bool]:
    """Is this process trusted for Accessibility? True or False, or None when
    the question could not be asked (off a Mac, or the framework is missing),
    so callers carry on as before.

    Goes through ctypes and ``AXIsProcessTrustedWithOptions(NULL)``, which asks
    macOS again on every call and never prompts. The PyObjC binding of
    ``AXIsProcessTrusted`` can keep answering False after the user turned the
    switch on, in a bundled app, which is why it is not used here. Costs
    microseconds, so it is safe on every hotkey press.
    """
    if not is_mac(platform):
        return None
    try:
        lib = lib or _load_application_services()
        try:
            fn = lib.AXIsProcessTrustedWithOptions
            fn.restype = ctypes.c_bool
            fn.argtypes = [ctypes.c_void_p]
            return bool(fn(None))
        except AttributeError:
            fn = lib.AXIsProcessTrusted
            fn.restype = ctypes.c_bool
            fn.argtypes = []
            return bool(fn())
    except Exception:
        return None


# ── Checked when the hotkey goes down ────────────────────────────────────────

@dataclass(frozen=True)
class PermissionProblem:
    """One missing permission, with the toast that explains it."""

    permission: str      # "microphone" | "accessibility" | "keyboard"
    status: str
    heading: str
    body: str
    button_label: str
    action: str          # a key of PANE_ACTIONS: the toast button's action id

    @property
    def pane(self) -> str:
        return PANE_ACTIONS[self.action]


def _problem(permission: str, status: str, message: str, action: str) -> PermissionProblem:
    heading, body = _msg.PERMISSION_MESSAGES[message]
    return PermissionProblem(permission, status, heading, body, "Open Settings", action)


def evaluate_permissions(mic_status: str, ax_trusted: Optional[bool],
                         listener_alive: Optional[bool] = True,
                         need_accessibility: bool = True) -> List[PermissionProblem]:
    """Which permissions are KNOWN to be missing, microphone first.

    ===================  =====================================  ============
    microphone           denied                                 microphone
                         restricted                             microphone
                         granted, not asked yet, unknown        nothing
    Accessibility        off (and wanted)                       accessibility
                         on, or the check failed                nothing
    key listener         dead, Accessibility not known to be    keyboard
                         off (the likelier cause)
                         alive, or not known                    nothing
    ===================  =====================================  ============

    "Not asked yet" never counts: the system prompt appears on first use, and
    sending someone to Settings first would be wrong. A check that failed
    (``None`` or "unknown") never counts either: nobody is stopped from
    dictating by a question Waffler could not answer.
    """
    problems: List[PermissionProblem] = []
    if mic_status == "denied":
        problems.append(_problem("microphone", mic_status, "microphone", "open_mic_settings"))
    elif mic_status == "restricted":
        problems.append(_problem("microphone", mic_status, "microphone_restricted",
                                 "open_mic_settings"))
    ax_off = need_accessibility and ax_trusted is False
    if ax_off:
        problems.append(_problem("accessibility", "denied", "accessibility", "open_ax_settings"))
    if listener_alive is False and ax_trusted is not False:
        problems.append(_problem("keyboard", "dead", "keyboard", "open_input_settings"))
    return problems


class PermissionPrecheck:
    """Asked when the hotkey goes down, before recording starts.

    ``check()`` returns the one problem to announce (a ``PermissionProblem``),
    or None to carry on exactly as before. At most one toast per press, at most
    one per permission every 60 s (the notifier's "permission_*" kinds keep the
    gaps, src/notifier.py), and a reader that errors counts as "unknown", never
    as a reason to block.

    The microphone read is slow (about 7 ms), so a "granted" answer is cached
    for ``mic_ttl`` seconds and refreshed in the background once stale; any
    other answer is read again on every press, so a new grant is noticed at
    once. Accessibility is read live (microseconds).

    ``check_idle()`` is the same question asked from time to time while the
    app idles: the hotkey's key listener dies silently when a permission is
    taken away, and then no press ever arrives to ask it. It says each thing
    once, and repeats a dead listener only every ten minutes, because nobody is
    pressing anything to ask for it.
    """

    def __init__(self, read_mic: Callable[[], str] = None,
                 read_ax: Callable[[], Optional[bool]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 mic_ttl: float = 30.0,
                 spawn: Callable[[Callable[[], None]], None] = None,
                 notifier=None):
        self._read_mic = read_mic or microphone_status
        self._read_ax = read_ax or accessibility_trusted
        self._clock = clock
        self._mic_ttl = mic_ttl
        self._spawn = spawn or (lambda fn: threading.Thread(
            target=fn, daemon=True, name="MicStatusRefresh").start())
        # The app passes its own notifier; alone, this keeps a private one in
        # memory on the same clock.
        self._notifier = notifier or _notifier.Notifier(clock=clock)
        self._mic_cache = None  # (status, read_at)
        self._refreshing = False
        self._lock = threading.Lock()
        self.last_status: Dict[str, object] = {}

    def _safe_mic(self) -> str:
        try:
            return self._read_mic()
        except Exception:
            return "unknown"

    def _safe_ax(self) -> Optional[bool]:
        try:
            return self._read_ax()
        except Exception:
            return None

    def _refresh_mic(self) -> None:
        status = self._safe_mic()
        with self._lock:
            self._mic_cache = (status, self._clock())
            self._refreshing = False

    def _mic(self) -> str:
        now = self._clock()
        with self._lock:
            cached = self._mic_cache
            if cached and cached[0] == "granted":
                refresh = (now - cached[1] >= self._mic_ttl) and not self._refreshing
                if refresh:
                    self._refreshing = True
                status = cached[0]
            else:
                status, refresh = None, False
        if status is not None:
            if refresh:
                self._spawn(self._refresh_mic)
            return status
        status = self._safe_mic()
        with self._lock:
            self._mic_cache = (status, now)
        return status

    def warm(self) -> None:
        """Prime the microphone cache (call once at start-up, off the hot path)."""
        self._refresh_mic()

    def check(self, need_accessibility: bool = True) -> Optional[PermissionProblem]:
        """Key-down: the first problem that may be announced now, or None."""
        mic = self._mic()
        ax = self._safe_ax() if need_accessibility else None
        self.last_status = {"microphone": mic, "accessibility": ax}
        for problem in evaluate_permissions(mic, ax, True, need_accessibility):
            if self._notifier.claim(f"permission_{problem.permission}"):
                return problem
        return None

    def check_idle(self, listener_alive: Optional[bool],
                   need_accessibility: bool = True) -> Optional[PermissionProblem]:
        """While idle: the one problem to announce now, or None.

        Accessibility being off is said once, and again only after it has been
        on in between. A dead key listener says Accessibility if that is off,
        else the keyboard (Input Monitoring), and is repeated every
        ten minutes because no key press will ever ask. The
        microphone is left to the key-down check."""
        ax = self._safe_ax() if need_accessibility else None
        problems = evaluate_permissions("unknown", ax, listener_alive, need_accessibility)
        active = {p.permission for p in problems}
        if "accessibility" not in active:
            self._notifier.reset_gap("permission_accessibility_idle")
        if "keyboard" not in active:
            self._notifier.reset_gap("permission_keyboard")
        for problem in problems:
            kind = "permission_accessibility_idle" if problem.permission == "accessibility" \
                else "permission_keyboard"
            if self._notifier.claim(kind):
                return problem
        return None
