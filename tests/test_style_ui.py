"""Settings, Style: what the window decides, and how it is wired (S6).

The section is in the Atelier design (a side-nav entry, sheets of rows, a
switch, selects, a text box), keyboard-reachable, and holds no copy of the
style rules: the example under each choice is made by the app
(``preview_style``, which runs src/style_post.py on a sample sentence). Its
small decisions live in ui/logic.js and run here in Node; the rest is read
from the source, as the other window tests do.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import style_layer  # noqa: E402
import style_post  # noqa: E402
from test_ui_logic import code_only, js, needs_node, read  # noqa: E402

KINDS = ["personal", "work", "email", "other"]


def section():
    html = read("index.html")
    start = html.index('id="secStyle"')
    return html[start:html.index("</section>", start)]


# ── the choices are the engine's ─────────────────────────────────────────────

@needs_node
def test_the_window_offers_the_same_kinds_and_choices_as_the_app():
    assert js("L.STYLE_KINDS") == list(style_layer.CATEGORIES) == KINDS
    assert js("L.STYLE_CHOICES") == {k: list(v) for k, v in style_layer.STYLE_CHOICES.items()}
    assert js("L.SIGNATURE_MAX") == style_layer.SIGNATURE_MAX_CHARS


@needs_node
def test_every_choice_has_a_label_and_a_description():
    labels = js("L.STYLE_LABELS")
    assert set(labels) == {"auto"} | set(style_post.STYLES)
    for style in labels:
        about = js(f"L.styleAbout({json.dumps(style)})")
        assert about.endswith(".") and about
        assert "—" not in about and "–" not in about
    assert js("L.styleAbout('nonsense')") == js("L.styleAbout('auto')")


@needs_node
def test_a_row_for_each_kind_with_its_choices():
    rows = js("L.styleRows({})")
    assert [r["kind"] for r in rows] == KINDS
    assert [r["title"] for r in rows] == ["Personal messages", "Work chat", "Email", "Everything else"]
    for r in rows:
        assert r["value"] == "auto"
        assert [c["value"] for c in r["choices"]] == list(style_layer.STYLE_CHOICES[r["kind"]])
        assert r["choices"][0] == {"value": "auto", "label": "Automatic"}
        assert r["about"] == js("L.styleAbout('auto')")
    assert [c["label"] for c in rows[0]["choices"]] == ["Automatic", "Formal", "Casual", "Very casual"]
    assert [c["label"] for c in rows[1]["choices"]] == ["Automatic", "Formal", "Casual", "Excited"]


@needs_node
def test_the_saved_choice_is_selected_and_junk_is_automatic():
    saved = {"style_by_category": {"personal": "very_casual", "work": "excited", "email": "casual", "other": "formal"}}
    assert [r["value"] for r in js(f"L.styleRows({json.dumps(saved)})")] == ["very_casual", "excited", "casual", "formal"]
    # A style the kind does not offer, or junk, reads as the app reads it: automatic.
    junk = {"style_by_category": {"personal": "excited", "work": "very_casual", "email": 5, "other": None}}
    assert [r["value"] for r in js(f"L.styleRows({json.dumps(junk)})")] == ["auto"] * 4
    for bad in ("null", "undefined", "5", "'x'", "[]", "{style_by_category: 'x'}"):
        assert [r["value"] for r in js(f"L.styleRows({bad})")] == ["auto"] * 4


@needs_node
def test_the_apps_named_under_each_kind_are_ones_waffler_knows():
    import app_categories as ac
    known = {
        "personal": [n.lower() for n in ac.PERSONAL_NAMES],
        "work": [n.lower() for n in ac.WORK_NAMES],
        "email": [n.lower() for n in ac.EMAIL_NAMES] + ["gmail"],
    }
    for r in js("L.styleRows({})"):
        if r["kind"] == "other":
            continue
        for app in re.split(r",\s*(?:and\s+)?|\s+and\s+", r["apps"]):
            name = app.split(" in ")[0].strip().lower()
            assert name in known[r["kind"]], (r["kind"], name)


# ── clean-up On or Off ───────────────────────────────────────────────────────

@needs_node
def test_clean_up_is_on_unless_saved_as_off():
    assert js("L.cleanupView({}).on") is True
    assert js("L.cleanupView(null).on") is True
    assert js("L.cleanupView({cleanup: 'on'}).on") is True
    assert js("L.cleanupView({cleanup: 'off'}).on") is False
    for junk in ("'OFF'", "false", "0", "''", "null", "{}"):
        assert js(f"L.cleanupView({{cleanup: {junk}}}).on") is True, junk


@needs_node
def test_the_descriptions_say_what_off_means():
    on, off = js("L.cleanupView({})"), js("L.cleanupView({cleanup: 'off'})")
    assert on["note"] == "" and off["note"]
    assert "exactly what it heard" in off["desc"] and "Vocabulary" in off["desc"] and "Snippets" in off["desc"]
    assert "Styles aren't used" in off["desc"] and "styles" in off["note"]
    assert "an email signature still is" in off["desc"]
    assert "Nothing is sent for clean-up" in off["desc"]
    for text in (on["desc"], on["note"], off["desc"], off["note"]):
        assert "—" not in text and "–" not in text


@needs_node
def test_the_toasts_are_plain_sentences():
    lines = [js("L.cleanupSaved(true)"), js("L.cleanupSaved(false)"), js("L.signatureSaved(true)"),
             js("L.signatureSaved(false)")] + [js(f"L.styleSaved({json.dumps(k)}, 'casual')") for k in KINDS]
    assert js("L.styleSaved('personal', 'very_casual')") == "Personal messages: Very casual."
    assert js("L.styleSaved('work', 'excited')") == "Work chat: Excited."
    assert js("L.styleSaved('moon', 'casual')") == "Style saved."
    for line in lines:
        assert line.endswith(".") and "—" not in line and "–" not in line
        assert "undefined" not in line and "null" not in line


# ── the signature box ────────────────────────────────────────────────────────

@needs_node
def test_the_signature_view_counts_and_trims():
    v = js("L.signatureView({enabled: true, text: 'Best'}, '  Best,\\r\\nJames \\n')")
    assert v["text"] == "Best,\nJames" and v["chars"] == 11 and v["over"] is False
    assert v["counter"] == "11 of 500" and v["enabled"] is True and v["hint"] == ""
    assert js("L.signatureView({}, undefined).counter") == "0 of 500"
    assert js("L.signatureView({enabled: false, text: 'Hi'}).text") == "Hi"


@needs_node
def test_the_limit_is_exact_and_too_long_is_said_not_cut():
    ok = js("L.signatureView({}, 'x'.repeat(500))")
    assert ok["over"] is False and ok["counter"] == "500 of 500"
    over = js("L.signatureView({}, 'x'.repeat(501))")
    assert over["over"] is True and over["counter"] == "1 over the limit of 500" and over["chars"] == 501
    assert js("L.signatureView({}, ' ' + 'x'.repeat(500) + ' ')")["over"] is False, "spaces round it do not count"
    assert js("L.signatureView({}, '\\u00e9'.repeat(500))")["over"] is False


@needs_node
def test_an_emoji_counts_as_one_like_the_app_counts_it():
    assert js("L.signatureView({}, '\\u{1F600}'.repeat(500))")["over"] is False
    assert len("\U0001F600" * 500) == 500


@needs_node
def test_a_switch_that_is_on_with_no_text_says_so():
    v = js("L.signatureView({enabled: true, text: ''})")
    assert v["hint"] == "Type your signature, or no signature will be added."
    assert js("L.signatureView({enabled: false, text: ''})")["hint"] == ""
    assert js("L.signatureView({enabled: true, text: 'x'})")["hint"] == ""


# ── the section is in the page, and wired ────────────────────────────────────

def test_style_is_a_section_of_settings_between_general_and_keys():
    html = read("index.html")
    assert "data-sec=\"style\" onclick=\"showSettingsSection('style')\">Style</button>" in html
    start = html.index('aria-label="Settings sections"')
    nav = html[start:html.index("</nav>", start)]
    order = re.findall(r'data-sec="(\w+)"', nav)
    assert order == ["general", "style", "keys", "hotkey", "usage", "privacy", "about"]
    assert 'id="secStyle" data-sec="style" hidden' in html
    app = code_only(read("app.js"))
    assert "const SETTINGS_SECTIONS = ['general', 'style', 'keys', 'hotkey', 'usage', 'privacy', 'about'];" in app


def test_every_control_has_a_name_and_a_tab_stop():
    sec = section()
    for ident in ("cleanupToggle", "signatureToggle", "signatureText", *[f"styleSelect-{k}" for k in KINDS]):
        assert f'<label for="{ident}">' in sec, ident
    assert sec.count('class="tog" role="switch"') == 2
    assert "tabindex" not in sec, "native controls: one Tab stop each, in reading order"
    assert sec.count("<select") == 4 and sec.count("<textarea") == 1
    for k in KINDS:
        assert f'aria-describedby="styleAbout-{k} styleEg-{k}"' in sec
    assert 'aria-describedby="cleanupDesc"' in sec and 'aria-describedby="signatureDesc"' in sec


def test_the_section_has_the_sheets_and_the_note():
    sec = section()
    assert sec.index("Clean-up</h2>") < sec.index("Style for each kind of app</h2>") < sec.index("Email signature</h2>")
    assert 'class="sheet"' in sec and sec.count('class="sheet"') == 3
    assert "Applies to new dictations. Works in English and other Latin-script languages." in sec
    assert '<h1 class="ptitle">Style</h1>' in sec


def test_the_section_says_what_the_signature_does_and_does_not_keep():
    sec = " ".join(section().split())
    assert "Waffler looks at the text before your cursor, only to check your signature isn't already there." in sec
    assert "It isn't kept or logged." in sec
    assert "ten words or more" in sec and style_layer.SIGNATURE_MIN_WORDS == 10


def test_sentence_case_no_dashes_no_jargon_in_the_section():
    sec = section()
    text = re.sub(r"<[^>]+>", " ", sec)
    assert "—" not in sec and "–" not in sec
    for word in ("Whisper", "ISO", "locale", "API", "model", "token", "LLM", "JSON", "settings.json", "provider",
                 "Accessibility", "pywebview", "prompt"):
        assert word not in text, word
    for h in re.findall(r"<h[12][^>]*>([^<]+)</h[12]>", sec):
        assert h[0].isupper() and h[1:] == h[1:].lower() or h in ("Clean-up",), h
    for label in re.findall(r"<label[^>]*>([^<]+)</label>", sec):
        assert label[0].isupper() and not label.isupper(), label
        assert re.sub(r"\b(Waffler|I)\b", "", label[1:]) == re.sub(r"\b(Waffler|I)\b", "", label[1:]).lower(), label


def test_the_handlers_exist_and_save_through_one_path():
    app = code_only(read("app.js"))
    sec = section()
    for handler in re.findall(r'on(?:change|input|blur)="(\w+)\(', sec):
        assert f"function {handler}(" in app or f"async function {handler}(" in app, handler
    assert "saveSetting('style_by_category', { [kind]: value })" in app
    assert "saveSetting('cleanup', on ? 'on' : 'off')" in app
    assert "saveSetting('email_signature', { text: v.text })" in app
    assert "saveSetting('email_signature', { enabled: !!on })" in app
    # A refused save puts the control back.
    assert "sel.value = previous" in app
    assert "tog.checked = !on" in app
    # Drawn from the last get_settings() answer, once Settings has loaded.
    assert "renderStyleSection();" in app[app.index("async function loadSettings()"):app.index("function renderCleanupRow")]


def test_the_example_is_the_apps_not_a_second_copy_of_the_rules():
    app = code_only(read("app.js"))
    logic = code_only(read("logic.js"))
    assert "pywebview.api.preview_style(style, kind)" in app
    block = logic[logic.index("const STYLE_KINDS"):logic.index("const METER_CELLS")]
    for rule in ("toLowerCase", "toUpperCase", ".replace(/\\.$", "endsWith('.')", "'!'"):
        assert rule not in block, rule
    assert "function styleRows" in block and "preview" not in block


def test_a_stale_example_is_not_shown_for_a_newer_choice():
    app = code_only(read("app.js"))
    fn = app[app.index("async function refreshStyleExample"):app.index("async function onStyleChange")]
    assert "sel.value !== style" in fn and "return;" in fn


def test_the_signature_is_saved_after_typing_stops_and_never_when_too_long():
    app = code_only(read("app.js"))
    assert "_sigTimer = setTimeout(commitSignature, 800)" in app
    fn = app[app.index("async function commitSignature"):app.index("async function setSignatureEnabled")]
    assert "if (v.over || v.text === (kept.text || '')) return;" in fn


def test_the_toasts_are_made_by_the_logic_file():
    app = code_only(read("app.js"))
    for call in ("WL.cleanupSaved(", "WL.styleSaved(", "WL.signatureSaved("):
        assert call in app, call


def test_the_style_css_uses_tokens_only():
    css = read("style.css")
    start = css.index("/* Settings, Style:")
    block = css[start:css.index(".row-ctl-col", start)]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), "colours come from tokens"
    assert "--faint" not in block, "never for readable text"
    assert "animation" not in block and "transition" not in block
