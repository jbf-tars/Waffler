#!/bin/bash
# Waffler Launcher
# Double-click this file to launch Waffler

# Get the directory where this script lives
cd "$(dirname "$0")"

# Launch Waffler.app
if [ -d "/Applications/Waffler.app" ]; then
    # Launch from Applications
    /Applications/Waffler.app/Contents/MacOS/Waffler
elif [ -d "dist/Waffler.app" ]; then
    # Launch from source directory
    dist/Waffler.app/Contents/MacOS/Waffler
else
    echo "❌ Waffler.app not found!"
    echo ""
    echo "Please install Waffler to /Applications first."
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi
