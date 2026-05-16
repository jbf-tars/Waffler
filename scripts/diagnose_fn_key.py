#!/usr/bin/env python3
"""Manual diagnostic — runs FnKeyMonitor in the foreground and prints a
line every time the OS reports an Fn press / release / Space press.

Run when:
- Waffler's Fn hotkey doesn't seem to fire at all (the wizard step-2
  pill won't go green)
- You want to see the raw OS event stream without Waffler's hold-quiet
  / sticky-mode logic on top (useful for diagnosing flag chatter)
- You're on a Mac model with a quirky Fn key (M3 Max Touch Bar, external
  keyboards with F13–F19 mapping, etc.)

NOT a unit test — runs indefinitely until Ctrl+C, requires you to
actually press keys, and uses the real CGEventTap which needs
Accessibility + Input Monitoring permissions on the running terminal.

Note: still imports from src/fn_key_cgevent.py rather than
src/mac_hotkey_monitor.py. fn_key_cgevent has been a thin
backward-compat shim since v3.14.13; the diagnostic still goes through
it deliberately so you can sanity-check both layers in one run.

Usage: python scripts/diagnose_fn_key.py    (Ctrl+C to stop)
"""
import sys
from pathlib import Path

# Add src to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fn_key_cgevent import FnKeyMonitor

def on_fn_press():
    print("✅ FN PRESSED!")

def on_fn_release():
    print("✅ FN RELEASED!")

def on_space():
    print("✅ SPACE PRESSED!")

print("Starting Fn key monitor test...")
print("Press the Fn key (bottom left)")
print("Press Ctrl+C to exit")

monitor = FnKeyMonitor(
    on_fn_press=on_fn_press,
    on_fn_release=on_fn_release,
    on_space_press=on_space
)

monitor.start()

# Keep running
import time
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopping...")
    monitor.stop()
