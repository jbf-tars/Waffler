# Wizard Setup Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Streamline wizard setup with compact header, simplified permission cards, and automatic macOS permission prompts.

**Architecture:** Update wizard UI to top-left icon layout with minimal progress bar, remove permission status tracking, add backend triggers that make macOS automatically prompt for permissions and add Waffler to System Settings lists.

**Tech Stack:** HTML/CSS (wizard UI), JavaScript (frontend), Python (backend permission triggers), macOS APIs (AXIsProcessTrusted, CGEventTapCreate)

---

## File Structure

### Modified Files
- `ui/index.html` - Wizard header, permission cards markup
- `ui/style.css` - Compact layout, spacing, remove unused classes
- `ui/app.js` - Remove permission monitoring, update step 1 logic
- `app.py` - Add permission trigger method and API endpoint

### No New Files
This is a refactor of existing wizard code - no new files created.

---

## Task 1: Update Wizard Header HTML

**Files:**
- Modify: `ui/index.html` (wizard header section, ~lines 279-290)

- [ ] **Step 1: Locate current wizard header**

Find the existing `.wizard-header` section in index.html:
```bash
grep -n "wizard-header" ui/index.html
```

Expected: Line showing current centered header with logo

- [ ] **Step 2: Replace header markup**

Replace the existing wizard header with compact top-left layout:

```html
<!-- Compact header with icon left, progress right -->
<div class="wizard-header-compact">
  <div class="wizard-header-left">
    <img src="logo-icon.png" class="wizard-icon" alt="Waffler">
    <span class="wizard-app-name">Waffler</span>
  </div>
  <div class="wizard-step-indicator">
    <span id="wizStepText">Step 1 of 4</span>
  </div>
</div>

<!-- Minimal progress bar -->
<div class="wizard-progress-bar">
  <div class="progress-segment" id="wizProgress1"></div>
  <div class="progress-segment" id="wizProgress2"></div>
  <div class="progress-segment" id="wizProgress3"></div>
  <div class="progress-segment" id="wizProgress4"></div>
</div>

<div class="wizard-title">Welcome to Waffler</div>
<div class="wizard-subtitle">Let's get you set up in 4 quick steps.</div>
```

- [ ] **Step 3: Verify HTML structure**

Check the file looks correct:
```bash
grep -A10 "wizard-header-compact" ui/index.html
```

Expected: New header structure with icon, app name, step indicator, progress bar

- [ ] **Step 4: Commit**

```bash
git add ui/index.html
git commit -m "refactor: update wizard header to compact top-left layout"
```

---

## Task 2: Update Permission Cards HTML

**Files:**
- Modify: `ui/index.html` (permissions step content, ~lines 312-354)

- [ ] **Step 1: Locate permission cards section**

Find the current permission display in wizContent1:
```bash
grep -n "wizContent1" ui/index.html
```

Expected: Line showing step 1 content div

- [ ] **Step 2: Replace permission cards markup**

Replace the permission cards with simplified grid layout (no status tracking):

```html
<!-- Step 1: Permissions -->
<div class="wizard-step-content" id="wizContent1">
  <div class="wizard-card" style="max-width: 900px; margin: 0 auto;">
    <div class="wizard-card-icon" style="font-size: 48px; margin-bottom: 12px;">🔐</div>
    <h2 style="margin-bottom: 8px;">Grant Permissions</h2>
    <p class="wizard-card-desc" style="margin-bottom: 24px; font-size: 15px; opacity: 0.8;">
      Waffler needs two permissions for push-to-talk functionality
    </p>

    <div class="wizard-permission-grid">
      <!-- Accessibility Card -->
      <div class="permission-card">
        <div class="permission-icon">🔑</div>
        <h4>Accessibility</h4>
        <p>For hotkey detection and auto-paste</p>
        <button class="btn-open-settings" onclick="openAccessibilitySettings()">
          Open System Settings
        </button>
      </div>

      <!-- Input Monitoring Card -->
      <div class="permission-card">
        <div class="permission-icon">⌨️</div>
        <h4>Input Monitoring</h4>
        <p>For Fn key push-to-talk</p>
        <button class="btn-open-settings" onclick="openInputMonitoringSettings()">
          Open System Settings
        </button>
      </div>
    </div>

    <p class="wizard-hint" style="margin-top: 20px;">
      Click the buttons above to grant permissions, then click Next below
    </p>
  </div>
</div>
```

