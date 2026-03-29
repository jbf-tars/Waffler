# Wizard Setup Improvements - Design Spec

**Date:** 2026-03-29
**Status:** Approved
**Author:** Claude (with user approval)

## Overview

### Problem
The current wizard setup has several UX issues:
1. Layout doesn't fit on one screen - requires scrolling
2. Permission status tracking is unreliable and confusing (shows "Not Granted" even when granted)
3. Debug panel clutters the interface
4. Waffler doesn't automatically appear in macOS System Settings permission lists
5. Centered header with large icon wastes vertical space

### Solution
Streamline the wizard with a compact header, simplified permission cards without status tracking, and programmatic permission triggers that make macOS automatically prompt users and add Waffler to permission lists.

### Goals
- Entire wizard step 1 visible without scrolling
- Automatic macOS permission prompts when wizard loads
- No confusing "Granted/Not Granted" status indicators
- Platform-appropriate behavior (Mac vs Windows)
- Cleaner, more trustworthy UX

## Design Decisions

### Header Layout
**Selected:** Option C - Icon top-left + minimal progress bar

**Structure:**
```
[Icon] Waffler                    Step 1 of 4
━━━━ ─── ─── ───
```

**Details:**
- Icon: `ui/logo-icon.png` (40px × 40px) positioned top-left
- App name "Waffler" next to icon
- Progress bar: 4 segments, filled segment highlighted
- "Step X of 4" text on the right
- Total height: ~50px (saves ~90px vs current centered layout)

### Permission Cards
**Selected:** Option A - Simple grid cards with no status tracking

**Structure:**
- 2-column grid layout (Accessibility | Input Monitoring)
- Each card contains:
  - Icon (emoji placeholder in mockups, can be icons in implementation)
  - Title (e.g., "Accessibility")
  - Description (e.g., "For hotkey detection and auto-paste")
  - "Open System Settings" button
- No status badges, checkmarks, or "Granted/Not Granted" text
- Hint text: "Click the buttons above to grant permissions, then click Next below"

**Why this works:**
- Removes unreliable status checking
- Users control when to proceed
- Simpler, clearer interface
- No confusion about permission state

### Implementation Approach
**Selected:** Approach C - Hybrid (UI Update + Smart Triggers)

**What we'll do:**
- Update UI to new compact design
- Add programmatic permission triggers on wizard load
- Keep backend permission-checking functions but disable polling
- Leave infrastructure dormant for potential future use

**Why hybrid:**
- Cleaner UI + better UX with auto-triggers
- Maintains flexibility to re-enable status checking later
- Balanced approach between minimal change and full cleanup

## Technical Design

### UI Components

#### Files Modified
- `ui/index.html` - Wizard header and permission card markup
- `ui/style.css` - Layout, spacing, and styling
- `ui/app.js` - Remove permission monitoring logic

#### Header Changes (`index.html`)
```html
<div class="wizard-header-compact">
  <div class="wizard-header-left">
    <img src="logo-icon.png" class="wizard-icon" alt="Waffler">
    <span class="wizard-app-name">Waffler</span>
  </div>
  <div class="wizard-step-indicator">Step 1 of 4</div>
</div>
<div class="wizard-progress-bar">
  <div class="progress-segment active"></div>
  <div class="progress-segment"></div>
  <div class="progress-segment"></div>
  <div class="progress-segment"></div>
</div>
```

#### Permission Cards (`index.html`)
```html
<div class="wizard-permission-grid">
  <div class="permission-card">
    <div class="permission-icon">🔑</div>
    <h4>Accessibility</h4>
    <p>For hotkey detection and auto-paste</p>
    <button class="btn-open-settings" onclick="openAccessibilitySettings()">
      Open System Settings
    </button>
  </div>

  <div class="permission-card">
    <div class="permission-icon">⌨️</div>
    <h4>Input Monitoring</h4>
    <p>For Fn key push-to-talk</p>
    <button class="btn-open-settings" onclick="openInputMonitoringSettings()">
      Open System Settings
    </button>
  </div>
</div>
```

#### Styling Updates (`style.css`)
- Wizard container padding: `12px 24px 16px` (down from `20px 40px 24px`)
- Permission grid: `max-width: 850px`, `gap: 14px`
- Permission cards: `padding: 12px 16px` (down from `24px 20px`)
- Remove unused CSS: `.permission-status`, `.permission-badge`, `.permission-granted`, `.permission-not-granted`

#### JavaScript Changes (`app.js`)
**Remove:**
- `checkPermissions()` - No longer called
- `startPermissionMonitoring()` - Disable polling
- `stopPermissionMonitoring()` - Disable cleanup
- `updatePermissionUI()` - No status badges to update

**Keep:**
- `openAccessibilitySettings()` - Opens System Settings to Accessibility pane
- `openInputMonitoringSettings()` - Opens System Settings to Input Monitoring pane
- Step navigation logic (Next button always enabled on step 1)

### Backend Changes

#### Permission Triggering (`app.py`)

**New method:**
```python
def trigger_permission_requests(self):
    """
    Trigger macOS permission prompts by attempting to use the APIs.
    This causes macOS to show system dialogs and add Waffler to permission lists.
    Only runs on macOS.
    """
    if sys.platform != "darwin":
        return

    try:
        # Trigger Accessibility permission prompt
        from ApplicationServices import AXIsProcessTrusted
        AXIsProcessTrusted()

        # Trigger Input Monitoring permission prompt
        from Quartz import CGEventTapCreate, kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventKeyDown
        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            0,  # passive listener
            1 << kCGEventKeyDown,
            lambda *args: None,
            None
        )
        # The tap creation triggers the prompt, we don't need to install it

    except Exception as e:
        _log_to_file(f"[INFO] Permission trigger: {e}")
        # Fail silently - users can still use "Open System Settings" buttons
```

