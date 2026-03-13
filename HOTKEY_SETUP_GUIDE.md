# Waffler Hotkey Setup Guide

## Quick Start (Recommended)

**Best Option: F13 Key with Caps Lock Remapping**

1. Install [Karabiner-Elements](https://karabiner-elements.pqrs.org/)
2. Run the configuration generator:
   ```bash
   python -c "from src.smart_hotkey_f13 import save_karabiner_config; save_karabiner_config()"
   ```
3. Import the generated config in Karabiner-Elements:
   - Open Karabiner-Elements
   - Go to "Complex Modifications" tab
   - Click "Add rule"
   - Click "Import more rules from the Internet" (optional, or use file)
   - Import `waffler-karabiner.json`
   - Enable "Waffler: Caps Lock → F13" rule
4. Restart Waffler

**Now Caps Lock = F13 = your push-to-talk key!**

## Why We Changed from Fn Key

The original implementation using the Fn (Function) key had critical issues:

### The Fn Key Problem

```
❌ Dispatch queue crashes (NSEvent on wrong queue)
❌ Hardware-level modifier (not exposed to macOS)
❌ Flag set on F1-F12 even when Fn NOT pressed
❌ Cannot be reliably remapped in software
❌ Inconsistent behavior across keyboards
```

**Crash Log Evidence:**
```
Exception: EXC_BREAKPOINT (SIGTRAP)
Fault: _dispatch_assert_queue_fail
Cause: NSEvent callback on wrong dispatch queue
```

See `/Users/james/Library/Logs/DiagnosticReports/Python-2026-02-27-203645.ips`

### What Professionals Use

After researching how apps like Raycast, CleanShot X, Discord, and TeamSpeak handle push-to-talk:

- **Discord/TeamSpeak**: Customizable hotkeys, often F13-F15 or Right Command
- **Raycast**: Cmd+Space (customizable)
- **CleanShot X**: Custom hotkeys, not Fn
- **MacWhisper**: Right Command (⌘) for push-to-talk

**Nobody relies on Fn key** because of its hardware limitations.

## Available Hotkey Options

### Option 1: F13 Key (Recommended) ⭐

**Pros:**
- ✅ Most reliable (no crashes)
- ✅ Unused on Mac keyboards (no conflicts)
- ✅ Ergonomic when mapped from Caps Lock
- ✅ Works with existing pynput library
- ✅ Similar to professional push-to-talk setups

**Cons:**
- Requires Karabiner-Elements for best experience
- Not available on built-in keyboards without remapping

**Implementation:**
```python
from src.smart_hotkey_f13 import SmartHotkeyListener

listener = SmartHotkeyListener(on_press, on_release)
listener.start()
```

**Usage:**
- Hold F13 (Caps Lock) to record
- F13 + Space for sticky mode
- F13 again to stop sticky mode

### Option 2: Right Command Key

**Pros:**
- ✅ Available on all Mac keyboards
- ✅ Rarely used (good candidate)
- ✅ No additional software needed
- ✅ Reliable detection

**Cons:**
- Some users may use it for other shortcuts
- Less ergonomic than Caps Lock

**Implementation:**
```python
from src.smart_hotkey_f13 import SmartHotkeyListener

listener = SmartHotkeyListener(on_press, on_release, use_right_cmd=True)
listener.start()
```

**Usage:**
- Hold Right ⌘ to record
- Right ⌘ + Space for sticky mode
- Right ⌘ again to stop

### Option 3: Fn Key (Experimental) ⚠️

**Only use if you specifically need Fn and understand the limitations.**

**Pros:**
- Matches original design intent

**Cons:**
- ❌ May crash (dispatch queue issues)
- ❌ Doesn't work on all keyboards
- ❌ False positives on F1-F12 keys
- ❌ Requires Input Monitoring permission
- ❌ CGEventTap leaks memory

**Implementation:**
```python
from src.fn_hotkey_cgeventtap import SmartHotkeyListener

listener = SmartHotkeyListener(on_press, on_release)
listener.start()
```

**Requires:**
- Input Monitoring permission
- System Preferences > Privacy & Security > Input Monitoring

**Warning:** This is experimental and not recommended for production use.

## Comparison Table

| Feature | F13 (Recommended) | Right Command | Fn Key (Experimental) |
|---------|-------------------|---------------|------------------------|
| **Reliability** | ✅ Excellent | ✅ Excellent | ⚠️  Experimental |
| **No Crashes** | ✅ Yes | ✅ Yes | ❌ May crash |
| **Keyboard Availability** | ⚠️  Needs remapping | ✅ All keyboards | ✅ Most laptops |
| **Ergonomics** | ✅ Excellent (Caps Lock) | ⚠️  Awkward position | ✅ Good position |
| **Setup Complexity** | ⚠️  Needs Karabiner | ✅ None | ❌ Complex permissions |
| **Conflicts** | ✅ None | ⚠️  Rare | ⚠️  F1-F12 issues |
| **Professional Use** | ✅ Common | ✅ Common | ❌ Not used |

## Migration Guide

### From Fn to F13

1. **Update imports in app.py:**

   ```python
   # OLD
   from smart_hotkey import SmartHotkeyListener

   # NEW
   from smart_hotkey_f13 import SmartHotkeyListener
   ```

2. **Auto-detect best key:**

   ```python
   from smart_hotkey_f13 import create_smart_hotkey

   listener = create_smart_hotkey(on_press, on_release, prefer_f13=True)
   listener.start()
   ```

   This will:
   - Use F13 if Karabiner-Elements is installed
   - Fall back to Right Command otherwise
   - Print setup instructions

3. **Remove Fn permission code from app.py:**

   Delete lines 1602-1620 in app.py:
   ```python
   def _request_input_monitoring_permission():
       # DELETE THIS ENTIRE FUNCTION
   ```

   And remove the call in main():
   ```python
   # DELETE THIS
   if _platform.system() == "Darwin":
       _request_input_monitoring_permission()
   ```

### Testing Your New Hotkey

```bash
# Test F13 implementation
python src/smart_hotkey_f13.py

# Generate Karabiner config
python src/smart_hotkey_f13.py --generate-karabiner

# Test Fn key (experimental)
python src/fn_hotkey_cgeventtap.py
```

## Karabiner-Elements Setup

### Installation

```bash
# Download from official site
open https://karabiner-elements.pqrs.org/

# Or install with Homebrew
brew install --cask karabiner-elements
```

### Configuration

**Option A: Auto-generate (recommended)**

```bash
cd waffler-app
python -c "from src.smart_hotkey_f13 import save_karabiner_config; save_karabiner_config()"
```

This creates `waffler-karabiner.json` with pre-configured Caps Lock → F13 mapping.

**Option B: Manual Setup**

1. Open Karabiner-Elements
2. Go to "Simple Modifications" tab
3. Add rule: From key = Caps Lock, To key = F13
4. Save

**Option C: Use Complex Modifications (advanced)**

For more sophisticated mappings (e.g., Caps Lock alone → F13, Caps Lock + another key → Caps Lock):

1. Open Karabiner-Elements
2. Go to "Complex Modifications" tab
3. Click "Add rule"
4. Import the generated `waffler-karabiner.json`
5. Enable the rule

### Alternative Key Mappings

You can map any key to F13. Popular choices:

- **Caps Lock** (recommended - ergonomic, rarely used)
- **Right Option** (Alt) - good for laptops
- **Right Control** - alternative option
- **§ key** (backtick area) - if you don't use it

## Troubleshooting

### "F13 not detected"

1. Check Karabiner-Elements is running (menu bar icon)
2. Verify mapping is enabled in Karabiner-Elements preferences
3. Test key with Karabiner-EventViewer:
   - Open Karabiner-Elements
   - Click "EventViewer" button
   - Press your mapped key
   - Should show "F13" keycode

### "Right Command not working"

1. Check for conflicting shortcuts:
   - System Preferences > Keyboard > Shortcuts
   - Disable any shortcuts using Right Command
2. Test with Karabiner-EventViewer to confirm key is detected

### "Fn key crashes the app"

This is a known issue. Switch to F13 or Right Command instead:

```python
# In app.py or main.py, update:
from smart_hotkey_f13 import create_smart_hotkey

listener = create_smart_hotkey(on_press, on_release)
listener.start()
```

### Permissions Issues (Fn key only)

If using the experimental Fn key listener:

1. Open System Preferences
2. Go to Privacy & Security > Input Monitoring
3. Add Python (or your app)
4. Restart the app

**Note:** F13 and Right Command don't require these permissions!

## User Experience

### Default Behavior

When user first runs Waffler:

```
🔍 Hotkey detection: Karabiner-Elements not detected. Recommend Right ⌘ or install Karabiner-Elements.
⌨️  Hotkey: Hold Right ⌘ to record | Right ⌘ + Space = sticky | Right ⌘ again = stop
💡 Tip: Right Command is rarely used, making it a good push-to-talk key
```

### With Karabiner-Elements Installed

```
🔍 Hotkey detection: Karabiner-Elements detected. F13 recommended (map from Caps Lock).
⌨️  Hotkey: Hold F13 to record | F13 + Space = sticky | F13 again = stop
💡 Tip: Install Karabiner-Elements and remap Caps Lock → F13 for easy access
    Download: https://karabiner-elements.pqrs.org/
```

### In-App Setup Wizard

Show a setup card in the UI for first-time users:

```
┌─────────────────────────────────────────────────┐
│ 🎤 Waffler Push-to-Talk Setup                  │
├─────────────────────────────────────────────────┤
│                                                  │
│ Choose your hotkey:                              │
│                                                  │
│ ○ F13 (Caps Lock) - Recommended                 │
│   ✓ Most ergonomic                               │
│   ✓ No conflicts                                 │
│   ℹ Requires Karabiner-Elements                  │
│   [Install Karabiner] [Setup Instructions]      │
│                                                  │
│ ○ Right Command (⌥) - Quick Start               │
│   ✓ Works immediately                            │
│   ✓ No setup needed                              │
│   ℹ Less ergonomic                               │
│                                                  │
│ ○ Fn Key - Experimental                         │
│   ⚠ May crash                                    │
│   ⚠ Not recommended                              │
│                                                  │
│                                      [Continue]  │
└─────────────────────────────────────────────────┘
```

## References

- [Karabiner-Elements Official Site](https://karabiner-elements.pqrs.org/)
- [CGEventTap Documentation](https://developer.apple.com/documentation/coregraphics/1454426-cgeventtapcreate)
- [Fn Key Modifier Flag](https://developer.apple.com/documentation/coregraphics/cgeventflags/masksecondaryfn)
- [PyObjC Quartz Framework](https://pyobjc.readthedocs.io/en/latest/apinotes/Quartz.html)
- [pynput Documentation](https://pynput.readthedocs.io/)

## Contributing

If you find a better solution for Fn key detection, please:

1. Test thoroughly on multiple Mac models
2. Verify no dispatch queue crashes
3. Check memory leaks
4. Submit PR with test results

Current maintainer: [Your Name]
Last updated: 2026-02-27