- [ ] **Step 3: Verify permission cards structure**

Check the new markup:
```bash
grep -A5 "wizard-permission-grid" ui/index.html
```

Expected: Grid with two permission cards, no status badges

- [ ] **Step 4: Commit**

```bash
git add ui/index.html
git commit -m "refactor: simplify permission cards, remove status tracking"
```

---

## Task 3: Add Wizard Header CSS

**Files:**
- Modify: `ui/style.css` (add new wizard header styles)

- [ ] **Step 1: Locate wizard styles section**

Find where wizard styles are defined:
```bash
grep -n "\.wizard-header" ui/style.css
```

Expected: Line showing current wizard header styles

- [ ] **Step 2: Add compact header styles**

Add these styles after the existing wizard header section:

```css
/* Compact wizard header */
.wizard-header-compact {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding: 0 4px;
}

.wizard-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.wizard-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
}

.wizard-app-name {
  font-size: 20px;
  font-weight: 600;
  color: var(--accent-color, #f4a261);
  margin: 0;
}

.wizard-step-indicator {
  font-size: 13px;
  color: #666;
}

/* Minimal progress bar */
.wizard-progress-bar {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
}

.progress-segment {
  flex: 1;
  height: 3px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  transition: background 0.3s ease;
}

.progress-segment.active {
  background: var(--accent-color, #f4a261);
}
```

- [ ] **Step 3: Update wizard container padding**

Find `.wizard-container` and update padding:

```css
.wizard-container {
  width: 100%;
  max-width: 800px;
  padding: 12px 24px 16px;  /* Reduced from 20px 40px 24px */
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-height: 90vh;
  overflow-y: auto;
}
```

- [ ] **Step 4: Commit**

```bash
git add ui/style.css
git commit -m "style: add compact wizard header styles"
```

---

## Task 4: Add Permission Cards CSS

**Files:**
- Modify: `ui/style.css` (add/update permission card styles)

- [ ] **Step 1: Add permission grid styles**

Add styles for the new permission card grid:

```css
/* Permission cards grid */
.wizard-permission-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  max-width: 850px;
  margin: 8px auto;
  width: 100%;
}

.permission-card {
  position: relative;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 12px 16px;  /* Compact padding */
  text-align: center;
  transition: all 0.3s ease;
}

.permission-card:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.2);
}

.permission-icon {
  font-size: 40px;
  margin-bottom: 12px;
}

.permission-card h4 {
  color: #fff;
  font-size: 16px;
  margin: 0 0 8px 0;
  font-weight: 600;
}

.permission-card p {
  color: #999;
  font-size: 13px;
  margin: 0 0 16px 0;
  line-height: 1.4;
}

.btn-open-settings {
  background: rgba(244, 162, 97, 0.2);
  color: #f4a261;
  border: 1px solid #f4a261;
  padding: 10px 20px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  transition: all 0.2s ease;
  width: 100%;
}

.btn-open-settings:hover {
  background: rgba(244, 162, 97, 0.3);
  border-color: #f59e6c;
}
```

- [ ] **Step 2: Verify CSS added correctly**

Check the new styles:
```bash
grep -A3 "wizard-permission-grid" ui/style.css
```

Expected: Grid layout styles with 2 columns

- [ ] **Step 3: Commit**

```bash
git add ui/style.css
git commit -m "style: add permission card grid styles"
```

---

## Task 5: Remove Unused CSS Classes

**Files:**
- Modify: `ui/style.css` (remove old permission status styles)

- [ ] **Step 1: Find and remove permission status styles**

Search for and delete these CSS classes (if they exist):
- `.permission-status`
- `.permission-badge`
- `.permission-granted`
- `.permission-not-granted`

