#!/bin/bash
# Waffler — a local DMG of dist/Waffler.app, with what the release's holds.
#
#   dmg-assets/create-dmg.sh            # dist-installer/Waffler-<version>-mac.dmg
#
# The release's own DMG is made by .github/workflows/macos-release.yml
# ("Package DMG"): the app, a link to Applications and the drag-to-install
# background (installer/mac/dmg-background.tiff), then a Finder script that
# places the icons. This makes the same disk, for trying an installer on this
# Mac, with two differences that keep it out of the way of whoever is using
# the Mac:
#   * the disk is filled at a mount point of its own, never /Volumes/Waffler.
#     A Waffler disk that is already open there (a downloaded release) made
#     `hdiutil create -srcfolder` fail, and the workflow's `hdiutil detach
#     /Volumes/Waffler` would have closed the wrong disk;
#   * the Finder script is not run (it opens a Finder window and needs
#     permission to control Finder), so the icons are not placed. The
#     background file is in the disk either way.
# It signs nothing: sign dist/Waffler.app first (build_mac.sh signs it ad hoc).
set -euo pipefail
cd "$(dirname "$0")/.."

APP="dist/Waffler.app"
[ -d "$APP" ] || { echo "ERROR: $APP not found: build it first (build_mac.sh)"; exit 1; }
VERSION=$(sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' src/__init__.py | head -1)
OUT_DIR="dist-installer"
DMG="$OUT_DIR/Waffler-${VERSION}-mac.dmg"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/waffler-dmg.XXXXXX")
MNT="$WORK/mnt"
RW="$WORK/rw.dmg"

cleanup() {
    if mount | grep -q " on .*$(basename "$WORK")/mnt "; then
        hdiutil detach "$MNT" -force >/dev/null 2>&1 || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$OUT_DIR" "$MNT"
rm -f "$DMG"

# A blank writable disk, big enough for the app with room to spare.
SIZE_MB=$(( $(du -sm "$APP" | cut -f1) + 40 ))
hdiutil create -size "${SIZE_MB}m" -fs HFS+ -volname "Waffler" -type UDIF -layout SPUD "$RW" >/dev/null

hdiutil attach "$RW" -nobrowse -noautoopen -mountpoint "$MNT" >/dev/null
ditto "$APP" "$MNT/Waffler.app"
ln -s /Applications "$MNT/Applications"
mkdir "$MNT/.background"
cp installer/mac/dmg-background.tiff "$MNT/.background/bg.tiff"
# macOS adds these to a new disk as soon as it is mounted; a release has neither.
rm -rf "$MNT/.fseventsd" "$MNT/.Trashes" 2>/dev/null || true
sync
hdiutil detach "$MNT" >/dev/null

# Compressed and read-only, as the release's is.
hdiutil convert "$RW" -format UDZO -o "$DMG" >/dev/null

echo "created $DMG ($(du -h "$DMG" | cut -f1))"
