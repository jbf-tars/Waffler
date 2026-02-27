"""
Waffler — Audio Device Manager
Lists available microphones and persists the selected device.
"""

import json
from pathlib import Path
from typing import List, Dict, Optional

try:
    import sounddevice as sd
    _HAS_SD = True
except ImportError:
    _HAS_SD = False

CONFIG_FILE = Path.home() / ".waffler" / "config.json"


def list_input_devices() -> List[Dict]:
    """
    Return a list of available audio input devices.
    Filters out virtual/null devices and shows only real microphones.

    Each entry:  {index, name, channels, sample_rates, is_default}
    """
    if not _HAS_SD:
        return []

    devices = []
    try:
        default_info = sd.query_devices(kind="input")
        default_name = default_info["name"]
    except Exception:
        default_name = ""

    # Keywords that indicate a real microphone (case-insensitive)
    REAL_MIC_KEYWORDS = [
        "mic", "microphone", "input", "audio", "built-in", "internal",
        "realtek", "usb", "bluetooth", "airpods", "headphone", "webcam"
    ]
    # Keywords that indicate virtual/ignored devices
    IGNORE_KEYWORDS = ["virtual", "wasapi", "loopback", "null"]

    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue

        name = dev["name"].lower()

        # Skip virtual devices
        if any(kw in name for kw in IGNORE_KEYWORDS):
            continue

        # Prioritize real microphones
        is_relevant = any(kw in name for kw in REAL_MIC_KEYWORDS)

        devices.append(
            {
                "index": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "default_sr": int(dev.get("default_samplerate", 44100)),
                "is_default": dev["name"] == default_name,
                "is_relevant": is_relevant,
            }
        )

    # Sort: relevant devices first, then by default
    devices.sort(key=lambda d: (not d.get("is_relevant", True), not d["is_default"]))

    return devices


def get_device_names() -> List[str]:
    """Return just the names of available input devices."""
    return [d["name"] for d in list_input_devices()]


def get_default_device_index() -> Optional[int]:
    """Return the system default input device index (or None)."""
    if not _HAS_SD:
        return None
    try:
        info = sd.query_devices(kind="input")
        for i, dev in enumerate(sd.query_devices()):
            if dev["name"] == info["name"]:
                return i
    except Exception:
        pass
    return None


def get_selected_device_index() -> Optional[int]:
    """
    Return the user-selected device index from ~/.waffler/config.json.
    Falls back to the system default.
    """
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            idx = cfg.get("audio_device_index")
            if idx is not None:
                return int(idx)
    except Exception:
        pass
    return get_default_device_index()


def set_selected_device_index(device_index: Optional[int]):
    """
    Persist the selected device index to ~/.waffler/config.json.
    Pass None to reset to system default.
    """
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    if device_index is None:
        cfg.pop("audio_device_index", None)
    else:
        cfg["audio_device_index"] = int(device_index)

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def get_selected_device_name() -> str:
    """Return the name of the currently selected device."""
    idx = get_selected_device_index()
    if idx is None:
        return "System Default"
    try:
        dev = sd.query_devices(idx)
        return dev["name"]
    except Exception:
        return f"Device {idx}"


# ── CLI helper ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Available input devices:\n")
    for d in list_input_devices():
        marker = "✅ default" if d["is_default"] else ""
        print(f"  [{d['index']:2d}] {d['name']} ({d['channels']}ch, {d['default_sr']}Hz) {marker}")
    print()
    sel = get_selected_device_index()
    print(f"Currently selected: {get_selected_device_name()} (index={sel})")