```bash
grep -n "permission-status\|permission-badge\|permission-granted\|permission-not-granted" ui/style.css
```

- [ ] **Step 2: Remove old wizard header styles**

Remove the old centered `.wizard-header` and `.wizard-logo` styles (keep the new compact ones):

```bash
# Find old header styles
grep -n "\.wizard-header {" ui/style.css
```

Remove the old centered layout styles, keep only the compact header styles added in Task 3.

- [ ] **Step 3: Update wizard card padding**

Find `.wizard-card` and reduce padding:

```css
.wizard-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  padding: 16px 24px;  /* Reduced from 20px 28px */
  width: 100%;
  box-sizing: border-box;
}
```

- [ ] **Step 4: Commit**

```bash
git add ui/style.css
git commit -m "style: remove unused permission status CSS, reduce padding"
```

---

## Task 6: Update JavaScript Progress Bar Logic

**Files:**
- Modify: `ui/app.js` (update wizard step navigation to use progress bar)

- [ ] **Step 1: Find wizard step change function**

Locate the function that handles wizard step navigation:
```bash
grep -n "function.*wizGotoStep\|wizardStep.*=" ui/app.js | head -20
```

Expected: Function that changes wizard steps

- [ ] **Step 2: Add progress bar update logic**

Add a function to update the progress bar:

```javascript
function updateWizardProgress(step) {
  // Update step text
  const stepText = document.getElementById('wizStepText');
  if (stepText) {
    stepText.textContent = `Step ${step} of 4`;
  }

  // Update progress segments
  for (let i = 1; i <= 4; i++) {
    const segment = document.getElementById(`wizProgress${i}`);
    if (segment) {
      if (i === step) {
        segment.classList.add('active');
      } else {
        segment.classList.remove('active');
      }
    }
  }
}
```

- [ ] **Step 3: Call progress update when step changes**

Find where `_wizardStep` is set and add the update call:

```javascript
function wizGotoStep(step) {
  // ... existing step change logic ...
  _wizardStep = step;
  updateWizardProgress(step);  // Add this line
  // ... rest of function ...
}
```

- [ ] **Step 4: Initialize progress on wizard load**

Find the wizard initialization function and add:

```javascript
// Initialize progress bar
updateWizardProgress(1);
```

- [ ] **Step 5: Commit**

```bash
git add ui/app.js
git commit -m "feat: add progress bar update logic"
```

---

## Task 7: Remove Permission Monitoring JavaScript

**Files:**
- Modify: `ui/app.js` (remove permission checking/monitoring functions)

- [ ] **Step 1: Find and comment out checkPermissions function**

Locate `checkPermissions()` function (~line 146-150):

```javascript
// Permission checking disabled - automatic triggers used instead
async function checkPermissions() {
  // No automatic checking - users will manually grant permissions
  return;
}
```

Verify it's already disabled (from earlier work).

- [ ] **Step 2: Find and comment out monitoring functions**

Locate and verify these are disabled:

```javascript
// Permission monitoring disabled
function startPermissionMonitoring() {
  // Disabled - no automatic checking
}

function stopPermissionMonitoring() {
  // Disabled
}
```

- [ ] **Step 3: Ensure updatePermissionUI is not called**

Search for calls to `updatePermissionUI`:
```bash
grep -n "updatePermissionUI" ui/app.js
```

Comment out or remove any calls to this function.

- [ ] **Step 4: Ensure Next button always enabled on step 1**

Find `wizUpdateNextButton()` function and verify step 1 logic:

```javascript
function wizUpdateNextButton() {
  const btn = document.getElementById('wizBtnNext');
  if (!btn) return;

  switch (_wizardStep) {
    case 1:
      btn.disabled = false;  // Always enabled - no permission validation
      break;
    case 2:
      btn.disabled = false;  // Hotkeys - always allow
      break;
    case 3:
      btn.disabled = !(_wizardGroqKeyValidated || _wizardApiKeyValidated);
      break;
    case 4:
      btn.disabled = !_wizardMicTested;
      break;
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add ui/app.js
git commit -m "refactor: remove permission monitoring, always enable step 1 Next"
```

