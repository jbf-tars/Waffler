# Overlay Reliability Fix - Design Specification

**Date:** 2026-04-09
**Status:** Approved
**Priority:** High (B in B, C, A sequence)

## Problem Statement

The Waffler recording overlay sometimes fails to appear when the user presses the hotkey, despite recording working correctly. This is purely a visual feedback issue - audio capture continues normally.

### Root Cause Analysis

Investigation of app.log revealed:

1. **No thread-safety**: The `_send()` method in `RecordingOverlay` has no locking mechanism
2. **Race condition**: Multiple threads call `_send()` simultaneously:
   - VU level monitoring thread: ~30 calls/second to `update_level()` → `_send()`
   - Main thread: calls to `show()` / `hide()` / etc. → `_send()`
3. **Silent failures**: `BrokenPipeError` and `OSError` are caught and silently swallowed
4. **Subprocess auto-quit**: When stdin gets corrupted/closed, `overlay_process.py`'s `_stdin_reader()` detects closure and sends quit command
5. **Result**: Overlay subprocess terminates unexpectedly, leaving recordings without visual feedback

**Evidence from logs:**
```
18:02:26  [overlay] _read_stdout: ended (subprocess stdout closed)
18:03:24  [overlay] Starting new subprocess...
```

The subprocess crashes and restarts, but during the gap, overlay commands fail silently.

## Solution: Robust Subprocess Management

Add thread-safety, detailed logging, automatic restart with exponential backoff, and health monitoring.

## Architecture Overview

### Current Flow (Broken)
```
VU Thread ──┐
            ├──> _send() ──> stdin (NO LOCK) ──> subprocess
Main Thread ┘                 ↓
                         Race condition!
                         Corrupted JSON
                              ↓
                         Broken pipe
                              ↓
                         Subprocess quits
```

### New Flow (Fixed)
```
VU Thread ──┐
            ├──> _send() ──> Lock ──> stdin ──> subprocess
Main Thread ┘                 ↑               (monitored)
                         Thread-safe            ↓
                         Atomic writes    Health check
                         Error logging          ↓
                                          Auto-restart
```

## Detailed Design

### 1. Thread-Safety Implementation

**File:** `src/overlay.py`

**Add lock to `__init__`:**
```python
def __init__(self, on_cancel=None, on_stop=None, on_cancel_request=None,
             on_toast_action=None):
    # ... existing init code ...
    self._send_lock = threading.Lock()  # NEW: Protect stdin writes
    self._restart_count = 0             # NEW: Track restart attempts
    self._last_restart_time = 0         # NEW: For exponential backoff
    self._restart_window = 60           # NEW: Reset counter after 60s
```

**Update `_send()` method:**
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

**Why this works:**
- `threading.Lock()` ensures only one thread writes to stdin at a time
- Prevents JSON corruption from interleaved writes
- Returns boolean status so callers can handle failures
- Logs all pipe errors for debugging

**Note on return value handling:**
- Only `show()` and `update_level()` need explicit failure handling (trigger restart)
- Other methods (`hide()`, `show_toast()`, etc.) can safely ignore `False` returns - failures are already logged and subprocess will auto-restart on next critical operation

### 2. Centralized Logging

**Add logging helper method:**
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

**Replace inline logging:**
- Replace all inline `log()` function definitions with `self._log()`
- Consistent logging format across all overlay operations

### 3. Automatic Restart with Exponential Backoff

**Add restart logic method:**
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

### 4. Health Monitoring & Auto-Recovery

**Update `update_level()` for auto-restart:**
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
    self._send({"type": "level", "value": level})
