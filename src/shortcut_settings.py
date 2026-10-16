"""The Shortcuts page: every shortcut in one place (Settings, Shortcuts).

The dictation hotkey, hands-free, cancel, Transform, Command mode, Paste last
and Copy last each have a row; this module is what that page and the app's
bridge read and write. The rules themselves (what a shortcut may be, what
clashes with what) are in src/action_hotkeys.py and are not repeated here: the
rows, the checks on a save and the key-recording dialog all ask the same
``validate``.

Three keys of settings.json are new with this page, all optional (a settings
file without them reads as the defaults, and an older Waffler ignores them):

    paste_last_hotkey   list of key names, [] or missing for none
    copy_last_hotkey    list of key names, [] or missing for none
    cancel_key          "esc" (missing means Esc) or an F key such as "f16"

Transform keeps its shortcut in ``transforms.hotkey`` and Command mode in
``command_mode.hotkey`` (src/transforms.py, src/command_mode.py), where they
always were; this page reads and writes them in place.

Pure: no keyboard, no window, no file. The caller passes settings.json as a
dict, and the platform name.
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional

import action_hotkeys as ah
import user_messages

SETTING_KEYS = ("paste_last_hotkey", "copy_last_hotkey", "cancel_key")
_HOTKEY_SETTINGS = {"paste_last_hotkey": "paste_last", "copy_last_hotkey": "copy_last"}


def touches(payload) -> bool:
    """Does a save's payload name one of the three settings of this page?"""
    return isinstance(payload, dict) and any(k in payload for k in SETTING_KEYS)


def settings(stored) -> dict:
    """The three settings as the pipeline reads them: every key present,
    anything unreadable the default (no shortcut, Esc)."""
    return {"paste_last_hotkey": ah.stored_keys(stored, "paste_last"),
            "copy_last_hotkey": ah.stored_keys(stored, "copy_last"),
            "cancel_key": ah.cancel_key_of(stored)}


def window_view(stored, platform: Optional[str] = None) -> dict:
    """What ``get_settings`` hands the window for these settings, with the
    shortcuts as they are shown ("Control + Option + V")."""
    platform = platform or _this_platform()
    view = settings(stored)
    view["paste_last_display"] = ah.display(view["paste_last_hotkey"], platform)
    view["copy_last_display"] = ah.display(view["copy_last_hotkey"], platform)
    view["cancel_display"] = ah.display([view["cancel_key"]], platform)
    return view


def _candidate(stored, payload) -> dict:
    """settings.json as it would be with the shortcuts of Transform and
    Command mode that ``payload`` also changes, so a save of two shortcuts at
    once cannot put both on one combination."""
    out = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    for section in ("transforms", "command_mode"):
        given = payload.get(section) if isinstance(payload, dict) else None
        if isinstance(given, dict) and isinstance(given.get("hotkey"), list):
            block = out.get(section) if isinstance(out.get(section), dict) else {}
            out[section] = dict(block, hotkey=list(given["hotkey"]))
    return out


def settings_problem(payload, stored=None, *, platform: Optional[str] = None) -> Optional[str]:
    """A plain sentence when ``payload`` (what a save is called with) holds a
    refused Paste last, Copy last or cancel value, else None. A refused value
    saves none of the payload."""
    if not touches(payload):
        return None
    platform = platform or _this_platform()
    candidate = _candidate(stored, payload)
    # Both shortcuts may change in one save: each is checked against the
    # other's new keys, not the old ones.
    for name in _HOTKEY_SETTINGS:
        if name in payload:
            if not isinstance(payload[name], list):
                return user_messages.SHORTCUT_BAD
            candidate[name] = list(payload[name])
    for name, action_id in _HOTKEY_SETTINGS.items():
        if name not in payload or not payload[name]:
            continue
        verdict = ah.validate(action_id, payload[name], platform, candidate)
        if not verdict["ok"]:
            return verdict["error"]
    if "cancel_key" in payload:
        value = payload["cancel_key"]
        if not isinstance(value, str):
            return user_messages.SHORTCUT_BAD
        problem = ah.cancel_problem(value, platform, candidate)
        if problem:
            return problem
    return None


