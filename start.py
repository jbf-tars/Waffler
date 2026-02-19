"""
VoiceFlow launcher — handles setup then starts the app.
Run: python start.py
"""
import sys
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).parent

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])

def check_and_install():
    required = [
        ("openai",        "openai"),
        ("sounddevice",   "sounddevice"),
        ("numpy",         "numpy"),
        ("pyperclip",     "pyperclip"),
        ("dotenv",        "python-dotenv"),
        ("webview",       "pywebview"),
        ("faster_whisper","faster-whisper"),
        ("pystray",       "pystray"),
        ("PIL",           "Pillow"),
    ]
    for import_name, pip_name in required:
        try:
            __import__(import_name)
        except ImportError:
            print(f"  Installing {pip_name}...")
            try:
                install(pip_name)
            except Exception as e:
                print(f"  WARNING: could not install {pip_name}: {e}")

def setup_env():
    env_file = HERE / ".env"
    if env_file.exists():
        return  # already set up
    print("\nNo .env file found — let's set it up (one time only).\n")
    key = input("Paste your OpenAI API key and press Enter: ").strip()
    if not key:
        print("No key entered. Exiting.")
        sys.exit(1)
    env_file.write_text(
        f"OPENAI_API_KEY={key}\n"
        f"LOCAL_WHISPER=1\n"
    )
    print("Saved!\n")

def main():
    print("\nVoiceFlow — Starting up...")
    print("=" * 40)

    print("\nChecking packages...")
    check_and_install()
    print("Packages OK.\n")

    setup_env()

    # Start system tray on Windows
    if sys.platform == "win32":
        print("Starting system tray...")
        try:
            from src.system_tray import SystemTrayManager
            
            tray = SystemTrayManager(
                on_open_ui=None,  # Will be handled by app
                on_quit=None     # Will exit gracefully
            )
            tray.start()
            print("System tray running.\n")
        except Exception as e:
            print(f"  Warning: Could not start system tray: {e}")

    print("Launching app...")
    print("Hotkey: Right Ctrl + Right Alt\n")

    os.chdir(HERE)
    result = subprocess.run([sys.executable, str(HERE / "app.py")])
    if result.returncode != 0:
        print(f"\nApp exited with error code {result.returncode}")
        print("Screenshot this and send it for help.")
        input("\nPress Enter to close...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nClosed.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close...")
