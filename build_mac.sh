#!/bin/bash
# ============================================
# Waffler — macOS Build Script
# Produces: dist/Waffler.app + Waffler.dmg
# ============================================

set -e
cd "$(dirname "$0")"

echo ""
echo "============================================"
echo "  Waffler — macOS Build"
echo "============================================"
echo ""

# Step 1: Check Python
echo "[1/6] Checking Python..."
python3 --version || { echo "ERROR: Python3 not found!"; exit 1; }

# Step 2: Install dependencies
echo ""
echo "[2/6] Installing dependencies..."
python3 -m pip install --upgrade pip -q
python3 -m pip install -q \
    pyinstaller \
    sounddevice numpy pynput pyperclip \
    requests pyyaml python-dotenv \
    openai groq supabase \
    httpx httpcore anyio sniffio \
    certifi h11 idna charset_normalizer urllib3 \
    pywebview rumps \
    pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz \
    2>&1
echo "  Done."

# Step 3: Create .icns icon if missing
echo ""
echo "[3/6] Preparing app icon..."
if [ ! -f "icon.icns" ] && [ -f "icon_512.png" ]; then
    echo "  Converting icon_512.png → icon.icns..."
    mkdir -p icon.iconset
    sips -z 16 16     icon_512.png --out icon.iconset/icon_16x16.png      >/dev/null
    sips -z 32 32     icon_512.png --out icon.iconset/icon_16x16@2x.png   >/dev/null
    sips -z 32 32     icon_512.png --out icon.iconset/icon_32x32.png      >/dev/null
    sips -z 64 64     icon_512.png --out icon.iconset/icon_32x32@2x.png   >/dev/null
    sips -z 128 128   icon_512.png --out icon.iconset/icon_128x128.png    >/dev/null
    sips -z 256 256   icon_512.png --out icon.iconset/icon_128x128@2x.png >/dev/null
    sips -z 256 256   icon_512.png --out icon.iconset/icon_256x256.png    >/dev/null
    sips -z 512 512   icon_512.png --out icon.iconset/icon_256x256@2x.png >/dev/null
    sips -z 512 512   icon_512.png --out icon.iconset/icon_512x512.png    >/dev/null
    iconutil -c icns icon.iconset -o icon.icns
    rm -rf icon.iconset
    echo "  Done."
elif [ -f "icon.icns" ]; then
    echo "  icon.icns already exists."
else
    echo "  WARNING: icon_512.png not found — app will have default icon."
fi

# Step 4: Clean previous build
echo ""
echo "[4/6] Cleaning previous build..."
rm -rf build dist
echo "  Done."

# Step 5: Build with PyInstaller
echo ""
echo "[5/6] Building Waffler.app..."
echo "  This may take a few minutes..."
echo ""
python3 -m PyInstaller Waffler_mac.spec --noconfirm 2>&1

# Step 6: Create professional DMG with custom window (uses create-dmg)
echo ""
echo "[6/6] Creating Waffler.dmg with custom installer window..."
if [ -d "dist/Waffler.app" ]; then
    rm -f dist/Waffler.dmg

    # Create pretty DMG with custom window and auto-layout
    create-dmg \
        --volname "Waffler Installer" \
        --volicon "icon.icns" \
        --window-pos 200 120 \
        --window-size 660 400 \
        --icon-size 160 \
        --icon "Waffler.app" 180 170 \
        --hide-extension "Waffler.app" \
        --app-drop-link 480 170 \
        --text-size 16 \
        --no-internet-enable \
        "dist/Waffler.dmg" \
        "dist/Waffler.app" 2>&1 | grep -v "^hdiutil: " || true

    echo "  Done. DMG created with custom installer window! 🧇"
else
    echo "  WARNING: Waffler.app not found — skipping DMG"
fi

echo ""
echo "============================================"
echo "  BUILD SUCCESSFUL!"
echo "  App:  dist/Waffler.app"
echo "  DMG:  dist/Waffler.dmg"
echo "============================================"
echo ""

# Sanity check
if [ -d "dist/Waffler.app" ]; then
    echo "  Waffler.app size:"
    du -sh "dist/Waffler.app"
fi
if [ -f "dist/Waffler.dmg" ]; then
    echo "  Waffler.dmg size:"
    du -sh "dist/Waffler.dmg"
fi

# Copy to Desktop for easy access
if [ -d "dist/Waffler.app" ]; then
    echo ""
    echo "  Copying Waffler.app to Desktop..."
    rm -rf "$HOME/Desktop/Waffler.app"
    cp -R dist/Waffler.app "$HOME/Desktop/Waffler.app"
    echo "  Done — double-click Waffler.app on your Desktop to launch!"
fi
if [ -f "dist/Waffler.dmg" ]; then
    cp dist/Waffler.dmg "$HOME/Desktop/Waffler.dmg"
    echo "  Waffler.dmg also copied to Desktop."
fi
