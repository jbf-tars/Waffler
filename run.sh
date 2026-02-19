#!/bin/bash
# VoiceFlow Launcher
# Simple script to run the app

# Ensure we're in the project directory
cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate 2>/dev/null || {
    echo "❌ Virtual environment not found. Run ./setup.sh first"
    exit 1
}

# Check for required files
if [ ! -f "config.yaml" ]; then
    echo "❌ config.yaml not found"
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "❌ .env not found. Please create it with:"
    echo "   DEEPGRAM_API_KEY=your_key"
    echo "   MINIMAX_API_KEY=your_key"
    exit 1
fi

# Run the app
echo "🎙️  Starting VoiceFlow..."
echo ""
echo "⌨️  Press and hold Cmd+Shift+Space to record"
echo "🎤 Speak your command or text"
echo "📋 Release to process and copy to clipboard"
echo ""
python main.py

