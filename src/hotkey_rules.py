"""Checking a hotkey before it is saved, with a plain answer either way.

Settings and the setup wizard offered keys the backend then refused: Fn,
Command + Shift and Option + Shift appeared on Windows, and the wizard's
"Ctrl + Alt + Space" preset used a key Windows never accepted. The refusal
came back as "Unknown key: fn", which neither screen showed; both flashed
success anyway. This module decides, for each platform, whether a set of keys
can be the hotkey, and returns the keys to save (Windows names normalised,
in one display order) or one sentence saying why not.

Pure logic with no keyboard-hook imports, so it is tested on any OS. The
caller passes the platform's own key table (windows_hotkey.KEY_TO_VK, or the
Mac table from mac_hotkey_monitor), so the keys accepted here are exactly the
keys the listener can watch.
"""

from __future__ import annotations

WINDOWS = "Windows"
MAC = "Darwin"

WINDOWS_MODIFIERS = ("win", "ctrl", "alt", "shift")

# Other spellings of Windows keys. Mac-only names (Fn, Command, Option) are
# refused rather than translated: Settings showed the Mac presets on Windows,
# and silently turning "Option + Shift" into Alt + Shift would bind Windows'
# own keyboard-layout switch.
_WINDOWS_ALIASES = {"control": "ctrl", "meta": "win", "windows": "win"}
_MAC_ONLY = {"fn", "cmd", "command", "option"}

_WINDOWS_NAMES = {"win": "Win", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift"}
_MAC_NAMES = {"fn": "Fn", "cmd": "Command", "command": "Command", "shift": "Shift",
              "option": "Option", "alt": "Option", "control": "Control", "ctrl": "Control",
              "space": "Space"}

MAX_KEYS = 3
_RESERVED = [{"ctrl", "alt"}, {"alt", "f4"}, {"alt", "tab"}]


def _key_name(key: str, platform: str) -> str:
    names = _WINDOWS_NAMES if platform == WINDOWS else _MAC_NAMES
    if key in names:
        return names[key]
    if len(key) == 1 or (key[:1] == "f" and key[1:].isdigit()):
        return key.upper()          # "K", "7", "F9"
    return key.capitalize()         # "Tab", "Space", "Escape"


def _windows_order(keys):
    mods = [k for k in WINDOWS_MODIFIERS if k in keys]
    return mods + [k for k in keys if k not in WINDOWS_MODIFIERS]


def display(keys, platform: str) -> str:
    """'Win + Ctrl', 'Ctrl + Shift', 'Command + Shift', 'Fn'. On Windows the
    modifiers always come in one order (Win, Ctrl, Alt, Shift), so the same
    hotkey has the same name on every screen and on the website, whichever
    key was pressed first when it was chosen."""
    if platform == WINDOWS:
        keys = _windows_order(list(keys))
    return " + ".join(_key_name(k, platform) for k in keys)


def check(keys, platform: str, known_keys, modifier_keys) -> dict:
    """Validate ``keys`` for ``platform`` ("Windows" or "Darwin").

    Returns {"ok": True, "keys": [...], "display": "..."} with the keys to
    save, or {"ok": False, "error": "<one sentence>"}.
    """
    if not isinstance(keys, (list, tuple)) or not keys \
            or not all(isinstance(k, str) and k.strip() for k in keys):
        return {"ok": False, "error": "Choose at least one key for the hotkey."}

    cleaned = []
    for k in keys:
        k = k.strip().lower()
        if platform == WINDOWS:
            k = _WINDOWS_ALIASES.get(k, k)
        if k not in cleaned:
            cleaned.append(k)

    if platform == WINDOWS:
        mac_only = [k for k in cleaned if k in _MAC_ONLY]
        if mac_only:
            name = _MAC_NAMES.get(mac_only[0], mac_only[0])
            return {"ok": False, "error": f"{name} is a Mac key, so Windows can't use it for "
                                          f"the hotkey. Choose Win + Ctrl or Ctrl + Shift "
                                          f"instead."}
        if "space" in cleaned:
            return {"ok": False, "error": "Space can't be part of the hotkey, because "
                                          "pressing Space while you hold the hotkey "
                                          "switches on hands-free mode."}

    for k in cleaned:
        if k not in known_keys:
            return {"ok": False, "error": f"{_key_name(k, platform)} can't be used in the "
                                          f"hotkey. Choose one of the suggested hotkeys."}

    if not any(k in modifier_keys for k in cleaned):
        needed = ("Win, Ctrl, Alt or Shift" if platform == WINDOWS
                  else "Fn, Command, Control, Option or Shift")
        return {"ok": False, "error": f"The hotkey needs at least one of {needed}."}

    if len(cleaned) > MAX_KEYS:
        return {"ok": False, "error": "Use up to three keys for the hotkey."}

    key_set = set(cleaned)
    if key_set == {"alt"} or key_set == {"win"}:
        return {"ok": False, "error": f"{_key_name(cleaned[0], platform)} on its own can't be "
                                      f"the hotkey. Add another key, such as Ctrl."}
    if key_set in _RESERVED:
        return {"ok": False, "error": "Your computer already uses that combination. "
                                      "Choose another."}

    if platform == WINDOWS:
        cleaned = _windows_order(cleaned)
    return {"ok": True, "keys": cleaned, "display": display(cleaned, platform)}
