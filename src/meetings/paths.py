"""Where meetings are kept. Override with WAFFLER_MEETINGS_HOME (tests)."""
import os
import sys
from pathlib import Path

from . import APP_NAME


def data_dir() -> Path:
    """Meetings live in a `meetings` folder inside Waffler's data folder
    (src/data_paths.py, which WAFFLER_DATA_DIR moves for tests)."""
    override = os.environ.get("WAFFLER_MEETINGS_HOME") or os.environ.get("NATTER_HOME")
    if override:
        p = Path(override)
    else:
        try:
            from src.data_paths import data_dir as waffler_data_dir
        except ImportError:
            from data_paths import data_dir as waffler_data_dir
        p = waffler_data_dir() / "meetings"
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
