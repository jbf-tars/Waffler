"""What is around the dictation: the app in front, the selection, the cursor.

Three questions, each answered in one cheap, bounded call that never raises:

    frontmost_app()               -> AppContext(bundle_id, name, title, category)
    get_selected_text(...)        -> (text, method)   method: "ax" | "copy" | "none"
    get_text_around_cursor(...)   -> (before, after)

The app in front
----------------
``frontmost_app`` is asked when the hotkey goes down (the app the words are
for is the one the user was in when they pressed, not the one in front when
processing ends). macOS reads NSWorkspace, which is a lookup, not a
subprocess. The front window's title is read through Accessibility, only when
the permission is already granted and, by default, only for a browser (it is
the one place a title says something: which site is open, see
src/app_categories.py). Windows has the process name and nothing else. On any
failure the answer is ``UNKNOWN`` (empty names, category ``other``).

The selection
-------------
``get_selected_text`` first asks the focused element through Accessibility,
with an Accessibility messaging timeout so a hung app cannot hang Waffler. When
that cannot say, and only if the caller passes ``allow_copy_fallback=True``,
it presses Copy and puts the user's clipboard back exactly as it was
(src/selection_copy.py). The fallback is for features the user asked for
(Transforms, Command mode), never for ordinary dictation: nothing in the
dictation path calls this. It is a blocking call: from the pipeline run it
through the watchdog like every other blocking step, for example
``run.call(text_context.get_selected_text, allow_copy_fallback=True,
stage=..., deadline=...)``. ``read_selection`` is the same call with the
reason it came back empty, for the message a feature shows.

``get_text_around_cursor`` reads the text before and after the caret, if the
app shows it through Accessibility, otherwise ``("", "")``. Dictation does not
use it yet.

Privacy: the window title, the selection and the surrounding text are held in
memory by the caller for as long as it needs them and are never logged,
stored or sent by anything here. A password field is never read.

Accessibility is read through ctypes (src/mac_input.py), lazily, so this
module imports on Windows and in CI; the platform pieces are injectable, which
is how the tests run without an app, a window or a clipboard.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

import app_categories
import selection_copy

# ── The records ──────────────────────────────────────────────────────────────

METHOD_AX = "ax"
METHOD_COPY = "copy"
METHOD_NONE = "none"
METHODS = (METHOD_AX, METHOD_COPY, METHOD_NONE)

# Why a selection came back empty, for the feature to say in a sentence.
# "" means it was read. (The copy fallback adds its own: see selection_copy.)
OK = selection_copy.OK
NO_SELECTION = "no_selection"            # something is focused and nothing is selected
ACCESSIBILITY_OFF = "accessibility_off"  # macOS has not given Waffler Accessibility
SECURE_FIELD = "secure_field"            # a password field: never read
TOO_LARGE = "too_large"                  # more text than Waffler will take
NOT_READABLE = "not_readable"            # the app does not share it and no copy was asked for
NOT_ALLOWED_HERE = "not_allowed_here"    # a Windows console: Ctrl+C would stop its program

NAME_MAX = 80
BUNDLE_MAX = 120
TITLE_MAX = 300
MAX_SELECTION_CHARS = 200_000
MAX_VALUE_CHARS = 1_000_000

# Overall time for a selection read, and for one Accessibility message.
DEFAULT_TIMEOUT_S = 1.5
AX_CALL_TIMEOUT_S = 0.25
# The title is read while the hotkey is going down: two quick messages, short.
TITLE_TIMEOUT_S = 0.1
# A copy round trip needs at least this much of the budget to be worth starting.
MIN_COPY_BUDGET_S = 0.25


def _short(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


@dataclass(frozen=True)
class AppContext:
    """The app a dictation is for.

    ``bundle_id`` is empty on Windows, ``title`` is empty unless it was read
    (and is left out of ``repr`` so that logging a context never prints a
    window title), ``category`` is one of src/app_categories.CATEGORIES.
    """

    bundle_id: str = ""
    name: str = ""
    title: str = field(default="", repr=False)
    category: str = app_categories.OTHER

    @property
    def known(self) -> bool:
        return bool(self.bundle_id or self.name)


UNKNOWN = AppContext()


def make_context(bundle_id="", name="", title="") -> AppContext:
    """An ``AppContext`` from raw values: trimmed, bounded, categorised."""
    bundle, app, ttl = _short(bundle_id, BUNDLE_MAX), _short(name, NAME_MAX), _short(title, TITLE_MAX)
    return AppContext(bundle, app, ttl, app_categories.category_for(bundle, app, ttl))


@dataclass(frozen=True)
class SelectionResult:
    """``text`` and ``method`` as ``get_selected_text`` returns them, plus
    ``reason`` (``OK`` or why the text is empty)."""

    text: str = ""
    method: str = METHOD_NONE
    reason: str = NOT_READABLE

    def as_tuple(self) -> Tuple[str, str]:
        return self.text, self.method


# ── The Accessibility reads (macOS) ──────────────────────────────────────────

AX_TEXT = "text"
AX_EMPTY = "empty"              # the focused element says nothing is selected
AX_SECURE = "secure"            # a password field
AX_TOO_LARGE = "too_large"
AX_UNAVAILABLE = "unavailable"  # no focused element, no such attribute, a hung app...

_SECURE_SUBROLE = "AXSecureTextField"


@dataclass(frozen=True)
class AXRead:
    status: str
    text: str = ""


class MacAX:
    """The few Accessibility reads this module needs, over mac_input's lazily
    loaded ctypes bindings. Every message carries a timeout, so a hung app
    answers "unavailable" after a moment instead of hanging the caller. No
    method raises; anything unexpected is "unavailable"."""

    def __init__(self, libs_factory: Optional[Callable[[], object]] = None,
                 trusted: Optional[Callable[[], Optional[bool]]] = None):
        self._libs_factory = libs_factory
        self._trusted = trusted
        self._libs = None
        self._lock = threading.Lock()

    def _lib(self):
        with self._lock:
            if self._libs is None:
                if self._libs_factory is not None:
                    self._libs = self._libs_factory()
                else:
                    import mac_input
                    self._libs = mac_input._AXLibs()
            return self._libs

    def warm(self) -> None:
        self._lib()

    def trusted(self) -> bool:
        """Accessibility is granted right now (asked again every time)."""
        try:
            if self._trusted is not None:
                return bool(self._trusted())
            import mac_permissions
            return bool(mac_permissions.accessibility_trusted())
        except Exception:      # noqa: BLE001
            return False

    def window_title(self, pid: int, timeout: float) -> str:
        """Title of the app's focused window ('' when it has none or hides it)."""
        try:
            return self._window_title(int(pid), float(timeout))
        except Exception:      # noqa: BLE001
            return ""

    def selected_text(self, pid: int, timeout: float) -> AXRead:
        try:
            return self._selected_text(int(pid), float(timeout))
        except Exception:      # noqa: BLE001
            return AXRead(AX_UNAVAILABLE)

    def cursor_text(self, pid: int, timeout: float) -> Optional[Tuple[str, int, int]]:
        """``(value, selection_start, selection_length)`` of the focused text
        field, positions in UTF-16 units as Accessibility counts them; None
        when it cannot be read or is a password field."""
        try:
            return self._cursor_text(int(pid), float(timeout))
        except Exception:      # noqa: BLE001
            return None

    # -- internals -------------------------------------------------------

    def _window_title(self, pid: int, timeout: float) -> str:
        L = self._lib()
        app = L.application(pid, timeout)
        refs = []
        try:
            _, win = L.copy_attr(app, "AXFocusedWindow")
            refs.append(win)
            if win is None:
                return ""
            L.ax.AXUIElementSetMessagingTimeout(win, timeout)
            _, title = L.copy_attr(win, "AXTitle")
            refs.append(title)
            return L.to_str(title) if L.is_string(title) else ""
        finally:
            for r in refs:
                L.release(r)
            L.release_element(app)

    def _focused(self, L, pid: int, timeout: float, refs: list):
        """The app's focused element (retained, added to ``refs``) or None."""
        app = L.application(pid, timeout)
        try:
            _, el = L.copy_attr(app, "AXFocusedUIElement")
        finally:
            L.release_element(app)
        refs.append(el)
        if el is not None:
            L.ax.AXUIElementSetMessagingTimeout(el, timeout)
        return el

    def _is_secure(self, L, el, refs: list) -> bool:
        _, sub = L.copy_attr(el, "AXSubrole")
        refs.append(sub)
        return bool(L.is_string(sub) and L.to_str(sub) == _SECURE_SUBROLE)

    def _selected_text(self, pid: int, timeout: float) -> AXRead:
        L = self._lib()
        refs: list = []
        try:
            el = self._focused(L, pid, timeout, refs)
            if el is None:
                return AXRead(AX_UNAVAILABLE)
            if self._is_secure(L, el, refs):
                return AXRead(AX_SECURE)
            _, sel = L.copy_attr(el, "AXSelectedText")
            refs.append(sel)
            if sel is None or not L.is_string(sel):
                return AXRead(AX_UNAVAILABLE)
            length = L.string_length(sel)
            if length == 0:
                return AXRead(AX_EMPTY)
            if length > MAX_SELECTION_CHARS:
                return AXRead(AX_TOO_LARGE)
            text = L.to_str(sel)
            return AXRead(AX_TEXT, text) if text.strip() else AXRead(AX_EMPTY)
        finally:
            for r in refs:
                L.release(r)

    def _cursor_text(self, pid: int, timeout: float) -> Optional[Tuple[str, int, int]]:
        L = self._lib()
        refs: list = []
        try:
            el = self._focused(L, pid, timeout, refs)
            if el is None or self._is_secure(L, el, refs):
                return None
            _, val = L.copy_attr(el, "AXValue")
            refs.append(val)
            if val is None or not L.is_string(val) or L.string_length(val) > MAX_VALUE_CHARS:
                return None
            _, rng = L.copy_attr(el, "AXSelectedTextRange")
            refs.append(rng)
            placed = L.to_range(rng) if rng is not None else None
            if placed is None:
                return None
            return L.to_str(val), placed[0], placed[1]
        finally:
            for r in refs:
                L.release(r)