```

**Update `show()` for better restart handling:**
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

### 5. Enhanced Error Logging

**Log these events:**

1. **Pipe errors**: All `BrokenPipeError` and `OSError` with context
2. **Subprocess lifecycle**: Start, stop, crash, restart events with PIDs
3. **Restart attempts**: Count, delay, success/failure
4. **Health check failures**: When subprocess is alive but not responding
5. **Give-up events**: When max retries exceeded

**Example log output:**
```
18:05:23  [overlay] show() called
18:05:23  [overlay] Subprocess already alive, sending show command
18:05:23  [overlay] ⚠️  Broken pipe during send: [Errno 32] Broken pipe
18:05:23  [overlay] Subprocess died during recording, attempting auto-restart...
18:05:23  [overlay] Restarting subprocess in 0.5s (attempt 1/3)
18:05:24  [overlay] ✓ Subprocess restarted successfully, PID=12345
```

## Testing Plan

### Manual Testing Scenarios

#### 1. Race Condition Stress Test
**Goal:** Verify thread-safety under high contention

**Steps:**
1. Start Waffler
2. Press hotkey to start recording
3. While recording (VU meter active at ~30 updates/sec):
   - Rapidly press hotkey multiple times (start/stop/start/stop)
   - Move mouse over overlay buttons
4. Check logs for any `BrokenPipeError`

**Success criteria:**
- ✅ No broken pipe errors in logs
- ✅ Overlay stays visible and responsive
- ✅ No subprocess crashes

#### 2. Subprocess Crash Simulation
**Goal:** Verify auto-restart works correctly

**Steps:**
1. Start recording
2. Find overlay subprocess PID: `ps aux | grep overlay_process`
3. Kill it: `kill -9 <PID>`
4. Continue speaking (recording should continue)
5. Check if overlay reappears

**Success criteria:**
- ✅ Overlay disappears briefly
- ✅ Logs show "attempting auto-restart..."
- ✅ Overlay reappears within 1 second
- ✅ Recording completes successfully

#### 3. Repeated Restart Test
**Goal:** Verify exponential backoff and max retry limit

**Steps:**
1. Start recording
2. Kill subprocess 4 times in quick succession (<60 seconds)
3. Observe delays between restarts

**Success criteria:**
- ✅ First restart: ~0.5s delay
- ✅ Second restart: ~1s delay
- ✅ Third restart: ~2s delay
- ✅ Fourth attempt: Logs "Max restart attempts exceeded", gives up
- ✅ Recording continues without overlay

#### 3b. Restart Counter Reset Test
**Goal:** Verify restart counter resets after 60s of stable operation

**Steps:**
1. Start recording
2. Kill subprocess once → verify restart (count = 1)
3. Wait 65 seconds (let counter reset)
4. Kill subprocess again → verify restart with 0.5s delay (count reset to 1, not 2)

**Success criteria:**
- ✅ Second restart uses 0.5s delay (not 1s), confirming counter reset
- ✅ Logs show restart attempt 1/3, not 2/3

#### 4. Long Recording Stability Test
**Goal:** Verify no pipe errors during extended use

**Steps:**
1. Start a 5+ minute recording
2. Speak continuously to keep VU meter active
3. Monitor logs in real-time: `tail -f ~/.waffler-hosted/app.log`

**Success criteria:**
- ✅ No broken pipe errors
- ✅ Overlay stays responsive throughout
- ✅ VU meter animates smoothly
- ✅ Recording completes successfully

#### 5. Multi-Mac Reproduction Test
**Goal:** Verify fix resolves the original issue

**Steps:**
1. Deploy fixed version to Macs where overlay "sometimes doesn't appear"
2. Use normally for several recordings
3. Monitor for any overlay failures

**Success criteria:**
- ✅ Overlay appears consistently on every recording
- ✅ No "subprocess died" messages in logs during normal use
- ✅ User reports improved reliability

### Success Criteria Summary

**Must have:**
- No `BrokenPipeError` in logs during normal operation
- Overlay auto-restarts when subprocess crashes
- Recording works even if overlay fails completely
- Detailed error logs help diagnose any remaining issues

**Nice to have:**
- Restart counter resets after stable operation
- Exponential backoff prevents restart storms
- Clear log messages for troubleshooting

## Implementation Notes

### Files Modified
- `src/overlay.py` - All changes in this one file

### Backward Compatibility
- No API changes - all modifications are internal
- Existing overlay commands work identically
- Log format enhanced but remains compatible

### Performance Impact
- Minimal: `threading.Lock()` adds ~microseconds per command
- VU level thread runs at 30Hz (33ms intervals), lock contention negligible
- No measurable user-facing performance impact

### Risks & Mitigations

**Risk:** Lock contention slows down VU meter updates
- **Mitigation:** Lock is only held during `stdin.write()` + `flush()` (~1ms)
- **Fallback:** If needed, can use lock-free queue approach (more complex)

**Risk:** Exponential backoff causes noticeable delay
- **Mitigation:** First restart is 0.5s, barely noticeable
- **Context:** Only happens when subprocess crashes (rare after fix)

**Risk:** Auto-restart during recording could confuse subprocess state
- **Mitigation:** State is simple (show/hide/level), easily re-sent
- **Testing:** Crash simulation test validates recovery

## Future Enhancements (Out of Scope)

1. **Heartbeat monitoring**: Periodic ping/pong to detect hung subprocess
2. **Metrics collection**: Track subprocess uptime, restart rate
3. **Lock-free queue**: If lock contention becomes measurable issue
4. **Subprocess pooling**: Pre-spawn backup subprocess for instant failover

These are not needed for the current fix but could be considered if issues persist.

## Acceptance Criteria

Implementation is complete when:

1. ✅ Thread lock added to `_send()` method
2. ✅ Centralized `_log()` helper method implemented
3. ✅ `_attempt_restart()` with exponential backoff implemented
4. ✅ `update_level()` auto-restart logic added
5. ✅ `show()` updated to use `_attempt_restart()`
6. ✅ All error logging enhanced (no silent failures)
7. ✅ Manual testing scenarios pass
8. ✅ No regressions in normal recording workflow
9. ✅ Logs show clear diagnostics when issues occur

## References

- Original issue: Overlay sometimes doesn't appear on some Macs
- Log analysis: `~/.waffler-hosted/app.log` line 1336 shows subprocess closure
- Root cause: Race condition in `src/overlay.py` `_send()` method
- Related code: `src/overlay_process.py` `_stdin_reader()` auto-quit behavior
