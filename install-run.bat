@echo off
setlocal EnableDelayedExpansion

:: ============================================
:: Waffler - One-Click Install & Run
:: ============================================
:: Double-click this file to install and launch Waffler
:: Works on Windows 10/11 with Python 3.10+
:: ============================================

title Waffler Installer
color 1f

cd /d "%~dp0"

:: ============================================
:: STEP 1: Check Python
:: ============================================
echo.
echo [1/5] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: Python not found!
    echo   ========================
    echo   Please install Python 3.10 or later:
    echo   1. Go to https://www.python.org/downloads/
    echo   2. Download and run the installer
    echo   3. IMPORTANT: Tick "Add Python to PATH"
    echo.
    echo   After installing, double-click this file again.
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo   Found: %PYTHON_VERSION%

:: ============================================
:: STEP 2: Create necessary folders
:: ============================================
echo.
echo [2/5] Setting up folders...
if not exist "%USERPROFILE%\.waffler" mkdir "%USERPROFILE%\.waffler"
if not exist "%USERPROFILE%\.waffler\recordings" mkdir "%USERPROFILE%\.waffler\recordings"
echo   Done.

:: ============================================
:: STEP 3: Create .env file with API key
:: ============================================
echo.
echo [3/5] Setting up configuration...

:: Create .env file if it doesn't exist
if not exist ".env" (
    (
    echo # Waffler Configuration
    echo OPENAI_API_KEY=your_openai_api_key_here
    echo PROMPT_STYLE=smart
    ) > .env
    echo   Created .env template
) else (
    echo   .env already exists, keeping existing config
)

:: ============================================
:: STEP 4: Install dependencies
:: ============================================
echo.
echo [4/5] Installing dependencies...
echo   This may take a few minutes on first run...

:: Install dependencies quietly but show errors
python -m pip install --upgrade pip -q 2>nul
if errorlevel 1 (
    echo   Warning: Could not upgrade pip
)

:: Install all required packages
python -m pip install -q -r requirements_windows.txt 2>nul
if errorlevel 1 (
    echo.
    echo   ERROR: Failed to install dependencies!
    echo   Try running this command in Command Prompt:
    echo   pip install -r requirements_windows.txt
    echo.
    pause
    exit /b 1
)
echo   Dependencies installed successfully

:: ============================================
:: STEP 5: Launch VoiceFlow
:: ============================================
echo.
echo [5/5] Starting Waffler...
echo.
echo   ===============================================
echo   Waffler is starting...
echo   ===============================================
echo.

:: Launch the app
python app.py

:: When app closes
echo.
echo Waffler has closed.
pause