# ── Text around the caret ────────────────────────────────────────────────────

def split_at_selection(value: str, start: int, length: int) -> Tuple[str, str]:
    """``(before, after)``: the text before the selection starts and after it
    ends. ``start`` and ``length`` count UTF-16 units, as Accessibility does,
    so an emoji counts two; a position inside one is rounded away."""
    if not isinstance(value, str) or start < 0 or length < 0:
        return "", ""
    raw = value.encode("utf-16-le", errors="surrogatepass")
    units = len(raw) // 2
    start = min(start, units)
    end = min(start + length, units)
    before = raw[:2 * start].decode("utf-16-le", errors="ignore")
    after = raw[2 * end:].decode("utf-16-le", errors="ignore")
    return before, after


def tail(text: str, chars: int) -> str:
    """The last ``chars`` characters of ``text``, starting at a word boundary
    when the cut would otherwise split a word."""
    if chars <= 0:
        return ""
    if len(text) <= chars:
        return text
    cut = text[-chars:]
    if not text[-chars - 1].isspace() and not cut[0].isspace():
        for i, ch in enumerate(cut):
            if ch.isspace():
                return cut[i + 1:]
    return cut


def head(text: str, chars: int) -> str:
    """The first ``chars`` characters of ``text``, ending at a word boundary
    when the cut would otherwise split a word."""
    if chars <= 0:
        return ""
    if len(text) <= chars:
        return text
    cut = text[:chars]
    if not text[chars].isspace() and not cut[-1].isspace():
        for i in range(len(cut) - 1, -1, -1):
            if cut[i].isspace():
                return cut[:i]
    return cut


