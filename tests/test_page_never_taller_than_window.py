"""Found on Windows: with Settings > Keys and providers > Advanced open, a
hidden screen-reader note (absolutely positioned) sat below the window, the
page became scrollable, and in dark mode a light strip showed at the bottom
because <html> painted the light background."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"


def rule(css: str, selector: str) -> str:
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_html_paints_no_background_of_its_own():
    css = (UI / "tokens.css").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^html\s*,\s*body\s*\{[^}]*background", css)
    assert not re.search(r"(?m)^html\s*\{[^}]*background", css)
    assert any("background: var(--bg)" in body
               for body in re.findall(r"(?m)^body\s*\{([^}]*)\}", css))


def test_the_settings_scroller_holds_its_hidden_notes():
    assert "position: relative" in rule((UI / "style.css").read_text(encoding="utf-8"), ".scontent")
