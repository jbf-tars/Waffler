#!/usr/bin/env python3
"""Stress test for `AudioRecorder` stream lifecycle.

Repro for the wizard → pipeline handoff segfault (Bug B, v3.14.14/15):
without the v3.14.15 fix (process-wide `_STREAM_LOCK` serialising stream
creation/teardown, plus `wizard_stop_hotkey_test` calling `shutdown()`
not just dropping the recorder), this loop reliably segfaults macOS
within a handful of iterations because CoreAudio's HAL I/O thread fires
a callback into the outgoing stream's freed CFFI closure while the next
stream is being constructed.

With the fix in place this script should complete 50 iterations cleanly.

Run with:
    python tests/test_audio_stream_lifecycle.py
"""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from audio import AudioRecorder


def stress_serial(iterations: int = 50) -> bool:
    """Create / start_monitoring / shutdown a recorder in tight serial loop.
    Mirrors the wizard → pipeline handoff exactly."""
    print(f"\n[serial] {iterations} iterations of create → monitor → shutdown")
    for i in range(iterations):
        rec = AudioRecorder(sample_rate=16000, channels=1)
        rec.start_monitoring()
        # Hold the stream for ~80ms — about one HAL buffer cycle at
        # 16kHz/1024-frame blocksize. This is the window where the segfault
        # used to fire reliably under the unsynchronised code.
        time.sleep(0.08)
        rec.shutdown()
        # Drop the reference IMMEDIATELY — that's what the buggy
        # `wizard_stop_hotkey_test = None` assignment was doing.
        del rec
        if (i + 1) % 10 == 0:
            print(f"[serial]   iter {i + 1}/{iterations} ok")
    print("[serial] ✓ completed without crash")
    return True


def stress_overlapping(iterations: int = 20) -> bool:
    """Create a new recorder before tearing down the previous one — the
    *exact* wizard→pipeline handoff pattern."""
    print(f"\n[overlap] {iterations} iterations of overlapping create/teardown")
    prev = None
    for i in range(iterations):
        new = AudioRecorder(sample_rate=16000, channels=1)
        new.start_monitoring()
        # While `new` is live, tear down the previous one. This is the
        # exact race condition the wizard→pipeline handoff hit.
        if prev is not None:
            prev.shutdown()
            del prev
        prev = new
        time.sleep(0.05)
        if (i + 1) % 5 == 0:
            print(f"[overlap]   iter {i + 1}/{iterations} ok")
    if prev is not None:
        prev.shutdown()
    print("[overlap] ✓ completed without crash")
    return True


if __name__ == "__main__":
    try:
        stress_serial(50)
        stress_overlapping(20)
        print("\n✅ All stress tests passed.")
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
