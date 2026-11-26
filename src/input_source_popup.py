"""The keyboard-language popup that holding Fn can bring up on a Mac.

Holding Fn (Waffler's default dictation key on a Mac) can make macOS show its
input-source popup ("ABC", "U.S.", ...) when more than one keyboard language
is set up, because Fn and Control-Space are tied to "Select the previous
input source" (system shortcut 60) and "Select next source in Input menu"
(61). Waffler used to switch both shortcuts off on every launch, silently and
for good. It no longer changes anything by itself:

- ``view()`` says whether the switch is on, and whether it would help: Fn is
  the dictation key, one of the two shortcuts is on and there is more than
  one input source. Settings shows the switch only then (or while it is on).
- ``turn_on()`` remembers the two shortcuts as they are, then switches them
  off. ``turn_off()`` puts back exactly what was remembered.
- ``adopt_legacy_change()`` runs once on a Mac: if the shortcuts look the way
  the old silent code left them (an entry holding nothing but
  ``enabled = false``), that is recorded, so the switch shows as on and
  switching it off restores macOS's own shortcuts.
- "Delete all my data" and a full reset put the shortcuts back first.

Every call to ``defaults`` goes through ``runner`` (``run(argv) -> (code,
stdout)``), so tests never touch the real settings. The record lives in the
data folder as ``input_source_shortcut.json``.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
from pathlib import Path
from typing import Callable, Optional

DOMAIN = "com.apple.symbolichotkeys"
KEY = "AppleSymbolicHotKeys"
HOTKEY_IDS = ("60", "61")
RECORD_NAME = "input_source_shortcut.json"

# macOS's own shortcuts, put back when the earlier value is not known (an
# older Waffler replaced the entries without keeping them): Control-Space and
# Control-Option-Space.
MAC_DEFAULTS = {
    "60": {"enabled": True, "value": {"parameters": [32, 49, 262144], "type": "standard"}},
    "61": {"enabled": True, "value": {"parameters": [32, 49, 786432], "type": "standard"}},
}

Runner = Callable[[list], "tuple[int, str]"]


def default_runner(argv: list) -> "tuple[int, str]":
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    return proc.returncode, proc.stdout or ""


# ── Reading ────────────────────────────────────────────────────────────────

def _export(domain: str, runner: Runner) -> dict:
    """A preferences domain as a dict ({} when it cannot be read)."""
    try:
        code, out = runner(["defaults", "export", domain, "-"])
        if code != 0 or not out.strip():
            return {}
        data = plistlib.loads(out.encode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_shortcuts(runner: Runner) -> dict:
    """{"60": entry or None, "61": entry or None}; None when macOS has no
    entry, which means the shortcut is as macOS ships it (on)."""
    table = _export(DOMAIN, runner).get(KEY) or {}
    return {i: (table.get(i) if isinstance(table.get(i), dict) else None) for i in HOTKEY_IDS}


def shortcut_on(entry: Optional[dict]) -> bool:
    return entry is None or bool(entry.get("enabled", True))


def input_source_count(runner: Runner) -> int:
    """How many keyboard languages are set up (layouts and input methods;
    the emoji and dictation palettes do not count)."""
    sources = _export("com.apple.HIToolbox", runner).get("AppleEnabledInputSources") or []
    n = 0
    for s in sources:
        if not isinstance(s, dict):
            continue
        kind = str(s.get("InputSourceKind", ""))
        if "KeyboardLayout Name" in s or kind in ("Keyboard Layout", "Input Mode"):
            n += 1
    return n


def fn_is_dictation_key(hotkey_keys) -> bool:
    """Fn alone is the Mac's dictation key, and the default when none is set."""
    keys = [str(k).lower() for k in (hotkey_keys or [])]
    return not keys or keys == ["fn"]


# ── The record ─────────────────────────────────────────────────────────────

def _record_path(data_dir) -> Path:
    return Path(data_dir) / RECORD_NAME


def load_record(data_dir) -> dict:
    try:
        rec = json.loads(_record_path(data_dir).read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) else {}
    except Exception:
        return {}


def _save_record(data_dir, rec: dict) -> None:
    p = _record_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    tmp.replace(p)


def is_on(data_dir) -> bool:
    return bool(load_record(data_dir).get("on"))


# ── Writing ────────────────────────────────────────────────────────────────

def _plist_fragment(entry: dict) -> str:
    """The entry as the XML ``defaults write -dict-add`` takes."""
    xml = plistlib.dumps(entry, fmt=plistlib.FMT_XML).decode("utf-8")
    start = xml.index("<dict>")
    end = xml.rindex("</dict>") + len("</dict>")
    return "".join(line.strip() for line in xml[start:end].splitlines())