---

## Task 8: Add Backend Permission Trigger Method

**Files:**
- Modify: `app.py` (add trigger_permission_requests method to API class)

- [ ] **Step 1: Find the API class**

Locate the main API class that handles wizard endpoints:
```bash
grep -n "class.*API\|def check_permissions" app.py | head -10
```

Expected: API class definition and existing permission methods

- [ ] **Step 2: Add trigger_permission_requests method**

Add this method to the API class (after existing permission methods):

```python
def trigger_permission_requests(self):
    """
    Trigger macOS permission prompts by attempting to use the APIs.
    This causes macOS to show system dialogs and add Waffler to permission lists.
    Only runs on macOS. Fails silently on other platforms.
    """
    if sys.platform != "darwin":
        _log_to_file("[INFO] Permission triggers skipped (not macOS)")
        return {"ok": True, "platform": sys.platform, "triggered": False}

    try:
        _log_to_file("[INFO] Triggering macOS permission requests...")

        # Trigger Accessibility permission prompt
        from ApplicationServices import AXIsProcessTrusted
        AXIsProcessTrusted()
        _log_to_file("[INFO] Accessibility permission trigger called")

        # Trigger Input Monitoring permission prompt
        try:
            from Quartz import (
                CGEventTapCreate,
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                kCGEventKeyDown,
            )
            tap = CGEventTapCreate(
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                0,  # passive listener
                1 << kCGEventKeyDown,
                lambda *args: None,
                None,
            )
            _log_to_file(f"[INFO] Input Monitoring permission trigger called (tap={tap})")
        except Exception as e:
            _log_to_file(f"[INFO] Input Monitoring trigger exception: {e}")

        return {"ok": True, "platform": "darwin", "triggered": True}

    except Exception as e:
        _log_to_file(f"[INFO] Permission trigger error: {e}")
        # Fail silently - users can still use "Open System Settings" buttons
        return {"ok": True, "platform": "darwin", "triggered": False, "error": str(e)}
```

- [ ] **Step 3: Verify method added correctly**

Check the method exists:
```bash
grep -A5 "def trigger_permission_requests" app.py
```

Expected: Method definition with platform check

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add permission trigger method for macOS prompts"
```

---

## Task 9: Add Trigger API Endpoint

**Files:**
- Modify: `app.py` (expose trigger_permission_requests as API endpoint)

- [ ] **Step 1: Find where API methods are exposed**

Look for how existing methods like `check_permissions` are exposed:
```bash
grep -n "api.*check_permissions\|expose.*check_permissions" app.py
```

Expected: Pattern showing how API methods are exposed to frontend

- [ ] **Step 2: Add trigger_permissions endpoint**

If using pywebview API pattern, the method is automatically exposed.
If using explicit mapping, add:

```python
# In the API exposure section
"trigger_permissions": self.trigger_permission_requests,
```

- [ ] **Step 3: Verify endpoint accessible**

Check that the method will be callable from JavaScript:
```bash
grep -B5 -A5 "trigger_permission" app.py
```

Expected: Method defined and exposed

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: expose trigger_permissions API endpoint"
```

---

## Task 10: Wire Frontend to Trigger Permissions

**Files:**
- Modify: `ui/app.js` (call trigger API when wizard step 1 loads)

- [ ] **Step 1: Find wizard step 1 initialization**

Locate where step 1 content is shown/initialized:
```bash
grep -n "wizContent1\|wizGotoStep.*1" ui/app.js
```

Expected: Function that shows step 1

- [ ] **Step 2: Add trigger call on step 1 load**

Add a function to trigger permissions:

```javascript
async function triggerMacOSPermissions() {
  try {
    const result = await pywebview.api.trigger_permission_requests();
    console.log('[Wizard] Permission triggers:', result);
  } catch (error) {
    console.warn('[Wizard] Permission trigger failed:', error);
    // Fail silently - users can still use "Open System Settings" buttons
  }
}
```

- [ ] **Step 3: Call trigger when step 1 loads**

Find where step 1 becomes active and add the trigger call:

