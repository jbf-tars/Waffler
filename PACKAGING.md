# VoiceFlow Packaging & Distribution Guide

## 📦 Distribution Formats

We will support 3 distribution formats:

1. **DMG (Disk Image)** - Primary distribution (Mac standard)
2. **ZIP** - For GitHub releases and web downloads
3. **Homebrew** - Community package manager (future)

---

## 🎯 Week 3 Packaging Roadmap

### Phase 1: Code Signing (Day 1-2)
- [ ] Create Apple Developer certificate (if not exists)
- [ ] Generate signing certificate (requires $99/year Apple Developer)
- [ ] Configure PyInstaller for code signing

### Phase 2: Build & Test (Day 2-3)
- [ ] Build .app bundle with code signature
- [ ] Create DMG installer
- [ ] Test on clean Mac (no dev tools)

### Phase 3: Notarization (Day 3-4)
- [ ] Submit to Apple for notarization
- [ ] Automated scanning (~30 mins)
- [ ] Staple notarization ticket

### Phase 4: Distribution (Day 4-5)
- [ ] Upload to website
- [ ] Create GitHub releases
- [ ] Setup auto-update mechanism

---

## 🔐 Code Signing Steps

### 1. Generate Signing Certificate

```bash
# Only if you don't have an Apple Developer account
# Visit: https://developer.apple.com/account
# Cost: $99/year

# Once you have the cert, export from Keychain:
# Keychain Access → right-click cert → Export
# This creates a .p12 file
```

### 2. Update PyInstaller Config

Edit `VoiceFlow.spec` to enable signing:

```python
app = BUNDLE(
    exe,
    name='VoiceFlow.app',
    icon='assets/icon.icns',
    bundle_identifier='ai.clawd.voiceflow',
    info_plist={...},
    codesign_identity='Developer ID Application: Your Name',  # ADD THIS
    entitlements_file='entitlements.plist',  # ADD THIS
)
```

### 3. Create Entitlements File

Create `entitlements.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.app-sandbox</key>
    <false/>
    <key>com.apple.security.device.microphone</key>
    <true/>
    <key>com.apple.security.input-monitoring</key>
    <true/>
</dict>
</plist>
```

---

## 📦 Building the DMG

### 1. Build the .app Bundle

```bash
cd /Users/tars/clawd/projects/voice-app-downloadable
source venv/bin/activate
pyinstaller VoiceFlow.spec --clean
```

This creates: `dist/VoiceFlow.app` (code-signed)

### 2. Create DMG Installer

Create `build_dmg.sh`:

```bash
#!/bin/bash

# Variables
APP_NAME="VoiceFlow"
VOLUME_NAME="VoiceFlow Installer"
DMG_NAME="VoiceFlow.dmg"
TEMP_DMG="temp.dmg"

# Remove old files
rm -f $TEMP_DMG $DMG_NAME

# Create temporary DMG (500 MB)
hdiutil create -srcfolder dist/VoiceFlow.app \
               -volname "$VOLUME_NAME" \
               -format UDRW \
               -size 500m \
               $TEMP_DMG

# Mount DMG
DEVICE=$(hdiutil attach $TEMP_DMG | grep '^/dev/' | awk '{print $1}')
MOUNT_POINT="/Volumes/$VOLUME_NAME"

# Copy icon and background
cp assets/icon.icns "$MOUNT_POINT/.VolumeIcon.icns"
mkdir -p "$MOUNT_POINT/.background"
cp assets/dmg-background.png "$MOUNT_POINT/.background/bg.png"

# Create symlink to Applications folder
ln -s /Applications "$MOUNT_POINT/Applications"

# Set DMG icon
SetFile -a C "$MOUNT_POINT"

# Create alias (shortcut) to Applications
osascript <<EOF
tell application "Finder"
  set vf to POSIX file "$MOUNT_POINT/VoiceFlow.app" as alias
  set af to POSIX file "$MOUNT_POINT/Applications" as alias
  make new alias file at POSIX file "$MOUNT_POINT" to af
  set name of result to "Applications"
end tell
EOF

# Eject DMG
hdiutil eject $DEVICE

# Convert to compressed format
hdiutil convert $TEMP_DMG \
         -format UDZO \
         -o $DMG_NAME

# Clean up
rm -f $TEMP_DMG

echo "✅ DMG created: $DMG_NAME"
```

### 3. Run DMG Builder

```bash
chmod +x build_dmg.sh
./build_dmg.sh
```

---

## ✅ Notarization (Apple's Malware Scan)

### Requirements
- Apple Developer account ($99/year)
- App ID + password (or Keychain item)

### Process

