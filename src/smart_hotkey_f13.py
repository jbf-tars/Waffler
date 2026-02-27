"""
Waffler Smart Hotkey - F13 Key Implementation

F13 alone          → Push-to-talk: hold to record, release to stop
F13 + Space        → Sticky mode: recording locks on (can release F13)
F13 (again)        → Stop sticky recording

Why F13?
- Reliable detection with pynput (no dispatch queue crashes)
- Unused on Mac keyboards (no conflicts)
- Can be mapped from Caps Lock via Karabiner-Elements
- Similar to how Discord/TeamSpeak handle push-to-talk

Alternative: Use Right Command (⌘) if F13 not available
"""

import threading
from pynput import keyboard


class SmartHotkeyListener:
    """
    Smart hotkey listener using F13 key (or fallback to Right Command).

    Supports two modes:
    1. Push-to-talk: Hold F13 to record, release to stop
    2. Sticky mode: F13 + Space locks recording on, F13 again to stop
    """

    def __init__(self, on_press, on_release, use_right_cmd=False):
        """
        Initialize the hotkey listener.

        Args:
            on_press: Callback when recording starts
            on_release: Callback when recording stops
            use_right_cmd: Use Right Command instead of F13 (fallback mode)
        """
        self._on_press = on_press
        self._on_release = on_release
        self._use_right_cmd = use_right_cmd

        self._key_held = False      # F13 or Right Cmd currently down
        self._sticky = False        # Locked-on (toggle) mode active
        self._recording = False     # Are we recording right now?
        self._listener = None

    # ── Key events ────────────────────────────────────────────────────

    def _on_key_press(self, key):
        # Determine which key we're listening for
        if self._use_right_cmd:
            is_hotkey = (key == keyboard.Key.cmd_r)
            key_name = "Right ⌘"
        else:
            is_hotkey = (key == keyboard.Key.f13)
            key_name = "F13"

        is_space = (key == keyboard.Key.space)

        if is_hotkey:
            if self._sticky and self._recording:
                # Already in sticky mode → hotkey stops it
                self._sticky = False
                self._recording = False
                self._key_held = False
                self._fire_release()
                print(f"⏹️  Recording stopped (sticky mode off)")

            elif not self._recording:
                # Start push-to-talk
                self._key_held = True
                self._recording = True
                self._fire_press()
                print(f"🎤 Recording started (hold {key_name})")

        elif is_space and self._key_held and self._recording:
            # Space pressed while holding hotkey → switch to sticky
            self._sticky = True
            key_display = "Right ⌘" if self._use_right_cmd else "F13"
            print(f"📌 Sticky mode — release {key_display} and keep talking; press {key_display} again to stop")

    def _on_key_release(self, key):
        # Determine which key we're listening for
        if self._use_right_cmd:
            is_hotkey = (key == keyboard.Key.cmd_r)
        else:
            is_hotkey = (key == keyboard.Key.f13)

        if is_hotkey:
            self._key_held = False
            if self._recording and not self._sticky:
                # Push-to-talk: release hotkey → stop
                self._recording = False
                self._fire_release()
                print("⏹️  Recording stopped")

    # ── Callbacks (run in a thread to avoid blocking pynput) ─────────

    def _fire_press(self):
        """Fire on_press callback in a separate thread."""
        threading.Thread(target=self._on_press, daemon=True, name="HotkeyPress").start()

    def _fire_release(self):
        """Fire on_release callback in a separate thread."""
        threading.Thread(target=self._on_release, daemon=True, name="HotkeyRelease").start()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        """Start listening for hotkeys."""
        if self._use_right_cmd:
            print("⌨️  Hotkey: Hold Right ⌘ to record | Right ⌘ + Space = sticky | Right ⌘ again = stop")
            print("💡 Tip: Right Command is rarely used, making it a good push-to-talk key")
        else:
            print("⌨️  Hotkey: Hold F13 to record | F13 + Space = sticky | F13 again = stop")
            print("💡 Tip: Install Karabiner-Elements and remap Caps Lock → F13 for easy access")
            print("    Download: https://karabiner-elements.pqrs.org/")

        self._listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._listener.start()

    def stop(self):
        """Stop listening for hotkeys."""
        if self._listener:
            self._listener.stop()

    def join(self):
        """Block until the listener thread terminates."""
        if self._listener:
            self._listener.join()


# ── Karabiner-Elements Configuration Helper ───────────────────────────

