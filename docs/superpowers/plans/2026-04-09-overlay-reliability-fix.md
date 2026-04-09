# Overlay Reliability Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix overlay subprocess crashes by adding thread-safety, auto-restart with exponential backoff, and health monitoring

**Architecture:** Add `threading.Lock()` to prevent race conditions in `_send()`, replace ad-hoc restart logic with robust `_attempt_restart()` method using exponential backoff (0.5s, 1s, 2s), and centralize logging for better diagnostics

**Tech Stack:** Python 3, threading, subprocess management

**Spec:** `docs/superpowers/specs/2026-04-09-overlay-reliability-fix-design.md`

---

## File Structure

**Modified:**
- `src/overlay.py` - All changes in this single file
  - Add `_log()` centralized logging helper
  - Add thread lock and restart tracking to `__init__()`
  - Update `_send()` to be thread-safe and return bool
  - Add `_attempt_restart()` with exponential backoff
  - Update `show()` to use `_attempt_restart()`
  - Update `update_level()` to use `_attempt_restart()`
  - Replace all inline `log()` calls with `self._log()`

**No new files created** - this is a focused refactor of existing code.

---

## Task 1: Add Centralized Logging Helper

**Files:**
- Modify: `src/overlay.py:28-64` (after class definition, before `__init__`)

- [ ] **Step 1: Add `_log()` method to RecordingOverlay class**

Add this method right after the class docstring, before `__init__`:

```python
def _log(self, msg: str):
    """Centralized logging to app.log with timestamp."""
    from pathlib import Path
    from datetime import datetime
    try:
        log_file = Path.home() / ".waffler-hosted" / "app.log"
        with open(log_file, "a") as f:
            ts = datetime.now().strftime("%H:%M:%S")
            f.write(f"{ts}  {msg}\n")
    except Exception:
        pass  # Don't crash if logging fails
```

- [ ] **Step 2: Verify logging works**

Test manually:
```bash
cd /Users/james/waffler
python3 -c "
from src.overlay import RecordingOverlay
overlay = RecordingOverlay()
overlay._log('[TEST] Centralized logging works')
"
tail -1 ~/.waffler-hosted/app.log
```

Expected output: Line with timestamp and `[TEST] Centralized logging works`

- [ ] **Step 3: Commit**

```bash
git add src/overlay.py
git commit -m "feat(overlay): add centralized logging helper

Add _log() method to RecordingOverlay for consistent timestamped
logging. This replaces scattered inline log() function definitions.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Add Thread Lock and Restart Tracking

**Files:**
- Modify: `src/overlay.py:42-64` (`__init__` method)

- [ ] **Step 1: Add new instance variables to `__init__`**

Add these lines after `self._visible = False` (line 57):

```python
# Thread safety and restart tracking
self._send_lock = threading.Lock()  # Protect stdin writes
self._restart_count = 0             # Track restart attempts
self._last_restart_time = 0         # For exponential backoff
self._restart_window = 60           # Reset counter after 60s
```

- [ ] **Step 2: Verify initialization**

Test manually:
```bash
python3 -c "
from src.overlay import RecordingOverlay
overlay = RecordingOverlay()
assert hasattr(overlay, '_send_lock')
assert hasattr(overlay, '_restart_count')
assert overlay._restart_count == 0
assert overlay._restart_window == 60
print('✅ Initialization verified')
"
```

Expected output: `✅ Initialization verified`

- [ ] **Step 3: Commit**

```bash
git add src/overlay.py
git commit -m "feat(overlay): add thread lock and restart tracking

Add threading.Lock() for stdin write protection and instance variables
for tracking restart attempts with 60s window.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Make _send() Thread-Safe

**Files:**
- Modify: `src/overlay.py:396-404` (`_send` method)

- [ ] **Step 1: Update `_send()` to be thread-safe and return status**

Replace the entire `_send()` method (lines 396-404) with:

```python
def _send(self, data: dict) -> bool:
    """Write a JSON command to the subprocess stdin (thread-safe).

    Returns:
        bool: True if command sent successfully, False otherwise
    """
    if not self._is_alive():
        return False

    with self._send_lock:  # Ensure atomic write
        try:
            self._process.stdin.write(json.dumps(data) + "\n")
            self._process.stdin.flush()
            return True
        except (BrokenPipeError, OSError) as e:
            # Log instead of silently swallowing
            self._log(f"[overlay] ⚠️  Broken pipe during send: {e}")
            return False
```

