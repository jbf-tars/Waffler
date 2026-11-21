"""Nobody should have to read to use a screen.

The rules for the words in Settings and the first-run screens:

  1. A section has a title and at most one sentence under it, 15 words at most.
  2. A row has a label of 4 words at most and at most one line under it, of 10
     words at most.
  3. What a careful person may still want to know (what is sent where, limits,
     conditions) is not deleted: it is under one small, collapsed "How it
     works" at the bottom of the section.
  4. The navigation does not say "(beta)".

The page is read as written (ui/index.html); the lines the page's code writes
over it are checked where they are made (ui/logic.js, src/shortcut_settings.py).
A row that asks "are you sure?" before deleting something is the exception:
it appears only when asked for, and says exactly what goes.
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import shortcut_settings as ss  # noqa: E402
from test_ui_logic import js, needs_node, read  # noqa: E402

VOID = {"input", "br", "img", "use", "hr", "meta", "link"}
SECTIONS = ("secAccount", "secGeneral", "secStyle", "secTransforms", "secCommand", "secKeys", "secClaude",
            "secHotkey", "secUsage", "secPrivacy", "secAbout")
STEPS = ("obStepConnect", "obStepPermissions", "obStepTry", "obStepAnywhere", "obStepAccount")
# Shown only after "Delete…" is pressed: each says exactly what goes and what stays.
CONFIRMATIONS = ("acctDeleteConfirm", "historyKeepConfirm", "unsentDeleteConfirm", "resetConfirm",
                 "resetKeysConfirm")


class Page(HTMLParser):
    """Every element with its text and the classes and ids it sits inside."""

    def __init__(self):
        super().__init__()
        self.stack, self.found = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            a = dict(attrs)
            self.stack.append((tag, (a.get("class") or "").split(), a.get("id") or "", []))

    def handle_endtag(self, tag):
        while tag not in VOID and self.stack:
            t, classes, ident, parts = self.stack.pop()
            if t == tag:
                text = " ".join("".join(parts).split())
                self.found.append({"tag": t, "classes": classes, "id": ident, "text": text,
                                   "in_classes": [c for s in self.stack for c in s[1]],
                                   "in_ids": [s[2] for s in self.stack if s[2]]})
                if self.stack:
                    self.stack[-1][3].append(f" {text} ")
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1][3].append(data)


def words(text):
    return [w for w in str(text).split() if re.search(r"[A-Za-z0-9]", w)]


def sentences(text):
    return [s for s in re.split(r"(?<=[.?!])\s+", str(text).strip()) if s]


def elements():
    page = Page()
    page.feed(read("index.html"))
    return page.found


def in_view(el):
    """Not inside a "How it works" box, and not a row that only asks before deleting."""
    inside = el["in_classes"] + el["classes"]
    if "how-body" in inside:
        return False
    return not any(c in el["in_ids"] + [el["id"]] for c in CONFIRMATIONS)


def home_of(el):
    return next((i for i in el["in_ids"] if i in SECTIONS + STEPS), "")


# ── the page as written ──────────────────────────────────────────────────────

def test_a_section_has_at_most_one_sentence_under_its_title():
    seen = set()
    for el in elements():
        if "pdesc" in el["classes"] and home_of(el) in SECTIONS:
            seen.add(home_of(el))
            assert len(sentences(el["text"])) == 1, el["text"]
            assert len(words(el["text"])) <= 15, el["text"]
    assert {"secAccount", "secGeneral", "secStyle", "secTransforms", "secCommand", "secKeys", "secClaude",
            "secHotkey", "secUsage"} <= seen


def test_a_note_under_a_heading_is_one_short_sentence():
    for el in elements():
        if "s-note" in el["classes"] and home_of(el) in SECTIONS and in_view(el):
            assert len(sentences(el["text"])) <= 1 and len(words(el["text"])) <= 15, el["text"]


def test_a_row_has_a_label_of_four_words_at_most():
    labels = [el for el in elements() if "row-t" in el["classes"] and home_of(el) and in_view(el)]
    assert len(labels) > 40
    for el in labels:
        assert len(words(el["text"])) <= 4, (home_of(el), el["text"])


def test_a_row_has_at_most_one_line_of_ten_words_under_it():
    lines = [el for el in elements() if "row-d" in el["classes"] and home_of(el) and in_view(el)]
    assert len(lines) > 25
    for el in lines:
        assert len(words(el["text"])) <= 10, (home_of(el), el["text"])
    # Most rows need no line at all: the two features nobody was trying have none at the top.
    for prefix in ("transforms", "command"):
        for ident in (f"{prefix}Row", f"{prefix}ShortcutRow"):
            assert not [el for el in lines if ident in el["in_ids"] and el["text"]], ident


def test_a_first_run_step_has_one_short_sentence_under_its_title():
    ledes = [el for el in elements() if "ob-lede" in el["classes"] and home_of(el) in STEPS]
    assert len(ledes) >= 8
    for el in ledes:
        if el["id"] == "obAcctCodeDesc":      # written by the page: which address the code went to
            continue
        # (On the first screen two alternatives share one element, one shown at a time: 7 and 12 words.)
        assert len(words(el["text"])) <= 19, el["text"]
    first = read("index.html")
    for said in ('<span data-rec="local">One download, then hold <span class="ob-hk-name">Fn</span> and talk.</span>',
                 '<span data-rec="key" hidden>A free Groq key is the quick way to start on this computer.</span>',
                 '<p class="ob-lede" data-when="start">Groq, a free AI service, turns your voice into tidy text.</p>',
                 '<p class="ob-lede">Allow all three and setup carries on by itself.</p>',
                 '<p class="ob-lede">Say it however it comes out, then let go.</p>'):
        assert said in first, said


# ── How it works ─────────────────────────────────────────────────────────────

def test_how_it_works_is_one_collapsed_disclosure_at_the_bottom_of_a_section():
    html = read("index.html")
    for ident in SECTIONS:
        start = html.index(f'id="{ident}"')
        sec = html[start:html.index("</section>", start)]
        if ident == "secUsage":
            assert sec.count(">How it works<") == 0     # numbers; its estimate has its own "How this is estimated"
            continue
        assert sec.count(">How it works<") == 1, ident
        button = sec[sec.rindex("<button", 0, sec.index(">How it works<")):sec.index(">How it works<")]
        assert 'aria-expanded="false"' in button and "link-btn how-btn" in button, ident
        box = re.search(r'aria-controls="(\w+)"', button).group(1)
        assert re.search(rf'<div class="[^"]*how-body[^"]*" id="{box}" hidden>', sec), ident
        after = sec[sec.index(f'id="{box}"'):]
        assert "<h2" not in after and 'class="row"' not in after, f"{ident}: nothing comes after it"


def test_what_was_taken_out_of_view_is_still_said_in_full():
    html = " ".join(re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", " ", read("index.html"), flags=re.S)).split())
    for said in (
        # General
        "each recording includes the half-second before you press, so your first word isn't clipped.",
        "The microphone ignores a fraction of a second around each one, so a tone is never recorded.",
        # Style
        "Nothing is sent for clean-up.",
        "Waffler looks at the text before your cursor, only to check your signature isn't already there. "
        "It isn't kept or logged.",
        # Keys and providers
        "your speech is sent to Groq or OpenAI with the key you add.",
        "Waffler tries your keys in order and uses the first that answers.",
        # AI apps
        "It can only read. It cannot change or delete anything.",
        "These reach this computer only.",
        # Shortcuts
        "The key of a shortcut doesn't reach the app in front, so it never types a letter or runs that app's "
        "own shortcut.",
        # Privacy and data
        "Everything stays on this computer, except what you say while you hold the keys, and the half-second "
        "before. That goes straight to the providers you set up, on your own key.",
        "It never includes your keys.",
        "It never reads a password field.",
        "The recording is not kept.",
        "A zip on your Desktop with no keys, no transcripts and no signature text.",
        # About
        "No account, no subscription and no Waffler server:",
        # First run
        "The free download does not tidy them or write meeting notes (a meeting still gets its full transcript).",
        "It's your own account, so your words go from this computer to Groq and nowhere else.",
    ):
        assert said in html, said


def test_the_navigation_says_names_only():
    html = read("index.html")
    nav = html[html.index('aria-label="Settings sections"'):]
    nav = nav[:nav.index("</nav>")]
    labels = re.findall(r">([^<]+)</button>", nav)
    assert labels == ["Account", "General", "Style", "Transforms", "Command mode", "Keys and providers", "AI apps",
                      "Shortcuts", "Usage", "Privacy and data", "About"], "the same sections in the same order"
    assert "beta" not in nav.lower()
    assert html.count('<span class="beta-tag">Beta</span>') == 2


# ── the lines the code writes ────────────────────────────────────────────────

@needs_node
def test_the_lines_the_page_writes_under_a_row_are_short_too():
    lines = [
        js("L.languageDesc('en', 'English')"), js("L.languageDesc('auto', 'Auto-detect')"),
        js("L.cleanupView({})")["desc"], js("L.cleanupView({cleanup: 'off'})")["desc"],
        js("L.clipboardAfterPasteView(true, 'restore', true)")["desc"],
        js("L.clipboardAfterPasteView(false, 'restore', true)")["desc"],
        js("L.doubleTapView({})")["desc"],
        js("L.transformShortcutLine({hotkey: []})"), js("L.commandShortcutLine({hotkey: []})"),
        js("L.WRITING_ABOUT"), js("L.tryItNeeds(null)")["line"],
        js("L.micStatus({ok: true, ranking: [], system: 'MacBook Pro Microphone'})")["desc"],
    ] + [r["desc"] for r in js("L.keyRows(['groq','openai','cerebras'], {})")]
    for line in lines:
        assert line and len(words(line)) <= 10, line


def test_every_shortcut_row_is_a_short_label_and_one_short_line():
    for platform in (ss.ah.MAC, ss.ah.WINDOWS):
        rows = ss.rows({}, platform)
        assert len(rows) >= 7
        for row in rows:
            assert len(words(row["label"])) <= 4, row["label"]
            assert len(words(row["description"])) <= 10, row["description"]
