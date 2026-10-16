"""Shortcuts that run an action: a modifier combination plus one key.

The dictation hotkey (src/smart_hotkey.py, src/windows_hotkey.py) is a state
machine: hold to talk, Space to lock, Esc to cancel, with years of fixes for
Fn chatter and stuck modifiers. An action shortcut is much simpler, and is
kept out of that machine on purpose. It is "when this combination goes down,
call this function", for things like Transform (src/transform_action.py).

What it is
----------
* ``ACTIONS`` is the one list of the "extra" actions that can have a shortcut:
  Transform, Command mode (a hold), Paste last and Copy last. Each has a
  label, a kind (``press`` or ``hold``), a plain description, the place its
  keys are stored in settings.json, and the feature (if any) that must be on
  for it to count. The Shortcuts page (src/shortcut_settings.py) and every
  check read this list.
* ``check`` says whether a set of keys can be an action shortcut, in one plain
  sentence if not, and returns the keys in one canonical order with the name to
  show; ``validate`` does the same for a named action against what is saved,
  and is the one call both the settings checks and the key-recording dialog
  make. The rules: one letter, number or F key plus at least two modifier
  keys, one of them Control or Command (Control or Windows key on Windows),
  never Fn, nothing a computer or most apps already use, nothing that overlaps
  the dictation hotkey, the cancel key or another action. The reasons for "at
  least two" are practical: Shift or Option alone with a letter types text,
  and one modifier with a letter is nearly always somebody's shortcut. Two
  exceptions: an F key may stand alone as a press (F13 to F20 are free on
  most keyboards), and on a Mac a hold may be modifier keys alone (Control and
  Option held), which types nothing.
* ``ActionHotkeys`` keeps the actions (``register``, ``unregister``,
  ``conflict``) and decides which of them a key press fires (``key_down``).
  A callback runs on a thread of its own, so the thread that heard the key is
  never held up by what the action does.
* The listeners swallow the main key of a shortcut that is registered (its
  key-down, its repeats and its key-up) so the app in front never types it:
  ``swallow_down`` and ``swallow_up`` are the decision, the tests' table. The
  modifier keys always pass through, nothing is swallowed unless the modifiers
  are exactly the shortcut's, and nothing at all while the key-recording
  dialog is open or the dictation hotkey is held.
* A hold-to-talk action (``register_hold``, used by Command mode) is told
  twice: ``on_down`` when the combination goes down and ``on_up`` when any
  part of it comes up (the key, or any one of its modifiers: letting go of
  Control first ends it as surely as letting go of the letter). Down and up
  of one action run one after the other on a thread of that action's own, in
  the order they happened, so an up can never overtake the down that started
  the recording. A hold that is modifier keys alone starts once they have been
  held, with nothing else pressed, for ``MODIFIER_HOLD_S``.

Where the keys come from
------------------------
macOS: the dictation hotkey already owns the one event tap this process may
have (src/mac_hotkey_monitor.py explains the crash a second one caused). A
``MacComboObserver`` is added to that tap as one more handler. It sits last in
the tap's list and catches its own errors, so it cannot touch the dictation
hotkey's handlers; the only thing it can answer "swallow" for is the main key
of a registered shortcut. ``attach_listener`` adds it again whenever the
dictation hotkey is rebuilt after a change in Settings.

Windows: a watcher with a low-level keyboard hook of its own, on its own
thread, in the manner of src/windows_hotkey.py (it ignores keys that programs
send, and reads the modifiers from the keyboard state at the moment the key
goes down). The hook asks the same questions and swallows the same keys.

Both are thin: the decisions are in ``ActionHotkeys``, which the tests call
directly. Nothing here reads a file, the clipboard or the screen.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import hotkey_rules

MAC = hotkey_rules.MAC
WINDOWS = hotkey_rules.WINDOWS

# A press of the same action inside this many seconds of the last is the same
# press (a key held down, a keyboard that doubles a key).
MIN_GAP_S = 0.5
# A key reported down this long ago, with no key-up since, is not still held.
HELD_FOR_S = 2.0
# A hold-to-talk that has lasted this long never got its key-up (the action
# stops listening long before, and so does everyone): it is ended, so the next
# press is a press.
HOLD_STALE_S = 120.0
# A hold that is modifier keys alone (Mac) starts only after they have been
# held this long with nothing else pressed, so Control + Option on the way to
# an ordinary shortcut starts nothing.
MODIFIER_HOLD_S = 0.35
# A swallowed key with no key-up after this long (and no repeat to keep it
# alive) is forgotten: a key-up that never came can never leave a key
# swallowed. A key held down repeats far more often than this, even at the
# slowest setting; the pause before the first repeat is shorter.
SWALLOW_STALE_S = 2.5
# Windows sends no repeat flag, so a key-down is taken for a repeat when the
# same key was seen this recently (the pause before the first repeat is at most
# a second; after that they come every few hundredths). Any later key-down is a
# new press, whatever a lost key-up left in the books.
REPEAT_WINDOW_S = 1.1
# The key-recording dialog tells us it is open; if it never says it closed
# (the window was reloaded) the shortcuts come back after this long.
RECORDING_MAX_S = 120.0

# What a shortcut does with the key: pressed once, or held while speaking.
PRESS, HOLD = "press", "hold"

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

# Combinations the computer or nearly every app already uses, each with who
# uses it and for what, so the refusal can say so. Compared as the set of
# modifier names plus the key. The first block can be typed as a shortcut here
# (a letter, a number or an F key with modifiers); the second is keys the
# shortcuts cannot use at all (Space, Tab, Esc), named so that the refusal says
# why instead of only "can't be used".
_RESERVED = {
    MAC: [
        ({"control", "cmd", "q"}, "Your Mac", "lock the screen"),
        ({"cmd", "shift", "q"}, "Your Mac", "log you out"),
        ({"cmd", "shift", "3"}, "Your Mac", "take a screenshot"),
        ({"cmd", "shift", "4"}, "Your Mac", "take a screenshot"),
        ({"cmd", "shift", "5"}, "Your Mac", "open the screenshot tools"),
        ({"cmd", "option", "d"}, "Your Mac", "hide or show the Dock"),
        ({"cmd", "option", "m"}, "Your Mac", "minimise all windows of an app"),
        ({"cmd", "option", "h"}, "Your Mac", "hide the other apps"),
        ({"control", "cmd", "d"}, "Your Mac", "look a word up"),
        ({"control", "cmd", "f"}, "Your Mac", "enter full screen"),
        ({"cmd", "option", "i"}, "Most browsers", "open the developer tools"),
        ({"cmd", "option", "j"}, "Most browsers", "open the developer tools"),
        ({"cmd", "option", "c"}, "Most browsers", "inspect an element"),
        ({"cmd", "shift", "z"}, "Most apps", "redo"),
        ({"cmd", "shift", "t"}, "Most browsers", "reopen a closed tab"),
        ({"cmd", "shift", "n"}, "Most apps", "open a new window or folder"),
        ({"cmd", "shift", "g"}, "Most apps", "go to a folder or find the previous match"),
        ({"control", "f2"}, "Your Mac", "move to the menu bar"),
        ({"control", "f3"}, "Your Mac", "move to the Dock"),
        ({"control", "f4"}, "Your Mac", "move to the window toolbar"),
        ({"cmd", "f3"}, "Your Mac", "show the desktop"),
        ({"cmd", "f5"}, "Your Mac", "turn VoiceOver on or off"),
    ],
    WINDOWS: [
        ({"win", "ctrl", "d"}, "Windows", "add a desktop"),
        ({"win", "ctrl", "o"}, "Windows", "open the on-screen keyboard"),
        ({"win", "ctrl", "f4"}, "Windows", "close the desktop"),
        ({"win", "ctrl", "shift", "b"}, "Windows", "restart the graphics driver"),
        ({"win", "shift", "s"}, "Windows", "take a snip"),
        ({"win", "shift", "m"}, "Windows", "restore the windows"),
        ({"win", "alt", "g"}, "Windows", "open the Xbox Game Bar"),
        ({"win", "alt", "r"}, "Windows", "start a Game Bar recording"),
        ({"ctrl", "shift", "n"}, "Most apps", "open a new window"),
        ({"ctrl", "shift", "t"}, "Most browsers", "reopen a closed tab"),
        ({"ctrl", "shift", "i"}, "Most browsers", "open the developer tools"),
        ({"ctrl", "shift", "j"}, "Most browsers", "open the developer tools"),
        ({"ctrl", "shift", "p"}, "Most apps", "open the command palette"),
        ({"ctrl", "shift", "c"}, "Most browsers", "inspect an element"),
        ({"ctrl", "shift", "z"}, "Most apps", "redo"),
        ({"alt", "f4"}, "Windows", "close the window"),
        ({"ctrl", "f4"}, "Most apps", "close the tab or document"),
        ({"shift", "f10"}, "Windows", "open the context menu"),
    ],
}
_RESERVED_KEYS = {
    MAC: [
        ({"cmd", "space"}, "Your Mac", "open Spotlight"),
        ({"control", "space"}, "Your Mac", "change the input language"),
        ({"control", "cmd", "space"}, "Your Mac", "open the emoji picker"),
        ({"cmd", "tab"}, "Your Mac", "switch apps"),
        ({"cmd", "shift", "tab"}, "Your Mac", "switch apps"),
        ({"cmd", "option", "esc"}, "Your Mac", "open Force Quit"),
    ],
    WINDOWS: [
        ({"win", "space"}, "Windows", "switch the keyboard layout"),
        ({"win", "tab"}, "Windows", "show your open windows"),
        ({"alt", "tab"}, "Windows", "switch windows"),
        ({"alt", "space"}, "Windows", "open the window menu"),
        ({"ctrl", "esc"}, "Windows", "open the Start menu"),
        ({"ctrl", "shift", "esc"}, "Windows", "open Task Manager"),
    ],
}


def _verb(who: str) -> str:
    return "use" if who.startswith("Most") else "uses"


def reserved_list(platform: str) -> List[dict]:
    """The combinations that are refused because the system or most apps use
    them, as ``{"keys": [...], "display": "Control + Command + Q", "who": ...,
    "what": ..., "line": "Your Mac uses Control + Command + Q to lock the
    screen."}``, for the page that says which shortcuts cannot be used."""
    out = []
    for table in (_RESERVED_KEYS.get(platform, ()), _RESERVED.get(platform, ())):
        for names, who, what in table:
            keys = _canonical_order(names, platform)
            shown = display(keys, platform)
            out.append({"keys": keys, "display": shown, "who": who, "what": what,
                        "line": f"{who} {_verb(who)} {shown} to {what}."})
    return out


def _reserved_message(names, platform: str, table) -> Optional[str]:
    here = comparable(names, platform)
    for combo, who, what in table.get(platform, ()):
        if here == comparable(combo, platform):
            shown = display(_canonical_order(combo, platform), platform)
            return f"{who} already {_verb(who)} {shown} to {what}. Choose another."
    return None


# F1 to F24, by name: a test of the digits with str.isdigit() lets "f\u00b2" and
# "f\u0663" through to int(), which raises, and "f0001" through to no key at all.
_F_KEYS = frozenset(f"f{i}" for i in range(1, 25))


def _is_key(name: str) -> bool:
    return (len(name) == 1 and (name in _LETTERS or name in _DIGITS)) or name in _F_KEYS


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


def display(keys, platform: str) -> str:
    """"Control + Option + T", as the window and the website name it."""
    return hotkey_rules.display(list(keys), platform) if keys else ""


# ── Waiting for the modifier keys to come up ────────────────────────────────

def windows_modifiers_down() -> bool:
    """Is Shift, Ctrl, Alt or a Windows key physically down now? False on
    anything but Windows, and when that cannot be read."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        get = ctypes.windll.user32.GetAsyncKeyState
        return any(get(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))
    except Exception:      # noqa: BLE001 - what cannot be read is "none held"
        return False


def wait_for_modifiers_up(is_down: Callable[[], bool], timeout: float,
                          sleep: Callable[[float], None] = time.sleep,
                          clock: Callable[[], float] = time.monotonic, poll: float = 0.01) -> bool:
    """Block until ``is_down()`` is False. True when it came up, False when
    ``timeout`` seconds passed first. Paste last on Windows waits like this
    before it sends Ctrl+V: macOS' paste does it itself (src/pasteboard.py)."""
    end = clock() + timeout
    while True:
        try:
            if not is_down():
                return True
        except Exception:      # noqa: BLE001
            return True
        if clock() >= end:
            return False
        sleep(poll)


# ── The actions ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionSpec:
    """One action that can have a shortcut: what the Shortcuts page calls it,
    whether it is pressed or held, where its keys are saved, and the feature
    (if any) that has to be on for it to count."""
    id: str
    label: str
    kind: str                                   # PRESS or HOLD
    blurb: str                                  # one plain sentence for the page
    path: Tuple[str, ...]                       # where the keys are in settings.json
    feature: str = ""                           # the section that turns it on, "" for none
    feature_path: Tuple[str, ...] = ()          # where that section's switch is


ACTIONS: Tuple[ActionSpec, ...] = (
    ActionSpec("transform", "Transform", PRESS,
               "Rewrites the text you selected, in any app.",
               ("transforms", "hotkey"), "Transforms", ("transforms", "enabled")),
    ActionSpec("command", "Command mode", HOLD,
               "Hold, say what you want done, then let go.",
               ("command_mode", "hotkey"), "Command mode", ("command_mode", "enabled")),
    ActionSpec("paste_last", "Paste last", PRESS,
               "Pastes your last dictation again, where your cursor is.",
               ("paste_last_hotkey",)),
    ActionSpec("copy_last", "Copy last", PRESS,
               "Puts your last dictation on the clipboard.",
               ("copy_last_hotkey",)),
)
SPECS: Dict[str, ActionSpec] = {a.id: a for a in ACTIONS}

# The key that cancels a recording: Esc until the person chooses another. Any
# F key works (F1 to F20); a letter would be typed.
CANCEL_DEFAULT = "esc"
CANCEL_KEYS: Tuple[str, ...] = (CANCEL_DEFAULT,) + tuple(f"f{i}" for i in range(1, 21))
# An F key may be a shortcut on its own (as a press): these are the ones.
BARE_KEYS: Tuple[str, ...] = tuple(f"f{i}" for i in range(1, 21))
# On a Mac the Fn key of some external keyboards arrives as one of these (the
# dictation hotkey treats them as Fn while its hotkey is Fn).
_FN_LOOKALIKES = ("f13", "f14", "f15")
# While the dictation hotkey is Fn, F1 to F12 on a Mac are reached by holding Fn
# (unless the keyboard settings use them as standard function keys), which would
# start a recording; F13 to F15 are what some keyboards send for Fn itself.
_FN_BUSY_KEYS = tuple(f"f{i}" for i in range(1, 13)) + _FN_LOOKALIKES
_FN_BUSY_MESSAGE = ("Your dictation hotkey is Fn. On a Mac the F1 to F12 keys need the Fn key unless your keyboard "
                    "settings use them as standard function keys, and some keyboards send F13 to F15 as Fn, so "
                    "they would start a recording. Use F16 to F20, or another key.")


def _dig(stored, path):
    cur = stored
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _key_list(value) -> List[str]:
    if isinstance(value, list) and value and all(isinstance(k, str) and k.strip() for k in value):
        return list(value)
    return []


def stored_keys(settings, action_id: str) -> List[str]:
    """The keys saved for ``action_id`` in settings.json, [] for none. Read
    from the raw file with no checking."""
    spec = SPECS.get(action_id)
    return _key_list(_dig(settings if isinstance(settings, dict) else {}, spec.path)) if spec else []


def feature_on(settings, action_id: str) -> bool:
    """Does the feature this action belongs to count as on? Transform and
    Command mode only do when switched on; Paste last and Copy last always."""
    spec = SPECS.get(action_id)
    if spec is None or not spec.feature:
        return True
    return _dig(settings if isinstance(settings, dict) else {}, spec.feature_path) is True


def cancel_key_of(settings) -> str:
    """The cancel key from settings.json: Esc unless an F key was chosen."""
    value = settings.get("cancel_key") if isinstance(settings, dict) else None
    if isinstance(value, str) and value.strip().lower() in CANCEL_KEYS:
        return value.strip().lower()
    return CANCEL_DEFAULT


# ── Checking a shortcut ──────────────────────────────────────────────────────

def overlaps_dictation(keys, dictation, platform: str) -> bool:
    """True when pressing ``keys`` would also hold the dictation hotkey down
    (it is part of them), or the dictation hotkey includes all of ``keys``.

    The hands-free chord (the dictation hotkey and Space) needs no rule of its
    own: Space cannot be part of a shortcut here, so no shortcut can equal it,
    and one that sat inside it would sit inside the dictation hotkey."""
    mine, ptt = comparable(keys, platform), comparable(dictation, platform)
    return bool(mine and ptt and (ptt <= mine or mine <= ptt))


def _labelled(item):
    """``others`` entries are a list of keys, or ``(label, keys)``."""
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) \
            and isinstance(item[1], (list, tuple)):
        return item[0], item[1]
    return "", item


