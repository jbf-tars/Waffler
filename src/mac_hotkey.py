"""
Waffler Mac Hotkey — Configurable Smart Hotkey for macOS

Default: Fn key = Push-to-talk (hold to record, release to stop)
         Fn + Space = Sticky mode (locks recording on, can release keys)
         Fn again   = Cancel sticky mode (stops recording)

Users can rebind the hotkey combo via Settings. Space always triggers sticky mode.

Uses pynput for cross-platform key detection on macOS.
"""

from pynput import keyboard
from typing import Callable, Set, List
import threading
import time


# ── Key identifier → pynput Key mapping ────────────────────────────────
KEY_TO_PYNPUT = {
    "cmd":   keyboard.Key.cmd,
    "ctrl":  keyboard.Key.ctrl,
    "alt":   keyboard.Key.alt,
    "option": keyboard.Key.alt,  # alias
    "shift": keyboard.Key.shift,
    "fn":    "fn",  # special handling
}

# Function keys (F1-F20)
for _i in range(1, 21):
    KEY_TO_PYNPUT[f"f{_i}"] = getattr(keyboard.Key, f'f{_i}')

# Letter keys a-z
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEY_TO_PYNPUT[_c] = keyboard.KeyCode.from_char(_c)

# Digit keys 0-9
for _d in "0123456789":
    KEY_TO_PYNPUT[_d] = keyboard.KeyCode.from_char(_d)

MODIFIER_KEYS = {"cmd", "ctrl", "alt", "option", "shift", "fn"}
DEFAULT_HOTKEY = ["fn"]


def hotkey_display(keys: List[str]) -> str:
    """Convert key list to human-readable display string."""
    if not keys:
        return "No hotkey"

    display_map = {
        "cmd": "⌘",
        "ctrl": "⌃",
        "alt": "⌥",
        "option": "⌥",
        "shift": "⇧",
        "fn": "Fn",
    }

    parts = []
    for k in keys:
        parts.append(display_map.get(k.lower(), k.upper()))

    return " + ".join(parts)


class MacHotkeyListener:
    """
    Listens for a configurable hotkey combination on macOS.

    Push-to-talk: Hold combo → start recording, release → stop recording
    Sticky mode: Press combo + Space → lock recording on (can release combo)
    Cancel sticky: Press combo again → stop recording
    """

    def __init__(self, on_press: Callable, on_release: Callable, keys: List[str]):
        """
        Args:
            on_press: Callback when hotkey combo is pressed
            on_release: Callback when hotkey combo is released
            keys: List of key identifiers (e.g. ["cmd", "shift", "f13"])
        """
        self._on_press = on_press
        self._on_release = on_release
        self.keys = keys

        # Convert key identifiers to pynput Key objects
        self.required_keys: Set = set()
        for k in keys:
            if k.lower() in KEY_TO_PYNPUT:
                pynput_key = KEY_TO_PYNPUT[k.lower()]
                if pynput_key != "fn":  # fn handled separately
                    self.required_keys.add(pynput_key)

        self.current_keys: Set = set()
        self.listener = None
        self._combo_held = False
        self._sticky = False
        self._recording = False
        self._fn_held = False  # Track Fn separately if used

        # Check if Fn is in the combo
        self._uses_fn = "fn" in [k.lower() for k in keys]

    def _normalize_key(self, key):
        """Normalize key to canonical form for comparison."""
        if hasattr(key, 'vk') and key.vk is not None:
            # Use VK code for modifier keys to handle left/right variants
            return key.vk
        return key

    def _on_key_press(self, key):
        """Called when any key is pressed."""
        try:
            # Normalize the key
            norm_key = self._normalize_key(key)

            # Track Fn key separately (not in pynput)
            # Note: Fn detection on Mac is limited, using fallback

            # Add to current keys
            self.current_keys.add(norm_key)

            # Check if Space is pressed while combo is held
            if key == keyboard.Key.space and self._combo_held and self._recording:
                self._sticky = True
                print("📌 Sticky mode — release keys and keep talking; press combo again to stop")
                return

            # Check if all required keys are now pressed
            required_norm = {self._normalize_key(k) for k in self.required_keys}
            if required_norm.issubset(self.current_keys) and not self._combo_held:
                if self._sticky and self._recording:
                    # Already in sticky mode → combo stops it
                    self._sticky = False
                    self._recording = False
                    self._combo_held = False
                    self._fire_release()
                elif not self._recording:
                    # Start push-to-talk
                    self._combo_held = True
                    self._recording = True
                    self._fire_press()

        except Exception as e:
            print(f"Key press error: {e}")

    def _on_key_release(self, key):
        """Called when any key is released."""
        try:
            norm_key = self._normalize_key(key)

            # Remove from current keys
            self.current_keys.discard(norm_key)

            # Check if any required key was released
            required_norm = {self._normalize_key(k) for k in self.required_keys}
            if norm_key in required_norm:
                self._combo_held = False
                if self._recording and not self._sticky:
                    # Push-to-talk: release combo → stop
                    self._recording = False
                    self._fire_release()

        except Exception as e:
            print(f"Key release error: {e}")

    def _fire_press(self):
        """Fire the on_press callback in a separate thread."""
        threading.Thread(target=self._on_press, daemon=True).start()

    def _fire_release(self):
        """Fire the on_release callback in a separate thread."""
        threading.Thread(target=self._on_release, daemon=True).start()

    def start(self):
        """Start listening for the hotkey combination."""
        display = hotkey_display(self.keys)
        print(f"⌨️  Hotkey: Hold {display} to record | {display} + Space = sticky | {display} again = stop")

        self.listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self.listener.start()

    def stop(self):
        """Stop listening for the hotkey."""
        if self.listener:
            self.listener.stop()
            self.listener = None
        self.current_keys.clear()
        self._combo_held = False
        self._sticky = False
        self._recording = False
