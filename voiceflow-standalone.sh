#!/bin/bash
# VoiceFlow Standalone Launcher
# Works without .app packaging

echo "🎙️  VoiceFlow - Starting..."
echo ""

# Check if we're in the right directory
if [ ! -f "main.py" ]; then
    echo "❌ Error: Run this script from the voice-app-downloadable directory"
    exit 1
fi

# Activate virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install dependencies if needed
if ! python -c "import deepgram" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install -r requirements.txt -q
fi

# Run the app
python main.py