- [ ] **Step 2: Verify thread-safety with concurrent sends**

Test manually (simulate race condition):
```bash
python3 -c "
import threading
import time
from src.overlay import RecordingOverlay

overlay = RecordingOverlay()
results = []

def send_many():
    for i in range(100):
        result = overlay._send({'type': 'test', 'i': i})
        results.append(result)

# Start multiple threads sending simultaneously
threads = [threading.Thread(target=send_many) for _ in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print(f'✅ Sent {len(results)} commands without crashes')
"
```

Expected: No crashes, output shows `✅ Sent 500 commands without crashes`

- [ ] **Step 3: Commit**

```bash
git add src/overlay.py
git commit -m "feat(overlay): make _send() thread-safe with locking

Add threading.Lock to prevent race conditions when multiple threads
write to subprocess stdin. Returns bool status instead of failing
silently. Logs all BrokenPipeError occurrences.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Implement Exponential Backoff Restart Logic

**Files:**
- Modify: `src/overlay.py` (add new method after `_is_alive()`)

- [ ] **Step 1: Add `_attempt_restart()` method**

Add this method right after `_is_alive()` (after line 394):

```python
def _attempt_restart(self) -> bool:
    """Restart subprocess with exponential backoff.

    Strategy:
    - Track restart attempts within 60-second window
    - Reset counter if last restart was >60s ago
    - Apply exponential backoff: 0.5s, 1s, 2s
    - Give up after 3 attempts in window

    Returns:
        bool: True if restart successful, False if max attempts exceeded
    """
    current_time = time.time()

    # Reset counter if last restart was >60s ago (stable period)
    if current_time - self._last_restart_time > self._restart_window:
        self._restart_count = 0

    self._restart_count += 1
    self._last_restart_time = current_time

    if self._restart_count > 3:
        self._log("[overlay] ✗ Max restart attempts (3) exceeded in 60s window, giving up")
        print("[overlay] ERROR: Max restart attempts exceeded", flush=True)
        return False

    # Exponential backoff: 0.5s, 1s, 2s (max 3 attempts)
    delay = 0.5 * (2 ** (self._restart_count - 1))
    self._log(f"[overlay] Restarting subprocess in {delay}s (attempt {self._restart_count}/3)")
    time.sleep(delay)

    self._start_process()
    success = self._is_alive()

    if success:
        self._log(f"[overlay] ✓ Subprocess restarted successfully, PID={self._process.pid}")
    else:
        self._log(f"[overlay] ✗ Subprocess restart failed")

    return success
```

- [ ] **Step 2: Verify exponential backoff timing**

Test manually:
```bash
python3 -c "
import time
from src.overlay import RecordingOverlay

overlay = RecordingOverlay()

# Simulate 3 restart attempts
for i in range(3):
    start = time.time()
    overlay._attempt_restart()
    elapsed = time.time() - start
    expected = 0.5 * (2 ** i)
    print(f'Attempt {i+1}: {elapsed:.2f}s (expected ~{expected}s)')

# 4th attempt should fail
result = overlay._attempt_restart()
print(f'4th attempt result: {result} (expected False)')
"
```

Expected output shows delays of ~0.5s, ~1s, ~2s, then False

- [ ] **Step 3: Verify counter reset after 60s**

Test manually:
```bash
python3 -c "
import time
from src.overlay import RecordingOverlay

overlay = RecordingOverlay()

# First restart
overlay._attempt_restart()
print(f'After 1st restart: count={overlay._restart_count}')

# Simulate 65 seconds passing
overlay._last_restart_time = time.time() - 65

# Second restart should reset counter
overlay._attempt_restart()
print(f'After 65s + 2nd restart: count={overlay._restart_count} (expected 1)')
"
```

Expected output: `count=1` after 65s window expires

- [ ] **Step 4: Commit**

```bash
git add src/overlay.py
git commit -m "feat(overlay): add exponential backoff restart logic

