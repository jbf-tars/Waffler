@echo off
REM ====================================================================
REM  Natter — Windows Setup Script
REM  Run this ONCE to install all Python dependencies.
REM
REM  Prerequisites:
REM    • Python 3.10+ installed from https://www.python.org/downloads/
REM      (tick "Add python.exe to PATH" during install)
REM    • Internet connection
REM ====================================================================

echo ============================================================
echo  Natter Windows Setup
echo ============================================================
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure you tick "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

echo Python found:
python --version
echo.

REM Upgrade pip first
echo Upgrading pip...
python -m pip install --upgrade pip
echo.

REM Install dependencies
echo Installing Natter dependencies...
python -m pip install -r requirements_windows.txt
echo.

REM Check tkinter (built-in on standard Python install)
python -c "import tkinter; print('tkinter OK:', tkinter.TkVersion)" 2>nul
if errorlevel 1 (
    echo WARNING: tkinter not found.
    echo Please reinstall Python from python.org and ensure tkinter is included.
)
echo.

REM Check pywebview backend info
echo Checking pywebview...
python -c "import webview; print('pywebview OK:', webview.__version__)" 2>nul
if errorlevel 1 (
    echo WARNING: pywebview import failed. Try: pip install pywebview
)
echo.

echo ============================================================
echo  Setup complete!
echo  Next: double-click run_windows.bat to launch Natter.
echo ============================================================
pause