# ── The reader ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Front:
    """The app in front as the system reports it."""

    bundle_id: str = ""
    name: str = ""
    pid: int = 0


def _mac_front() -> Optional[Front]:
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    return Front(str(app.bundleIdentifier() or ""), str(app.localizedName() or ""),
                 int(app.processIdentifier()))


def _windows_front() -> Optional[Front]:
    import app_detection
    name = app_detection.frontmost_app_name()
    return Front("", name, 0) if name else None


class ContextReader:
    """The three questions over injected platform pieces.

    ``front`` returns the app in front (a ``Front`` or None), ``ax`` is a
    ``MacAX``-shaped object or None where there is no Accessibility,
    ``round_trip`` a ``selection_copy.CopyRoundTrip`` or None (or
    ``round_trip_factory``, called the first time a copy is wanted, so that
    nothing touches the clipboard object before then), ``platform`` the
    ``sys.platform`` value (it decides which apps may be sent a copy key).
    """

    def __init__(self, *, front: Callable[[], Optional[Front]], ax=None, round_trip=None,
                 round_trip_factory: Optional[Callable[[], object]] = None,
                 platform: Optional[str] = None, clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = lambda _m: None):
        self._front = front
        self._ax = ax
        self._round_trip = round_trip
        self._round_trip_factory = round_trip_factory
        self._platform = platform or sys.platform
        self._clock = clock
        self._log = log

    def warm(self) -> None:
        """Load AppKit and the Accessibility bindings now (the first use costs
        tens of milliseconds)."""
        try:
            self._front()
            if self._ax is not None:
                self._ax.warm()
        except Exception as e:      # noqa: BLE001
            self._log(f"[context] warm-up skipped: {type(e).__name__}")

    # -- the app in front ------------------------------------------------

    def frontmost_app(self, read_title: Optional[bool] = None,
                      timeout: float = TITLE_TIMEOUT_S) -> AppContext:
        """The app in front now. ``read_title`` None reads the window title
        only for a browser; True always (when Accessibility is granted); False
        never."""
        try:
            front = self._front()
            if front is None:
                return UNKNOWN
            title = ""
            want = (app_categories.is_browser(front.bundle_id, front.name)
                    if read_title is None else bool(read_title))
            if want and front.pid and self._ax is not None:
                try:
                    if self._ax.trusted():
                        title = self._ax.window_title(front.pid, timeout) or ""
                except Exception as e:      # noqa: BLE001 - the app is still known without it
                    self._log(f"[context] window title unavailable: {type(e).__name__}")
            return make_context(front.bundle_id, front.name, title)
        except Exception as e:      # noqa: BLE001
            self._log(f"[context] app lookup failed: {type(e).__name__}")
            return UNKNOWN

    # -- the selection ---------------------------------------------------

    def read_selection(self, allow_copy_fallback: bool = False,
                       timeout: float = DEFAULT_TIMEOUT_S) -> SelectionResult:
        try:
            return self._read_selection(allow_copy_fallback, timeout)
        except Exception as e:      # noqa: BLE001
            self._log(f"[context] selection read failed: {type(e).__name__}")
            return SelectionResult("", METHOD_NONE, NOT_READABLE)

    def _read_selection(self, allow_copy_fallback: bool, timeout: float) -> SelectionResult:
        budget = max(0.05, float(timeout))
        deadline = self._clock() + budget
        front = self._front()
        reason = NOT_READABLE
        if self._ax is not None:
            if not self._ax.trusted():
                # Without Accessibility macOS drops the copy keystroke too.
                return SelectionResult("", METHOD_NONE, ACCESSIBILITY_OFF)
            if front is not None and front.pid:
                read = self._ax.selected_text(front.pid, min(AX_CALL_TIMEOUT_S, budget / 2))
                if read.status == AX_TEXT:
                    return SelectionResult(read.text, METHOD_AX, OK)
                if read.status == AX_SECURE:
                    return SelectionResult("", METHOD_NONE, SECURE_FIELD)
                if read.status == AX_EMPTY:
                    return SelectionResult("", METHOD_NONE, NO_SELECTION)
                if read.status == AX_TOO_LARGE:
                    return SelectionResult("", METHOD_NONE, TOO_LARGE)
        if not allow_copy_fallback:
            return SelectionResult("", METHOD_NONE, reason)
        round_trip = self._get_round_trip()
        if round_trip is None:
            return SelectionResult("", METHOD_NONE, NOT_READABLE)
        if not selection_copy.keystroke_allowed(front.name if front else "", self._platform):
            return SelectionResult("", METHOD_NONE, NOT_ALLOWED_HERE)
        left = deadline - self._clock()
        if left < MIN_COPY_BUDGET_S:
            return SelectionResult("", METHOD_NONE, selection_copy.TIMED_OUT)
        copied = round_trip.read(left)
        if copied.ok:
            if len(copied.text) > MAX_SELECTION_CHARS:
                return SelectionResult("", METHOD_NONE, TOO_LARGE)
            return SelectionResult(copied.text, METHOD_COPY, OK)
        why = NO_SELECTION if copied.reason == selection_copy.NO_COPY else copied.reason
        return SelectionResult("", METHOD_NONE, why)

    def _get_round_trip(self):
        if self._round_trip is None and self._round_trip_factory is not None:
            self._round_trip = self._round_trip_factory()
            self._round_trip_factory = None
        return self._round_trip

    # -- the caret -------------------------------------------------------

    def text_around_cursor(self, chars: int = 300,
                           timeout: float = DEFAULT_TIMEOUT_S) -> Tuple[str, str]:
        try:
            if self._ax is None or chars <= 0 or not self._ax.trusted():
                return "", ""
            front = self._front()
            if front is None or not front.pid:
                return "", ""
            read = self._ax.cursor_text(front.pid, min(AX_CALL_TIMEOUT_S, max(0.05, float(timeout)) / 2))
            if read is None:
                return "", ""
            before, after = split_at_selection(*read)
            return tail(before, int(chars)), head(after, int(chars))
        except Exception as e:      # noqa: BLE001
            self._log(f"[context] caret read failed: {type(e).__name__}")
            return "", ""