```javascript
function wizGotoStep(step) {
  // ... existing step navigation logic ...

  if (step === 1) {
    // Trigger macOS permission prompts automatically
    triggerMacOSPermissions();
  }

  // ... rest of function ...
}
```

- [ ] **Step 4: Test trigger fires on wizard load**

Start the app and check console logs to verify the trigger is called.

- [ ] **Step 5: Commit**

```bash
git add ui/app.js
git commit -m "feat: trigger permission prompts when wizard step 1 loads"
```

---

## Task 11: Manual Testing - macOS

**Prerequisites:**
- macOS system
- Waffler removed from System Settings → Privacy & Security → Accessibility and Input Monitoring lists

- [ ] **Step 1: Remove Waffler from permission lists**

Open System Settings → Privacy & Security → Accessibility, remove Waffler.
Open System Settings → Privacy & Security → Input Monitoring, remove Waffler.

- [ ] **Step 2: Launch app and start wizard**

```bash
python3 app.py
```

Expected: App launches and wizard appears

- [ ] **Step 3: Verify macOS permission dialogs appear**

When wizard step 1 loads:
- macOS should show "Waffler would like to control this computer using accessibility features"
- macOS should show "Waffler would like to receive keystrokes from any application"

Allow both prompts.

- [ ] **Step 4: Verify new header layout**

Check that:
- Icon appears in top-left corner (40px × 40px)
- "Waffler" text next to icon
- "Step 1 of 4" text on right
- Progress bar below with first segment highlighted
- Total header height ~50px
- Wizard fits on screen without scrolling

- [ ] **Step 5: Verify permission cards**

Check that:
- Two cards in grid layout (Accessibility, Input Monitoring)
- Each card has icon, title, description, "Open System Settings" button
- No status badges or checkmarks
- Hint text: "Click the buttons above to grant permissions, then click Next below"

- [ ] **Step 6: Test "Open System Settings" buttons**

Click "Open System Settings" on Accessibility card:
- System Settings opens to Privacy & Security → Accessibility
- Waffler appears in the list

Click "Open System Settings" on Input Monitoring card:
- System Settings opens to Privacy & Security → Input Monitoring
- Waffler appears in the list

- [ ] **Step 7: Verify Next button always enabled**

Check that Next button is enabled even before granting permissions.

- [ ] **Step 8: Complete wizard flow**

Click Next through all 4 steps and complete the wizard.
Expected: No errors, wizard completes successfully.

- [ ] **Step 9: Document test results**

Create a test log:
```bash
echo "## macOS Test Results - $(date)" >> test-results.md
echo "- Permission dialogs: [PASS/FAIL]" >> test-results.md
echo "- Header layout: [PASS/FAIL]" >> test-results.md
echo "- Permission cards: [PASS/FAIL]" >> test-results.md
echo "- Open Settings buttons: [PASS/FAIL]" >> test-results.md
echo "- Next button enabled: [PASS/FAIL]" >> test-results.md
echo "- Wizard completion: [PASS/FAIL]" >> test-results.md
```

---

## Task 12: Manual Testing - Windows (Optional)

**Prerequisites:**
- Windows system
- Waffler installed

- [ ] **Step 1: Launch app**

```bash
python app.py
```

Expected: App launches without errors

- [ ] **Step 2: Verify no Mac permission code runs**

Check console for errors related to Accessibility or Input Monitoring.
Expected: No Mac-specific errors

- [ ] **Step 3: Verify wizard displays correctly**

Check that:
- Header layout displays (icon, name, progress bar)
- Step 1 content displays appropriately
- No crashes or errors

- [ ] **Step 4: Complete wizard**

Click through wizard steps.
Expected: Wizard completes without errors.

- [ ] **Step 5: Document test results**

```bash
echo "## Windows Test Results - $(date)" >> test-results.md
echo "- No Mac errors: [PASS/FAIL]" >> test-results.md
echo "- Wizard displays: [PASS/FAIL]" >> test-results.md
echo "- Wizard completion: [PASS/FAIL]" >> test-results.md
```

---

## Task 13: Visual Verification & Screenshots

