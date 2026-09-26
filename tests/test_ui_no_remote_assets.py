"""The app window must not fetch anything from the network when it opens.

Until 3.14.100, ui/style.css loaded Inter and Source Serif 4 with an @import
from fonts.googleapis.com. Every launch sent the user's IP address to Google,
which breaks Waffler's rule that the only network calls are the user's chosen
AI provider and the GitHub update check. The request was also malformed, so
Google answered HTTP 400 and the fonts never loaded at all. The fonts now ship
in ui/fonts/; these tests keep it that way.

Click-to-open links (pywebview.api.open_url(...), window.open(...)) are fine:
they open the user's browser only when clicked and load nothing on start-up.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
UI = os.path.join(ROOT, "ui")
FONTS = os.path.join(UI, "fonts")


def _read(name):
    with open(os.path.join(UI, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _ui_files(ext):
    return [f for f in os.listdir(UI) if f.endswith(ext)]


def test_no_remote_font_services_anywhere_in_the_ui():
    for name in _ui_files(".css") + _ui_files(".html"):
        text = _read(name)
        for host in ("fonts.googleapis.com", "fonts.gstatic.com", "use.typekit.net", "fonts.bunny.net"):
            assert host not in text, f"ui/{name} references {host}; bundle the font in ui/fonts/ instead"


def test_css_loads_nothing_remote():
    """No @import or url() in any stylesheet may point off the machine."""
    for name in _ui_files(".css"):
        css = _read(name)
        imports = re.findall(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", css)
        urls = re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", css)
        remote = [u for u in imports + urls if re.match(r"(?i)(https?:)?//", u)]
        assert not remote, f"ui/{name} loads remote resources on start-up: {remote}"


def test_html_loads_no_remote_scripts_styles_or_images():
    """src= and <link href=> in the UI HTML must be local (hrefs on <a> are just links)."""
    for name in _ui_files(".html"):
        html = _read(name)
        srcs = re.findall(r"""<(?:script|img|source|iframe|video|audio)[^>]*\ssrc\s*=\s*['"]([^'"]+)""", html, re.I)
        links = re.findall(r"""<link[^>]*\shref\s*=\s*['"]([^'"]+)""", html, re.I)
        remote = [u for u in srcs + links if re.match(r"(?i)(https?:)?//", u)]
        assert not remote, f"ui/{name} loads remote resources on start-up: {remote}"


# Since 3.15 the @font-face rules live in tokens.css (colours, type, sizes).
FONT_CSS = "tokens.css"


def test_every_bundled_font_referenced_by_the_css_exists():
    css = _read(FONT_CSS)
    refs = re.findall(r"url\(\s*['\"]?(fonts/[^'\")]+)['\"]?\s*\)", css)
    assert refs, "tokens.css should load the bundled fonts from ui/fonts/"
    for ref in refs:
        assert os.path.isfile(os.path.join(UI, ref)), f"tokens.css references missing file ui/{ref}"
    # Every stylesheet's font files must exist, not just tokens.css's.
    for name in _ui_files(".css"):
        for ref in re.findall(r"url\(\s*['\"]?(fonts/[^'\")]+)['\"]?\s*\)", _read(os.path.basename(name))):
            assert os.path.isfile(os.path.join(UI, ref)), f"{name} references missing file ui/{ref}"


def test_the_intended_families_are_declared_locally():
    css = _read(FONT_CSS)
    faces = re.findall(r"@font-face\s*\{([^}]*)\}", css)
    families = {re.search(r"font-family:\s*['\"]([^'\"]+)", f).group(1) for f in faces}
    assert {"Inter", "Source Serif 4", "Geist", "Geist Mono"} <= families
    italic = [f for f in faces if "Source Serif 4" in f and re.search(r"font-style:\s*italic", f)]
    assert italic, "Source Serif 4 italic is used by the Journal dates and timestamps; it must be bundled"


def test_font_licences_ship_with_the_fonts():
    for licence in ("OFL-Inter.txt", "OFL-SourceSerif4.txt", "OFL-Geist.txt", "OFL-GeistMono.txt"):
        path = os.path.join(FONTS, licence)
        assert os.path.isfile(path), f"missing {licence}"
        with open(path, "r", encoding="utf-8") as fh:
            assert "SIL Open Font License" in fh.read()


def test_both_builds_package_the_whole_ui_folder():
    """ui/fonts/ ships only because each spec bundles ('ui', 'ui') recursively."""
    for spec in ("Waffler_windows.spec", "Waffler_mac.spec"):
        with open(os.path.join(ROOT, spec), "r", encoding="utf-8") as fh:
            assert "('ui', 'ui')" in fh.read(), f"{spec} no longer bundles the ui folder"
