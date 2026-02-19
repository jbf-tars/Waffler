#!/bin/bash
# ============================================
# VoiceFlow — macOS Build Script
# Produces: dist/VoiceFlow.app
# ============================================

set -e
cd "$(dirname "$0")"

echo ""
echo "============================================"
echo "  VoiceFlow — macOS Build"
echo "============================================"
echo ""

# Step 1: Check Python
echo "[1/4] Checking Python..."
python3 --version || { echo "ERROR: Python3 not found!"; exit 1; }

# Step 2: Install dependencies
echo ""
echo "[2/4] Installing dependencies..."
python3 -m pip install --upgrade pip -q
python3 -m pip install -q pyinstaller sounddevice numpy pynput pyperclip requests pyyaml python-dotenv openai pywebview rumps pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz httpx httpcore anyio sniffio certifi h11 idna 2>&1
echo "  Done."

# Step 3: Clean previous build
echo ""
echo "[3/4] Cleaning previous build..."
rm -rf build dist
echo "  Done."

# Step 4: Build with PyInstaller
echo ""
echo "[4/4] Building VoiceFlow.app..."
echo "  This may take a few minutes..."
echo ""
pyinstaller VoiceFlow_mac.spec --noconfirm 2>&1

echo ""
echo "============================================"
echo "  BUILD SUCCESSFUL!"
echo "  Output: dist/VoiceFlow.app"
echo "============================================"
echo ""

# Quick sanity check
if [ -d "dist/VoiceFlow.app" ]; then
    echo "  VoiceFlow.app created successfully."
    du -sh "dist/VoiceFlow.app"
else
    echo "  WARNING: VoiceFlow.app not found in dist/"
fi