Implement _attempt_restart() with 0.5s/1s/2s delays, max 3 attempts
per 60s window. Counter resets after stable operation. Logs all
restart attempts and outcomes.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Update show() to Use New Restart Logic

**Files:**
- Modify: `src/overlay.py:67-110` (`show` method)

- [ ] **Step 1: Simplify show() to use _attempt_restart() and _log()**

Replace the entire `show()` method (lines 67-110) with:

```python
def show(self):
    """Show the recording overlay."""
    self._log("[overlay] show() called")

    if self._is_alive():
        self._log("[overlay] Subprocess already alive, sending show command")
        self._send({"type": "show"})
        self._visible = True
        return

    # Subprocess not running, start it
    self._log("[overlay] Starting new subprocess...")
    if self._attempt_restart():
        self._send({"type": "show"})
        self._visible = True
    else:
        self._log("[overlay] ✗ ERROR: Failed to start subprocess after retries")
        print("[overlay] ERROR: Subprocess failed to start!", flush=True)
```

- [ ] **Step 2: Verify show() uses centralized restart**

Test manually:
```bash
cd /Users/james/waffler
python3 -c "
from src.overlay import RecordingOverlay
overlay = RecordingOverlay()
overlay.show()  # Should start subprocess
"
tail -10 ~/.waffler-hosted/app.log | grep -E "show\\(\\)|Restarting|restarted"
```

Expected: Logs show `show() called`, `Restarting subprocess`, `restarted successfully`

- [ ] **Step 3: Commit**

```bash
git add src/overlay.py
git commit -m "refactor(overlay): use _attempt_restart() in show()

Replace ad-hoc retry logic with centralized _attempt_restart().
Use _log() for consistent logging. Simplifies code and ensures
exponential backoff applies to all restarts.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Update update_level() for Auto-Restart

**Files:**
- Modify: `src/overlay.py:118-154` (`update_level` method)

- [ ] **Step 1: Replace update_level() with new auto-restart logic**

Replace the entire `update_level()` method (lines 118-154) with:

```python
def update_level(self, level: float):
    """
    Push an audio RMS level (0.0-1.0) to animate the VU bars.
    Safe to call from any thread at any rate.
    Auto-restarts subprocess if it died during recording.
    """
    # Detect subprocess death during recording
    if not self._is_alive():
        if self._visible:
            # Process died while recording — attempt auto-restart
            self._log("[overlay] Subprocess died during recording, attempting auto-restart...")

            if self._attempt_restart():
                # Restart successful, resume showing overlay
                self._send({"type": "show"})
            else:
                # Max restarts exceeded, give up gracefully
                self._visible = False
                self._log("[overlay] Recording continues without overlay (subprocess unrecoverable)")
        return

    # Send level update (thread-safe)
    level = max(0.0, min(1.0, float(level)))
    self._send({"type": "level", "value": level})
```

- [ ] **Step 2: Verify auto-restart during recording**

This requires running the app:
```bash
# Start the app
cd /Users/james/waffler
python3 app.py &
APP_PID=$!

# Start recording
# (Press hotkey manually)

# Find and kill overlay subprocess
sleep 2
OVERLAY_PID=$(ps aux | grep overlay_process.py | grep -v grep | awk '{print $2}')
echo "Killing overlay subprocess PID: $OVERLAY_PID"
kill -9 $OVERLAY_PID

# Check logs for auto-restart
sleep 2
tail -20 ~/.waffler-hosted/app.log | grep -E "died|auto-restart|restarted"

# Clean up
kill $APP_PID
```

Expected: Logs show "Subprocess died during recording, attempting auto-restart..." and "restarted successfully"

- [ ] **Step 3: Commit**

```bash
git add src/overlay.py
git commit -m "feat(overlay): auto-restart subprocess in update_level()

Detect subprocess death during recording and trigger _attempt_restart().
Uses exponential backoff. Recording continues even if overlay fails.
Removes old _restart_attempted flag logic.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Replace Inline log() Calls

**Files:**
- Modify: `src/overlay.py` (multiple methods: `show_toast`, `_find_python`, `_start_process`, `_read_stdout`, `_read_stderr`)

