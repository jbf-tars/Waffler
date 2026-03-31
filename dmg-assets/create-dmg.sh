#!/bin/bash
# Create professional macOS DMG installer

APP_NAME="Waffler"
VERSION="v3.2.3"
DMG_NAME="${APP_NAME}-${VERSION}.dmg"
STAGING_DIR="dmg_staging"

# Clean up
rm -rf "$STAGING_DIR" "$DMG_NAME"
mkdir -p "$STAGING_DIR"

# Copy app
cp -R dist/Waffler.app "$STAGING_DIR/"

# Create Applications symlink
ln -s /Applications "$STAGING_DIR/Applications"

# Create temporary DMG
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGING_DIR" -ov -format UDRW temp.dmg

# Mount it
hdiutil attach temp.dmg -readwrite -mountpoint /Volumes/"$APP_NAME $VERSION"

# Set window properties
echo '
   tell application "Finder"
     tell disk "'$APP_NAME' '$VERSION'"
           open
           set current view of container window to icon view
           set toolbar visible of container window to false
           set statusbar visible of container window to false
           set the bounds of container window to {400, 100, 900, 400}
           set viewOptions to the icon view options of container window
           set arrangement of viewOptions to not arranged
           set icon size of viewOptions to 72
           set position of item "'$APP_NAME'.app" of container window to {125, 150}
           set position of item "Applications" of container window to {375, 150}
           update without registering applications
           delay 1
     end tell
   end tell
' | osascript

# Unmount
hdiutil detach /Volumes/"$APP_NAME $VERSION"

# Convert to compressed
hdiutil convert temp.dmg -format UDZO -o "$DMG_NAME"
rm temp.dmg
rm -rf "$STAGING_DIR"

echo "✓ Created $DMG_NAME"
