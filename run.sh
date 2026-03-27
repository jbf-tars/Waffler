#!/bin/bash
# Waffler Launcher
# Simple script to run the app

# Ensure we're in the project directory
cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate 2>/dev/null || {
    echo "Virtual environment not found. Run ./setup.sh first"
    exit 1
}

# Check for required files
if [ ! -f "config.yaml" ]; then
    echo "config.yaml not found"
    exit 1
fi

if [ ! -f ".env" ]; then
    echo ".env not found. Copy .env.example to .env and add your API key."
    exit 1
fi

# Run the app
echo "Starting Waffler..."
python app.py
