@echo off
setlocal EnableDelayedExpansion

:: ============================================
:: VoiceFlow Launcher for Windows
:: ============================================

cd /d "%~dp0"

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python not found!
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Check required files exist
if not exist "app.py" (
    echo.
    echo ERROR: VoiceFlow files not found.
    echo Please extract ALL files from the VoiceFlow zip.
    pause
    exit /b 1
)

if not exist "config.yaml" (
    echo ERROR: config.yaml missing! Please re-download VoiceFlow.
    pause
    exit /b 1
)

:: Create .env if missing (user will need to add their API key)
if not exist ".env" (
    echo Creating config file...
    (
        echo # VoiceFlow Configuration
        echo # Add your OpenAI API key below (get one at https://platform.openai.com/api-keys)
        echo OPENAI_API_KEY=
        echo PROMPT_STYLE=smart
    ) > .env
    echo.
    echo NOTE: Please edit .env and add your OpenAI API key.
    echo.
)

:: Install dependencies
echo Installing dependencies...
python -m pip install pywebview openai sounddevice numpy pynput pyperclip pyyaml python-dotenv --quiet 2>nul

:: Launch VoiceFlow
echo Starting VoiceFlow...
python app.py

echo.
echo VoiceFlow closed.
pause
