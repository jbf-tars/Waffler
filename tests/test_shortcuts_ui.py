"""Settings, Shortcuts: what the window decides, and how it is wired (S11a).

The page is in the Atelier design (a side-nav entry, a sheet of rows, keycaps,
the shared hotkey dialog), keyboard-reachable, and holds no rule of its own:
the rows, the rules and the refused combinations come from the app
(src/shortcut_settings.py), and the dialog asks the app whether the keys pass.
Its small decisions run in Node; the rest is read from the source, as the other
window tests do.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys as ah  # noqa: E402
import shortcut_settings as ss  # noqa: E402
from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


def section():
    html = read("index.html")
    start = html.index('id="secHotkey"')
    return html[start:html.index("</section>", start)]


def no_dashes(text):
    return "—" not in text and "–" not in text


# ── which key a keyboard event is ────────────────────────────────────────────

@needs_node
def test_a_windows_letter_is_the_key_the_layout_types_because_the_hook_hears_it_that_way():
    """An AZERTY keyboard: the physical key labelled A is `KeyQ` and types "a".
    The keyboard hook reports it as virtual key A, so the name saved is "a"."""
    assert js("L.captureKeyId({key: 'a', code: 'KeyQ'}, false)") == "a"
    assert js("L.captureKeyId({key: 'z', code: 'KeyY'}, false)") == "z", "QWERTZ"
    assert js("L.captureKeyId({key: 'A', code: 'KeyA'}, false)") == "a", "Shift or Caps Lock"
    assert js("L.captureKeyId({key: 'p', code: 'KeyR'}, false)") == "p", "Dvorak"


@needs_node
def test_a_mac_letter_is_the_physical_key_because_the_tap_hears_it_that_way():
    assert js("L.captureKeyId({key: 'a', code: 'KeyQ'}, true)") == "q"
    assert js("L.captureKeyId({key: '˚', code: 'KeyK'}, true)") == "k", "Option changes what a Mac key types"
    assert js("L.captureKeyId({key: 'z', code: 'KeyY'}, true)") == "y"


@needs_node
def test_a_key_that_types_no_latin_letter_is_its_physical_position_on_windows():
    assert js("L.captureKeyId({key: 'ф', code: 'KeyA'}, false)") == "a", "Cyrillic"
    assert js("L.captureKeyId({key: '€', code: 'KeyE'}, false)") == "e", "AltGr types a symbol"
    assert js("L.captureKeyId({key: 'Dead', code: 'KeyN'}, false)") == "n"


@needs_node
def test_digits_and_function_keys_are_the_same_on_both():
    for mac in ("true", "false"):
        assert js(f"L.captureKeyId({{key: '&', code: 'Digit1'}}, {mac})") == "1", "an AZERTY top row"
        assert js(f"L.captureKeyId({{key: '7', code: 'Digit7'}}, {mac})") == "7"
        assert js(f"L.captureKeyId({{key: 'F16', code: 'F16'}}, {mac})") == "f16"
        assert js(f"L.captureKeyId({{key: 'F1', code: 'F1'}}, {mac})") == "f1"
        assert js(f"L.captureKeyId({{key: 'F24', code: 'F24'}}, {mac})") == "f24"
        assert js(f"L.captureKeyId({{key: 'F25', code: 'F25'}}, {mac})") is None


@needs_node
def test_modifiers_are_named_as_the_platform_names_them():
    mac = {"Control": "control", "Alt": "option", "Shift": "shift", "Meta": "cmd", "OS": "cmd"}
    win = {"Control": "ctrl", "Alt": "alt", "Shift": "shift", "Meta": "win", "OS": "win"}
    for key, name in mac.items():
        assert js(f"L.captureKeyId({{key: {json.dumps(key)}, code: ''}}, true)") == name
    for key, name in win.items():
        assert js(f"L.captureKeyId({{key: {json.dumps(key)}, code: ''}}, false)") == name


@needs_node
def test_esc_is_a_key_to_record_only_in_the_cancel_dialog_and_space_only_for_the_checked_shortcuts():
    assert js("L.captureKeyId({key: 'Escape', code: 'Escape'}, true, 'cancel')") == "esc"
    assert js("L.captureKeyId({key: 'Escape', code: 'Escape'}, true, 'paste_last')") is None
    assert js("L.captureKeyId({key: 'Escape', code: 'Escape'}, true)") is None
    for target in ("transform", "command", "paste_last", "copy_last"):
        assert js(f"L.captureKeyId({{key: ' ', code: 'Space'}}, true, {json.dumps(target)})") == "space"
    assert js("L.captureKeyId({key: ' ', code: 'Space'}, true)") is None, "the dictation hotkey never recorded it"
    assert js("L.captureKeyId({key: ' ', code: 'Space'}, true, 'cancel')") is None


@needs_node
@pytest.mark.parametrize("ev", ["null", "undefined", "{}", "{key: 5, code: 7}", "{key: 'Tab', code: 'Tab'}",
                                "{key: 'ArrowUp', code: 'ArrowUp'}", "'x'"])
def test_an_odd_event_is_no_key(ev):
    assert js(f"L.captureKeyId({ev}, true)") is None and js(f"L.captureKeyId({ev}, false)") is None


@needs_node
def test_every_name_the_dialog_can_record_is_a_name_the_apps_listeners_know():
    """The dialog's names and the listeners' tables agree (action_hotkeys.py)."""
    for c in "abcdefghijklmnopqrstuvwxyz":
        assert js(f"L.captureKeyId({{key: {json.dumps(c)}, code: 'Key{c.upper()}'}}, false)") == c
        assert ah._WINDOWS_KEY_FOR_VK[ord(c.upper())] == c, "the Windows hook names the virtual key the same"
        assert js(f"L.captureKeyId({{key: {json.dumps(c)}, code: 'Key{c.upper()}'}}, true)") == c
        assert ah._MAC_KEY_FOR_CODE[ah.MAC_KEYCODES[c]] == c, "and the Mac tap names the physical key the same"
    for n in range(1, 21):
        name = js(f"L.captureKeyId({{key: 'F{n}', code: 'F{n}'}}, true)")
        assert name in ah.MAC_KEYCODES and name in ah.WINDOWS_VKS
    for d in "0123456789":
        assert js(f"L.captureKeyId({{key: {json.dumps(d)}, code: 'Digit{d}'}}, true)") == d
        assert d in ah.MAC_KEYCODES and d in ah.WINDOWS_VKS


# ── what the dialog says ─────────────────────────────────────────────────────

@needs_node
def test_every_dialog_has_a_title_and_a_plain_sentence():
    for target in ("dictation", "transform", "command", "paste_last", "copy_last", "cancel"):
        for mac in ("true", "false"):
            t = js(f"L.hotkeyDialogText({json.dumps(target)}, {mac})")
            assert t["title"] and t["sub"].endswith(".") and no_dashes(t["title"] + t["sub"]), (target, t)
            assert t["title"][0].isupper() and not t["title"].endswith("."), t
    assert js("L.hotkeyDialogText('dictation', true)") == {
        "title": "Hold your new keys", "sub": "Hold the combination you want, then let go. Esc cancels."}
    assert js("L.hotkeyDialogText('dictation')") == js("L.hotkeyDialogText('dictation', false)")
    assert js("L.hotkeyDialogText('nonsense')") == js("L.hotkeyDialogText('dictation')")


@needs_node
def test_the_command_dialog_offers_modifier_keys_alone_on_a_mac_only():
    assert "modifier keys, such as Control and Command" in js("L.hotkeyDialogText('command', true)")["sub"]
    assert "modifier keys, such as" not in js("L.hotkeyDialogText('command', false)")["sub"]
    assert "F key on its own" in js("L.hotkeyDialogText('transform', true)")["sub"]
    assert "F key on its own" not in js("L.hotkeyDialogText('command', true)")["sub"], "a hold has no bare key"


@needs_node
def test_the_cancel_dialog_says_esc_is_recorded_and_how_to_close_it():
    mac = js("L.hotkeyDialogText('cancel', true)")["sub"]
    assert "Esc is recorded here" in mac and "Cancel below" in mac and "F1 to F12 need the Fn key" in mac
    assert "F1 to F12" not in js("L.hotkeyDialogText('cancel', false)")["sub"]


@needs_node
def test_the_lines_under_transform_and_command_no_longer_say_the_keys_reach_the_app():
    for expr in ("L.transformShortcutLine({hotkey: ['control', 't']})", "L.commandShortcutLine({hotkey: ['control', 'c']})"):
        line = js(expr)
        assert "also reach" not in line and "does not reach the app" in line, line


# ── the rows ─────────────────────────────────────────────────────────────────

def rows_json(stored, platform):
    return json.dumps(ss.view(stored, platform))


@needs_node
def test_the_rows_are_drawn_from_what_the_app_sent():
    data = rows_json({"paste_last_hotkey": ["control", "option", "v"], "transforms": {"enabled": True}}, ah.MAC)
    rows = js(f"L.shortcutRows({data}, true)")
    assert [r["id"] for r in rows] == ["dictation", "hands_free", "cancel", "transform", "command", "paste_last", "copy_last"]
    by = {r["id"]: r for r in rows}
    assert by["dictation"]["shown"] == "Fn" and by["dictation"]["canChange"] is True and by["dictation"]["canClear"] is False
    assert by["hands_free"]["fixed"] is True and by["hands_free"]["canChange"] is False and by["hands_free"]["shown"] == "Fn + Space"
    assert by["paste_last"]["shown"] == "Control + Option + V" and by["paste_last"]["changeText"] == "Change…"
    assert by["paste_last"]["canClear"] is True and by["paste_last"]["clearText"] == "Clear"
    assert by["copy_last"]["set"] is False and by["copy_last"]["shown"] == "" and by["copy_last"]["changeText"] == "Set…"
    assert by["copy_last"]["canClear"] is False
    assert by["transform"]["off"] is False and by["transform"]["canChange"] is True and by["transform"]["needs"] == ""
    assert by["command"]["off"] is True and by["command"]["canChange"] is False and by["command"]["canClear"] is False
    assert by["command"]["needs"] == "Turn on in Command mode" and by["command"]["needsSection"] == "command"


@needs_node
def test_transform_off_links_to_the_transforms_section():
    rows = js(f"L.shortcutRows({rows_json({}, ah.MAC)}, true)")
    by = {r["id"]: r for r in rows}
    assert by["transform"]["needs"] == "Turn on in Transforms" and by["transform"]["needsSection"] == "transforms"
    assert by["transform"]["canChange"] is False and by["transform"]["off"] is True


@needs_node
def test_a_shortcut_set_while_its_feature_is_off_shows_but_cannot_be_changed():
    stored = {"transforms": {"enabled": False, "hotkey": ["control", "option", "t"]}}
    row = {r["id"]: r for r in js(f"L.shortcutRows({rows_json(stored, ah.MAC)}, true)")}["transform"]
    assert row["set"] is True and row["shown"] == "Control + Option + T" and row["canChange"] is False
    assert row["canClear"] is False and row["needs"]


@needs_node
def test_the_cancel_row_clears_back_to_esc_in_its_own_words():
    row = {r["id"]: r for r in js(f"L.shortcutRows({rows_json({'cancel_key': 'f16'}, ah.MAC)}, true)")}["cancel"]
    assert row["shown"] == "F16" and row["canClear"] is True
    assert row["clearText"] == "Use Esc" and row["clearLabel"] == "Use Esc to cancel"
    plain = {r["id"]: r for r in js(f"L.shortcutRows({rows_json({}, ah.MAC)}, true)")}["cancel"]
    assert plain["shown"] == "Esc" and plain["canClear"] is False


@needs_node
def test_windows_names_the_modifiers_the_windows_way():
    stored = {"paste_last_hotkey": ["ctrl", "shift", "v"]}
    rows = {r["id"]: r for r in js(f"L.shortcutRows({rows_json(stored, ah.WINDOWS)}, false)")}
    assert rows["dictation"]["shown"] == "Win + Ctrl" and rows["hands_free"]["shown"] == "Win + Ctrl + Space"
    assert rows["paste_last"]["shown"] == "Ctrl + Shift + V"


@needs_node
def test_a_conflict_comes_through_as_the_apps_sentence():
    stored = {"hotkey_keys": ["control", "option"], "paste_last_hotkey": ["control", "option", "v"]}
    rows = {r["id"]: r for r in js(f"L.shortcutRows({rows_json(stored, ah.MAC)}, true)")}
    assert rows["paste_last"]["conflict"] == ss.rows(stored, ah.MAC)[5]["conflict"] and rows["paste_last"]["conflict"]
    assert rows["copy_last"]["conflict"] == ""


@needs_node
def test_every_row_has_a_name_for_its_buttons_and_its_keys():
    rows = js(f"L.shortcutRows({rows_json({'copy_last_hotkey': ['f16']}, ah.MAC)}, true)")
    for r in rows:
        assert r["capsLabel"].startswith(r["label"]) and r["changeLabel"] and r["clearLabel"], r["id"]
        assert r["label"][0].isupper() and r["label"] != r["label"].upper()
        assert no_dashes(r["label"] + r["description"] + r["changeLabel"] + r["capsLabel"]), r["id"]
    by = {r["id"]: r for r in rows}
    assert by["copy_last"]["changeLabel"] == "Change the Copy last shortcut"
    assert by["paste_last"]["changeLabel"] == "Set the Paste last shortcut"
    assert by["paste_last"]["capsLabel"] == "Paste last shortcut: not set"


@needs_node
@pytest.mark.parametrize("data", ["null", "undefined", "{}", "{rows: 5}", "{rows: [null, 5, {}, {id: 1}]}",
                                  "{rows: [{id: 'x', label: 'X', keys: 'f16', needs_section: '__proto__', enabled: false}]}"])
def test_odd_data_still_draws(data):
    rows = js(f"L.shortcutRows({data}, true)")
    assert isinstance(rows, list)
    for r in rows:
        assert r["keys"] == [] and r["needsSection"] == ""


@needs_node
def test_the_rules_are_the_apps_and_odd_data_is_empty():
    data = json.dumps(ss.view({}, ah.MAC))
    v = js(f"L.shortcutRulesView({data})")
    assert v["lines"] == ss.rules(ah.MAC)["lines"]
    assert v["reserved"] == [x["line"] for x in ss.rules(ah.MAC)["reserved"]]
    for odd in ("null", "{}", "{rules: 5}", "{rules: {lines: 'x', reserved: [null, 5, {}]}}"):
        assert js(f"L.shortcutRulesView({odd})") == {"lines": [], "reserved": []}


@needs_node
def test_the_toasts_are_plain_and_say_key_for_the_cancel_key():
    assert js("L.shortcutSaved('Paste last', 'Control + Option + V', false, 'paste_last')") == \
        "Paste last shortcut is now Control + Option + V."
    assert js("L.shortcutSaved('Paste last', '', true, 'paste_last')") == "Paste last shortcut cleared."
    assert js("L.shortcutSaved('Cancel', 'F16', false, 'cancel')") == "Cancel key is now F16."
    assert js("L.shortcutSaved('Cancel', 'Esc', false, 'cancel')") == "Cancel key is now Esc."
    assert js("L.shortcutSaved('Copy last', '', false, 'copy_last')") == "Copy last shortcut saved."
    assert js("L.shortcutSaved()") == "Shortcut saved."
    for text in js("Object.values(L.SHORTCUT_TEXT)"):
        assert text.endswith((".", "?")) and no_dashes(text), text


@needs_node
def test_the_targets_the_dialog_records_are_the_apps_actions_and_the_cancel_key():
    targets = js("L.SHORTCUT_TARGETS")
    assert targets == [a.id for a in ah.ACTIONS] + ["cancel"]
    assert js("L.shortcutIsTarget('cancel')") is True and js("L.shortcutIsTarget('dictation')") is False
    assert js("L.shortcutIsTarget('hands_free')") is False and js("L.shortcutIsTarget('__proto__')") is False


# ── the section is in the page, and wired ────────────────────────────────────

def test_shortcuts_is_the_one_section_where_the_hotkey_page_was():
    html = read("index.html")
    nav = html[html.index('aria-label="Settings sections"'):html.index("</nav>", html.index('aria-label="Settings sections"'))]
    assert "data-sec=\"hotkey\" onclick=\"showSettingsSection('hotkey')\">Shortcuts</button>" in nav
    assert "Hotkey</button>" not in nav, "no second page for the same thing"
    assert html.count('class="snav-item"') == 9
    assert '<h1 class="ptitle">Shortcuts</h1>' in section()
    assert "'general', 'style', 'transforms', 'command', 'keys', 'hotkey', 'usage', 'privacy', 'about'" in read("app.js")


def test_the_page_has_the_list_the_rules_the_reset_and_the_dictation_hotkeys_test():
    sec = section()
    assert 'id="shortcutsList" role="list" aria-label="Shortcuts"' in sec
    assert '<p class="s-error" id="shortcutsError" role="alert" hidden></p>' in sec
    assert 'id="shortcutsRulesBtn" aria-expanded="false" aria-controls="shortcutsRules"' in sec
    assert 'id="shortcutsRules" hidden' in sec and 'id="shortcutsReserved"' in sec
    assert 'onclick="askShortcutsReset(true)"' in sec and 'onclick="resetShortcuts(this)"' in sec
    assert 'id="shortcutsResetConfirm" hidden' in sec
    # What the Hotkey page had stays: the keys, big, the test, the Fn notice, the presets.
    for ident in ('id="settingsHotkeyCaps"', 'id="hkTest"', 'id="settingsFnWarn"', 'id="hotkeyPresetCards"',
                  "openHotkeyCapture()"):
        assert ident in sec, ident
    assert "Keep going hands-free" not in sec, "hands-free and cancel are rows now"


def test_every_control_has_a_name_and_one_tab_stop():
    sec = section()
    assert "tabindex" not in sec
    for button in re.findall(r"<button[^>]*>([^<]*)</button>", sec):
        assert button.strip(), "a button with no words has an aria-label instead"
    app = read("app.js")
    row = app[app.index("function _shortcutRowHtml("):app.index("function renderShortcuts(")]
    assert "tabindex" not in row
    assert row.count('aria-label="${escHtml(r.changeLabel)}"') == 1 and row.count('aria-label="${escHtml(r.clearLabel)}"') == 1
    assert 'aria-describedby="${described}"' in row and 'role="listitem"' in row
    assert 'role="img" aria-label="${escHtml(r.capsLabel)}"' in row
    assert "disabled" in row, "a shortcut whose feature is off cannot be changed"


def test_sentence_case_no_dashes_in_the_section():
    sec = section()
    assert no_dashes(sec)
    for h in re.findall(r"<h[123][^>]*>([^<]+)</h[123]>", sec):
        assert h[0].isupper(), h
    text = " ".join(re.sub(r"<[^>]+>", " ", sec).split())
    assert "never types a letter or runs that app's own shortcut" in text


def test_the_handlers_exist_and_every_save_goes_through_the_apps_calls():
    app = code_only(read("app.js"))
    sec = section()
    for handler in re.findall(r'on(?:change|input|blur|click|submit)="(\w+)\(', sec):
        assert f"function {handler}(" in app or f"async function {handler}(" in app, handler
    block = app[app.index("let _shortcuts = null;"):app.index("async function refreshAll")]
    assert block.count("pywebview.api.save_shortcut(") == 2, "the dialog's save, and Clear"
    assert "api.save_shortcut(target, JSON.stringify(keys))" in block and "api.save_shortcut(id, '[]')" in block
    assert "api.reset_shortcuts()" in block and "api.get_shortcuts()" in block
    assert "confirm(" not in app, "asks in the page, not in a native box"
    # The app's own sentence is shown, not a made-up one.
    assert "(result && result.error) || WL.SHORTCUT_TEXT.saveFailed" in block
    assert "(r && r.error) || WL.SHORTCUT_TEXT.resetFailed" in block


def test_the_rows_are_drawn_when_the_page_opens_and_after_every_change():
    app = code_only(read("app.js"))
    show = app[app.index("function showSettingsSection("):app.index("function _micMeterWanted")]
    assert "if (sec === 'hotkey') { _hotkeyHeard = false; _renderHotkeyTest(); loadShortcuts(); }" in show
    saved = app[app.index("async function _onHotkeySaved("):app.index("async function changeSettingsHotkey(")]
    assert "loadShortcuts();" in saved
    for fn in ("refreshTransformSettings", "refreshCommandSettings"):
        body = app[app.index(f"async function {fn}"):]
        body = body[:body.index("\n}\n")]
        assert "loadShortcuts();" in body, fn
    assert "_scSeq" in app and "if (seq !== _scSeq) return;" in app, "an older answer is dropped"


def test_focus_returns_to_the_row_that_was_changed():
    app = code_only(read("app.js"))
    render = app[app.index("function renderShortcuts("):app.index("(function wireShortcuts()")]
    assert "_scFocus" in render and ".focus()" in render
    for fn in ("saveShortcutCapture", "clearShortcut"):
        body = app[app.index(f"async function {fn}("):]
        body = body[:body.index("\n}\n")]
        assert "_scFocus =" in body, fn


def test_the_dialog_is_shared_and_asks_the_app_before_save():
    app = code_only(read("app.js"))
    open_fn = app[app.index("function openHotkeyCapture("):app.index("function closeHotkeyCapture")]
    assert "WL.shortcutIsTarget(target)" in open_fn
    assert "WL.hotkeyDialogText(_captureTarget, isMacPlatform)" in open_fn
    assert "_tellShortcutRecording(true)" in open_fn
    assert "_captureTarget === 'cancel'" in open_fn, "the cancel key keeps its default button"
    close = app[app.index("function closeHotkeyCapture"):app.index("function _announceCaptured")]
    assert "_tellShortcutRecording(false)" in close and "_captureTarget = 'dictation'" in close
    down = app[app.index("function _onCaptureKeyDown("):app.index("function _onCaptureKeyUp(")]
    assert "e.key === 'Escape' && _captureTarget !== 'cancel'" in down
    up = app[app.index("function _onCaptureKeyUp("):app.index("function resetHotkeyDefault")]
    assert "_liveCheckCaptured();" in up
    live = app[app.index("async function _liveCheckCaptured("):app.index("function resetHotkeyDefault")]
    assert "api.check_shortcut(_captureTarget, JSON.stringify(keys))" in live and "if (seq !== _liveSeq" in live
    save = app[app.index("async function saveHotkeyCapture"):app.index("let _shortcuts = null;")]
    assert "if (WL.shortcutIsTarget(_captureTarget)) { await saveShortcutCapture(_captureTarget, keys, fail); return; }" in save
    assert "jsKeyToId" in down and "WL.captureKeyId(e, isMacPlatform, _captureTarget)" in app


def test_the_dialog_tells_the_app_it_is_open_and_the_app_listens():
    app = code_only(read("app.js"))
    assert "pywebview.api.shortcut_recording(!!active)" in app
    py = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "def shortcut_recording(self, active)" in py and "set_recording(bool(active))" in py


def test_transforms_and_command_mode_link_to_the_shortcuts_page_and_keep_their_own_rows():
    html = read("index.html")
    for ident, section_id in (("transformsAllShortcuts", "secTransforms"), ("commandAllShortcuts", "secCommand")):
        start = html.index(f'id="{ident}"')
        block = html[start:html.index("</p>", start)]
        assert "showSettingsSection('hotkey')" in block and ">Shortcuts</button>" in block
        assert html.index(f'id="{section_id}"') < start < html.index("</section>", html.index(f'id="{section_id}"'))
    assert "openHotkeyCapture('transform')" in html and "openHotkeyCapture('command')" in html
    assert 'id="transformsCaps"' in html and 'id="commandCaps"' in html


def test_transforms_and_command_mode_show_the_same_conflict_sentence_for_their_own_shortcut():
    html = read("index.html")
    for ident, caps in (("transformsConflict", "transformsCaps"), ("commandConflict", "commandCaps")):
        assert f'<p class="s-error sc-conflict" id="{ident}" hidden></p>' in html, ident
        assert html.index(f'id="{caps}"') < html.index(f'id="{ident}"')
    app = code_only(read("app.js"))
    render = app[app.index("function renderShortcuts("):app.index("(function wireShortcuts()")]
    assert "['transform', 'transformsConflict'], ['command', 'commandConflict']" in render
    assert "node.textContent = row ? row.conflict : '';" in render


def test_the_css_uses_tokens_only():
    css = read("style.css")
    start = css.index("/* Settings, Shortcuts:")
    block = css[start:css.index("/* ── Keys and providers", start)]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), "colours come from tokens"
    assert "--faint" not in block and "animation" not in block and "transition" not in block
    for rule in (".sc-row", ".sc-acts", ".sc-conflict", ".sc-rules", ".sc-reserved"):
        assert rule in block, rule
    narrow = css[css.index("@media (max-width: 560px) {"):]
    assert ".sc-reserved { columns: 1; }" in narrow[:narrow.index("\n}\n")]


def test_the_page_loads_no_new_remote_asset_and_the_logic_file_loads_first():
    html = read("index.html")
    assert html.index("logic.js") < html.index("app.js")


# ── from the independent review ──────────────────────────────────────────────

@needs_node
def test_the_cancel_key_is_named_esc_until_the_app_says_otherwise():
    assert js("L.cancelLabel()") == "Esc" and js("L.cancelLabel('')") == "Esc" and js("L.cancelLabel(5)") == "Esc"
    assert js("L.cancelLabel(' F16 ')") == "F16"


@needs_node
def test_no_sentence_in_the_window_says_esc_where_the_cancel_key_may_have_changed():
    assert "Esc" not in js("L.commandShortcutLine({hotkey: ['control', 'c']})")
    card = {"failed": True, "unsent_id": "recording-2026-09-26T10-04-31.wav", "not_sent_reason": "cancelled",
            "will_retry": False, "provider_name": "Groq"}
    assert "You pressed Esc while" in js(f"L.notSentView({json.dumps(card)})")["text"]
    assert "You pressed F16 while" in js(f"L.notSentView({json.dumps(card)}, 'F16')")["text"]
    assert "You pressed Esc while" in js(f"L.notSentView({json.dumps(card)}, '  ')")["text"]
    assert "WL.notSentView(item, _cancelLabel)" in read("app.js")
    msgs = (ROOT / "src" / "user_messages.py").read_text(encoding="utf-8")
    assert "press Esc to stop it" not in msgs


def test_the_journals_live_line_names_the_cancel_key_the_app_reports():
    app = code_only(read("app.js"))
    assert "let _cancelLabel = 'Esc';" in app
    live = app[app.index("const esc = document.getElementById('journalLiveEsc');"):]
    live = live[:live.index("\n}\n")]
    assert "caps.textContent = _cancelLabel;" in live
    load = app[app.index("async function loadHotkeyConfig()"):app.index("function renderSettingsHotkeyPresets(")]
    assert "_cancelLabel = WL.cancelLabel(config.cancel_display);" in load
    shortcuts = app[app.index("async function loadShortcuts()"):app.index("function _shortcutRowHtml(")]
    assert "_cancelLabel = WL.cancelLabel(cancel.shown);" in shortcuts
    py = (ROOT / "app.py").read_text(encoding="utf-8")
    get = py[py.index("    def get_hotkey_config(self)"):py.index("    def save_hotkey_config(self")]
    assert get.count('"cancel_key": cancel, "cancel_display": cancel_display') == 2, "Mac and Windows both say it"


def test_the_dialog_checks_a_moment_after_the_last_key_goes_down_not_only_on_key_up():
    """A Mac sends no key-up for a letter while Command is held."""
    app = code_only(read("app.js"))
    down = app[app.index("function _onCaptureKeyDown("):app.index("function _onCaptureKeyUp(")]
    assert "clearTimeout(_liveTimer);" in down and "_liveTimer = setTimeout(_liveCheckCaptured, 600);" in down
    close = app[app.index("function closeHotkeyCapture"):app.index("function _announceCaptured")]
    assert "clearTimeout(_liveTimer);" in close, "a check does not run after the dialog is closed"


def test_the_apps_are_told_the_dialog_opened_and_closed_in_that_order():
    app = code_only(read("app.js"))
    tell = app[app.index("function _tellShortcutRecording("):]
    tell = tell[:tell.index("\n}\n") + 3]
    assert "_recordingChain = _recordingChain.then(async () => {" in tell
    assert "await pywebview.api.shortcut_recording(!!active)" in tell