# ── This computer's reader, and the public calls ─────────────────────────────

_logger: Callable[[str], None] = lambda _m: None
_reader: Optional[ContextReader] = None
_reader_lock = threading.Lock()


def set_logger(fn: Callable[[str], None]) -> None:
    """Route this module's one-line diagnostics (never any text, title or
    selection)."""
    global _logger, _reader
    _logger = fn or (lambda _m: None)
    _reader = None


def _default_reader() -> ContextReader:
    global _reader
    with _reader_lock:
        if _reader is None:
            log = lambda m: _logger(m)      # noqa: E731
            factory = lambda: selection_copy.default_round_trip(log)      # noqa: E731
            if sys.platform == "darwin":
                _reader = ContextReader(front=_mac_front, ax=MacAX(),
                                        round_trip_factory=factory, log=log)
            elif sys.platform == "win32":
                _reader = ContextReader(front=_windows_front, ax=None,
                                        round_trip_factory=factory, log=log)
            else:
                _reader = ContextReader(front=lambda: None, ax=None, round_trip=None, log=log)
        return _reader


def warm() -> None:
    """Load what the first call would otherwise load. Safe from any thread."""
    try:
        _default_reader().warm()
    except Exception as e:      # noqa: BLE001
        _logger(f"[context] warm-up skipped: {type(e).__name__}")


