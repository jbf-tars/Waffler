"""
Active App Detection for Waffler
Detects which app is in focus and suggests appropriate prompt style.
"""

import sys
import platform

if sys.platform == "darwin":
    import subprocess
elif sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


def get_active_app() -> dict:
    """
    Detect the currently active/frontmost application.
    Returns: {"name": str, "bundleId": str, "suggested_style": str}
    """
    system = platform.system()
    
    if system == "Darwin":
        return _get_active_app_macos()
    elif system == "Windows":
        return _get_active_app_windows()
    else:
        return {"name": "Unknown", "bundleId": "", "suggested_style": "smart"}


def _get_active_app_macos() -> dict:
    """Use AppleScript to get frontmost app on macOS."""
    try:
        script = '''
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            set appPath to path of frontApp
        end tell
        return appName
        '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2
        )
        app_name = result.stdout.strip() if result.returncode == 0 else "Unknown"
        return _map_app_to_style(app_name)
    except Exception as e:
        print(f"[app_detection] macOS error: {e}")
        return {"name": "Unknown", "bundleId": "", "suggested_style": "smart"}


def _get_active_app_windows() -> dict:
    """Use Win32 API to get foreground window app on Windows."""
    try:
        # Get foreground window handle
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return {"name": "Unknown", "bundleId": "", "suggested_style": "smart"}
        
        # Get window title (process name)
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return {"name": "Unknown", "bundleId": "", "suggested_style": "smart"}
        
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
        window_title = buffer.value
        
        # Try to get process name
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        # Get process name
        PROCESS_QUERY_INFORMATION = 0x0401
        PROCESS_VM_READ = 0x0010
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid.value)
        
        if handle:
            try:
                exe_buffer = ctypes.create_unicode_buffer(260)
                if kernel32.GetModuleFileNameExW(handle, 0, exe_buffer, 260):
                    exe_path = exe_buffer.value
                    app_name = exe_path.split("\\")[-1].replace(".exe", "")
                else:
                    app_name = window_title[:50] if window_title else "Unknown"
            finally:
                kernel32.CloseHandle(handle)
        else:
            app_name = window_title[:50] if window_title else "Unknown"
        
        return _map_app_to_style(app_name)
        
    except Exception as e:
        print(f"[app_detection] Windows error: {e}")
        return {"name": "Unknown", "bundleId": "", "suggested_style": "smart"}


def _map_app_to_style(app_name: str) -> dict:
    """Map application name to suggested prompt style."""
    app_lower = app_name.lower()
    
    # App category mappings
    CASUAL_APPS = [
        "slack", "teams", "discord", "whatsapp", "telegram", "signal",
        "messenger", "messages", "skype", "zoom", "bluejeans", "webex"
    ]
    
    AGENTIC_APPS = [
        "visual studio code", "vscode", "cursor", "terminal", "iterm",
        "powershell", "cmd", "git", "github desktop", "figma"
    ]
    
    PROSE_APPS = [
        "notes", "textedit", "pages", "word", "google docs", "notion",
        "obsidian", "bear", "simplenote", "typora", "sublime", "atom"
    ]
    
    # Check matches
    for app in CASUAL_APPS:
        if app in app_lower:
            return {"name": app_name, "bundleId": "", "suggested_style": "adhd_ramble"}
    
    for app in AGENTIC_APPS:
        if app in app_lower:
            return {"name": app_name, "bundleId": "", "suggested_style": "agentic_engineering"}
    
    for app in PROSE_APPS:
        if app in app_lower:
            return {"name": app_name, "bundleId": "", "suggested_style": "smart"}
    
    # Default
    return {"name": app_name, "bundleId": "", "suggested_style": "smart"}


# Test
if __name__ == "__main__":
    import time
    print("Testing app detection...")
    for i in range(3):
        app = get_active_app()
        print(f"Active app: {app}")
        time.sleep(1)
