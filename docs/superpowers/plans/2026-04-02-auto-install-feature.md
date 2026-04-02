# Auto-Install Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when Waffler runs from DMG/invalid location and auto-install to Applications with one click

**Architecture:** Add startup check in `main()` before any initialization. If running from invalid location, show native dialog, copy to Applications (or ~/Applications fallback), relaunch, and exit.

**Tech Stack:** Python pathlib, subprocess (osascript), shutil

---

## File Structure

**Modified files:**
- `app.py` - Add 5 helper functions + modify main() startup

**No new files** - all code goes in app.py before main()

---

## Task 1: Add Location Detection

**Files:**
- Modify: `app.py` (add function before main())

- [ ] **Step 1: Add import for pathlib if not present**

Check if `from pathlib import Path` exists at top of app.py. Add if missing.

Expected: Path import available

- [ ] **Step 2: Add location detection function**

Add before `main()`:

```python
def _is_running_from_invalid_location():
    """Check if app is running from DMG or non-Applications location."""
    app_path = Path(sys.executable).resolve()

    # Running from mounted DMG volume?
    if '/Volumes/' in str(app_path):
        return True

    # Check if in valid install locations
    valid_locations = [
        Path('/Applications'),
        Path.home() / 'Applications'
    ]

    for location in valid_locations:
        try:
            app_path.relative_to(location)
            return False  # Found in valid location
        except ValueError:
            continue

    # Not in any valid location (Downloads, Desktop, etc.)
    return True
```

- [ ] **Step 3: Test detection logic manually**

Test in Python REPL or add temporary test at bottom of app.py:

```python
if __name__ == "__main__":
    # Temporary test
    print(f"Running from: {sys.executable}")
    print(f"Invalid location: {_is_running_from_invalid_location()}")
    # main()  # Comment out for testing
```

Run from different locations:
- `/Applications/Waffler.app` → should print `Invalid location: False`
- `~/Downloads/Waffler.app` → should print `Invalid location: True`

Expected: Detection works correctly for both cases

- [ ] **Step 4: Remove temporary test code**

Remove test code, restore normal `main()` call

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: add location detection for auto-install

Detects if app is running from DMG (/Volumes/), Downloads, Desktop,
or other invalid locations. Returns True if not in /Applications or
~/Applications.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Add Install Dialog

**Files:**
- Modify: `app.py` (add function before main())

- [ ] **Step 1: Add subprocess import if not present**

Check if `import subprocess` exists at top of app.py. Add if missing.

Expected: subprocess import available

- [ ] **Step 2: Add dialog function**

Add before `main()`:

```python
def _show_install_dialog():
    """Show native macOS dialog asking to install. Returns True if user clicks Install."""
    result = subprocess.run([
        'osascript', '-e',
        'display dialog "Waffler needs to be installed in your Applications folder to work properly.\\n\\nWould you like to install it now?" '
        'with title "Install Waffler" '
        'buttons {"Not Now", "Install"} '
        'default button "Install" '
        'with icon caution'
    ], capture_output=True, text=True)

    return "Install" in result.stdout
```

- [ ] **Step 3: Test dialog manually**

Add temporary test code:

```python
if __name__ == "__main__":
    if _show_install_dialog():
        print("User clicked Install")
    else:
        print("User clicked Not Now")
    # main()
```

Run: `python app.py`
Expected: Dialog appears, clicking "Install" → prints "User clicked Install"

- [ ] **Step 4: Remove temporary test**

Remove test code

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: add install dialog for auto-install

Shows native macOS dialog asking user to install to Applications.
Returns True if Install clicked, False if Not Now.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Add Installation Logic

**Files:**
- Modify: `app.py` (add function before main())

- [ ] **Step 1: Add shutil import if not present**

Check if `import shutil` exists at top of app.py. Add if missing.

Expected: shutil import available

- [ ] **Step 2: Add installation function**

Add before `main()`:

```python
def _install_to_applications():
    """Copy app to Applications folder. Returns path to installed app."""
    # Source is the .app bundle (2 levels up from executable)
    source = Path(sys.executable).resolve().parent.parent

    # Try /Applications first
    try:
        dest = Path('/Applications/Waffler.app')
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest, symlinks=True)
        return dest
    except PermissionError:
        # Fallback to ~/Applications (doesn't require admin)
        user_apps = Path.home() / 'Applications'
        user_apps.mkdir(exist_ok=True)
        dest = user_apps / 'Waffler.app'
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest, symlinks=True)
        return dest
```

