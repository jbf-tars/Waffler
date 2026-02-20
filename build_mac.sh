#!/bin/bash
# ============================================
# VoiceFlow — macOS Build Script
# Produces: dist/VoiceFlow.app + VoiceFlow.dmg
# ============================================

set -e
cd "$(dirname "$0")"

echo ""
echo "============================================"
echo "  VoiceFlow — macOS Build"
echo "============================================"
echo ""

# Step 1: Check Python
echo "[1/5] Checking Python..."
python3 --version || { echo "ERROR: Python3 not found!"; exit 1; }

# Step 2: Install dependencies
echo ""
echo "[2/5] Installing dependencies..."
python3 -m pip install --upgrade pip -q
python3 -m pip install -q \
    pyinstaller \
    sounddevice numpy pynput pyperclip \
    requests pyyaml python-dotenv \
    openai httpx httpcore anyio sniffio \
    certifi h11 idna charset_normalizer urllib3 \
    pywebview rumps \
    pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz \
    2>&1
echo "  Done."

# Step 3: Clean previous build
echo ""
echo "[3/5] Cleaning previous build..."
rm -rf build dist
echo "  Done."

# Step 4: Build with PyInstaller
echo ""
echo "[4/5] Building VoiceFlow.app..."
echo "  This may take a few minutes..."
echo ""
pyinstaller VoiceFlow_mac.spec --noconfirm 2>&1

# Step 5: Create DMG
echo ""
echo "[5/5] Creating VoiceFlow.dmg..."
if [ -d "dist/VoiceFlow.app" ]; then
    rm -f dist/VoiceFlow.dmg
    hdiutil create -volname "VoiceFlow" \
        -srcfolder dist/VoiceFlow.app \
        -ov -format UDZO \
        dist/VoiceFlow.dmg 2>&1
    echo "  Done."
else
    echo "  WARNING: VoiceFlow.app not found — skipping DMG"
fi

echo ""
echo "============================================"
echo "  BUILD SUCCESSFUL!"
echo "  App:  dist/VoiceFlow.app"
echo "  DMG:  dist/VoiceFlow.dmg"
echo "============================================"
echo ""

# Sanity check
if [ -d "dist/VoiceFlow.app" ]; then
    echo "  VoiceFlow.app created successfully."
    du -sh "dist/VoiceFlow.app"
fi
if [ -f "dist/VoiceFlow.dmg" ]; then
    du -sh "dist/VoiceFlow.dmg"
fi