```bash
# 1. Create app-specific password (if using 2FA)
# Visit: https://appleid.apple.com/account/manage
# Create app password: copy the result

# 2. Store in Keychain (one-time)
xcrun notarytool store-credentials "voiceflow" \
  --apple-id "your-apple-id@example.com" \
  --team-id "XXXXXXXXXX"  # From Apple Developer account

# 3. Submit for notarization
xcrun notarytool submit VoiceFlow.dmg \
  --keychain-profile "voiceflow" \
  --wait

# If successful, output will include RequestUUID
# Example: 12345678-1234-1234-1234-123456789012

# 4. Staple notarization ticket (adds proof to app)
xcrun stapler staple dist/VoiceFlow.app
```

### Troubleshooting Notarization

```bash
# Check notarization status
xcrun notarytool info <RequestUUID> \
  --keychain-profile "voiceflow"

# View detailed logs if failed
xcrun notarytool log <RequestUUID> \
  --keychain-profile "voiceflow" \
  log.json
cat log.json
```

---

## 📱 App Icon Design

### Requirements
- **Icon file:** `assets/icon.icns` (macOS icon format)
- **Sizes:** 512x512 px minimum
- **Formats:** PNG or ICNS

### Creating Icon

```bash
# Using ImageMagick
convert app-icon.png icon.png
iconutil -c icns -o icon.icns icon.iconset/

# Or use a tool like:
# - Figma (web-based design)
# - Pixelmator Pro (macOS native)
# - Icon Slate (icon creator for Mac)
```

### DMG Background

- **File:** `assets/dmg-background.png`
- **Size:** 1024x672 px (standard for 2x retina)
- **Design:** Professional, branded

---

## 🚀 GitHub Release Setup

### 1. Create GitHub Release

```bash
cd /Users/tars/clawd/projects/voice-app-downloadable
git tag v1.0.0
git push origin v1.0.0

# Or use gh CLI
gh release create v1.0.0 \
  --title "VoiceFlow 1.0.0" \
  --notes "Initial release" \
  VoiceFlow.dmg
```

### 2. Release Notes Template

```markdown
# VoiceFlow 1.0.0

Your voice-to-text command assistant for Mac.

## ✨ Features
- 🎙️ Press Cmd+Shift+Space to record
- 🤖 AI-powered text styling
- 📋 Auto-copy to clipboard
- ⚡ <3s latency

## 🐛 Bug Fixes
- Fixed hotkey detection on Big Sur+

## 📊 System Requirements
- macOS 11 (Big Sur) or later
- 100MB free disk space
- Microphone

## 📥 Installation
1. Download VoiceFlow.dmg
2. Drag VoiceFlow.app to Applications
3. Launch and grant accessibility permission

## 💡 Quick Start
- Press & hold Cmd+Shift+Space
- Speak your command
- Release to copy to clipboard

## 🙏 Feedback
Issues? Questions? Open an issue on GitHub!
```

---

## 🔄 Auto-Update Mechanism (Future)

For future releases, implement auto-update:

### Option 1: Sparkle Framework
- Popular, battle-tested
- Requires code signing
- Automatic update checks

### Option 2: Manual Check
- Simple version file on server
- Prompt user to download new DMG
- Less elegant but easier to implement

---

## 📋 Pre-Release Checklist

Before distributing:

- [ ] All tests pass on clean Mac
- [ ] Code signed and notarized
- [ ] Icon looks good in Finder
- [ ] DMG background displays correctly
- [ ] App launches without errors
- [ ] Hotkey works
- [ ] Microphone permission prompts
- [ ] Accessibility permission required message clear
- [ ] Help documentation accessible
- [ ] Version number updated (main.py, VoiceFlow.spec)
- [ ] GitHub release created
- [ ] Website download link updated

---

## 📊 Distribution Channels

1. **Website:** voiceflow.app (future)
2. **GitHub:** Releases page
3. **ProductHunt:** (after launch)
4. **Homebrew:** `brew install voiceflow` (future)
5. **MacAppStore:** (if approved)

---

## 🎯 Success Criteria (Week 3 End)

✅ App is code-signed
✅ App is notarized by Apple
✅ DMG installer created
✅ GitHub release published
✅ Installs cleanly on fresh Mac
✅ All features work post-install
✅ Download available from website

---

## 📝 Version Management

Track versions in these files:

1. **VoiceFlow.spec:**
```python
exe = EXE(..., name='voiceflow-1.0.0')
```

2. **main.py:**
```python
VERSION = "1.0.0"
```

3. **GitHub:**
- Tag: `v1.0.0`
- Release: `VoiceFlow 1.0.0`

---

## 💾 Backup & Archive

Before distribution:

```bash
# Archive the build
tar -czf VoiceFlow-1.0.0-build.tar.gz dist/
cp VoiceFlow.dmg backups/VoiceFlow-1.0.0.dmg

# Tag in git
git tag release/1.0.0
git push origin release/1.0.0
```

---

**Ready for Week 3 distribution!** 🚀