- [ ] **Step 1: Replace inline log() in show_toast() method**

In `show_toast()` (lines 164-192), remove the inline `log()` definition (lines 169-178) and replace all `log(...)` calls with `self._log(...)`:

Before:
```python
def show_toast(self, style: str, heading: str, body: str):
    """..."""
    from pathlib import Path
    from datetime import datetime
    log_file = Path.home() / ".waffler-hosted" / "app.log"
    def log(msg):
        try:
            with open(log_file, "a") as f:
                ts = datetime.now().strftime("%H:%M:%S")
                f.write(f"{ts}  {msg}\n")
        except Exception:
            pass

    log(f"[overlay.py] show_toast: style={style}, heading='{heading}'")
    ...
```

After:
```python
def show_toast(self, style: str, heading: str, body: str):
    """
    Show a floating toast popup above the pill overlay.
    style: "cancel" or "error"
    """
    self._log(f"[overlay.py] show_toast: style={style}, heading='{heading}'")
    if self._is_alive():
        self._log("[overlay.py] Subprocess ALIVE, sending show_toast command")
        self._send({
            "type": "show_toast",
            "style": style,
            "heading": heading,
            "body": body,
        })
        self._log("[overlay.py] show_toast command SENT successfully")
    else:
        self._log("[overlay.py] ERROR: Subprocess is DEAD, cannot show toast!")
```

- [ ] **Step 2: Replace inline log() in _find_python() method**

In `_find_python()` (lines 231-287), remove inline `log()` definition and imports (lines 232-243), replace all `log(...)` with `self._log(...)`:

Keep only `import os` at top of method, remove:
```python
from pathlib import Path
from datetime import datetime
log_file = Path.home() / ".waffler-hosted" / "app.log"
def log(msg):
    ...
```

Replace all `log(...)` calls with `self._log(...)`

- [ ] **Step 3: Replace inline log() in _start_process() method**

In `_start_process()` (lines 289-391), remove inline `log()` definition (lines 291-300), replace all `log(...)` with `self._log(...)`

- [ ] **Step 4: Replace inline log() in _read_stdout() method**

In `_read_stdout()` (lines 406-448), remove inline `log()` definition (lines 407-417), replace all `log(...)` with `self._log(...)`

- [ ] **Step 5: Replace inline log() in _read_stderr() method**

In `_read_stderr()` (lines 450-469), remove inline `log()` definition (lines 451-461), replace all `log(...)` with `self._log(...)`

- [ ] **Step 6: Verify no inline log() definitions remain**

Search for inline log definitions:
```bash
grep -n "def log(msg)" src/overlay.py
```

Expected output: Nothing (no matches)

- [ ] **Step 7: Verify logging still works**

```bash
cd /Users/james/waffler
python3 -c "
from src.overlay import RecordingOverlay
overlay = RecordingOverlay()
overlay.show_toast('test', 'Test Toast', 'Testing logging')
"
tail -5 ~/.waffler-hosted/app.log
```

Expected: Logs show toast messages with timestamps

- [ ] **Step 8: Commit**

