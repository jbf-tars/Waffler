@echo off
setlocal EnableDelayedExpansion

:: ============================================
:: VoiceFlow — Windows Build Script
:: Produces: dist\VoiceFlow\VoiceFlow.exe
:: ============================================

title VoiceFlow Windows Build
color 1f
cd /d "%~dp0"

echo.
echo ============================================
echo   VoiceFlow — Windows Build
echo ============================================
echo.

:: Step 1: Check Python
echo [1/4] Checking Python...
python --version 2>nul
if errorlevel 1 (
    echo ERROR: Python not found! Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

:: Step 2: Install dependencies
echo.
echo [2/4] Installing dependencies...
python -m pip install --upgrade pip -q 2>nul
python -m pip install -q pyinstaller sounddevice numpy pynput pyperclip requests pyyaml python-dotenv openai pywebview pystray Pillow httpx httpcore anyio sniffio certifi h11 idna 2>&1
if errorlevel 1 (
    echo WARNING: Some packages may have failed. Continuing...
)
echo   Done.

:: Step 3: Clean previous build
echo.
echo [3/4] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo   Done.

:: Step 4: Build with PyInstaller
echo.
echo [4/4] Building VoiceFlow.exe...
echo   This may take a few minutes...
echo.
pyinstaller VoiceFlow_windows.spec --noconfirm 2>&1

if errorlevel 1 (
    echo.
    echo BUILD FAILED! Check errors above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   BUILD SUCCESSFUL!
echo   Output: dist\VoiceFlow\VoiceFlow.exe
echo ============================================
echo.

:: Quick sanity check
if exist "dist\VoiceFlow\VoiceFlow.exe" (
    echo   File exists. Size:
    for %%A in ("dist\VoiceFlow\VoiceFlow.exe") do echo   %%~zA bytes
) else (
    echo   WARNING: VoiceFlow.exe not found in dist\VoiceFlow\
)

echo.
pause
