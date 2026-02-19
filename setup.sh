#!/bin/bash
# VoiceFlow Setup Script

echo "🎙️  VoiceFlow Setup"
echo "=================="

# Install all dependencies
echo ""
echo "📦 Installing dependencies..."
pip3 install -r requirements.txt

# Check for .env
if [ ! -f ".env" ]; then
    echo ""
    echo "⚙️  Creating .env from example..."
    cp .env.example .env
    echo "✏️  Edit .env and add your OpenAI API key, then run: python3 main.py"
else
    echo ""
    echo "✅ .env already exists"
    echo ""
    echo "🚀 Ready! Run: python3 main.py"
fi