def _conflict(stored, platform: str, dictation_hotkey, others, cancel_key: str) -> Optional[str]:
    """The sentence for why ``stored`` (already in its stored form) clashes
    with the dictation hotkey, the cancel key or another action, else None."""
    here = comparable(stored, platform)
    if overlaps_dictation(stored, dictation_hotkey, platform):
        return ("That overlaps your dictation hotkey, so one of them would "
                "fire by mistake. Choose another.")
    if cancel_key and cancel_key != CANCEL_DEFAULT and cancel_key in here:
        return (f"{display([cancel_key], platform)} is your cancel key, so a shortcut can't use it. "
                f"Choose another.")
    for item in others:
        label, other = _labelled(item)
        theirs = comparable(other, platform)
        named = f" ({label})" if label else ""
        if here == theirs:
            return f"Another shortcut{named} already uses that combination. Choose another."
        if theirs and (here <= theirs or theirs <= here):
            # The same key with more or fewer modifiers: pressed in a rush
            # (the letter a moment before the last modifier) it would set the
            # other one off.
            return (f"That overlaps another shortcut{named}, so one of them would "
                    f"fire by mistake. Choose another.")
    return None


def _check_modifier_only(mods, platform: str, dictation_hotkey, others, cancel_key: str) -> dict:
    """A hold that is modifier keys alone (Mac only; Command mode). It types
    nothing, so nothing needs swallowing, but it must not be something a
    person holds on the way to a real shortcut."""
    mods = list(dict.fromkeys(mods))
    if len(mods) < 2:
        return {"ok": False, "error": "One modifier key on its own would start every time you use it in another "
                                      "shortcut. Hold two or more, such as Control and Command."}
    if not any(m in ("control", "cmd") for m in mods):
        return {"ok": False, "error": "Include Control or Command. Option and Shift together are used to type "
                                      "special characters and select text."}
    stored = _canonical_order(mods, platform)
    clash = _conflict(stored, platform, dictation_hotkey, others, cancel_key)
    if clash:
        return {"ok": False, "error": clash}
    return {"ok": True, "keys": stored, "display": display(stored, platform)}


