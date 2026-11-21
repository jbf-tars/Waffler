#!/bin/bash
# ============================================
# Waffler — macOS Build Script
# Produces: dist/Waffler.app + dist-installer/Waffler-<version>-mac.dmg
# ============================================

set -e
cd "$(dirname "$0")"

echo ""
echo "============================================"
echo "  Waffler — macOS Build"
echo "============================================"
echo ""

# The Python that builds. The release is built with Python 3.11
# (.github/workflows/macos-release.yml); name another with PYTHON=, for
# example a virtualenv's: PYTHON=.build-env/venv/bin/python ./build_mac.sh
PYTHON="${PYTHON:-python3}"

# Step 1: Check Python
echo "[1/7] Checking Python..."
"$PYTHON" --version || { echo "ERROR: $PYTHON not found!"; exit 1; }

# Step 2: Install dependencies
# The same list the release workflow installs. (This step used to carry a
# list of its own, which had fallen behind requirements.txt: no soundfile and
# no on-device speech engine.) The writing engine, llama-cpp-python, is not in
# requirements.txt on purpose: the pip line that compiles it is written there.
echo ""
echo "[2/7] Installing dependencies..."
"$PYTHON" -m pip install --upgrade pip -q
"$PYTHON" -m pip install -q -r requirements.txt pyinstaller 2>&1
echo "  Done."

# Step 3: Create .icns icon if missing
echo ""
echo "[3/7] Preparing app icon..."
# Source file in the repo is icon_master_1024.png. sips downsamples to
# every iconset resolution. Older script versions referenced icon_512.png
# which never existed in the tree, so every build emitted a spurious
# 'icon not found' warning and shipped a generic-icon app.
ICON_SRC="icon_master_1024.png"
if [ ! -f "icon.icns" ] && [ -f "$ICON_SRC" ]; then
    echo "  Converting $ICON_SRC → icon.icns..."
    mkdir -p icon.iconset
    sips -z 16 16     "$ICON_SRC" --out icon.iconset/icon_16x16.png      >/dev/null
    sips -z 32 32     "$ICON_SRC" --out icon.iconset/icon_16x16@2x.png   >/dev/null
    sips -z 32 32     "$ICON_SRC" --out icon.iconset/icon_32x32.png      >/dev/null
    sips -z 64 64     "$ICON_SRC" --out icon.iconset/icon_32x32@2x.png   >/dev/null
    sips -z 128 128   "$ICON_SRC" --out icon.iconset/icon_128x128.png    >/dev/null
    sips -z 256 256   "$ICON_SRC" --out icon.iconset/icon_128x128@2x.png >/dev/null
    sips -z 256 256   "$ICON_SRC" --out icon.iconset/icon_256x256.png    >/dev/null
    sips -z 512 512   "$ICON_SRC" --out icon.iconset/icon_256x256@2x.png >/dev/null
    sips -z 512 512   "$ICON_SRC" --out icon.iconset/icon_512x512.png    >/dev/null
    sips -z 1024 1024 "$ICON_SRC" --out icon.iconset/icon_512x512@2x.png >/dev/null
    iconutil -c icns icon.iconset -o icon.icns
    rm -rf icon.iconset
    echo "  Done."
elif [ -f "icon.icns" ]; then
    echo "  icon.icns already exists."
else
    echo "  WARNING: $ICON_SRC not found — app will have default icon."
fi

# Step 4: Clean previous build
echo ""
echo "[4/7] Cleaning previous build..."
rm -rf build dist
echo "  Done."

# Step 5: Build with PyInstaller
echo ""
echo "[5/7] Building Waffler.app..."
echo "  This may take a few minutes..."
echo ""
# The helper that records what the Mac is playing in a meeting: the spec
# bundles mac/build/natter-sysaudio when it is there.
sh mac/build_sysaudio.sh
"$PYTHON" -m PyInstaller Waffler_mac.spec --noconfirm 2>&1
# Every part the build needs, asked of the built app itself (no window).
dist/Waffler.app/Contents/MacOS/Waffler --self-check > dist/self-check.json \
    || { echo "ERROR: the built app is missing a part:"; cat dist/self-check.json; exit 1; }

# Step 6: Code sign the app (ad-hoc signature for local distribution)
echo ""
echo "[6/7] Code signing Waffler.app..."
if [ -d "dist/Waffler.app" ]; then
    # Remove quarantine attributes and sign with ad-hoc signature
    xattr -cr dist/Waffler.app
    codesign --force --deep --sign - dist/Waffler.app
    echo "  Done."
else
    echo "  WARNING: Waffler.app not found — skipping code signing"
fi

# Step 7: Create the DMG (dmg-assets/create-dmg.sh: the app, a link to
# Applications and the release's background, filled at a mount point of its
# own so a Waffler disk that is already open is left alone).
echo ""
echo "[7/7] Creating the DMG..."
if [ -d "dist/Waffler.app" ]; then
    bash dmg-assets/create-dmg.sh
    echo "  Done."
else
    echo "  WARNING: Waffler.app not found — skipping DMG"
fi

echo ""
echo "============================================"
echo "  BUILD SUCCESSFUL!"
echo "  App:  dist/Waffler.app"
echo "  DMG:  dist-installer/Waffler-<version>-mac.dmg"
echo "============================================"
echo ""

# Sanity check
if [ -d "dist/Waffler.app" ]; then
    echo "  Waffler.app size:"
    du -sh "dist/Waffler.app"
fi

# Copy to Desktop for easy access (WAFFLER_BUILD_NO_COPY=1 leaves the Desktop alone)
if [ -n "$WAFFLER_BUILD_NO_COPY" ]; then
    exit 0
fi
if [ -d "dist/Waffler.app" ]; then
    echo ""
    echo "  Copying Waffler.app to Desktop..."
    rm -rf "$HOME/Desktop/Waffler.app"
    cp -R dist/Waffler.app "$HOME/Desktop/Waffler.app"
    echo "  Done — double-click Waffler.app on your Desktop to launch!"
fi
for dmg in dist-installer/Waffler-*-mac.dmg; do
    if [ -f "$dmg" ]; then
        cp "$dmg" "$HOME/Desktop/"
        echo "  $(basename "$dmg") also copied to Desktop."
    fi
done
