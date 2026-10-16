"""Shortcuts that run an action: a modifier combination plus one key.

The dictation hotkey (src/smart_hotkey.py, src/windows_hotkey.py) is a state
machine: hold to talk, Space to lock, Esc to cancel, with years of fixes for
Fn chatter and stuck modifiers. An action shortcut is much simpler, and is
kept out of that machine on purpose. It is "when this combination goes down,
call this function", for things like Transform (src/transform_action.py).

What it is
----------
* ``check`` says whether a set of keys can be an action shortcut, in one plain
  sentence if not, and returns the keys in one canonical order with the name to
  show. The rules: one letter, number or F key plus at least two modifier
  keys, one of them Control or Command (Control or Windows key on Windows),
  never Fn, nothing a computer or most apps already use, nothing that overlaps
  the dictation hotkey, and nothing another action already has. The reasons for
  "at least two" are practical: Shift or Option alone with a letter types
  text, and one modifier with a letter is nearly always somebody's shortcut.
* ``ActionHotkeys`` keeps the actions (``register``, ``unregister``,
  ``conflict``) and decides which of them a key press fires (``key_down``).
  A callback runs on a thread of its own, so the thread that heard the key is
  never held up by what the action does.
* The listeners only listen. They never swallow a key, so the combination
  also reaches the app in front, as any key does. That is why the Settings
  text suggests a combination other apps do not use.
* A hold-to-talk action (``register_hold``, used by Command mode) is told
  twice: ``on_down`` when the combination goes down and ``on_up`` when any
  part of it comes up (the key, or any one of its modifiers: letting go of
  Control first ends it as surely as letting go of the letter). Down and up
  of one action run one after the other on a thread of that action's own, in
  the order they happened, so an up can never overtake the down that started
  the recording.

Where the keys come from
------------------------
macOS: the dictation hotkey already owns the one event tap this process may
have (src/mac_hotkey_monitor.py explains the crash a second one caused). A
``MacComboObserver`` is added to that tap as one more handler, which only reads
the key and the modifier flags and always answers "do not swallow it". It sits
last in the tap's list and catches its own errors, so it cannot touch the
dictation hotkey's handlers. ``attach_listener`` adds it again whenever the
dictation hotkey is rebuilt after a change in Settings.

Windows: a watcher with a low-level keyboard hook of its own, on its own
thread, in the manner of src/windows_hotkey.py (it ignores keys that programs
send, and reads the modifiers from the keyboard state at the moment the key
goes down).

Both are thin: the decisions are in ``ActionHotkeys.key_down``, which the tests
call directly. Nothing here reads a file, the clipboard or the screen.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
import weakref
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import hotkey_rules

MAC = hotkey_rules.MAC
WINDOWS = hotkey_rules.WINDOWS

# A press of the same action inside this many seconds of the last is the same
# press (a key held down, a keyboard that doubles a key).
MIN_GAP_S = 0.5
# A key reported down this long ago, with no key-up since, is not still held.
HELD_FOR_S = 2.0

# ── Keys ─────────────────────────────────────────────────────────────────────

# Modifier names as the recorder in the window gives them (ui/app.js
# JS_KEY_TO_ID), in the order they are stored and shown.
MAC_MODIFIERS = ("control", "option", "shift", "cmd")
WINDOWS_MODIFIERS = ("win", "ctrl", "alt", "shift")

_MAC_ALIASES = {"ctrl": "control", "alt": "option", "command": "cmd", "meta": "cmd"}
_WINDOWS_ALIASES = {"control": "ctrl", "windows": "win", "meta": "win"}
# Names that mean a key the other platform has (and this one does not).
_MAC_ONLY = {"cmd", "command", "option", "fn"}
_WINDOWS_ONLY = {"win", "windows"}

_LETTERS = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"

# macOS virtual key codes (the physical key, whatever the layout says it types:
# the window's recorder reports the physical key too, KeyboardEvent.code).
MAC_KEYCODES: Dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
    "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}
_MAC_KEY_FOR_CODE = {code: name for name, code in MAC_KEYCODES.items()}

# Windows virtual-key codes.
WINDOWS_VKS: Dict[str, int] = {c: ord(c.upper()) for c in _LETTERS + _DIGITS}
WINDOWS_VKS.update({f"f{i}": 0x70 + i - 1 for i in range(1, 25)})
_WINDOWS_KEY_FOR_VK = {vk: name for name, vk in WINDOWS_VKS.items()}

# The physical modifier bits on a macOS key event (CGEventFlags).
_MAC_FLAG_BITS = {"control": 0x40000, "option": 0x80000, "shift": 0x20000, "cmd": 0x100000}

# Key events (Quartz): the values are fixed by the system.
_KEY_DOWN, _KEY_UP, _FLAGS_CHANGED = 10, 11, 12
_FIELD_KEYCODE, _FIELD_AUTOREPEAT = 9, 8
# The process that posted an event (0 for the keyboard itself): Waffler's own
# Cmd+C and Cmd+V come back through the tap, and are not the person.
_FIELD_SOURCE_PID = 41

# Combinations the computer or nearly every app already uses. Compared as the
# set of modifier names plus the key.
_RESERVED = {
    MAC: [
        {"control", "cmd", "q"},                  # lock the screen
        {"cmd", "shift", "q"},                    # log out
        {"cmd", "shift", "3"}, {"cmd", "shift", "4"}, {"cmd", "shift", "5"},   # screenshots
        {"cmd", "option", "d"},                   # hide or show the Dock
        {"cmd", "option", "m"},                   # minimise all windows of an app
        {"cmd", "option", "h"},                   # hide the others
        {"cmd", "option", "i"},                   # developer tools in browsers
        {"cmd", "option", "j"}, {"cmd", "option", "c"},   # more of the same
        {"control", "cmd", "f"},                  # full screen
    ],
    WINDOWS: [
        {"win", "ctrl", "d"}, {"win", "ctrl", "o"},       # new desktop, on-screen keyboard
        {"win", "shift", "s"},                            # snip
        {"win", "shift", "m"},                            # restore windows
        {"ctrl", "shift", "n"}, {"ctrl", "shift", "t"},   # new window, reopen a tab
        {"ctrl", "shift", "i"}, {"ctrl", "shift", "j"},   # developer tools
        {"ctrl", "shift", "p"}, {"ctrl", "shift", "c"},   # command palette, inspect
    ],
}


def _is_key(name: str) -> bool:
    return name in _LETTERS or name in _DIGITS or (
        name[:1] == "f" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24)


def _alias(key: str, platform: str) -> str:
    key = key.strip().lower()
    return (_MAC_ALIASES if platform == MAC else _WINDOWS_ALIASES).get(key, key)


def modifiers_of(platform: str) -> Tuple[str, ...]:
    return MAC_MODIFIERS if platform == MAC else WINDOWS_MODIFIERS


def _canonical_order(names: Iterable[str], platform: str) -> List[str]:
    mods = modifiers_of(platform)
    names = list(dict.fromkeys(names))
    return [m for m in mods if m in names] + [k for k in names if k not in mods]


def comparable(keys, platform: str) -> frozenset:
    """A set of key names that means the same on both platforms (Control is
    ``control`` on a Mac and ``ctrl`` on Windows, Option is Alt), for comparing
    one shortcut with another. Unknown or non-text entries are ignored."""
    same = {"control": "ctrl", "option": "alt", "command": "cmd"}
    out = set()
    for k in keys or ():
        if isinstance(k, str) and k.strip():
            k = _alias(k, platform)
            out.add(same.get(k, k))
    return frozenset(out)


def default_dictation_keys(platform: str) -> List[str]:
    return ["fn"] if platform == MAC else ["win", "ctrl"]


def dictation_keys(settings, platform: str) -> List[str]:
    """The dictation hotkey from settings.json (the default when none is saved)."""
    saved = (settings or {}).get("hotkey_keys") if isinstance(settings, dict) else None
    if isinstance(saved, list) and saved and all(isinstance(k, str) for k in saved):
        return list(saved)
    return default_dictation_keys(platform)


def display(keys, platform: str) -> str:
    """"Control + Option + T", as the window and the website name it."""
    return hotkey_rules.display(list(keys), platform) if keys else ""


# ── Checking a shortcut ──────────────────────────────────────────────────────

def overlaps_dictation(keys, dictation, platform: str) -> bool:
    """True when pressing ``keys`` would also hold the dictation hotkey down
    (it is part of them), or the dictation hotkey includes all of ``keys``."""
    mine, ptt = comparable(keys, platform), comparable(dictation, platform)
    return bool(mine and ptt and (ptt <= mine or mine <= ptt))


def check(keys, platform: str, dictation_hotkey: Sequence[str] = (),
          others: Iterable[Sequence[str]] = ()) -> dict:
    """Is ``keys`` a shortcut that can run an action?

    ``dictation_hotkey`` is the push-to-talk hotkey; ``others`` is the keys of
    the actions that already have a shortcut (not the one being checked).
    Returns ``{"ok": True, "keys": [...], "display": "..."}`` with the keys in
    their stored order, or ``{"ok": False, "error": "<one sentence>"}``.
    """
    if not isinstance(keys, (list, tuple)) or not keys \
            or not all(isinstance(k, str) and k.strip() for k in keys):
        return {"ok": False, "error": "Hold the keys you want, then save."}
    given = [k.strip().lower() for k in keys]
    if platform == WINDOWS:
        mac_only = [k for k in given if k in _MAC_ONLY]
        if mac_only:
            return {"ok": False, "error": f"{hotkey_rules.display([mac_only[0]], MAC)} is a Mac key. "
                                          f"On Windows use Ctrl, Alt or Shift."}
    else:
        win_only = [k for k in given if k in _WINDOWS_ONLY]
        if win_only:
            return {"ok": False, "error": "The Windows key is for Windows. On a Mac use Control, "
                                          "Option, Shift or Command."}
    names = [_alias(k, platform) for k in given]
    if "fn" in names:
        return {"ok": False, "error": "The Fn key can't be part of this shortcut. Use Control, "
                                      "Option, Shift or Command."
                                      if platform == MAC else "The Fn key can't be part of this shortcut."}
    mods = [n for n in names if n in modifiers_of(platform)]
    rest = [n for n in names if n not in modifiers_of(platform)]
    if not rest:
        return {"ok": False, "error": "Add a letter, a number or an F key to the shortcut."}
    if len(set(rest)) > 1:
        return {"ok": False, "error": "Use one letter, number or F key, with your modifier keys."}
    key = rest[0]
    if not _is_key(key) or (platform == MAC and key not in MAC_KEYCODES):
        shown = hotkey_rules.display([key], platform)
        return {"ok": False, "error": f"{shown} can't be used in this shortcut. Use a letter, "
                                      f"a number or an F key."}
    mods = list(dict.fromkeys(mods))
    strong = ("control", "cmd") if platform == MAC else ("ctrl", "win")
    if len(mods) < 2 or not any(m in strong for m in mods):
        need = ("Control or Command, with another modifier key" if platform == MAC
                else "Ctrl or the Windows key, with another modifier key")
        return {"ok": False, "error": f"Use {need}, so the shortcut doesn't type text or clash "
                                      f"with a shortcut in another app."}
    stored = _canonical_order(mods + [key], platform)
    here = comparable(stored, platform)
    if any(here == comparable(r, platform) for r in _RESERVED.get(platform, ())):
        return {"ok": False, "error": "Your computer or most apps already use that combination. "
                                      "Choose another."}
    if overlaps_dictation(stored, dictation_hotkey, platform):
        return {"ok": False, "error": "That overlaps your dictation hotkey, so one of them would "
                                      "fire by mistake. Choose another."}
    for other in others:
        theirs = comparable(other, platform)
        if here == theirs:
            return {"ok": False, "error": "Another shortcut already uses that combination. "
                                          "Choose another."}
        if theirs and (here <= theirs or theirs <= here):
            # The same key with more or fewer modifiers: pressed in a rush
            # (the letter a moment before the last modifier) it would set the
            # other one off.
            return {"ok": False, "error": "That overlaps another shortcut, so one of them would "
                                          "fire by mistake. Choose another."}
    return {"ok": True, "keys": stored, "display": display(stored, platform)}


def saved_shortcuts(settings, skip: str = "") -> Dict[str, List[str]]:
    """The action shortcuts saved in settings.json, by action: ``transform``
    (``transforms.hotkey``) and ``command`` (``command_mode.hotkey``). Read
    from the raw file with no checking, so a Transform setting can be checked
    against the Command shortcut without the two modules importing each other.
    ``skip`` leaves one action out (the one being saved). Anything that is not
    a list of names is no shortcut."""
    stored = settings if isinstance(settings, dict) else {}
    out: Dict[str, List[str]] = {}
    for action, section in (("transform", "transforms"), ("command", "command_mode")):
        if action == skip:
            continue
        block = stored.get(section)
        keys = block.get("hotkey") if isinstance(block, dict) else None
        if isinstance(keys, list) and keys and all(isinstance(k, str) and k.strip() for k in keys):
            out[action] = list(keys)
    return out


def dictation_clash_message(dictation_keys_, shortcuts: Iterable[Sequence[str]], platform: str) -> str:
    """When a new dictation hotkey would overlap one of the action shortcuts:
    the sentence that says so (the dictation hotkey is then not saved), else ""."""
    for keys in shortcuts:
        if keys and overlaps_dictation(keys, dictation_keys_, platform):
            return (f"That overlaps your shortcut {display(keys, platform)}, so one of them would "
                    f"fire by mistake. Change that shortcut first, or choose another hotkey.")
    return ""


# ── The registry and the decision ────────────────────────────────────────────

class _Serial:
    """Runs callbacks one after the other, in the order they were posted, on a
    thread that exists only while there is something to run. The key listener
    posts a hold's down and its up within milliseconds of each other; with a
    thread for each, the up could run before the down had begun."""

    def __init__(self, spawn: Callable[[Callable[[], None]], None], log: Callable[[str], None]):
        self._spawn = spawn
        self._log = log
        self._lock = threading.Lock()
        self._queue = collections.deque()
        self._draining = False

    def post(self, name: str, fn: Callable[[], None]) -> None:
        with self._lock:
            self._queue.append((name, fn))
            if self._draining:
                return
            self._draining = True
        self._spawn(self._drain)

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._draining = False
                    return
                name, fn = self._queue.popleft()
            try:
                fn()
            except Exception as e:      # noqa: BLE001 - one callback must not stop the next
                self._log(f"[shortcut] {name} failed: {type(e).__name__}")


class ActionHotkeys:
    """The actions that have a shortcut, and which of them a key press fires."""

    def __init__(self, platform: Optional[str] = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 log: Optional[Callable[[str], None]] = None,
                 spawn: Optional[Callable[[Callable[[], None]], None]] = None):
        self.platform = platform or (MAC if sys.platform == "darwin" else WINDOWS)
        self._clock = clock
        self._log = log or (lambda _m: None)
        self._spawn = spawn or self._spawn_thread
        self._lock = threading.RLock()
        self._actions: Dict[str, dict] = {}     # id -> {keys, mods, key, callback, on_up (a hold)}
        self._last_fired: Dict[str, float] = {}
        self._down: Dict[str, float] = {}       # key -> when it went down
        self._holds: Dict[str, dict] = {}       # action id -> {key, mods}: a hold in progress
        self._serial: Dict[str, _Serial] = {}   # action id -> its one-at-a-time runner
        self._observer = None
        self._attached = weakref.WeakSet()      # listeners whose tap carries the observer
        self._watcher = None

    @staticmethod
    def _spawn_thread(fn):
        threading.Thread(target=fn, daemon=True, name="ActionHotkey").start()

    # -- the actions -------------------------------------------------------

    def conflict(self, action_id: str, keys, dictation_hotkey: Sequence[str] = ()) -> Optional[str]:
        """Why ``keys`` cannot be this action's shortcut now (the same
        sentence ``register`` would return), or None."""
        verdict = check(keys, self.platform, dictation_hotkey, self._others(action_id))
        return None if verdict["ok"] else verdict["error"]

    def _others(self, action_id: str) -> List[List[str]]:
        with self._lock:
            return [a["keys"] for k, a in self._actions.items() if k != action_id]

    def register(self, action_id: str, keys, callback: Callable[[], None],
                 dictation_hotkey: Sequence[str] = ()) -> dict:
        """Run ``callback`` when ``keys`` go down. A second call for the same
        ``action_id`` replaces the first. Returns ``check``'s answer; nothing
        is registered when it is not ok."""
        verdict = check(keys, self.platform, dictation_hotkey, self._others(action_id))
        if not verdict["ok"]:
            return verdict
        return self._store(action_id, verdict, callback, None)

    def register_hold(self, action_id: str, keys, on_down: Callable[[], None],
                      on_up: Callable[[], None], dictation_hotkey: Sequence[str] = ()) -> dict:
        """A hold-to-talk action: ``on_down`` when ``keys`` go down, ``on_up``
        when any part of them comes up (the key or one of the modifiers). Both
        run on a thread of the action's own, in order. A second call for the
        same ``action_id`` replaces the first and ends a hold in progress.
        Returns ``check``'s answer; nothing is registered when it is not ok."""
        verdict = check(keys, self.platform, dictation_hotkey, self._others(action_id))
        if not verdict["ok"]:
            return verdict
        return self._store(action_id, verdict, on_down, on_up)

    def _store(self, action_id: str, verdict: dict, callback, on_up) -> dict:
        stored = verdict["keys"]
        mods = frozenset(k for k in stored if k in modifiers_of(self.platform))
        key = next(k for k in stored if k not in mods)
        with self._lock:
            ended = self._end_hold_locked(action_id)
            self._actions[action_id] = {"keys": stored, "mods": mods, "key": key,
                                        "callback": callback, "on_up": on_up}
            self._last_fired.pop(action_id, None)
        self._post_up(action_id, ended)
        return verdict

    def unregister(self, action_id: str) -> bool:
        with self._lock:
            self._last_fired.pop(action_id, None)
            ended = self._end_hold_locked(action_id)
            gone = self._actions.pop(action_id, None) is not None
        self._post_up(action_id, ended)
        return gone

    def registered(self) -> Dict[str, List[str]]:
        with self._lock:
            return {k: list(a["keys"]) for k, a in self._actions.items()}

    # -- the decision ------------------------------------------------------

    def key_down(self, key: str, modifiers: Iterable[str], repeat: bool = False) -> List[str]:
        """A key went down while exactly ``modifiers`` were held. Calls the
        callback of every action whose shortcut it is (on a thread of its
        own) and returns their ids. ``repeat`` is a key held down and
        repeating: it never fires."""
        now = self._clock()
        with self._lock:
            if not self._actions:
                return []               # nothing has a shortcut: every key is just a key
            # A key-up that never came must not keep a key "held" for ever.
            for k in [k for k, t in self._down.items() if now - t > HELD_FOR_S]:
                del self._down[k]
            # The key of a hold in progress is held for as long as the hold is,
            # whether or not the system keeps repeating it.
            held = key in self._down or any(h["key"] == key for h in self._holds.values())
            self._down[key] = now           # a repeating key keeps itself "held"
            if repeat or held:
                return []
            mods = frozenset(modifiers)
            fired = []
            for action_id, a in self._actions.items():
                if a["key"] != key or a["mods"] != mods:
                    continue
                last = self._last_fired.get(action_id)
                if last is not None and 0 <= now - last < MIN_GAP_S:
                    continue
                self._last_fired[action_id] = now
                if a.get("on_up") is not None:
                    self._holds[action_id] = {"key": key, "mods": a["mods"]}
                fired.append((action_id, a["callback"], a.get("on_up") is not None))
        for action_id, callback, hold in fired:
            if hold:
                self._serial_for(action_id).post(action_id, callback)
            else:
                self._spawn(lambda cb=callback, aid=action_id: self._run(aid, cb))
        return [aid for aid, _cb, _hold in fired]

    def key_up(self, key: str) -> None:
        with self._lock:
            self._down.pop(key, None)
            ended = [aid for aid in self._holds if self._holds[aid]["key"] == key]
            ups = {aid: self._end_hold_locked(aid) for aid in ended}
        for aid, on_up in ups.items():
            self._post_up(aid, on_up)

    def modifiers_changed(self, modifiers: Iterable[str]) -> None:
        """The modifier keys held are now exactly ``modifiers`` (a Mac reports
        them all at once). A hold ends when one of its own is not among them;
        an extra modifier changes nothing."""
        now_held = frozenset(modifiers)
        with self._lock:
            if not self._holds:
                return
            ended = [aid for aid, h in self._holds.items() if not h["mods"] <= now_held]
            ups = {aid: self._end_hold_locked(aid) for aid in ended}
        for aid, on_up in ups.items():
            self._post_up(aid, on_up)

    def modifier_up(self, modifier: str) -> None:
        """One modifier key came up (Windows reports them one at a time)."""
        with self._lock:
            if not self._holds:
                return
            ended = [aid for aid, h in self._holds.items() if modifier in h["mods"]]
            ups = {aid: self._end_hold_locked(aid) for aid in ended}
        for aid, on_up in ups.items():
            self._post_up(aid, on_up)

    def _end_hold_locked(self, action_id: str):
        """Take ``action_id``'s hold out of the books (call with the lock
        held) and return what to call for it, or None if none was running."""
        if self._holds.pop(action_id, None) is None:
            return None
        a = self._actions.get(action_id)
        return a.get("on_up") if a else None

    def _post_up(self, action_id: str, on_up) -> None:
        if on_up is not None:
            self._serial_for(action_id).post(action_id, on_up)

    def _serial_for(self, action_id: str) -> _Serial:
        with self._lock:
            serial = self._serial.get(action_id)
            if serial is None:
                serial = self._serial[action_id] = _Serial(self._spawn, self._log)
            return serial

    def _run(self, action_id: str, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as e:      # noqa: BLE001 - one action must not stop the others
            self._log(f"[shortcut] {action_id} failed: {type(e).__name__}")

    # -- the keyboard ------------------------------------------------------

    def attach_listener(self, listener) -> bool:
        """macOS: ask the dictation hotkey's listener to show this registry
        its key events (it owns the one event tap). Call it each time that
        listener is made. False when this listener has no tap to join, or
        this is not a Mac."""
        if self.platform != MAC:
            return False
        adder = getattr(listener, "attach_observer", None)
        if adder is None:
            return False
        if self._observer is None:
            self._observer = MacComboObserver(self)
        try:
            if listener in self._attached:
                return True             # this tap already carries it
        except TypeError:
            pass                        # not hashable: attach (once per call)
        try:
            adder(self._observer)
            try:
                self._attached.add(listener)
            except TypeError:
                pass
            return True
        except Exception as e:      # noqa: BLE001
            self._log(f"[shortcut] could not join the key listener: {type(e).__name__}")
            return False

    def start(self) -> bool:
        """Windows: start the keyboard watcher on its own thread. Does nothing
        on a Mac (``attach_listener`` does that job) and when already running."""
        if self.platform != WINDOWS or sys.platform != "win32":
            return False
        if self._watcher is None:
            self._watcher = WindowsComboWatcher(self, log=self._log)
        return self._watcher.start()

    def stop(self) -> None:
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            watcher.stop()


# ── macOS: one more handler on the dictation hotkey's tap ───────────────────

class MacComboObserver:
    """Reads key events from the dictation hotkey's event tap and tells the
    registry. ``handle`` always answers False: it never swallows a key, and
    nothing it does can raise into the tap's other handlers. Key events and
    changes of the modifier keys are both read (a hold ends when any of its
    modifiers does), and events Waffler posted itself (the Cmd+C and Cmd+V of
    reading a selection and pasting) are left out."""

    def __init__(self, registry: ActionHotkeys, *, get_flags=None, get_field=None, own_pid=None):
        self._registry = registry
        self._get_flags = get_flags
        self._get_field = get_field
        self._own_pid = own_pid if own_pid is not None else os.getpid()

    def _quartz(self):
        if self._get_flags is None or self._get_field is None:
            from Quartz import CGEventGetFlags, CGEventGetIntegerValueField
            self._get_flags = self._get_flags or CGEventGetFlags
            self._get_field = self._get_field or CGEventGetIntegerValueField
        return self._get_flags, self._get_field

    def _mine(self, get_field, event) -> bool:
        try:
            return int(get_field(event, _FIELD_SOURCE_PID)) == self._own_pid
        except Exception:      # noqa: BLE001 - an event that cannot say who sent it is the keyboard's
            return False

    def handle(self, event_type, event) -> bool:
        try:
            if event_type not in (_KEY_DOWN, _KEY_UP, _FLAGS_CHANGED):
                return False
            get_flags, get_field = self._quartz()
            if self._mine(get_field, event):
                return False
            if event_type == _FLAGS_CHANGED:
                self._registry.modifiers_changed(self._modifiers(int(get_flags(event))))
                return False
            key = _MAC_KEY_FOR_CODE.get(int(get_field(event, _FIELD_KEYCODE)))
            if key is None:
                return False
            if event_type == _KEY_UP:
                self._registry.key_up(key)
                return False
            mods = self._modifiers(int(get_flags(event)))
            repeat = bool(get_field(event, _FIELD_AUTOREPEAT))
            self._registry.key_down(key, mods, repeat=repeat)
        except Exception:      # noqa: BLE001 - see the class docstring
            pass
        return False

    @staticmethod
    def _modifiers(flags: int) -> List[str]:
        return [m for m, bit in _MAC_FLAG_BITS.items() if flags & bit]


# ── Windows: a keyboard hook of its own ──────────────────────────────────────

_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP, _WM_QUIT = 0x0100, 0x0101, 0x0104, 0x0105, 0x0012
_LLKHF_INJECTED = 0x10
_WINDOWS_MODIFIER_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}
# The hook reports the left and right modifier keys by their own codes (and a
# program may use the general ones): either side is the same modifier.
_WINDOWS_MODIFIER_FOR_VK = {0x10: "shift", 0xA0: "shift", 0xA1: "shift",
                            0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
                            0x12: "alt", 0xA4: "alt", 0xA5: "alt",
                            0x5B: "win", 0x5C: "win"}


