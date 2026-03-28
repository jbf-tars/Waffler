# Wizard UX Improvements - Testing Guide

## Overview

This guide covers manual testing for the wizard UX improvements implemented in this branch.

---

## Task 8: Integration Testing

### Prerequisites
```bash
cd /Users/james/waffler/.worktrees/wizard-ux-improvements
# Clear onboarding state to see wizard
rm ~/.waffler/onboarding_done
```

### Test 1: Permissions Page Layout (No Scrolling)

**Steps:**
1. Launch app: `python3 app.py`
2. Verify permissions page displays without scrolling
3. Check that both permission cards are visible side-by-side

**Expected:**
- ✓ No vertical scrolling needed
- ✓ Two cards displayed in 2-column grid
- ✓ Next button visible at bottom
- ✓ All content fits on screen

---

### Test 2: Permission Status Polling

**Steps:**
1. Note initial state: both badges show "○", status shows "Not granted"
2. Click "Open System Settings" for Accessibility
3. Grant Accessibility permission in System Settings
4. Return to Waffler (keep open)
5. Wait up to 2 seconds

**Expected:**
- ✓ Accessibility badge changes from "○" to "✓" (green)
- ✓ Accessibility button text changes to "✓ Granted" (green)
- ✓ Accessibility status changes to "Granted" (green)
- ✓ Next button remains disabled (only 1 of 2 granted)

**Steps (continued):**
6. Click "Open System Settings" for Input Monitoring
7. Grant Input Monitoring permission in System Settings
8. Return to Waffler
9. Wait up to 2 seconds

**Expected:**
- ✓ Input Monitoring badge changes from "○" to "✓" (green)
- ✓ Input Monitoring button text changes to "✓ Granted" (green)
- ✓ Input Monitoring status changes to "Granted" (green)
- ✓ Next button becomes enabled (both granted)

---

### Test 3: API Keys Provider Selection

**Steps:**
1. Click Next to proceed to API keys page (after granting permissions)
2. Verify header shows 🔑 key emoji (not ⚡ lightning)
3. Verify text shows "(you only need one)" in bold
4. Verify Groq pill button is active (gold gradient)
5. Verify only Groq field is visible

**Expected:**
- ✓ Header shows "🔑 API Keys"
- ✓ "(you only need one)" is emphasized
- ✓ Groq button has gold gradient background
- ✓ OpenAI button is gray/inactive
- ✓ Only Groq input field visible

---

### Test 4: Provider Switching

**Steps:**
1. Click OpenAI pill button
2. Observe transition

**Expected:**
- ✓ OpenAI button becomes active (gold gradient)
- ✓ Groq button becomes inactive (gray)
- ✓ Groq field fades out smoothly (300ms)
- ✓ OpenAI field fades in smoothly (300ms)
- ✓ Only one field visible at a time

**Steps (continued):**
3. Click Groq pill button again
4. Observe transition back

**Expected:**
- ✓ Groq button becomes active again
- ✓ OpenAI button becomes inactive
- ✓ Smooth transition back to Groq field

---

### Test 5: Provider Preference Persistence

**Steps:**
1. Select OpenAI provider
2. Close app completely
3. Relaunch app: `python3 app.py`
4. Navigate back to API keys page

**Expected:**
- ✓ OpenAI provider is still selected
- ✓ OpenAI field is visible
- ✓ Preference saved in localStorage

---

### Test 6: Key Visibility Toggle

**Steps:**
1. Enter a test key in Groq field: "test-key-12345"
2. Verify key appears as dots (password field)
3. Click eye button 👁️
4. Verify key becomes visible as plain text
5. Click eye button again
6. Verify key returns to dots

**Expected:**
- ✓ Initial state: password (dots)
- ✓ After click: plain text visible
- ✓ After second click: password (dots) again

---

### Test 7: Complete Wizard Flow

**Steps:**
1. Clear onboarding: `rm ~/.waffler/onboarding_done`
2. Launch app: `python3 app.py`
3. Complete all wizard steps:
   - Grant both permissions
   - Skip hotkeys step (or configure)
   - Enter API key for chosen provider
   - Complete "Try It" step
