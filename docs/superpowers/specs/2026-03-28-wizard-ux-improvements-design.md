# Wizard UX Improvements Design

**Date:** 2026-03-28
**Status:** Approved
**Author:** Claude Code + User

## Overview

Redesign Waffler's setup wizard to fix critical UX issues: cramped permissions page requiring scrolling, non-updating permission status, and confusing API key selection that looks like both providers are required.

## Goals

1. **Permissions page fits on screen** - no scrolling needed
2. **Real-time permission status** - users see when permissions are granted
3. **Clear API key selection** - obvious that you only need ONE provider
4. **Professional polish** - smooth transitions, clear visual hierarchy

## Non-Goals

- Changing wizard step order or adding new steps
- Redesigning hotkeys or "Try It" steps
- Mobile/responsive design (desktop macOS only)

## Design

### 1. Permissions Page Redesign

#### Current Problems
- Container max-width 480px makes layout too tall and narrow
- Requires vertical scrolling to see Next button
- "Not granted" status never updates even after user grants permissions
- No visual feedback when permissions are successfully granted
- User can proceed without granting permissions (Next button always enabled)

#### Proposed Changes

**Layout:**
- Increase container max-width from 480px to 700px
- Change permission cards from vertical stack to horizontal grid (2 columns)
- Each card: ~340px width with 16px gap between
- Centered icons, clear labels, individual "Open System Settings" buttons
- Everything visible on screen without scrolling

**Permission Status Tracking:**

Backend (Python):
```python
def check_accessibility_permission() -> bool:
    """Check if Accessibility permission is granted"""
    # Use CGPreflightScreenCaptureAccess() or similar
    # Returns True if granted, False otherwise

def check_input_monitoring_permission() -> bool:
    """Check if Input Monitoring permission is granted"""
    # Check TCC database or use IOKit
    # Returns True if granted, False otherwise
```

Frontend (JavaScript):
```javascript
// Poll every 2 seconds
setInterval(async () => {
    const accessibility = await pywebview.api.check_accessibility_permission();
    const inputMonitoring = await pywebview.api.check_input_monitoring_permission();

    updatePermissionUI('accessibility', accessibility);
    updatePermissionUI('inputMonitoring', inputMonitoring);

    // Enable Next only if both granted
    const nextBtn = document.getElementById('wizNextBtn');
    nextBtn.disabled = !(accessibility && inputMonitoring);
}, 2000);
```

**Visual Indicators:**
- Green checkmark badge (✓) in top-right corner of card when granted
- Empty circle badge when not granted
- Button text changes: "Open System Settings" → "✓ Granted"
- Button styling changes: neutral → green tint when granted
- Status text updates: "Not granted" → "Granted"

**Next Button Logic:**
- Disabled by default (`<button disabled>Next</button>`)
- Enabled only when both permissions are granted
- Visual state: gray/muted when disabled, gold/bright when enabled

#### Technical Details

**Files to Modify:**
- `ui/index.html` - Update step 1 HTML structure
- `ui/style.css` - Add grid layout, badge styles, permission states
- `ui/app.js` - Add polling logic, UI update functions
- `app.py` - Add permission checking API methods

**CSS Grid Structure:**
```css
.wizard-permission-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    max-width: 700px;
    margin: 0 auto;
}
```

**Permission Check Implementation:**
- Use `CGPreflightScreenCaptureAccess()` for Accessibility
- For Input Monitoring: check TCC database at `~/Library/Application Support/com.apple.TCC/TCC.db` or use IOKit APIs
- Polling interval: 2 seconds (not too aggressive, responsive enough)
- Stop polling when user proceeds to next step

---

### 2. API Keys Page Redesign

#### Current Problems
- Shows both Groq and OpenAI input fields simultaneously
- Looks like you need to enter BOTH keys (confusing)
- Lightning emoji (⚡) doesn't clearly represent "API keys"
- No clear indication that you only need ONE provider

#### Proposed Changes

**Icon:**
- Replace lightning emoji (⚡) with key emoji (🔑)

**Provider Selection UI:**

Top of page:
```html
<h2>🔑 API Keys</h2>
<p>Choose your provider <strong>(you only need one)</strong></p>

<div class="provider-pills">
    <button class="pill-button active" data-provider="groq">Groq</button>
    <button class="pill-button" data-provider="openai">OpenAI</button>
</div>

<div id="groqField" class="provider-field active">
    <!-- Groq input field -->
</div>

<div id="openaiField" class="provider-field">
    <!-- OpenAI input field (hidden by default) -->
</div>
```

**Pill Button Styling:**

Active state:
```css
.pill-button.active {
    background: linear-gradient(135deg, #d4a373 0%, #c4935f 100%);
    border: 2px solid rgba(212, 163, 115, 0.5);
    box-shadow: 0 4px 12px rgba(212, 163, 115, 0.3);
    color: white;
    font-weight: 600;
}
```

Inactive state:
```css
.pill-button {
    background: rgba(255, 255, 255, 0.05);
    border: 2px solid rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.6);
    border-radius: 24px;
    padding: 12px 32px;
    transition: all 0.3s ease;
}
```