def generate_karabiner_config():
    """
    Generate a Karabiner-Elements configuration to map Caps Lock → F13.

    Returns:
        dict: Configuration that can be saved to Karabiner-Elements config file
    """
    return {
        "title": "Waffler: Caps Lock → F13 (Push-to-Talk)",
        "rules": [
            {
                "description": "Map Caps Lock to F13 for Waffler push-to-talk",
                "manipulators": [
                    {
                        "type": "basic",
                        "from": {
                            "key_code": "caps_lock",
                            "modifiers": {"optional": ["any"]}
                        },
                        "to": [
                            {"key_code": "f13"}
                        ]
                    }
                ]
            }
        ]
    }


def save_karabiner_config(output_path=None):
    """
    Save Karabiner-Elements configuration to a file.

    Args:
        output_path: Path to save config (default: current directory)

    Returns:
        str: Path to saved configuration file
    """
    import json
    from pathlib import Path

    if output_path is None:
        output_path = Path.cwd() / "waffler-karabiner.json"

    config = generate_karabiner_config()

    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"✅ Karabiner-Elements configuration saved to: {output_path}")
    print("\nTo use this configuration:")
    print("1. Install Karabiner-Elements: https://karabiner-elements.pqrs.org/")
    print("2. Open Karabiner-Elements")
    print("3. Go to 'Complex Modifications' tab")
    print("4. Click 'Add rule'")
    print(f"5. Import the configuration file: {output_path}")
    print("6. Enable 'Waffler: Caps Lock → F13' rule")
    print("\nNow Caps Lock will act as F13 for Waffler push-to-talk!")

    return str(output_path)


# ── Auto-detection: F13 vs Right Command ──────────────────────────────

def detect_f13_availability():
    """
    Check if F13 key is likely available on this keyboard.

    Returns:
        tuple: (has_f13: bool, recommendation: str)
    """
    import platform
    import subprocess

    # F13 is available if:
    # 1. External keyboard with F13-F20 keys
    # 2. Karabiner-Elements is installed and mapping Caps Lock → F13

    # Check if Karabiner-Elements is installed
    karabiner_path = "/Applications/Karabiner-Elements.app"
    has_karabiner = Path(karabiner_path).exists()

    if has_karabiner:
        return True, "Karabiner-Elements detected. F13 recommended (map from Caps Lock)."

    # On MacBooks without external keyboard, recommend Right Command
    try:
        result = subprocess.run(
            ["system_profiler", "SPUSBDataType"],
            capture_output=True,
            text=True,
            timeout=5
        )
        # Look for external keyboards in USB data
        has_external_keyboard = "keyboard" in result.stdout.lower()

        if has_external_keyboard:
            return True, "External keyboard detected. F13 may be available."
    except Exception:
        pass

    return False, "F13 not detected. Recommend Right ⌘ or install Karabiner-Elements."


# ── Smart Factory ─────────────────────────────────────────────────────

def create_smart_hotkey(on_press, on_release, prefer_f13=True):
    """
    Auto-detect and create the best hotkey listener for this system.

    Args:
        on_press: Callback when recording starts
        on_release: Callback when recording stops
        prefer_f13: Try to use F13 if available (default: True)

    Returns:
        SmartHotkeyListener: Configured listener
    """
    if prefer_f13:
        has_f13, recommendation = detect_f13_availability()
        print(f"🔍 Hotkey detection: {recommendation}")

        if not has_f13:
            print("📝 To enable F13:")
            print("   1. Install Karabiner-Elements")
            print("   2. Run: python -c 'from smart_hotkey_f13 import save_karabiner_config; save_karabiner_config()'")
            print("   3. Import the config in Karabiner-Elements")
            print("")
            use_right_cmd = True
        else:
            use_right_cmd = False
    else:
        use_right_cmd = True

    return SmartHotkeyListener(on_press, on_release, use_right_cmd=use_right_cmd)


# ── CLI for testing ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path

    def test_press():
        print("▶️  Recording started!")

    def test_release():
        print("⏹️  Recording stopped!")

    if len(sys.argv) > 1 and sys.argv[1] == "--generate-karabiner":
        save_karabiner_config()
    else:
        print("Waffler Smart Hotkey - F13 Test")
        print("=" * 60)

        listener = create_smart_hotkey(test_press, test_release)
        listener.start()

        try:
            print("\n✅ Hotkey listener active. Test your hotkey!")
            print("Press Ctrl+C to exit.\n")
            listener.join()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down...")
            listener.stop()
