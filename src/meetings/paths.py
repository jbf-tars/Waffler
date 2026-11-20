"""Where meetings are kept. Override with WAFFLER_MEETINGS_HOME (tests)."""
import os
import sys
from pathlib import Path

from . import APP_NAME


def data_dir() -> Path:
    """Meetings live beside Waffler's own data (~/.waffler-hosted/meetings)."""
    override = os.environ.get("WAFFLER_MEETINGS_HOME") or os.environ.get("NATTER_HOME")
    p = Path(override) if override else Path.home() / ".waffler-hosted" / "meetings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def audio_dir(note_id: str) -> Path:
    p = data_dir() / "audio" / note_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def ui_dir() -> Path:
    """The built UI: beside the package in a checkout, inside the bundle when frozen."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "ui" / "dist"