**Progressive Disclosure:**
- Only ONE input field visible at a time
- Default: Show Groq (recommended)
- When user clicks OpenAI button:
  1. Groq button fades to inactive state
  2. OpenAI button animates to active state
  3. Groq field fades out (opacity 1→0, height collapse)
  4. OpenAI field fades in (opacity 0→1, height expand)
  5. Animation duration: 300ms with ease-in-out

**Visual Hierarchy:**
- Selected provider's input wrapped in highlighted container
- Container border color matches active pill button (gold tint)
- Clear visual connection between button and corresponding field
- Hint text: "Want to use [other provider]? Click the button above"

**Messaging Clarity:**
- Main heading includes: "(you only need one)" in bold
- Provider badges: "Recommended — Free & Fast" for Groq, "Optional Fallback" for OpenAI
- Clear link to get free key for selected provider

#### Technical Details

**Files to Modify:**
- `ui/index.html` - Restructure step 3 with pill buttons and conditional fields
- `ui/style.css` - Add pill button styles, transition animations
- `ui/app.js` - Add provider toggle logic with animations

**JavaScript Toggle Logic:**
```javascript
function switchProvider(provider) {
    // Update button states
    document.querySelectorAll('.pill-button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.provider === provider);
    });

    // Animate field transitions
    const fields = document.querySelectorAll('.provider-field');
    fields.forEach(field => {
        if (field.id === `${provider}Field`) {
            field.classList.add('active');
            // Fade in animation
        } else {
            field.classList.remove('active');
            // Fade out animation
        }
    });

    // Save preference to localStorage
    localStorage.setItem('preferredProvider', provider);
}
```

**Animation CSS:**
```css
.provider-field {
    opacity: 0;
    max-height: 0;
    overflow: hidden;
    transition: opacity 0.3s ease, max-height 0.3s ease;
}

.provider-field.active {
    opacity: 1;
    max-height: 500px; /* Enough for content */
}
```

**State Persistence:**
- Remember last selected provider in `localStorage`
- On page load, restore previous selection
- Default to Groq if no preference saved

---

### 3. Fn Key Language Popup (Experimental)

#### Problem
When user presses Fn key for push-to-talk, macOS shows "Should 'A, GB'" language/input source switcher popup. This is purely visual and doesn't break functionality, but it's distracting.

#### Attempted Solution

Modified `src/fn_key_cgevent.py` to strip Fn flag from event instead of complete suppression:

**Previous Approach:**
```python
if is_fn_pressed:
    return None  # Suppress event completely
```

**New Approach:**
```python
if is_fn_pressed:
    # Create modified event with Fn flag removed
    modified_event = CGEventCreateCopy(event)
    new_flags = flags & ~fn_flag  # Remove Fn flag (0x800000)
    CGEventSetFlags(modified_event, new_flags)
    return modified_event  # Pass through without Fn flag
```

**Rationale:**
- macOS popup is likely triggered by seeing Fn flag in event stream
- By removing the flag but still passing event through, might prevent popup
- Keeps other system functions working
- Also tries `kCGAnnotatedSessionEventTap` (higher priority) if available

**Testing Required:**
- Build app with changes
- Press Fn key and watch for language switcher popup
- If popup still appears, approach doesn't work
- Fallback: Document as expected macOS behavior in README/wizard

**Fallback Plan:**
If experimental fix doesn't work, add clear documentation:
- In setup wizard: "Note: You may briefly see a system popup when pressing Fn - this is normal and doesn't affect functionality"
- In README: Explain this is a macOS system behavior that can't be fully suppressed without invasive system changes

---

## Implementation Plan Summary

### Phase 1: Permissions Page (Backend + Frontend)
1. Add permission checking methods to `app.py`
2. Update HTML structure in `ui/index.html` (grid layout)
3. Add CSS for grid, badges, states in `ui/style.css`
4. Implement polling and UI updates in `ui/app.js`
5. Test with real permission granting/revoking

### Phase 2: API Keys Page (Frontend Only)
1. Update HTML with pill buttons and conditional fields
2. Add pill button styles and transitions in CSS
3. Implement toggle logic in JavaScript
4. Test provider switching animations
5. Verify localStorage persistence

### Phase 3: Integration & Polish
1. Test complete wizard flow end-to-end
2. Verify Next button logic works correctly
3. Test animations are smooth and responsive
4. Check edge cases (rapidly clicking buttons, etc.)

### Phase 4: Fn Key Testing
1. Build app with experimental fix
2. Test if language popup still appears
3. If not fixed, add documentation as fallback

## Success Criteria

- [ ] Permissions page fits on screen without scrolling
- [ ] Permission status updates in real-time (within 2 seconds)
- [ ] Next button only enables when both permissions granted
- [ ] Visual checkmarks appear when permissions granted
- [ ] API keys page clearly shows "you only need one"
- [ ] Provider selection is obvious and intuitive
- [ ] Only one input field visible at a time
- [ ] Smooth transitions when switching providers
- [ ] Wizard flow works end-to-end without issues

## Open Questions

- None - design approved and ready for implementation

## Future Enhancements (Out of Scope)

- Add "Skip" option for users who want to configure later
- Add tooltips explaining why each permission is needed
- Add progress indicators during permission checking
- Add celebratory animation when both permissions granted
- Support for other language switcher configurations (not just Fn)