- [ ] **Step 3: Test installation manually**

**CAREFUL:** This will actually copy files. Test from a safe location.

Add temporary test:

```python
if __name__ == "__main__":
    try:
        new_path = _install_to_applications()
        print(f"Installed to: {new_path}")
    except Exception as e:
        print(f"Error: {e}")
    # main()
```

Run from non-Applications location
Expected: App copied to /Applications or ~/Applications, prints path

- [ ] **Step 4: Verify installation**

Check that `/Applications/Waffler.app` or `~/Applications/Waffler.app` exists and is runnable

Run: `open /Applications/Waffler.app` or `open ~/Applications/Waffler.app`
Expected: App launches successfully

- [ ] **Step 5: Remove temporary test**

Remove test code

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: add installation logic for auto-install

Copies app bundle to /Applications (or ~/Applications fallback if
permission denied). Removes old version if exists. Returns path to
installed app.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Add Relaunch and Error Dialog

**Files:**
- Modify: `app.py` (add 2 functions before main())

- [ ] **Step 1: Add relaunch function**

Add before `main()`:

```python
def _relaunch_from_new_location(app_path: Path):
    """Launch the newly installed app and exit this process."""
    subprocess.Popen(['open', str(app_path)])
    sys.exit(0)
```

- [ ] **Step 2: Add error dialog function**

Add before `main()`:

```python
def _show_error_dialog(message: str):
    """Show error dialog with manual installation instructions."""
    subprocess.run([
        'osascript', '-e',
        f'display dialog "{message}" '
        'with title "Installation Error" '
        'buttons {"OK"} '
        'default button "OK" '
        'with icon stop'
    ])
```

- [ ] **Step 3: Test error dialog manually**

Add temporary test:

```python
if __name__ == "__main__":
    _show_error_dialog("Test error message\\n\\nThis is a test.")
    # main()
```

Run: `python app.py`
Expected: Error dialog appears with stop icon

- [ ] **Step 4: Remove temporary test**

Remove test code

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: add relaunch and error dialog for auto-install

Relaunch: Opens newly installed app and exits current process.
Error dialog: Shows native macOS error with manual instructions.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Integrate into main()

**Files:**
- Modify: `app.py:main()` (add check at very start)

- [ ] **Step 1: Locate main() function**

Find the `def main():` function in app.py

Expected: Found at line ~2117

- [ ] **Step 2: Add auto-install check at start of main()**

Add this as the FIRST code in main(), before any other logic:

```python
def main():
    # ── Auto-Install Check ────────────────────────────────────────────
    # Detect if running from DMG/invalid location and prompt to install
    if _is_running_from_invalid_location():
        if _show_install_dialog():
            try:
                new_path = _install_to_applications()
                _relaunch_from_new_location(new_path)
                return  # Never reached (process exits in relaunch)
            except Exception as e:
                _show_error_dialog(f"Installation failed: {e}\\n\\nPlease drag Waffler to Applications manually.")
                sys.exit(1)
        else:  # User clicked "Not Now"
            sys.exit(0)

    # ── Rest of normal startup ─────────────────────────────────────────
    global _config, _window_ref
    # ... existing code continues ...
```

- [ ] **Step 3: Test end-to-end flow from DMG**

**Manual test:**

