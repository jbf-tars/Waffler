"""Centralized read/write for ~/.waffler-hosted/settings.json.

Other modules should call `load()` or `is_private_mode()` rather than
reading the JSON file directly. This gives us one place to validate,
cache, and version the settings schema.
"""

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".waffler-hosted" / "settings.json"


def load() -> dict:
    """Return the full settings dict. Missing file or parse error → {}."""
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}


def set_key(key: str, value) -> None:
    """Set a single key and persist, preserving other keys."""
    data = load()
    data[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def is_private_mode() -> bool:
    """True if Private Mode is toggled on. Defaults to False."""
    return bool(load().get("private_mode", False))