def windows_event(registry: ActionHotkeys, vk: int, message: int, flags: int,
                  key_state: Callable[[int], bool]) -> None:
    """One keyboard event, as the hook receives it. A key a program sent
    (``flags`` has the injected bit) is ignored, as windows_hotkey.py ignores
    the paste's own Ctrl+V. ``key_state(vk)`` says whether a key is down now;
    the modifiers are read from it when the key goes down."""
    if flags & _LLKHF_INJECTED:
        return
    modifier = _WINDOWS_MODIFIER_FOR_VK.get(vk)
    if modifier is not None:
        if message in (_WM_KEYUP, _WM_SYSKEYUP):
            registry.modifier_up(modifier)
        return
    key = _WINDOWS_KEY_FOR_VK.get(vk)
    if key is None:
        return
    if message in (_WM_KEYUP, _WM_SYSKEYUP):
        registry.key_up(key)
    elif message in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
        mods = [m for m, vks in _WINDOWS_MODIFIER_VKS.items() if any(key_state(v) for v in vks)]
        registry.key_down(key, mods)


class WindowsComboWatcher:
    """A low-level keyboard hook on a thread of its own. Untested on a real
    Windows session by the automated tests: the decisions are in
    ``windows_event`` and ``ActionHotkeys.key_down``, which are."""

    def __init__(self, registry: ActionHotkeys, log=None):
        self._registry = registry
        self._log = log or (lambda _m: None)
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, daemon=True, name="ActionHotkeyHook")
        self._thread.start()
        self._ready.wait(2.0)
        return self._thread.is_alive()

    def stop(self) -> None:
        if self._thread_id:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            except Exception:      # noqa: BLE001
                pass

    def _run(self) -> None:
        import ctypes
        import ctypes.wintypes as wt

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                        ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        hookproc_t = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc_t, wt.HINSTANCE, wt.DWORD]
        user32.SetWindowsHookExW.restype = wt.HHOOK
        user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_long
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetCurrentThreadId.restype = wt.DWORD
        hook = {"h": None}

        def key_state(vk: int) -> bool:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        def proc(n_code, w_param, l_param):
            try:
                if n_code >= 0:
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    windows_event(self._registry, kb.vkCode, int(w_param), int(kb.flags), key_state)
            except Exception:      # noqa: BLE001 - never break the system's keyboard
                pass
            return user32.CallNextHookEx(hook["h"], n_code, w_param, l_param)

        # Kept alive for as long as the hook is: a collected callback crashes the process.
        callback = hookproc_t(proc)
        self._thread_id = kernel32.GetCurrentThreadId()
        hook["h"] = user32.SetWindowsHookExW(13, callback, None, 0)     # WH_KEYBOARD_LL
        if not hook["h"]:
            self._log("[shortcut] the keyboard hook could not be installed")
            self._ready.set()
            return
        self._ready.set()
        try:
            msg = wt.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook["h"])