def _write(hotkey_id: str, entry: dict, runner: Runner) -> bool:
    code, _ = runner(["defaults", "write", DOMAIN, KEY, "-dict-add",
                      hotkey_id, _plist_fragment(entry)])
    return code == 0


def _apply_now(runner: Runner) -> None:
    """Ask macOS to read the shortcuts again, so the change holds without
    signing out. Best effort: without it the change holds from next sign-in."""
    tool = "/System/Library/PrivateFrameworks/SystemAdministration.framework/Resources/activateSettings"
    if Path(tool).exists():
        try:
            runner([tool, "-u"])
        except Exception:
            pass


def turn_on(data_dir, runner: Runner = default_runner, apply_now: bool = True) -> dict:
    """Remember the two shortcuts, then switch them off. Idempotent: when the
    switch is already on, nothing is written and the memory is kept."""
    rec = load_record(data_dir)
    if rec.get("on"):
        return {"ok": True, "on": True}
    previous = read_shortcuts(runner)
    rec = {"on": True, "inherited": False, "previous": previous, "legacy_checked": True}
    _save_record(data_dir, rec)       # remembered BEFORE anything is changed
    ok = True
    for i in HOTKEY_IDS:
        entry = dict(previous[i] or MAC_DEFAULTS[i])
        entry["enabled"] = False
        ok = _write(i, entry, runner) and ok
    if apply_now:
        _apply_now(runner)
    return {"ok": ok, "on": True}


def turn_off(data_dir, runner: Runner = default_runner, apply_now: bool = True) -> dict:
    """Put back the shortcuts as they were before the switch was turned on
    (macOS's own when that is not known). Nothing is written when the switch
    is not on."""
    rec = load_record(data_dir)
    if not rec.get("on"):
        return {"ok": True, "on": False}
    previous = rec.get("previous") or {}
    ok = True
    for i in HOTKEY_IDS:
        before = previous.get(i)
        ok = _write(i, before if isinstance(before, dict) else MAC_DEFAULTS[i], runner) and ok
    if apply_now:
        _apply_now(runner)
    if ok:
        _save_record(data_dir, {"on": False, "legacy_checked": True})
    return {"ok": ok, "on": not ok}


def _looks_like_legacy(entry: Optional[dict]) -> bool:
    """What the old silent code wrote: an entry holding only enabled=false
    (it replaced macOS's entry, value and all)."""
    return isinstance(entry, dict) and set(entry) == {"enabled"} and not entry["enabled"]


def adopt_legacy_change(data_dir, runner: Runner = default_runner) -> bool:
    """Once per data folder, on a Mac: if an older Waffler switched the
    shortcuts off, record it so the switch shows as on and can be undone.
    Reads the settings only; writes nothing to them. True when adopted."""
    rec = load_record(data_dir)
    if rec.get("legacy_checked"):
        return False
    shortcuts = read_shortcuts(runner)
    adopted = any(_looks_like_legacy(shortcuts[i]) for i in HOTKEY_IDS)
    if adopted:
        # The old entries were thrown away, so "before" is macOS's own.
        rec = {"on": True, "inherited": True,
               "previous": {i: None for i in HOTKEY_IDS}, "legacy_checked": True}
    else:
        rec = dict(rec, legacy_checked=True)
    _save_record(data_dir, rec)
    return adopted


def restore_and_forget(data_dir, runner: Runner = default_runner) -> dict:
    """For "Delete all my data" and a full reset: put the shortcuts back if
    Waffler changed them, then drop the record."""
    result = turn_off(data_dir, runner)
    try:
        if result.get("ok"):
            _record_path(data_dir).unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return result


def view(data_dir, hotkey_keys, runner: Runner = default_runner) -> dict:
    """What Settings shows: whether the switch is on, and whether it would
    help (Fn is the dictation key, a shortcut is on, more than one input
    source). ``show`` is True when either is."""
    rec = load_record(data_dir)
    on = bool(rec.get("on"))
    would_help = False
    if not on and fn_is_dictation_key(hotkey_keys):
        shortcuts = read_shortcuts(runner)
        if any(shortcut_on(shortcuts[i]) for i in HOTKEY_IDS):
            would_help = input_source_count(runner) > 1
    return {"on": on, "inherited": bool(rec.get("inherited")) and on,
            "would_help": would_help, "show": on or would_help}
