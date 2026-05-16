#!/usr/bin/env python3
"""Manual diagnostic — reports whether macOS Accessibility permission is
granted to the current process.

Run when:
- Waffler isn't pasting transcripts (probably missing Accessibility grant)
- The wizard's permission pill won't go green
- You want to confirm pyobjc/ApplicationServices is actually importable

NOT a unit test. Lives in scripts/ not tests/ because it queries live
macOS state (the answer is whatever Privacy & Security currently says,
not a stable assertion). Exit codes: 0 granted · 1 not granted ·
2 ApplicationServices import failed · 3 other error.

Usage: python scripts/diagnose_accessibility.py
"""

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