4. Verify wizard completes and main app loads

**Expected:**
- ✓ Wizard completes successfully
- ✓ Main app interface loads
- ✓ `~/.waffler/onboarding_done` file created
- ✓ No errors in console

---

### Test 8: Responsive Behavior (Optional)

**Steps:**
1. Resize window to narrow width (< 768px)
2. Navigate to permissions page

**Expected:**
- ✓ Permission cards stack vertically (1 column)
- ✓ Max-width adjusts to 480px
- ✓ Still no scrolling needed

---

## Task 9: Fn Key Popup Testing

### Background

The Fn key experimental fix attempts to suppress the macOS language switcher popup ("Should 'A, GB'") that appears when pressing the Fn key for push-to-talk.

**Fix Applied:** Modified `src/fn_key_cgevent.py` to strip Fn flag from event instead of complete suppression.

### Test Procedure

**Steps:**
1. Build app with changes:
   ```bash
   cd /Users/james/waffler/.worktrees/wizard-ux-improvements
   pyinstaller Waffler_mac.spec
   ```

2. Launch built app: `dist/Waffler.app`

3. Complete wizard setup (grant all permissions)

4. Press and hold Fn key

5. Observe screen for popup

**Expected Outcomes:**

**If popup does NOT appear:**
- ✅ **Success!** Experimental fix works
- The event flag stripping approach is effective
- Document as resolved in design spec

**If popup STILL appears:**
- ❌ **Fix unsuccessful**
- macOS system UI cannot be fully suppressed with current approach
- Implement fallback: Add documentation explaining this is expected macOS behavior
- Update wizard hint: "Note: You may briefly see a system popup when pressing Fn - this is normal and doesn't affect functionality"

### Verification Steps

6. Test push-to-talk functionality:
   - Press Fn+Space
   - Verify audio recording starts
   - Release Fn
   - Verify audio recording stops and transcript appears

**Expected:**
- ✓ Push-to-talk works correctly
- ✓ Audio recording starts/stops as expected
- ✓ Transcription completes successfully

---

## Test Results Documentation

After completing all tests, document results:

```bash
cd /Users/james/waffler/.worktrees/wizard-ux-improvements
cat > test-results.txt << 'EOF'
Wizard UX Improvements - Test Results
=====================================
Date: $(date)
Tester: [Your Name]

Task 8: Integration Testing
----------------------------
[ ] Test 1: Permissions page layout (no scrolling)
[ ] Test 2: Permission status polling
[ ] Test 3: API keys provider selection
[ ] Test 4: Provider switching
[ ] Test 5: Provider preference persistence
[ ] Test 6: Key visibility toggle
[ ] Test 7: Complete wizard flow
[ ] Test 8: Responsive behavior

Task 9: Fn Key Popup Testing
-----------------------------
[ ] Fn key popup appears: YES / NO
[ ] Push-to-talk works correctly: YES / NO
[ ] Conclusion: FIX WORKS / FIX UNSUCCESSFUL

Issues Found:
-------------
(List any bugs or unexpected behavior here)

EOF
cat test-results.txt
```

---

## Next Steps After Testing

1. **If all tests pass:**
   - Proceed to merge/PR creation
   - Use `finishing-a-development-branch` skill

2. **If issues found:**
   - Document issues in test-results.txt
   - Create fix tasks for critical bugs
   - Re-test after fixes

3. **Fn key popup decision:**
   - If fix works: Update design spec status to "Resolved"
   - If fix fails: Add documentation fallback as specified in design spec

---

## Quick Commands Reference

```bash
# Clear onboarding to test wizard
rm ~/.waffler/onboarding_done

# Launch app for testing
python3 app.py

# Build for Fn key testing
pyinstaller Waffler_mac.spec

# Check git status
git status

# View commits
git log --oneline

# Run any tests (if available)
pytest  # or npm test, cargo test, etc.
```
