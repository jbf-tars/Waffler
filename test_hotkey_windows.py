"""
Hotkey test - run this BEFORE app.py to check if keyboard detection works.
Press Right Ctrl + Right Alt. Should print DETECTED.
Press Ctrl+C to quit.
"""
import sys
print("Testing keyboard detection...")
print("Press Right Ctrl + Right Alt to test. Ctrl+C to quit.\n")

try:
    import keyboard
    print("✅ keyboard library imported OK")
except ImportError:
    print("❌ keyboard not installed. Run: pip install keyboard")
    sys.exit(1)

def on_hotkey():
    print("✅ DETECTED — hotkey is working!")

keyboard.add_hotkey('right ctrl+right alt', on_hotkey)
print("Listening... (press the keys now)")
keyboard.wait()