**When to call:**
- Add new API endpoint: `trigger_permissions()`
- Called from frontend when wizard step 1 loads
- Only on macOS (platform check: `sys.platform == "darwin"`)

**Existing permission checking:**
- Keep `check_accessibility_permission()` and `check_input_monitoring_permission()` functions
- Keep `check_permissions()` API endpoint
- Don't call these from frontend (dormant but available)
- No polling or monitoring

#### Platform Handling

**macOS:**
```python
if _platform.system() == "Darwin":
    # Trigger permission requests when wizard loads
    trigger_permission_requests()
    # "Open System Settings" buttons open to Privacy & Security panes
```

**Windows:**
```python
if _platform.system() == "Windows":
    # Skip permission triggers (Accessibility/Input Monitoring don't exist)
    # Could show different content or skip step 1 entirely
    pass
```

**Existing detection:**
- Code already uses `_platform.system()` checks throughout
- No Mac-specific APIs called on Windows
- No cross-platform issues expected

## User Flow

### macOS - Wizard Step 1 (Permissions)

1. **Wizard loads step 1**
   - Frontend calls `trigger_permissions()` API
   - Backend runs `trigger_permission_requests()`

2. **macOS shows system dialogs**
   - "Waffler would like to control this computer using accessibility features"
   - "Waffler would like to receive keystrokes from any application"
   - Waffler automatically added to permission lists in System Settings

3. **User sees permission cards**
   - Two cards in grid layout
   - Each has "Open System Settings" button
   - No status indicators

4. **User grants permissions**
   - If they allowed dialogs: permissions granted
   - If they denied/dismissed: click "Open System Settings" to grant manually
   - macOS System Settings opens to appropriate pane

5. **User clicks Next**
   - Button always enabled (no validation)
   - Proceeds to step 2 (Hotkeys)

### Windows - Wizard Step 1

- Skip permission triggers (Windows doesn't have Accessibility/Input Monitoring)
- Show different content or skip step entirely (TBD in implementation)
- No Mac-specific API calls attempted

### Error Handling

**Permission trigger fails:**
- Fail silently, log to file
- Users can still use "Open System Settings" buttons
- No blocking errors

**No status checking:**
- Eliminates false negatives/positives
- Users proceed when ready
- App shows helpful errors at actual usage time (not during setup)

## Files Changed

### Modified
- `ui/index.html` - Wizard header, permission cards markup
- `ui/style.css` - Compact layout, remove unused classes
- `ui/app.js` - Remove permission monitoring, keep settings buttons
- `app.py` - Add `trigger_permission_requests()`, add API endpoint

### Removed Elements
- Debug panel `<div id="debugPanel">` - Already removed in commit e1a9759
- Permission status badges - CSS classes and HTML elements
- Permission monitoring logic - JavaScript polling code

### Logo Reference
- Use `ui/logo-icon.png` for wizard header icon (40px × 40px)
- Same logo used throughout app (menu bar, sidebar, etc.)

## Success Criteria

### Visual
- ✅ Entire wizard step 1 fits on screen without scrolling
- ✅ Header height reduced to ~50px (down from ~140px)
- ✅ No debug panel or status badges visible
- ✅ Clean, professional appearance

### Functional
- ✅ macOS system dialogs appear automatically when wizard loads
- ✅ Waffler appears in System Settings permission lists immediately
- ✅ "Open System Settings" buttons open correct panes
- ✅ Next button always enabled on step 1
- ✅ No permission validation blocking progress

### Platform
- ✅ Works correctly on macOS (triggers permissions)
- ✅ Works correctly on Windows (skips Mac-specific code)
- ✅ No cross-platform errors or crashes

## Testing Plan

### Manual Testing

**macOS:**
1. Delete Waffler from System Settings permission lists
2. Launch app and start wizard
3. Verify macOS system dialogs appear automatically
4. Check that Waffler appears in both permission lists
5. Test "Open System Settings" buttons open correct panes
6. Verify Next button is always enabled
7. Complete wizard flow

**Windows:**
1. Launch app and start wizard
2. Verify no Mac permission code runs
3. Verify no errors in console
4. Complete wizard flow

### Visual Testing
1. Verify wizard fits on screen without scrolling
2. Check header layout matches design (icon left, progress bar)
3. Verify permission cards in grid layout
4. Confirm no status badges or debug panels
5. Test on different screen sizes

### Edge Cases
1. User denies permission dialogs → "Open Settings" buttons still work
2. User dismisses dialogs → Can proceed anyway (Next enabled)
3. Permission APIs fail → Fail silently, log error
4. Windows platform → No Mac code runs

## Future Considerations

### Potential Enhancements
- Optional: Add toggle to re-enable permission status checking
- Optional: Show permission icons (green/red) only after user grants/denies
- Optional: Add tooltip explaining what each permission does
- Optional: Windows-specific permission step (if needed)

### Technical Debt
- Dormant permission-checking code could be removed in future if never needed
- Could further reduce vertical space if needed
- Consider A/B testing auto-trigger vs manual-only approach

## Appendix

### Related Commits
- e1a9759: "fix: remove permission detection, always enable Next button"
- d80c20e: "debug: add visible debug panel" (later removed)

### Design Mockups
- Visual companion mockups saved in `.superpowers/brainstorm/80578-1774787343/`
- `wizard-layout.html` - Header layout options
- `permission-cards.html` - Permission card designs

### Platform Detection Pattern
```python
import platform as _platform

if _platform.system() == "Darwin":
    # macOS-specific code
elif _platform.system() == "Windows":
    # Windows-specific code
```

This pattern is already used throughout `app.py` for platform-specific features (hotkeys, tray icons, etc.).