def frontmost_app(read_title: Optional[bool] = None) -> AppContext:
    """The app in front, as an ``AppContext``. Cheap, no subprocess, never
    raises; ``UNKNOWN`` when it cannot be told."""
    try:
        return _default_reader().frontmost_app(read_title)
    except Exception:      # noqa: BLE001
        return UNKNOWN


def read_selection(allow_copy_fallback: bool = False,
                   timeout: float = DEFAULT_TIMEOUT_S) -> SelectionResult:
    """``get_selected_text`` with the reason an empty answer is empty."""
    try:
        return _default_reader().read_selection(allow_copy_fallback, timeout)
    except Exception:      # noqa: BLE001
        return SelectionResult("", METHOD_NONE, NOT_READABLE)


def get_selected_text(allow_copy_fallback: bool = False,
                      timeout: float = DEFAULT_TIMEOUT_S) -> Tuple[str, str]:
    """``(text, method)`` for the text selected in the app in front.

    ``method`` is ``"ax"`` (read through Accessibility), ``"copy"`` (read by
    pressing Copy, clipboard put back) or ``"none"`` (with ``text == ""``).
    The copy is tried only with ``allow_copy_fallback=True``: pass it for a
    feature the user asked for, never for ordinary dictation. ``timeout`` is
    the whole budget in seconds. Blocking: run it under the watchdog.
    """
    return read_selection(allow_copy_fallback, timeout).as_tuple()


def get_text_around_cursor(chars: int = 300, timeout: float = DEFAULT_TIMEOUT_S) -> Tuple[str, str]:
    """``(before, after)``: up to ``chars`` characters either side of the
    caret (a selection is left out), cut at word boundaries; ``("", "")`` when
    the app does not show its text. Blocking: run it under the watchdog."""
    try:
        return _default_reader().text_around_cursor(chars, timeout)
    except Exception:      # noqa: BLE001
        return "", ""