def check(keys, platform: str, dictation_hotkey: Sequence[str] = (),
          others: Iterable = (), hold: bool = False, cancel_key: str = "") -> dict:
    """Is ``keys`` a shortcut that can run an action?

    ``dictation_hotkey`` is the push-to-talk hotkey; ``others`` is the keys of
    the actions that already have a shortcut (not the one being checked), each
    a list of keys or ``(label, keys)`` so the refusal can name the action.
    ``cancel_key`` is the key that cancels a recording ("" for Esc).
    ``hold`` says the shortcut is held while the person speaks (Command mode),
    not pressed once. A hold may be modifier keys alone on a Mac. On Windows
    Ctrl and Alt together (without the Windows key) are the AltGr key on many
    keyboards, so a letter or number with them would be typed into the
    person's text: refused for every shortcut, and for a hold whatever its key.
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
        if hold and platform == MAC:
            return _check_modifier_only(mods, platform, dictation_hotkey, others, cancel_key)
        return {"ok": False, "error": "Add a letter, a number or an F key to the shortcut."}
    if len(set(rest)) > 1:
        return {"ok": False, "error": "Use one letter, number or F key, with your modifier keys."}
    key = rest[0]
    mods = list(dict.fromkeys(mods))
    if not _is_key(key) or (platform == MAC and key not in MAC_KEYCODES):
        reason = _reserved_message(mods + [key], platform, _RESERVED_KEYS)
        if reason:
            return {"ok": False, "error": reason}
        shown = hotkey_rules.display([key], platform)
        return {"ok": False, "error": f"{shown} can't be used in this shortcut. Use a letter, "
                                      f"a number or an F key."}
    strong = ("control", "cmd") if platform == MAC else ("ctrl", "win")
    alone_ok = not hold and key in BARE_KEYS        # an F key may be pressed with few or no modifiers
    if not alone_ok and (len(mods) < 2 or not any(m in strong for m in mods)):
        need = ("Control or Command, with another modifier key" if platform == MAC
                else "Ctrl or the Windows key, with another modifier key")
        tail = "" if hold else " An F key from F1 to F20 can also be used on its own."
        return {"ok": False, "error": f"Use {need}, so the shortcut doesn't type text or clash "
                                      f"with a shortcut in another app.{tail}"}
    if platform == WINDOWS and "ctrl" in mods and "alt" in mods and "win" not in mods \
            and (hold or key in _LETTERS or key in _DIGITS):
        return {"ok": False, "error": "Ctrl and Alt together are the AltGr key on many keyboards, and it types "
                                      "letters. Use Ctrl with Shift, or the Windows key, instead."}
    if platform == MAC and key in _FN_BUSY_KEYS and "fn" in comparable(dictation_hotkey, platform):
        return {"ok": False, "error": _FN_BUSY_MESSAGE}
    stored = _canonical_order(mods + [key], platform)
    reason = _reserved_message(stored, platform, _RESERVED)
    if reason:
        return {"ok": False, "error": reason}
    clash = _conflict(stored, platform, dictation_hotkey, others, cancel_key)
    if clash:
        return {"ok": False, "error": clash}
    return {"ok": True, "keys": stored, "display": display(stored, platform)}


def saved_shortcuts(settings, skip: str = "") -> Dict[str, List[str]]:
    """The action shortcuts saved in settings.json, by action: ``transform``
    (``transforms.hotkey``), ``command`` (``command_mode.hotkey``),
    ``paste_last`` and ``copy_last``. Read from the raw file with no checking,
    so one setting can be checked against the others without the modules
    importing each other. ``skip`` leaves one action out (the one being
    saved). Anything that is not a list of names is no shortcut. A shortcut
    counts whether or not its feature is on: switching the feature on later
    must not bring a clash with it."""
    out: Dict[str, List[str]] = {}
    for spec in ACTIONS:
        if spec.id == skip:
            continue
        keys = stored_keys(settings, spec.id)
        if keys:
            out[spec.id] = keys
    return out


def validate(action_id: str, keys, platform: str, settings=None, *, dictation_hotkey=None) -> dict:
    """``check`` for one named action against what is saved in ``settings``
    (settings.json as it is now): the dictation hotkey, the cancel key and the
    other actions' shortcuts. The one call both the settings checks and the
    key-recording dialog make, so the dialog says what a save would say.
    ``dictation_hotkey`` overrides the saved one (a dictation hotkey about to
    be saved)."""
    spec = SPECS[action_id]
    ptt = dictation_hotkey if dictation_hotkey is not None else dictation_keys(settings, platform)
    others = [(SPECS[a].label, k) for a, k in saved_shortcuts(settings, skip=action_id).items()]
    return check(keys, platform, ptt, others, hold=spec.kind == HOLD, cancel_key=cancel_key_of(settings))


def cancel_problem(value, platform: str, settings=None, *, dictation_hotkey=None) -> Optional[str]:
    """A sentence for why ``value`` cannot be the cancel key, else None. One
    key: Esc, or an F key. Not a key of the dictation hotkey, not the key of a
    shortcut, and on a Mac not an F13 to F15 while the dictation hotkey is Fn."""
    if not isinstance(value, str) or not value.strip():
        return "Choose one key to cancel with."
    name = value.strip().lower()
    name = "esc" if name == "escape" else name
    if name not in CANCEL_KEYS:
        return "Cancel can be Esc or an F key, from F1 to F20."
    if name == CANCEL_DEFAULT:
        return None
    ptt = dictation_hotkey if dictation_hotkey is not None else dictation_keys(settings, platform)
    shown = display([name], platform)
    if name in comparable(ptt, platform):
        return f"{shown} is part of your dictation hotkey. Choose another key to cancel with."
    if platform == MAC and name in _FN_LOOKALIKES and "fn" in comparable(ptt, platform):
        return ("Some keyboards send the Fn key as F13, F14 or F15, and your dictation hotkey is Fn. "
                "Use F16 to F20, or another key.")
    for action_id, keys in saved_shortcuts(settings).items():
        if name in comparable(keys, platform):
            return f"{SPECS[action_id].label} uses {shown}. Choose another key to cancel with."
    return None


def default_dictation_keys(platform: str) -> List[str]:
    return ["fn"] if platform == MAC else ["win", "ctrl"]


def dictation_keys(settings, platform: str) -> List[str]:
    """The dictation hotkey from settings.json (the default when none is saved)."""
    saved = (settings or {}).get("hotkey_keys") if isinstance(settings, dict) else None
    if isinstance(saved, list) and saved and all(isinstance(k, str) for k in saved):
        return list(saved)
    return default_dictation_keys(platform)


def dictation_clash_message(dictation_keys_, shortcuts: Iterable[Sequence[str]], platform: str,
                            cancel_key: str = "") -> str:
    """When a new dictation hotkey would overlap one of the action shortcuts,
    or include the cancel key: the sentence that says so (the dictation hotkey
    is then not saved), else "". An Esc cancel key is not checked (it never
    was, and Esc is not a key a hotkey is made of)."""
    shortcuts = [k for k in shortcuts if k]
    for keys in shortcuts:
        if overlaps_dictation(keys, dictation_keys_, platform):
            return (f"That overlaps your shortcut {display(keys, platform)}, so one of them would "
                    f"fire by mistake. Change that shortcut first, or choose another hotkey.")
    if cancel_key and cancel_key != CANCEL_DEFAULT and cancel_key in comparable(dictation_keys_, platform):
        return (f"That includes {display([cancel_key], platform)}, which cancels a recording. "
                f"Change the cancel key first, or choose another hotkey.")
    if platform == MAC and "fn" in comparable(dictation_keys_, platform):
        # Fn would answer to these keys too (see _FN_BUSY_KEYS).
        for keys in shortcuts:
            if any(k in _FN_BUSY_KEYS for k in comparable(keys, platform)):
                return (f"Your shortcut {display(keys, platform)} uses an F key that Fn would also start, "
                        f"on a Mac. Change that shortcut first, or choose another hotkey.")
        if cancel_key in _FN_LOOKALIKES:
            return (f"Your cancel key {display([cancel_key], platform)} is what some keyboards send for Fn. "
                    f"Change the cancel key first, or choose another hotkey.")
    return ""


# ── The registry and the decision ────────────────────────────────────────────

class _Dispatcher:
    """Starts the threads that actions run on, from one long-lived thread of
    its own. The keyboard's callback (an event tap on a Mac, a hook on Windows)
    must return at once or the system disables it, and starting a thread waits
    for the new thread to be running; a callback that only queues the work
    never waits for anything. Each action still gets a thread of its own, so a
    slow one (a paste waiting for the modifiers to come up) holds up no other."""

    def __init__(self):
        self._queue = collections.deque()
        self._wake = threading.Semaphore(0)
        self._thread = None
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="ActionHotkeyStarter")
                self._thread.start()
        self._queue.append(fn)
        self._wake.release()

    def _loop(self) -> None:
        while True:
            self._wake.acquire()
            try:
                fn = self._queue.popleft()
            except IndexError:
                continue
            try:
                threading.Thread(target=fn, daemon=True, name="ActionHotkey").start()
            except Exception:      # noqa: BLE001 - out of threads: the action cannot run now
                pass


_DISPATCHER = _Dispatcher()


def _dispatcher() -> _Dispatcher:
    return _DISPATCHER


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

    def enqueue(self, name: str, fn: Callable[[], None]) -> bool:
        """Put a callback in the queue. True when nothing is draining it, so
        the caller is to ``kick`` the queue once it has let go of its own
        locks. Queueing under the caller's lock is what keeps a hold's down
        ahead of its up: the order is then the order the books were written in,
        not the order two threads got round to posting."""
        with self._lock:
            self._queue.append((name, fn))
            if self._draining:
                return False
            self._draining = True
            return True

    def kick(self, name: str) -> None:
        """Start the thread that runs the queue (after ``enqueue`` said so)."""
        try:
            self._spawn(self._drain)
        except Exception as e:      # noqa: BLE001 - out of threads: what is queued cannot run now
            with self._lock:
                self._queue.clear()
                self._draining = False          # the next press may try again
            self._log(f"[shortcut] {name} could not start: {type(e).__name__}")

    def post(self, name: str, fn: Callable[[], None]) -> None:
        if self.enqueue(name, fn):
            self.kick(name)

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
                 spawn: Optional[Callable[[Callable[[], None]], None]] = None,
                 timer: Optional[Callable[[float, Callable[[], None]], object]] = None):
        self.platform = platform or (MAC if sys.platform == "darwin" else WINDOWS)
        self._clock = clock
        self._log = log or (lambda _m: None)
        self._spawn = spawn or self._spawn_thread
        self._timer = timer or self._start_timer
        self._lock = threading.RLock()
        # id -> {keys, mods, key (None for modifier keys alone), callback, on_up (a hold)}
        self._actions: Dict[str, dict] = {}
        self._last_fired: Dict[str, float] = {}
        self._down: Dict[str, float] = {}       # key -> when it went down
        self._holds: Dict[str, dict] = {}       # action id -> {key, mods}: a hold in progress
        self._pending: Dict[str, object] = {}   # action id -> token: modifier keys held, not yet a hold
        self._swallowed: Dict[str, float] = {}  # key -> when we last swallowed it
        self._serial: Dict[str, _Serial] = {}   # action id -> its one-at-a-time runner
        self._recording_since: Optional[float] = None   # the key-recording dialog is open
        self._cancel_key = CANCEL_DEFAULT
        self._ptt_probe: Optional[Callable[[], bool]] = None
        self._observer = None
        self._attached = weakref.WeakSet()      # listeners whose tap carries the observer
        self._watcher = None

    @staticmethod
    def _spawn_thread(fn):
        _dispatcher().submit(fn)

    @staticmethod
    def _start_timer(delay: float, fn: Callable[[], None]):
        t = threading.Timer(delay, fn)
        t.daemon = True
        t.start()
        return t

    # -- the actions -------------------------------------------------------

    def set_cancel_key(self, key: str) -> None:
        """The key that cancels a recording, so no shortcut can use it."""
        self._cancel_key = key if key in CANCEL_KEYS else CANCEL_DEFAULT

    def conflict(self, action_id: str, keys, dictation_hotkey: Sequence[str] = (),
                 hold: bool = False) -> Optional[str]:
        """Why ``keys`` cannot be this action's shortcut now (the same
        sentence ``register`` would return), or None."""
        verdict = check(keys, self.platform, dictation_hotkey, self._others(action_id), hold=hold,
                        cancel_key=self._cancel_key)
        return None if verdict["ok"] else verdict["error"]

    def _others(self, action_id: str) -> List[tuple]:
        with self._lock:
            return [((SPECS[k].label if k in SPECS else ""), a["keys"])
                    for k, a in self._actions.items() if k != action_id]

    def register(self, action_id: str, keys, callback: Callable[[], None],
                 dictation_hotkey: Sequence[str] = ()) -> dict:
        """Run ``callback`` when ``keys`` go down. A second call for the same
        ``action_id`` replaces the first. Returns ``check``'s answer; nothing
        is registered when it is not ok."""
        verdict = check(keys, self.platform, dictation_hotkey, self._others(action_id),
                        cancel_key=self._cancel_key)
        if not verdict["ok"]:
            return verdict
        return self._store(action_id, verdict, callback, None)

    def register_on_release(self, action_id: str, keys, callback: Callable[[], None],
                            dictation_hotkey: Sequence[str] = ()) -> dict:
        """A press whose action starts when the keys come back up (any part of
        them), not when they go down: for an action that types into the app in
        front (Paste last), which must not run while the shortcut's own
        modifiers are still held. Checked as a press, run on the action's own
        thread. Returns ``check``'s answer; nothing is registered when it is
        not ok."""
        verdict = check(keys, self.platform, dictation_hotkey, self._others(action_id),
                        cancel_key=self._cancel_key)
        if not verdict["ok"]:
            return verdict
        return self._store(action_id, verdict, lambda: None, callback, on_release=True)

    def register_hold(self, action_id: str, keys, on_down: Callable[[], None],
                      on_up: Callable[[], None], dictation_hotkey: Sequence[str] = ()) -> dict:
        """A hold-to-talk action: ``on_down`` when ``keys`` go down, ``on_up``
        when any part of them comes up (the key or one of the modifiers). Both
        run on a thread of the action's own, in order. A second call for the
        same ``action_id`` replaces the first and ends a hold in progress.
        On a Mac the keys may be modifier keys alone. Returns ``check``'s
        answer; nothing is registered when it is not ok."""
        verdict = check(keys, self.platform, dictation_hotkey, self._others(action_id), hold=True,
                        cancel_key=self._cancel_key)
        if not verdict["ok"]:
            return verdict
        return self._store(action_id, verdict, on_down, on_up)

    def _store(self, action_id: str, verdict: dict, callback, on_up, on_release: bool = False) -> dict:
        stored = verdict["keys"]
        mods = frozenset(k for k in stored if k in modifiers_of(self.platform))
        key = next((k for k in stored if k not in mods), None)      # None: modifier keys alone
        kicks = []
        with self._lock:
            ended = self._end_hold_locked(action_id, fire=False)
            self._pending.pop(action_id, None)
            self._actions[action_id] = {"keys": stored, "mods": mods, "key": key,
                                        "callback": callback, "on_up": on_up, "on_release": on_release}
            self._last_fired.pop(action_id, None)
            self._queue_locked(action_id, ended, kicks)
        self._kick(kicks)
        return verdict

    def unregister(self, action_id: str) -> bool:
        kicks = []
        with self._lock:
            self._last_fired.pop(action_id, None)
            self._pending.pop(action_id, None)
            ended = self._end_hold_locked(action_id, fire=False)
            self._queue_locked(action_id, ended, kicks)
            gone = self._actions.pop(action_id, None) is not None
        self._kick(kicks)
        return gone

    def registered(self) -> Dict[str, List[str]]:
        with self._lock:
            return {k: list(a["keys"]) for k, a in self._actions.items()}

    # -- the decision ------------------------------------------------------

    def key_down(self, key: str, modifiers: Iterable[str], repeat: bool = False) -> List[str]:
        """A key went down while exactly ``modifiers`` were held. Calls the
        callback of every action whose shortcut it is (on a thread of its
        own) and returns their ids. ``repeat`` is a key held down and
        repeating: it never fires. While the key-recording dialog is open
        nothing fires (the person is choosing keys, not using them)."""
        now = self._clock()
        kicks = []
        with self._lock:
            self._pending.clear()           # a key pressed: modifier keys held are not a hold on their own
            if not self._actions:
                return []               # nothing has a shortcut: every key is just a key
            # A key-up that never came must not keep a key "held" for ever.
            for k in [k for k, t in self._down.items() if now - t > HELD_FOR_S]:
                del self._down[k]
            stale = [aid for aid, h in self._holds.items() if now - h["since"] > HOLD_STALE_S]
            for aid in stale:
                self._queue_locked(aid, self._end_hold_locked(aid, fire=False), kicks)
            # The key of a hold in progress is held for as long as the hold is,
            # whether or not the system keeps repeating it.
            held = key in self._down or any(h["key"] == key for h in self._holds.values())
            self._down[key] = now           # a repeating key keeps itself "held"
            mods = frozenset(modifiers)
            fired = []
            choosing = self._recording_locked(now)
            for action_id, a in ([] if repeat or held or choosing else self._actions.items()):
                if a["key"] != key or a["mods"] != mods:
                    continue
                last = self._last_fired.get(action_id)
                if last is not None and 0 <= now - last < MIN_GAP_S:
                    continue
                self._last_fired[action_id] = now
                if a.get("on_up") is not None:
                    self._holds[action_id] = {"key": key, "mods": a["mods"], "since": now}
                    # Queued here, with the hold booked: a key-up on another thread
                    # queues its up behind this down, whatever the threads do next.
                    self._queue_locked(action_id, a["callback"], kicks)
                fired.append((action_id, a["callback"], a.get("on_up") is not None))
        self._kick(kicks)
        for action_id, callback, hold in fired:
            if not hold:
                self._spawn(lambda cb=callback, aid=action_id: self._run(aid, cb))
        return [aid for aid, _cb, _hold in fired]

    def other_key_down(self) -> None:
        """A key went down that no shortcut can use (Space, an arrow, Return):
        modifier keys that were being held for a hold of modifier keys alone
        are on their way to some other shortcut, and start nothing."""
        with self._lock:
            self._pending.clear()

    def key_up(self, key: str) -> None:
        kicks = []
        with self._lock:
            self._down.pop(key, None)
            ended = [aid for aid in self._holds if self._holds[aid]["key"] == key]
            for aid in ended:
                self._queue_locked(aid, self._end_hold_locked(aid), kicks)
        self._kick(kicks)

    def modifiers_changed(self, modifiers: Iterable[str]) -> None:
        """The modifier keys held are now exactly ``modifiers`` (a Mac reports
        them all at once). A hold ends when one of its own is not among them;
        an extra modifier changes nothing. A hold of modifier keys alone
        starts ``MODIFIER_HOLD_S`` after exactly its modifiers are held, if
        nothing else has happened by then."""
        now_held = frozenset(modifiers)
        now = self._clock()
        arm = []
        kicks = []
        with self._lock:
            self._expire_swallowed_locked(now)      # a modifier change is a chance to clean up
            stale = [aid for aid, h in self._holds.items() if now - h["since"] > HOLD_STALE_S]
            ended = [aid for aid, h in self._holds.items() if not h["mods"] <= now_held or aid in stale]
            for aid in ended:
                self._queue_locked(aid, self._end_hold_locked(aid, fire=aid not in stale), kicks)
            for aid, a in self._actions.items():
                if a["key"] is not None:
                    continue
                if a["mods"] == now_held and aid not in self._holds and aid not in self._pending \
                        and not self._recording_locked(now):
                    token = object()
                    self._pending[aid] = token
                    arm.append((aid, token))
                elif a["mods"] != now_held:
                    self._pending.pop(aid, None)    # more or fewer keys than the hold: not (yet) it
        self._kick(kicks)
        for aid, token in arm:
            self._timer(MODIFIER_HOLD_S, lambda aid=aid, token=token: self._start_modifier_hold(aid, token))

    def _start_modifier_hold(self, action_id: str, token) -> None:
        """The timer: the modifier keys have been held long enough with nothing
        else pressed. Starts the hold unless something cancelled it."""
        now = self._clock()
        kicks = []
        with self._lock:
            if self._pending.get(action_id) is not token:
                return
            del self._pending[action_id]
            a = self._actions.get(action_id)
            if a is None or a["key"] is not None or self._recording_locked(now):
                return
            self._holds[action_id] = {"key": None, "mods": a["mods"], "since": now}
            # Queued with the hold booked, so a release on the keyboard's thread
            # queues its up behind this down (an up first would find nothing to
            # end, and the down would then record until the hold's limit).
            self._queue_locked(action_id, a["callback"], kicks)
        self._kick(kicks)

    def modifier_up(self, modifier: str) -> None:
        """One modifier key came up (Windows reports them one at a time)."""
        kicks = []
        with self._lock:
            if not self._holds:
                return
            ended = [aid for aid, h in self._holds.items() if modifier in h["mods"]]
            for aid in ended:
                self._queue_locked(aid, self._end_hold_locked(aid), kicks)
        self._kick(kicks)

    # -- swallowing the key ------------------------------------------------
    #
    # The listeners ask these two after key_down / key_up, and swallow the key
    # when the answer is True. The table:
    #
    #   event                                        swallowed?
    #   key down, a registered shortcut, exactly
    #     its modifiers                              yes (and remembered)
    #   key down, repeating, that key remembered     yes, whatever the modifiers are now
    #                                                (Shift let go first must not type the letter)
    #   key up, that key remembered                  yes
    #   anything else                                no: modifiers always pass, a key with
    #                                                other modifiers is not the shortcut
    #   anything at all while the key-recording
    #     dialog is open, or the dictation hotkey
    #     is held                                    no
    #
    # A remembered key is forgotten when its key-up is swallowed, when a fresh
    # press of the key arrives (a key-up that was lost), and SWALLOW_STALE_S
    # after the last time it was seen, checked at every key and modifier event.

    def set_recording(self, active: bool) -> None:
        """The key-recording dialog is open (True) or closed (False): shortcuts
        neither fire nor swallow keys while a new one is being chosen."""
        with self._lock:
            self._recording_since = self._clock() if active else None
            if active:
                self._swallowed.clear()
                self._pending.clear()

    def set_ptt_probe(self, probe: Optional[Callable[[], bool]]) -> None:
        """How to ask whether the dictation hotkey is held right now."""
        self._ptt_probe = probe

    def _recording_locked(self, now: float) -> bool:
        since = self._recording_since
        if since is None:
            return False
        if now - since > RECORDING_MAX_S:
            self._recording_since = None            # the dialog never said it closed
            return False
        return True

    def _expire_swallowed_locked(self, now: float) -> None:
        for k in [k for k, t in self._swallowed.items() if now - t > SWALLOW_STALE_S]:
            del self._swallowed[k]

    def is_held(self, key: str) -> bool:
        """Has a key-down of ``key`` been seen (recently) with no key-up since?"""
        now = self._clock()
        with self._lock:
            t = self._down.get(key)
            return t is not None and now - t <= HELD_FOR_S

    def is_repeat(self, key: str, modifiers: Iterable[str] = ()) -> bool:
        """Windows has no repeat flag: is a key-down of ``key``, with exactly
        ``modifiers`` held, a repeat of the press already seen? Yes when the
        key was seen a moment ago (``REPEAT_WINDOW_S``), or when it is a
        registered shortcut's key with the shortcut's own modifiers and its
        key-down is on the books. A key-up that was lost does not make the next
        plain press of that letter a repeat."""
        now = self._clock()
        with self._lock:
            self._expire_swallowed_locked(now)
            seen = [t for t in (self._swallowed.get(key), self._down.get(key)) if t is not None]
            if not seen:
                return False
            if now - max(seen) <= REPEAT_WINDOW_S:
                return True
            mods = frozenset(modifiers)
            return any(a["key"] == key and a["mods"] == mods for a in self._actions.values())

    def is_swallowing(self, key: str) -> bool:
        """Is ``key`` one whose key-down was swallowed and which has not come
        up? (Windows has no repeat flag: a key-down for such a key is a repeat.)"""
        now = self._clock()
        with self._lock:
            self._expire_swallowed_locked(now)
            return key in self._swallowed

    def _ptt_held(self) -> bool:
        probe = self._ptt_probe
        if probe is None:
            return False
        try:
            return bool(probe())
        except Exception:      # noqa: BLE001 - a listener that cannot say is not holding
            return False

    def swallow_down(self, key: str, modifiers: Iterable[str], repeat: bool = False) -> bool:
        """Should this key-down be kept from the app in front? See the table
        above. Call it with the same arguments as ``key_down``."""
        held = self._ptt_held()         # asked before the lock: it belongs to another object
        now = self._clock()
        with self._lock:
            self._expire_swallowed_locked(now)
            if held or self._recording_locked(now):
                self._swallowed.pop(key, None)
                return False
            if repeat:
                if key in self._swallowed:
                    self._swallowed[key] = now      # still held: still ours, whatever the modifiers are now
                    return True
                return False                        # a repeat of a press that went through: not ours to start
            self._swallowed.pop(key, None)          # a fresh press: any old memory of the key is stale
            mods = frozenset(modifiers)
            if any(a["key"] == key and a["mods"] == mods for a in self._actions.values()):
                self._swallowed[key] = now
                return True
            return False

    def swallow_up(self, key: str) -> bool:
        """Should this key-up be kept from the app in front? True when its
        key-down was swallowed (and not yet forgotten)."""
        now = self._clock()
        with self._lock:
            self._expire_swallowed_locked(now)
            return self._swallowed.pop(key, None) is not None

    def _end_hold_locked(self, action_id: str, fire: bool = True):
        """Take ``action_id``'s hold out of the books (call with the lock
        held) and return what to call for it, or None if none was running.
        ``fire`` False is for a hold that ends for no reason of the person's
        (it went stale, or the action was replaced or removed): a hold of
        Command mode still has to stop its recording, but a press that starts
        when the keys come up (Paste last) is dropped, not run late."""
        if self._holds.pop(action_id, None) is None:
            return None
        a = self._actions.get(action_id)
        if not a or (a.get("on_release") and not fire):
            return None
        return a.get("on_up")

    def _queue_locked(self, action_id: str, fn, kicks: list) -> None:
        """Put ``fn`` in ``action_id``'s queue, in the order the books were
        written (call with the lock held). The caller kicks the queues once it
        has let go of the lock (``_kick``): a thread is only started outside it."""
        if fn is None:
            return
        serial = self._serial_for(action_id)
        if serial.enqueue(action_id, fn):
            kicks.append((action_id, serial))

    @staticmethod
    def _kick(kicks: list) -> None:
        for action_id, serial in kicks:
            serial.kick(action_id)

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
        this is not a Mac. On every platform the registry also learns how to
        ask that listener whether the dictation hotkey is held, so a
        shortcut never swallows a key while it is."""
        self.set_ptt_probe(lambda: bool(getattr(listener, "is_combo_active", False)))
        try:
            known = listener in self._attached
        except TypeError:
            known = False
        with self._lock:
            # A new listener is a new tap, and nothing the old one was told is
            # still true. The same listener asked again (every time a setting
            # is applied) is not: a key held through that has had its key-down
            # kept from the app, so its repeats and key-up must still be.
            if not known:
                self._swallowed.clear()
            self._pending.clear()
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
    registry. ``handle`` answers True ("swallow it") for exactly one thing: the
    main key of a registered shortcut (its key-down, its repeats and its
    key-up; ``ActionHotkeys.swallow_down`` has the table). Modifier keys always
    pass. Nothing it does can raise into the tap's other handlers: any error
    is "do not swallow". Key events and changes of the modifier keys are both
    read (a hold ends when any of its modifiers does), and events Waffler posted
    itself (the Cmd+C and Cmd+V of reading a selection and pasting) are left
    out, and never swallowed."""

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
                if event_type == _KEY_DOWN:
                    self._registry.other_key_down()
                return False
            if event_type == _KEY_UP:
                self._registry.key_up(key)
                return bool(self._registry.swallow_up(key))
            mods = self._modifiers(int(get_flags(event)))
            repeat = bool(get_field(event, _FIELD_AUTOREPEAT))
            self._registry.key_down(key, mods, repeat=repeat)
            return bool(self._registry.swallow_down(key, mods, repeat=repeat))
        except Exception:      # noqa: BLE001 - see the class docstring
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
                  key_state: Callable[[int], bool],
                  mask: Optional[Callable[[], None]] = None) -> bool:
    """One keyboard event, as the hook receives it. A key a program sent
    (``flags`` has the injected bit) is ignored, as windows_hotkey.py ignores
    the paste's own Ctrl+V. ``key_state(vk)`` says whether a key is down now;
    the modifiers are read from it when the key goes down. Returns True when
    the hook should swallow the key (the main key of a registered shortcut, as
    ``ActionHotkeys.swallow_down`` decides; modifier keys always pass).

    Windows opens the Start menu when the Windows key comes up with nothing
    pressed in between, and an application's menu bar when Alt does: a swallowed
    key is "nothing" to it. So when the first key-down of a swallowed shortcut
    has the Windows key or Alt held, ``mask`` is called (it sends a key that
    does nothing, as other programs that swallow keys do)."""
    if flags & _LLKHF_INJECTED:
        return False
    modifier = _WINDOWS_MODIFIER_FOR_VK.get(vk)
    if modifier is not None:
        if message in (_WM_KEYUP, _WM_SYSKEYUP):
            registry.modifier_up(modifier)
        return False
    key = _WINDOWS_KEY_FOR_VK.get(vk)
    if key is None:
        if message in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
            registry.other_key_down()
        return False
    if message in (_WM_KEYUP, _WM_SYSKEYUP):
        registry.key_up(key)
        return bool(registry.swallow_up(key))
    if message in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
        mods = [m for m, vks in _WINDOWS_MODIFIER_VKS.items() if any(key_state(v) for v in vks)]
        # Windows sends no repeat flag: a key-down for a key that went down
        # (and was perhaps swallowed) a moment ago and has not come up is a
        # repeat. Much later it is a new press: its key-up was lost.
        repeat = registry.is_repeat(key, mods)
        registry.key_down(key, mods)
        swallow = bool(registry.swallow_down(key, mods, repeat=repeat))
        if swallow and not repeat and mask is not None and ("win" in mods or "alt" in mods):
            try:
                mask()
            except Exception:      # noqa: BLE001 - the key is swallowed either way
                pass
        return swallow
    return False


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
        # Libraries of this watcher's own: the prototypes set below would otherwise
        # replace the ones the dictation hotkey's hook has on the shared user32.
        user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
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

        def mask():
            # VK 0xE8 is unassigned: a tap of it between the Windows key or Alt going
            # down and coming up stops the Start menu or the menu bar from opening.
            user32.keybd_event(0xE8, 0, 0, 0)
            user32.keybd_event(0xE8, 0, 2, 0)           # 2: key up

        def proc(n_code, w_param, l_param):
            swallow = False
            try:
                if n_code >= 0:
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    swallow = windows_event(self._registry, kb.vkCode, int(w_param), int(kb.flags),
                                            key_state, mask)
            except Exception:      # noqa: BLE001 - never break the system's keyboard
                swallow = False
            if swallow:
                return 1            # the main key of a shortcut: the app in front does not get it
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