**Files:**
- Create: `docs/screenshots/wizard-v3.1.7/` (optional)

- [ ] **Step 1: Take before/after screenshots**

Capture:
- Old wizard header (from v3.1.6)
- New wizard header (compact layout)
- Old permission cards (with status badges)
- New permission cards (grid layout, no badges)

- [ ] **Step 2: Verify fits on screen**

Test on various screen sizes:
- 13" MacBook display
- 15" MacBook display
- External monitor

Expected: No scrolling required on step 1

- [ ] **Step 3: Verify spacing and alignment**

Check:
- Icon properly aligned left
- Progress bar segments even
- Permission cards centered in grid
- Buttons properly sized

- [ ] **Step 4: Check visual consistency**

Verify:
- Colors match app theme
- Fonts consistent with rest of wizard
- Hover states work on buttons and cards

---

## Task 14: Final Cleanup & Documentation

**Files:**
- Modify: `CHANGELOG.md` or release notes

- [ ] **Step 1: Update changelog**

Add entry for v3.1.7:

```markdown
## v3.1.7 - Wizard UX Improvements

### Changed
- Compact wizard header with icon in top-left position
- Simplified permission cards without status tracking
- Automatic macOS permission prompts when wizard loads
- Reduced wizard height to fit on screen without scrolling

### Removed
- Permission status badges and automatic checking
- Debug panel (already removed in v3.1.6)

### Fixed
- macOS permission requests now trigger automatically
- Waffler appears in System Settings lists immediately
- Next button no longer blocked by unreliable permission checks
```

- [ ] **Step 2: Verify all changes committed**

```bash
git log --oneline --since="1 day ago"
```

Expected: All task commits visible

- [ ] **Step 3: Create summary commit (optional)**

```bash
git commit --allow-empty -m "docs: wizard UX improvements implementation complete

- Compact header layout (saves ~90px vertical space)
- Simplified permission cards without status tracking
- Automatic macOS permission prompts on wizard load
- All wizard step 1 content fits on screen without scrolling"
```

- [ ] **Step 4: Push to repository**

```bash
git push origin main
```

---

## Success Criteria Checklist

### Visual
- [ ] Entire wizard step 1 fits on screen without scrolling
- [ ] Header height ~50px (down from ~140px)
- [ ] No debug panel or status badges visible
- [ ] Clean, professional appearance
- [ ] Icon displays correctly in top-left

### Functional
- [ ] macOS system dialogs appear automatically when wizard loads
- [ ] Waffler appears in System Settings permission lists
- [ ] "Open System Settings" buttons open correct panes
- [ ] Next button always enabled on step 1
- [ ] No permission validation blocking progress

### Platform
- [ ] Works correctly on macOS (triggers permissions)
- [ ] Works correctly on Windows (skips Mac-specific code)
- [ ] No cross-platform errors or crashes

### Code Quality
- [ ] All changes committed with descriptive messages
- [ ] No unused CSS classes remaining
- [ ] Console logs show permission triggers firing
- [ ] Error handling in place (fail silently)

---

## Rollback Plan

If issues arise, revert commits in reverse order:

```bash
# Revert to before wizard improvements
git log --oneline --since="1 day ago"
git revert <commit-hash>..HEAD
```

Specific revert points:
- After Task 10: Permission triggers added but not tested
- After Task 7: UI updated, backend not changed
- After Task 1: Just header changed, easy to verify

---

## Notes

- **TDD Adaptation:** This plan uses visual verification instead of automated tests for UI changes, since the codebase appears to lack comprehensive UI tests.
- **Manual Testing Required:** Tasks 11-12 require manual testing on macOS and Windows.
- **Platform Detection:** Code already has robust platform detection - we're leveraging existing patterns.
- **Fail Silently:** Permission triggers log errors but don't block users - they can still use "Open Settings" buttons.

---

## References

- Design Spec: `docs/superpowers/specs/2026-03-29-wizard-setup-improvements-design.md`
- Visual Mockups: `.superpowers/brainstorm/80578-1774787343/`
- Related Commits: e1a9759 (permission detection removal)
