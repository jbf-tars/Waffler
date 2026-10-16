"""
Active App Detection for Waffler
Detects which app is in focus and suggests appropriate prompt style.
"""

import sys
import platform

if sys.platform == "darwin":
    import subprocess


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
        return {"name": "Unknown", "bundleId": "", "suggested_style": "normal"}


def frontmost_app_name() -> str:
    """Name of the frontmost app, cheaply enough to call when the hotkey goes
    down ('' when unknown). No subprocess: ``get_active_app`` shells out to
    osascript on macOS, which is far too slow for that moment. The Journal
    records it as the dictation's app (src/history_meta.py)."""
    try:
        if sys.platform == "darwin":
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return str(app.localizedName() or "") if app is not None else ""
        if sys.platform == "win32":
            name = _get_active_app_windows().get("name") or ""
            return "" if name == "Unknown" else name
    except Exception:
        pass
    return ""


def frontmost_window_class() -> str:
    """The Windows class name of the window in front ('' elsewhere, and when
    unknown). A terminal is told from other windows by it as well as by the
    program's name: a terminal inside an IDE or a new terminal program has a
    name nobody listed, but the class of its window gives it away."""
    try:
        if sys.platform == "win32":
            api = _Win32()
            hwnd = api.foreground_window()
            return api.window_class(hwnd) if hwnd else ""
    except Exception:
        pass
    return ""


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
        return {"name": "Unknown", "bundleId": "", "suggested_style": "normal"}


# The least a program may ask for to read another program's file name, and
# the right that works across integrity levels (an elevated program, a
# protected one). The older pair of rights (query information and read memory)
# is refused for exactly the windows that matter here.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _Win32:
    """The few Win32 calls the window lookup makes. Imported on use, so this
    module loads anywhere, and replaced by a stand-in in the tests. The
    libraries are this class's own (``ctypes.WinDLL``), so the prototypes set
    here are not set on the process-wide Windows libraries that other code
    shares. ``loader`` is how a library is opened (the tests give a fake)."""

    def __init__(self, loader=None):
        self._loader = loader
        self._dlls = {}

    def _dll(self, name: str):
        if name not in self._dlls:
            import ctypes
            self._dlls[name] = (self._loader or ctypes.WinDLL)(name)
        return self._dlls[name]

    def foreground_window(self) -> int:
        import ctypes
        user32 = self._dll("user32")
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        return int(user32.GetForegroundWindow() or 0)

    def window_pid(self, hwnd: int) -> int:
        import ctypes
        user32 = self._dll("user32")
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return int(pid.value)

    def window_class(self, hwnd: int) -> str:
        import ctypes
        user32 = self._dll("user32")
        user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
        return buf.value

    def process_image_path(self, pid: int) -> str:
        """The full path of the program with process id ``pid``, or '' when it
        cannot be read."""
        import ctypes
        from ctypes import wintypes
        kernel32 = self._dll("kernel32")
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)


def _get_active_app_windows(api=None) -> dict:
    """The program that owns the window in front, by its file name without
    ``.exe``. When that cannot be read the answer is "Unknown": never the
    window's title. A title is the person's own text (a document's name, a
    file in a folder), and this name is stored in the Journal."""
    unknown = {"name": "Unknown", "bundleId": "", "suggested_style": "normal"}
    try:
        api = api or _Win32()
        hwnd = api.foreground_window()
        if not hwnd:
            return unknown
        path = api.process_image_path(api.window_pid(hwnd))
        name = path.replace("/", "\\").split("\\")[-1] if path else ""
        if name.lower().endswith(".exe"):
            name = name[:-4]
        return _map_app_to_style(name) if name else unknown
    except Exception as e:
        print(f"[app_detection] Windows error: {type(e).__name__}")
        return unknown


def _map_app_to_style(app_name: str) -> dict:
    """Map application name to detected app info."""
    return {"name": app_name, "bundleId": "", "suggested_style": "normal"}


# Test
if __name__ == "__main__":
    import time
    print("Testing app detection...")
    for i in range(3):
        app = get_active_app()
        print(f"Active app: {app}")
        time.sleep(1)
