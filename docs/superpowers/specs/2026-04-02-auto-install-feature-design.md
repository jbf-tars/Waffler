# Auto-Install Feature Design

**Date:** 2026-04-02
**Status:** Approved

## Problem

Users download Waffler DMG and often launch the app directly from the mounted DMG volume instead of dragging it to Applications first. Running from `/Volumes/` causes:
- macOS security restrictions with hardened runtime
- Permission issues with CGEventTap (breaks spacebar sticky mode)
- Unreliable app behavior

The DMG installer window stays open after dragging, making it easy to accidentally double-click the app from the DMG.

## Solution

Detect when Waffler is running from an invalid location (DMG, Downloads, Desktop, etc.) and automatically prompt the user to install it to Applications with one click.

## Goals

1. **Prevent issues:** Stop users from running the app from invalid locations
2. **Seamless UX:** Auto-install and relaunch with one click
3. **Graceful fallback:** Handle permission issues (offer ~/Applications)
4. **Simple:** No state tracking, just check path every launch

## Architecture

### Detection (Startup Check)

Add check at the very start of `main()` before any initialization:

```python
def main():
    # FIRST THING: Check install location
    if _is_running_from_invalid_location():
        if _show_install_dialog():
            try:
                new_path = _install_to_applications()
                _relaunch_from_new_location(new_path)
                return  # Never reached (exits)
            except Exception as e:
                _show_error_dialog(f"Installation failed: {e}\n\nPlease drag Waffler to Applications manually.")
                sys.exit(1)
        else:  # User declined
            sys.exit(0)

    # ... normal startup continues
```

### Components

#### 1. Location Detection

**Function:** `_is_running_from_invalid_location() -> bool`

**Logic:**
- Check if path contains `/Volumes/` → running from DMG
- Check if NOT in `/Applications/` or `~/Applications/` → invalid location
- Valid locations: `/Applications/Waffler.app` or `~/Applications/Waffler.app`

**Implementation:**
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

**Catches:**
- `/Volumes/Waffler v3.3.0/Waffler.app` (DMG)
- `~/Downloads/Waffler.app`
- `~/Desktop/Waffler.app`
- Any non-standard location

**Allows:**
- `/Applications/Waffler.app` ✅
- `~/Applications/Waffler.app` ✅

#### 2. Install Dialog

**Function:** `_show_install_dialog() -> bool`

**UI:**
- Native macOS dialog (using `osascript`)
- Title: "Install Waffler"
- Message: "Waffler needs to be installed in your Applications folder to work properly.\n\nWould you like to install it now?"
- Buttons: "Not Now" (cancel), "Install" (default)
- Icon: Caution

**Implementation:**
```python
def _show_install_dialog():
    """Show native macOS dialog asking to install. Returns True if user clicks Install."""
    import subprocess

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

**Returns:**
- `True` if user clicks "Install"
- `False` if user clicks "Not Now"

#### 3. Installation

**Function:** `_install_to_applications() -> Path`

**Process:**
1. Get source app bundle path
2. Try copying to `/Applications/Waffler.app`
3. If permission denied, fallback to `~/Applications/Waffler.app`
4. If destination exists, remove it first (replace old version)
5. Return new app path

**Implementation:**
```python
def _install_to_applications():
    """Copy app to Applications folder. Returns path to installed app."""
    import shutil

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

**Error handling:**
- Raises exception if both `/Applications` and `~/Applications` fail
- Caller shows error dialog and quits

#### 4. Relaunch

**Function:** `_relaunch_from_new_location(app_path: Path)`

**Process:**
1. Launch new app using `open` command
2. Exit current process immediately

**Implementation:**
```python
def _relaunch_from_new_location(app_path: Path):
    """Launch the newly installed app and exit this process."""
    import subprocess
    subprocess.Popen(['open', str(app_path)])
    sys.exit(0)
```

**Behavior:**
- New app opens in Applications
- Old DMG process exits cleanly
- User sees seamless transition

#### 5. Error Dialog

**Function:** `_show_error_dialog(message: str)`

**UI:**
- Native macOS dialog
- Title: "Installation Error"
- Message: User-friendly error + manual instructions
- Button: "OK"
- Icon: Stop

**Implementation:**
```python
def _show_error_dialog(message: str):
    """Show error dialog with manual installation instructions."""
    import subprocess
    subprocess.run([
        'osascript', '-e',
        f'display dialog "{message}" '
        'with title "Installation Error" '
        'buttons {"OK"} '
        'default button "OK" '
        'with icon stop'
    ])
```

## User Flow

### Happy Path

1. User downloads `Waffler-3.3.0-installer.dmg`
2. DMG opens, shows Waffler.app + Applications folder
3. User double-clicks Waffler.app (mistake!)
4. **Dialog appears:** "Install Waffler to Applications?"
5. User clicks "Install"
6. App copies to `/Applications/Waffler.app` (or `~/Applications/` if no permission)
7. New app launches automatically
8. DMG version exits
9. ✅ User now running from Applications, all features work

### Decline Path

1. User sees install dialog
2. User clicks "Not Now"
3. App exits cleanly
4. User can try again later

### Error Path

1. Install dialog → User clicks "Install"
2. Copy fails (disk full, unexpected error)
3. **Error dialog:** "Installation failed: {error}. Please drag Waffler to Applications manually."
4. App exits
5. User must install manually

## Edge Cases

### Already Installed
- If running from `/Applications/` → no dialog, normal startup
- If running from `~/Applications/` → no dialog, normal startup

### Version Mismatch
- If newer version in `/Applications/` exists → replaces it
- User can downgrade by launching older DMG

### Multiple Locations
- Always tries `/Applications/` first
- Only uses `~/Applications/` if permission denied
- Doesn't install to both

### DMG Cleanup
- **Not implemented** - don't try to eject DMG (can fail, not critical)
- User can manually eject after app relaunches

## Testing

**Test cases:**

1. **Launch from DMG** → Shows dialog, installs, relaunches ✅
2. **Launch from Downloads** → Shows dialog, installs, relaunches ✅
3. **Launch from /Applications** → No dialog, normal startup ✅
4. **Launch from ~/Applications** → No dialog, normal startup ✅
5. **User clicks "Not Now"** → App exits cleanly ✅
6. **Permission denied for /Applications** → Installs to ~/Applications ✅
7. **Disk full / other error** → Shows error, quits ✅
8. **Replace existing version** → Old version removed, new installed ✅

## Implementation Notes

- Add functions to `app.py` before `main()`
- Import `subprocess`, `shutil` at top
- Keep detection logic simple (no config files, no state)
- Use native dialogs (better UX than pywebview alerts)
- Don't block if app is already in valid location (fast path)

## Success Metrics

- Users no longer experience spacebar sticky mode failures
- Support requests about "app not working" decrease
- Clean telemetry: % of launches from invalid locations drops to ~0%

## Future Enhancements

- Add telemetry to track how often this triggers
- Consider showing "Successfully installed!" confirmation toast
- Option to remember "Not Now" preference (low priority)
