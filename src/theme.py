"""The window's background colour, chosen to match the UI theme.

pywebview paints the native window before the page loads. It was always
created with the old dark theme's background, so with the default Cream
theme every launch flashed dark before the page appeared. The UI keeps its
theme in the web view's localStorage, which Python cannot read before the
window exists, so the UI also saves it to settings.json (Api.set_theme) and
the next launch reads it from there.

"auto" (Settings: System) follows the operating system's light or dark
setting, as the UI does.
"""

from __future__ import annotations

import sys
from typing import Optional

THEMES = ("cream", "dark", "auto")
DEFAULT_THEME = "cream"

# Must equal --bg for each theme in ui/tokens.css (a test checks this).
# 3.15: the website's eggshell and warm night.
BACKGROUNDS = {
    "cream": "#FDFCFC",
    "dark": "#0C0A09",
}


def normalise(theme) -> str:
    theme = str(theme or "").strip().lower()
    return theme if theme in THEMES else DEFAULT_THEME


def os_prefers_dark() -> Optional[bool]:
    """True when the OS is set to dark mode, False for light, None if unknown."""
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            try:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            finally:
                winreg.CloseKey(key)
            return int(value) == 0
        if sys.platform == "darwin":
            from Foundation import NSUserDefaults
            style = NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle")
            return str(style or "").lower() == "dark"
    except Exception:
        return None
    return None


def resolve(theme, prefers_dark: Optional[bool]) -> str:
    """The theme actually shown: 'cream' or 'dark'. An unknown OS setting
    counts as light, the same as the UI's CSS media query."""
    theme = normalise(theme)
    if theme == "auto":
        return "dark" if prefers_dark else "cream"
    return theme


def window_background(theme, prefers_dark: Optional[bool] = None) -> str:
    """Hex colour for the native window behind the page."""
    return BACKGROUNDS[resolve(theme, prefers_dark)]
