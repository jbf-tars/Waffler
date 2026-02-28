#!/usr/bin/env python3
"""Quick test to check if accessibility permission is detected."""

import sys

try:
    from ApplicationServices import AXIsProcessTrusted
    is_trusted = AXIsProcessTrusted()
    print(f"Accessibility permission detected: {is_trusted}")
    print(f"Type: {type(is_trusted)}")
    print(f"Bool value: {bool(is_trusted)}")
    sys.exit(0 if is_trusted else 1)
except ImportError as e:
    print(f"ERROR: Cannot import AXIsProcessTrusted: {e}")
    sys.exit(2)
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(3)