1. Build app with PyInstaller
2. Create DMG
3. Mount DMG
4. Double-click Waffler.app from DMG window (don't drag to Applications)
5. Verify dialog appears
6. Click "Install"
7. Verify app copies to Applications
8. Verify app relaunches from Applications
9. Verify spacebar sticky mode works

Expected: Seamless auto-install flow, all features work

- [ ] **Step 4: Test "Not Now" path**

Repeat test but click "Not Now"

Expected: App exits cleanly, no installation

- [ ] **Step 5: Test running from Applications (no dialog)**

Launch app from /Applications/Waffler.app

Expected: No dialog, app starts normally

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: integrate auto-install check into main()

Checks location before any initialization. Shows dialog if running
from DMG/invalid location. Auto-installs and relaunches. Exits if
user declines or installation fails.

Fixes spacebar sticky mode issues caused by running from DMG.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Build and Test Release

**Files:**
- None (verification only)

- [ ] **Step 1: Build signed app locally**

```bash
cd /Users/james/waffler
rm -rf build dist
SIGNING_IDENTITY="Developer ID Application: JAMES BERNARD FARRELLY (653JYT4U23)" ./build_mac.sh
```

Expected: App builds successfully with Developer ID signature

- [ ] **Step 2: Manually sign with hardened runtime**

```bash
codesign --sign "Developer ID Application: JAMES BERNARD FARRELLY (653JYT4U23)" \
  --force --options runtime --entitlements entitlements.plist \
  --timestamp --deep dist/Waffler.app
```

Expected: App signed with hardened runtime

- [ ] **Step 3: Create installer DMG**

```bash
create-dmg \
  --volname "Waffler v3.3.1" \
  --window-pos 200 120 \
  --window-size 800 500 \
  --icon-size 100 \
  --icon "Waffler.app" 200 220 \
  --hide-extension "Waffler.app" \
  --app-drop-link 600 220 \
  "Waffler-3.3.1-test.dmg" \
  "dist/Waffler.app"
```

Expected: DMG created with proper installer layout

- [ ] **Step 4: Clean system for fresh test**

```bash
pkill -f Waffler
rm -rf /Applications/Waffler.app ~/Applications/Waffler.app
rm -rf ~/.waffler-hosted
```

Expected: No Waffler installations on system

- [ ] **Step 5: Test fresh install flow**

1. Mount DMG
2. Double-click Waffler.app from DMG (don't drag!)
3. Dialog should appear: "Install Waffler to Applications?"
4. Click "Install"
5. App should copy to /Applications
6. App should relaunch from /Applications
7. Complete wizard setup
8. Test spacebar sticky mode (hold hotkey + press space)

Expected:
- ✅ Auto-install works
- ✅ Relaunch works
- ✅ Spacebar sticky mode works (this was broken when running from DMG)

- [ ] **Step 6: Test permission fallback**

Simulate permission error by making /Applications read-only:

```bash
# Clean previous install
rm -rf /Applications/Waffler.app ~/Applications/Waffler.app

# Make /Applications read-only (requires admin)
# Skip if you don't have admin - test in VM instead
sudo chmod 555 /Applications

# Test install
# Should fallback to ~/Applications

# Restore permissions
sudo chmod 755 /Applications
```

Expected: Installs to ~/Applications when /Applications blocked

- [ ] **Step 7: Verify no issues with proper installs**

Test that apps already in /Applications don't trigger dialog:

1. Install Waffler to /Applications (drag from DMG)
2. Launch from /Applications
3. Should start normally with NO dialog

Expected: No auto-install prompt when already in Applications

- [ ] **Step 8: Document test results**

Create brief test report:

```bash
echo "Auto-Install Feature Test Results" > test-results.txt
echo "==================================" >> test-results.txt
echo "" >> test-results.txt
echo "✅ Detection: Correctly identifies DMG/invalid locations" >> test-results.txt
echo "✅ Dialog: Native macOS dialog appears" >> test-results.txt
echo "✅ Install: Copies to /Applications successfully" >> test-results.txt
echo "✅ Fallback: Uses ~/Applications if permission denied" >> test-results.txt
echo "✅ Relaunch: Auto-relaunches from new location" >> test-results.txt
echo "✅ Features: Spacebar sticky mode works after auto-install" >> test-results.txt
echo "✅ Skip: No dialog when already in Applications" >> test-results.txt
git add test-results.txt
git commit -m "test: auto-install feature verification results"
```

Expected: All tests pass, spacebar issue resolved

---

## Success Criteria

- [ ] App detects when running from DMG or invalid location
- [ ] Native dialog prompts user to install
- [ ] One-click install copies to /Applications (or ~/Applications fallback)
- [ ] App auto-relaunches from proper location
- [ ] Spacebar sticky mode works (previously broken when running from DMG)
- [ ] No dialog when app already in Applications
- [ ] Clean error handling if installation fails
- [ ] User can decline installation (app exits cleanly)

---

## Notes

- This feature prevents the spacebar sticky mode bug by ensuring the app always runs from a proper install location with correct permissions
- The DMG window staying open is the root UX issue - users double-click the app from the DMG instead of dragging to Applications
- Detection runs on EVERY launch but is fast (simple path check)
- No state tracking needed - stateless design is simpler and more reliable
- Manual testing required for dialogs and file operations (can't easily unit test macOS UI)