def merged(stored, payload, *, platform: Optional[str] = None) -> Dict[str, object]:
    """What to write for the settings ``payload`` names: ``{key: value}``, a
    value of None meaning "remove the key" (the default). Keys in the order a
    shortcut is stored. Call after ``settings_problem`` found nothing."""
    platform = platform or _this_platform()
    out: Dict[str, object] = {}
    for name, action_id in _HOTKEY_SETTINGS.items():
        if name not in payload:
            continue
        keys = payload[name]
        verdict = ah.check(keys, platform, hold=False) if keys else None
        out[name] = verdict["keys"] if verdict and verdict["ok"] else None
    if "cancel_key" in payload:
        name = str(payload["cancel_key"]).strip().lower()
        name = ah.CANCEL_DEFAULT if name == "escape" else name
        out["cancel_key"] = None if name == ah.CANCEL_DEFAULT else name
    return out


def reset(stored) -> None:
    """What "Reset shortcuts" does to settings.json (``stored``, changed in
    place): the dictation hotkey back to its default, Esc to cancel, and no
    shortcut for Transform, Command mode, Paste last and Copy last. The
    switches of Transform and Command mode are left as they are."""
    if not isinstance(stored, dict):
        return
    for name in ("hotkey_keys", "cancel_key", "paste_last_hotkey", "copy_last_hotkey"):
        stored.pop(name, None)
    for section in ("transforms", "command_mode"):
        if isinstance(stored.get(section), dict):
            stored[section]["hotkey"] = []


def check_one(action_id, keys, stored, platform: Optional[str] = None) -> dict:
    """Would ``keys`` be accepted for ``action_id`` (a row of the page: an
    action, "cancel" or "dictation")? The same rules as a save, nothing saved:
    what the key-recording dialog asks when the keys are let go. Returns
    ``{"ok": True, "keys", "display"}`` or ``{"ok": False, "error": "<one
    sentence>"}``. For the dictation hotkey only the clash with the other
    shortcuts and the cancel key is checked here (its own save checks the rest)."""
    platform = platform or _this_platform()
    if not isinstance(keys, list) or not keys:
        return {"ok": False, "error": user_messages.SHORTCUT_HOLD_KEYS}
    stored = stored if isinstance(stored, dict) else {}
    if action_id in ah.SPECS:
        return ah.validate(action_id, keys, platform, stored)
    if action_id == "cancel":
        if len(keys) != 1 or not isinstance(keys[0], str):
            return {"ok": False, "error": user_messages.SHORTCUT_ONE_KEY}
        problem = ah.cancel_problem(keys[0], platform, stored)
        if problem:
            return {"ok": False, "error": problem}
        name = ah.cancel_key_of({"cancel_key": keys[0]})
        return {"ok": True, "keys": [name], "display": ah.display([name], platform)}
    if action_id == "dictation":
        clash = ah.dictation_clash_message(keys, list(ah.saved_shortcuts(stored).values()), platform,
                                           cancel_key=ah.cancel_key_of(stored))
        if clash:
            return {"ok": False, "error": clash}
        return {"ok": True, "keys": keys, "display": ah.display(keys, platform)}
    return {"ok": False, "error": user_messages.SHORTCUT_UNKNOWN}


def saved_view(action_id, stored, platform: Optional[str] = None) -> dict:
    """What a save of Paste last, Copy last or the cancel key answers with:
    ``{"ok": True, "keys", "display"}`` as settings.json holds them now."""
    platform = platform or _this_platform()
    if action_id == "cancel":
        keys = [ah.cancel_key_of(stored)]
    else:
        keys = ah.stored_keys(stored, action_id)
    return {"ok": True, "keys": keys, "display": ah.display(keys, platform)}


# ── The page ─────────────────────────────────────────────────────────────────

_DICTATION_BLURB = "Hold it while you speak, then let go to paste."
_HANDS_FREE_BLURB = ("While you hold the dictation hotkey, tap Space to keep recording without "
                     "holding. Tap the hotkey again to finish.")
_CANCEL_BLURB = "Stops a recording. Nothing is pasted."


def _row(row_id, label, kind, blurb, keys, platform, **extra) -> dict:
    row = {"id": row_id, "label": label, "kind": kind, "description": blurb,
           "keys": list(keys), "display": ah.display(keys, platform),
           "editable": True, "clearable": False, "clear_text": "Clear",
           "clear_label": f"Clear the {label} shortcut",
           "enabled": True, "needs": "", "needs_section": "", "conflict": ""}
    row.update(extra)
    return row


