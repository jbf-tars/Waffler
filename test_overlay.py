#!/usr/bin/env python3
"""
Overlay Diagnostic Test
Run this to check if the overlay subprocess can start correctly.
"""

import sys
import time
from pathlib import Path

# Add src directory to path
src_dir = Path(__file__).parent / "src"
sys.path.insert(0, str(src_dir))

print("=" * 60)
print("Waffler Overlay Diagnostic Test")
print("=" * 60)
print()

# Check Python version
print(f"✓ Python version: {sys.version}")
print(f"✓ Python executable: {sys.executable}")
print()

# Check if overlay script exists
overlay_script = src_dir / "overlay_process.py"
print(f"Checking overlay script: {overlay_script}")
print(f"  Exists: {overlay_script.exists()}")
print()

# Try importing PyObjC (Mac requirement)
print("Checking PyObjC dependencies...")
try:
    import AppKit
    print("  ✓ AppKit imported successfully")
except ImportError as e:
    print(f"  ✗ ERROR: Cannot import AppKit - {e}")
    print("  → Install: pip install pyobjc-framework-Cocoa")

try:
    from Foundation import NSObject
    print("  ✓ Foundation imported successfully")
except ImportError as e:
    print(f"  ✗ ERROR: Cannot import Foundation - {e}")
    print("  → Install: pip install pyobjc-core")

print()

# Try to create overlay instance
print("Creating overlay instance...")
try:
    from overlay import RecordingOverlay
    overlay = RecordingOverlay()
    print("  ✓ RecordingOverlay created")
except Exception as e:
    print(f"  ✗ ERROR: Cannot create RecordingOverlay - {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# Try to show overlay
print("Attempting to show overlay...")
print("(You should see a waffle appear at the bottom of your screen)")
print()

try:
    overlay.show()
    print("  ✓ show() called successfully")
    print()
    print("Waiting 3 seconds...")
    time.sleep(1)

    # Check if subprocess is alive
    if overlay._is_alive():
        print("  ✓ Overlay subprocess is ALIVE")
        print(f"    PID: {overlay._process.pid}")
    else:
        print("  ✗ ERROR: Overlay subprocess is DEAD")
        print("  → Check ~/.waffler-hosted/app.log for errors")

    time.sleep(2)

    # Try updating level
    print()
    print("Testing level animation...")
    for i in range(10):
        level = (i % 5) / 5.0
        overlay.update_level(level)
        time.sleep(0.1)
    print("  ✓ Level updates sent")

    time.sleep(1)

    # Hide overlay
    print()
    print("Hiding overlay...")
    overlay.hide()
    print("  ✓ Hidden")

    time.sleep(1)

    # Stop overlay
    print()
    print("Stopping overlay...")
    overlay.stop()
    print("  ✓ Stopped")

except Exception as e:
    print(f"  ✗ ERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    try:
        overlay.stop()
    except:
        pass

print()
print("=" * 60)
print("Test complete!")
print()
print("Check the log file for detailed information:")
print("  ~/.waffler-hosted/app.log")
print("=" * 60)