```bash
git add src/overlay.py
git commit -m "refactor(overlay): replace inline log() with _log()

Remove all inline log() function definitions and use centralized
_log() method throughout. Reduces code duplication and ensures
consistent logging format.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Manual Testing & Validation

**Files:**
- Test: `src/overlay.py` (full integration testing)

- [ ] **Step 1: Race Condition Stress Test**

**Goal:** Verify thread-safety under high contention

1. Build and run the app: `cd /Users/james/waffler && python3 app.py`
2. Press hotkey to start recording
3. While recording (VU meter active):
   - Rapidly press hotkey 10 times (start/stop/start/stop)
   - Move mouse over overlay buttons
4. Check logs: `grep "BrokenPipeError" ~/.waffler-hosted/app.log`

**Success criteria:**
- ✅ No broken pipe errors in logs
- ✅ Overlay stays visible and responsive
- ✅ No subprocess crashes

- [ ] **Step 2: Subprocess Crash Simulation Test**

**Goal:** Verify auto-restart works correctly

1. Start recording
2. Find overlay subprocess PID: `ps aux | grep overlay_process`
3. Kill it: `kill -9 <PID>`
4. Continue speaking (recording should continue)
5. Check logs: `tail -20 ~/.waffler-hosted/app.log`

**Success criteria:**
- ✅ Overlay disappears briefly
- ✅ Logs show "attempting auto-restart..."
- ✅ Logs show "restarted successfully, PID=..."
- ✅ Overlay reappears within 1 second
- ✅ Recording completes successfully

- [ ] **Step 3: Repeated Restart Test**

**Goal:** Verify exponential backoff and max retry limit

1. Start recording
2. Kill subprocess 4 times in quick succession (<60 seconds between kills)
3. Observe delays between restarts in logs

**Success criteria:**
- ✅ First restart: "Restarting subprocess in 0.5s (attempt 1/3)"
- ✅ Second restart: "Restarting subprocess in 1.0s (attempt 2/3)"
- ✅ Third restart: "Restarting subprocess in 2.0s (attempt 3/3)"
- ✅ Fourth attempt: "Max restart attempts (3) exceeded", no more restarts
- ✅ Recording continues without overlay

- [ ] **Step 4: Restart Counter Reset Test**

**Goal:** Verify restart counter resets after 60s

1. Start recording
2. Kill subprocess once → verify restart (count = 1)
3. Wait 65 seconds (let counter reset)
4. Kill subprocess again → verify restart delay

**Success criteria:**
- ✅ Second restart shows "Restarting subprocess in 0.5s (attempt 1/3)", NOT 2/3
- ✅ Logs confirm counter reset

- [ ] **Step 5: Long Recording Stability Test**

**Goal:** Verify no pipe errors during extended use

1. Start a 5+ minute recording
2. Speak continuously to keep VU meter active
3. Monitor logs in real-time: `tail -f ~/.waffler-hosted/app.log`

**Success criteria:**
- ✅ No broken pipe errors
- ✅ Overlay stays responsive throughout
- ✅ VU meter animates smoothly
- ✅ Recording completes successfully

- [ ] **Step 6: Document test results**

Create test report:
```bash
echo "# Overlay Reliability Fix - Test Results

Date: $(date)

## Test 1: Race Condition Stress Test
Status: PASS/FAIL
Notes: ...

## Test 2: Subprocess Crash Simulation
Status: PASS/FAIL
Notes: ...

## Test 3: Repeated Restart Test
Status: PASS/FAIL
Notes: ...

## Test 4: Restart Counter Reset Test
Status: PASS/FAIL
Notes: ...

## Test 5: Long Recording Stability Test
Status: PASS/FAIL
Notes: ...

## Summary
All tests passing: YES/NO
Ready for deployment: YES/NO
" > test-results-$(date +%Y%m%d).md
```

- [ ] **Step 7: Final commit**

```bash
git add test-results-*.md
git commit -m "test(overlay): validate reliability fix

All manual tests pass:
✅ Thread-safety verified under high contention
✅ Auto-restart works when subprocess crashes
✅ Exponential backoff prevents restart storms
✅ Counter resets after 60s stable operation
✅ No broken pipes in long recordings

Overlay reliability issue resolved.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Completion Checklist

Implementation is complete when:

- ✅ Thread lock added to `_send()` method
- ✅ Centralized `_log()` helper method implemented
- ✅ `_attempt_restart()` with exponential backoff implemented
- ✅ `update_level()` auto-restart logic added
- ✅ `show()` updated to use `_attempt_restart()`
- ✅ All inline `log()` definitions replaced with `self._log()`
- ✅ All 5 manual testing scenarios pass
- ✅ No regressions in normal recording workflow
- ✅ Logs show clear diagnostics when issues occur

---

## Notes

**No automated tests:** This fix addresses threading and subprocess behavior that's difficult to unit test. Manual testing with crash simulation provides better validation than mocked tests.

**Backward compatible:** All changes are internal to `RecordingOverlay` class. No API changes.

**Single file change:** All modifications in `src/overlay.py` only - focused, low-risk change.

**DRY:** Centralized logging and restart logic eliminates code duplication.

**YAGNI:** No over-engineering - just the minimum needed to fix the race condition and add resilient restart logic.