def rows(stored, platform: Optional[str] = None) -> List[dict]:
    """The page's rows, in order. Each is ``{id, label, kind, description,
    keys, display, editable, clearable, clear_text, clear_label, enabled,
    needs, needs_section, conflict}``: ``kind`` is "hold", "press" or "fixed";
    ``enabled`` is False for a shortcut whose feature is off (``needs`` then
    says where to turn it on and ``needs_section`` is that section's name in
    the window); ``conflict`` is a plain sentence when a saved shortcut no
    longer passes the rules (empty when it does)."""
    platform = platform or _this_platform()
    stored = stored if isinstance(stored, dict) else {}
    ptt = ah.dictation_keys(stored, platform)
    cancel = ah.cancel_key_of(stored)
    saved = ah.saved_shortcuts(stored)
    out = [
        _row("dictation", "Hold to dictate", ah.HOLD, _DICTATION_BLURB, ptt, platform,
             conflict=ah.dictation_clash_message(ptt, saved.values(), platform, cancel)),
        _row("hands_free", "Hands-free", "fixed", _HANDS_FREE_BLURB, ptt + ["space"], platform,
             editable=False),
        _row("cancel", "Cancel", ah.PRESS, _CANCEL_BLURB, [cancel], platform,
             clearable=cancel != ah.CANCEL_DEFAULT, clear_text="Use Esc", clear_label="Use Esc to cancel",
             conflict=ah.cancel_problem(cancel, platform, stored) or ""),
    ]
    for spec in ah.ACTIONS:
        keys = saved.get(spec.id, [])
        on = ah.feature_on(stored, spec.id)
        conflict = ""
        if keys:
            verdict = ah.validate(spec.id, keys, platform, stored)
            conflict = "" if verdict["ok"] else verdict["error"]
        out.append(_row(spec.id, spec.label, spec.kind, spec.blurb, keys, platform,
                        clearable=bool(keys), enabled=on,
                        needs="" if on else f"Turn on in {spec.feature}", needs_section="" if on else spec.feature,
                        conflict=conflict))
    return out


def rules(platform: Optional[str] = None) -> dict:
    """What a shortcut may be, in plain sentences, and the combinations that
    are refused with who uses them: for the "Which shortcuts can I use?"
    part of the page. The sentences follow ``action_hotkeys.check``; a test
    holds them to it."""
    platform = platform or _this_platform()
    mac = platform == ah.MAC
    lines = [
        ("Use a letter, a number or an F key with Control or Command and one more modifier key."
         if mac else
         "Use a letter, a number or an F key with Ctrl or the Windows key and one more modifier key."),
        "Transform, Paste last and Copy last can also be an F key on its own, from F1 to F20. "
        "F13 to F20 are free on most keyboards.",
        "A shortcut can't be the same as, or sit inside, your dictation hotkey, your cancel key or another shortcut.",
        "The key of a shortcut doesn't reach the app in front, so it never types a letter or runs the app's own shortcut.",
    ]
    if mac:
        lines.append("Command mode can also be two or more modifier keys held together, such as Control and "
                     "Option. Include Control or Command, and hold them a moment.")
        lines.append("While your dictation hotkey is Fn, F13 to F15 are left for the Fn key of some keyboards.")
    else:
        lines.append("Ctrl and Alt together are the AltGr key on many keyboards, so they can't be used with "
                     "a letter or a number.")
    lines.append("Cancel can be Esc or an F key from F1 to F20."
                 + (" On a Mac laptop F1 to F12 need the Fn key unless your keyboard settings use them as "
                    "standard function keys." if mac else ""))
    return {"lines": lines, "reserved": ah.reserved_list(platform)}


def view(stored, platform: Optional[str] = None) -> dict:
    """The whole page: ``{"ok": True, "platform", "rows", "rules"}``."""
    platform = platform or _this_platform()
    return {"ok": True, "platform": platform, "rows": rows(stored, platform), "rules": rules(platform)}


def _this_platform() -> str:
    import sys
    return ah.MAC if sys.platform == "darwin" else ah.WINDOWS
