#!/usr/bin/env python3
"""Test Fn key detection"""
import sys
from pathlib import Path

# Add src to path
PROJECT_ROOT = Path(__file__).parent
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
