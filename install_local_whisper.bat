@echo off
REM Install faster-whisper for on-device transcription on Windows (no internet after setup).
REM
REM After installing, add LOCAL_WHISPER=1 to your .env file.
REM First run will download the Whisper "base" model (~150MB) — then it's offline.

echo Installing faster-whisper for Windows...
pip install faster-whisper

echo.
echo Done! Add this line to your .env file:
echo    LOCAL_WHISPER=1
echo.
echo First dictation will download the model (~150MB).
echo After that: roughly 0.5-1.5 seconds per clip on CPU, no internet needed.
echo.
echo Note: if you have an NVIDIA GPU, faster-whisper will use it automatically
echo and will be even faster (under 0.5s).
pause
