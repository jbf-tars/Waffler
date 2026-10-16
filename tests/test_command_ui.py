"""Settings, Command mode (beta) and the Journal's commands: what the window
decides, and how it is wired (S8).

The section is in the Atelier design (a side-nav entry, a sheet of rows, a
switch, a select, the shared hotkey dialog), keyboard-reachable, and the page
holds no copy of the app's rules: the engines and phrases in ui/logic.js are
compared here with src/command_mode.py. Its small decisions run in Node; the
rest is read from the source, as the other window tests do.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import command_mode as cm  # noqa: E402
from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


def section():
    html = read("index.html")
    start = html.index('id="secCommand"')
    return html[start:html.index("</section>", start)]


# ── the page and the app agree ───────────────────────────────────────────────

@needs_node
def test_the_engines_are_the_apps_in_the_apps_order():
    engines = js("L.COMMAND_ENGINES")
    assert [e["value"] for e in engines] == [e.id for e in cm.ENGINES]
    assert [e["label"] for e in engines] == [e.label for e in cm.ENGINES]
    assert engines[0]["value"] == cm.DEFAULT_ENGINE


@needs_node
def test_the_three_example_phrases_are_the_apps():
    examples = js("L.COMMAND_EXAMPLES")
    assert [x["say"] for x in examples] == list(cm.EXAMPLES)
    for x in examples:
        assert x["does"] and x["does"].endswith(".") and "—" not in x["does"] and "–" not in x["does"]


@needs_node
def test_an_engine_label_is_found_by_id_and_anything_else_is_the_default():
    for e in cm.ENGINES:
        assert js(f"L.commandEngineLabel({json.dumps(e.id)})") == e.label
    for odd in ("'bing'", "null", "undefined", "5", "'__proto__'", "'toString'"):
        assert js(f"L.commandEngineLabel({odd})") == "Perplexity"


# ── what the section shows ───────────────────────────────────────────────────

def settings(**over):
    c = {"enabled": False, "hotkey": [], "web_engine": "perplexity", "hotkey_display": ""}
    c.update(over)
    return {"command_mode": c}


@needs_node
def test_the_defaults_are_off_no_shortcut_and_perplexity():
    for given in ("{}", "null", "undefined", "5", "{command_mode: 5}", "{command_mode: null}"):
        v = js(f"L.commandView({given}, true)")
        assert v["enabled"] is False and v["hotkey"] == [] and v["hotkeyShown"] == "", given
        assert v["engine"] == "perplexity" and v["engineLabel"] == "Perplexity"
        assert len(v["engines"]) == 5 and len(v["examples"]) == 3


@needs_node
def test_the_saved_settings_are_drawn_as_they_are():
    s = settings(enabled=True, hotkey=["control", "option", "c"], web_engine="duckduckgo")
    v = js(f"L.commandView({json.dumps(s)}, true)")
    assert v["enabled"] is True and v["hotkey"] == ["control", "option", "c"]
    assert v["hotkeyShown"] == "Control + Option + C"
    assert v["engine"] == "duckduckgo" and v["engineLabel"] == "DuckDuckGo"


@needs_node
def test_the_windows_shortcut_is_named_the_windows_way():
    s = settings(hotkey=["ctrl", "alt", "c"])
    assert js(f"L.commandView({json.dumps(s)}, false)")["hotkeyShown"] == "Ctrl + Alt + C"


@needs_node
def test_an_engine_that_is_not_offered_and_junk_are_dropped():
    s = settings(web_engine="bing", hotkey=["control", 5, None, "c"], enabled="yes")
    v = js(f"L.commandView({json.dumps(s)}, true)")
    assert v["engine"] == "perplexity" and v["hotkey"] == ["control", "c"] and v["enabled"] is False


@needs_node
def test_the_sentences_are_plain():
    texts = [js("L.commandShortcutLine({hotkey: []})"), js("L.commandShortcutLine({hotkey: ['control','c']})"),
             js("L.commandSaved('enabled', true)"), js("L.commandSaved('enabled', false)"),
             js("L.commandSaved('hotkey', 'Control + Option + C')"), js("L.commandSaved('hotkey', '')"),
             js("L.commandSaved('engine', 'Google')"), js("L.commandSaved('x')"),
             js("L.hotkeyDialogText('command')")["title"], js("L.hotkeyDialogText('command')")["sub"]]
    for t in texts:
        assert t and "—" not in t and "–" not in t, t
    assert js("L.commandSaved('hotkey', 'Control + Option + C')") == "Command shortcut is now Control + Option + C."
    assert js("L.commandSaved('engine', 'Google')") == "Questions now open in Google."
    assert "two modifier keys" in js("L.hotkeyDialogText('command')")["sub"]
    assert "hold" in js("L.hotkeyDialogText('command')")["sub"].lower()
    assert js("L.hotkeyDialogText('dictation')") == {"title": "Hold your new keys",
                                                      "sub": "Hold the combination you want, then let go. Esc cancels."}


# ── a command in the Journal ─────────────────────────────────────────────────

BEFORE = "so yesterday i went to the shop and bought some milk and eggs and then i came home"
AFTER = "Yesterday I went to the shop and bought some milk and eggs. Then I came home."


@needs_node
def test_an_edit_shows_what_was_said_what_was_taken_out_and_what_was_put_in():
    item = {"id": 12, "kind": "command", "action": "edit", "instruction": "fix this up", "before": BEFORE,
            "after": AFTER, "paste": "pasted", "app": "Notes"}
    v = js(f"L.commandEntryView({json.dumps(item)})")
    assert v["id"] == 12 and v["action"] == "edit" and v["chip"] == "Edited" and v["icon"] == "pencil"
    assert v["instruction"] == "fix this up" and v["before"] == BEFORE and v["after"] == AFTER
    assert [p["text"].strip() for p in v["beforePieces"] if p["cut"]] == ["so", "and"]
    assert "".join(p["text"] for p in v["beforePieces"]) == BEFORE
    assert "".join(p["text"] for p in v["afterPieces"]) == AFTER
    assert v["changes"] == 2 and v["changesLabel"] == "2 changes" and v["words"] == "16 words"
    assert v["status"] == "Replaced your selection in Notes."


@needs_node
def test_a_draft_shows_the_draft_and_where_it_went():
    item = {"kind": "command", "action": "draft", "instruction": "reply saying yes", "after": "Yes, that works.",
            "paste": "pasted", "app": "Mail"}
    v = js(f"L.commandEntryView({json.dumps(item)})")
    assert v["chip"] == "Drafted" and v["icon"] == "text-cursor" and v["after"] == "Yes, that works."
    assert v["beforePieces"] == [] and v["changesLabel"] == "" and v["words"] == "3 words"
    assert v["status"] == "Added at your cursor in Mail."


@needs_node
def test_a_search_says_which_engine_and_shows_the_question():
    item = {"kind": "command", "action": "ask", "instruction": "what is the capital of Australia",
            "query": "capital of Australia", "engine": "google"}
    v = js(f"L.commandEntryView({json.dumps(item)})")
    assert v["chip"] == "Searched Google" and v["icon"] == "search" and v["query"] == "capital of Australia"
    assert v["words"] == "" and v["status"] == "Opened in your browser."
    assert js("L.commandEntryView({action: 'ask', engine: 'duckduckgo'})")["chip"] == "Searched DuckDuckGo"


@needs_node
def test_a_search_in_an_engine_that_has_gone_is_the_default_engine():
    assert js("L.commandEntryView({action: 'ask', engine: 'gone'})")["chip"] == "Searched Perplexity"


@pytest.mark.parametrize("action,paste,fragment", [
    ("edit", "pasted", "Replaced your selection in Notes."),
    ("edit", "failed", "Couldn't paste in Notes. The result is on your clipboard."),
    ("edit", "copied", "Copied to the clipboard, not pasted."),
    ("edit", "not_pasted", "Not pasted."),
    ("draft", "unverified", "Added at your cursor in Notes."),
    ("draft", "failed", "Couldn't paste in Notes. The draft is on your clipboard."),
    ("draft", "copied", "Copied to the clipboard, not pasted."),
    ("draft", "", ""), ("edit", "nonsense", ""), ("edit", "__proto__", ""), ("draft", "toString", ""),
])
@needs_node
def test_what_became_of_the_text_is_said_in_a_sentence(action, paste, fragment):
    v = js(f"L.commandEntryView({{action: {json.dumps(action)}, before: 'a b', after: 'a c', app: 'Notes', "
           f"paste: {json.dumps(paste)}}})")
    assert v["status"] == fragment


@needs_node
@pytest.mark.parametrize("item", ["null", "undefined", "5", "'x'", "{}", "{action: 5, before: 5, after: null}",
                                  "{id: 'x', instruction: 7, action: 'edit'}", "{action: '__proto__'}"])
def test_an_odd_entry_still_draws(item):
    v = js(f"L.commandEntryView({item})")
    assert v["chip"] and v["icon"] and v["action"] in ("edit", "draft", "ask")
    assert v["id"] is None or isinstance(v["id"], int)
    assert isinstance(v["instruction"], str) and isinstance(v["status"], str)


# ── the section is in the page, and wired ────────────────────────────────────

def test_command_mode_is_a_section_of_settings_marked_beta_after_transforms():
    html = read("index.html")
    assert "data-sec=\"command\" onclick=\"showSettingsSection('command')\">Command mode (beta)</button>" in html
    assert 'id="secCommand" data-sec="command" hidden' in html
    assert '<h1 class="ptitle">Command mode (beta)</h1>' in section()
    nav = html[html.index('aria-label="Settings sections"'):html.index("</nav>", html.index('aria-label="Settings sections"'))]
    order = re.findall(r'data-sec="(\w+)"', nav)
    assert order.index("command") == order.index("transforms") + 1
    assert "'transforms', 'command'," in code_only(read("app.js"))


def test_the_section_has_the_switch_the_shortcut_the_engine_and_the_examples():
    sec = section()
    assert sec.count('class="tog" role="switch"') == 1 and 'id="commandToggle"' in sec
    assert "openHotkeyCapture('command')" in sec and 'id="commandCaps"' in sec and 'id="commandRemove"' in sec
    assert '<select id="commandEngine" class="select"' in sec
    assert 'id="commandExamples"' in sec and 'role="list"' in sec


def test_every_control_has_a_name_and_one_tab_stop():
    sec = section()
    for ident in ("commandToggle", "commandEngine"):
        assert f'<label for="{ident}">' in sec, ident
    assert "tabindex" not in sec
    for button in re.findall(r"<button[^>]*>([^<]*)</button>", sec):
        assert button.strip(), "a button with no words has an aria-label instead"
    assert 'aria-describedby="commandDesc"' in sec and 'aria-describedby="commandEngineDesc"' in sec
    assert 'aria-describedby="commandShortcutTitle"' in sec


def test_the_section_says_what_is_sent_where_and_that_undo_works():
    sec = " ".join(re.sub(r"<[^>]+>", " ", section()).split())
    assert "your recording is turned into text by your speech provider" in sec
    assert "the text you selected (or, with nothing selected, up to 500 characters before your cursor)" in sec
    assert "sent to the provider you chose in Keys and providers" in sec
    assert "Nothing is recorded, read or sent at any other time" in sec
    assert "Only your spoken question is sent to the engine, never the text you selected" in sec
    assert "Your app's own Undo takes an edit back" in sec
    assert "Hold your shortcut, say what you want done, and let go" in sec


def test_the_privacy_page_says_it_in_both_places():
    html = " ".join((ROOT / "ui" / "index.html").read_text(encoding="utf-8").split())
    assert ("Only while you hold your Command shortcut, Waffler records what you say, turns it into text with your "
            "speech provider, and sends that with the text you selected (or up to 500 characters before your cursor) "
            "to your provider on your own key. A question opens your browser with only your spoken question.") in html
    assert "The recording is not kept." in html and "Delete all my data clears them with the rest" in html
    assert ("Only while you hold your Command shortcut with nothing selected: Waffler reads up to 500 characters "
            "before your cursor, to give your command some context, and sends them to your provider on your own key. "
            "It never reads a password field.") in html
    for ident in ('id="commandPrivacyRow"',):
        block = html[html.index(ident):html.index("</div></div>", html.index(ident))]
        assert "—" not in block and "–" not in block


def test_sentence_case_no_dashes_in_the_section():
    sec = section()
    assert "—" not in sec and "–" not in sec
    for h in re.findall(r"<h[123][^>]*>([^<]+)</h[123]>", sec):
        assert h[0].isupper(), h
    for label in re.findall(r"<label[^>]*>([^<]+)</label>", sec):
        assert label[0].isupper() and not label.isupper(), label


def test_the_handlers_exist_and_every_save_goes_through_one_path():
    app = code_only(read("app.js"))
    sec = section()
    for handler in re.findall(r'on(?:change|input|blur|click|submit)="(\w+)\(', sec):
        assert f"function {handler}(" in app or f"async function {handler}(" in app, handler
    block = app[app.index("const _cmView"):app.index("let _txEditing")]
    assert block.count("pywebview.api.save_settings(") == 1, "one place saves"
    assert "save_settings({ command_mode: part })" in block
    # The app's own sentence is shown, not a made-up one.
    assert "(r && r.error) || \"Couldn't save that. Try again.\"" in block
    # A refused save puts the controls back to what is saved.
    assert "renderCommandSection();\n    return false;" in block
    assert "confirm(" not in app


def test_the_shortcut_uses_the_shared_hotkey_dialog_and_its_own_call():
    app = code_only(read("app.js"))
    open_fn = app[app.index("function openHotkeyCapture("):app.index("function closeHotkeyCapture")]
    assert "target === 'transform' || target === 'command' ? target : 'dictation'" in open_fn
    assert "WL.commandView(_lastSettings, isMacPlatform).hotkey.slice()" in open_fn
    assert "presets.hidden = forTransform ||" in open_fn and "reset.hidden = forTransform" in open_fn
    save = app[app.index("async function saveHotkeyCapture"):app.index("async function refreshAll")]
    assert "if (_captureTarget === 'command') { await saveCommandHotkey(keys, fail); return; }" in save
    assert "if (_captureTarget === 'transform')" in save and "api.save_hotkey_config(" in save
    assert "api.save_command_hotkey(JSON.stringify(keys))" in app
    assert "api.save_command_hotkey('[]')" in app, "Remove clears it"


def test_toasts_come_from_the_logic_file():
    app = code_only(read("app.js"))
    for call in ("WL.commandSaved('enabled'", "WL.commandSaved('hotkey'", "WL.commandSaved('engine'"):
        assert call in app, call


def test_the_settings_are_drawn_once_loaded_and_after_every_save():
    app = code_only(read("app.js"))
    assert "renderCommandSection();" in app[app.index("async function loadSettings()"):app.index("function renderCleanupRow")]
    refresh = app[app.index("async function refreshCommandSettings"):app.index("function renderCommandSection")]
    assert "get_settings()" in refresh and "renderCommandSection();" in refresh


def test_the_journal_draws_a_command_with_its_own_card_and_escapes_everything():
    app = code_only(read("app.js"))
    assert "if (item && item.kind === 'command') return makeCommandCard(item, isNew);" in app
    card = app[app.index("function makeCommandCard"):app.index("window.waffler_show_command")]
    for escaped in ("escHtml(p.text)", "escHtml(v.chip)", "escHtml(v.instruction)", "escHtml(v.status)",
                    "escHtml(v.after)", "escHtml(v.query)"):
        assert escaped in card, escaped
    assert "${v.before}" not in card and "${v.after}" not in card and "${v.instruction}" not in card
    assert "deleteEntry(item, div)" in card and 'class="rbtn rbtn-sm card-del"' in card
    for idle in ("Copy result", "Copy original", "Copy draft", "Copy question"):
        assert f'data-idle="{idle}"' in card, idle
    assert "redo" not in card.lower(), "no Redo for a command"
    assert "tx-card cmd-card" in card and "dataset.id" in card


def test_a_new_command_joins_the_journal_without_a_second_message_and_search_finds_it():
    app = code_only(read("app.js"))
    refresh = app[app.index("window.waffler_refresh = "):]
    refresh = refresh[:refresh.index("\n};")]
    assert "newItem.kind !== 'command'" in refresh and "'Added to your Journal.'" in refresh
    assert "newItem.kind === 'command'" in refresh and "newItem.instruction" in refresh and "newItem.query" in refresh


def test_show_in_journal_for_a_command_uses_the_same_scroll_to_the_card():
    app = code_only(read("app.js"))
    assert "window.waffler_show_command = (id) => window.waffler_show_transform(id);" in app


def test_the_command_css_uses_tokens_only():
    css = read("style.css")
    start = css.index("/* Settings, Command mode:")
    block = css[start:css.index("/* ── Keys and providers", start)]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), "colours come from tokens"
    assert "--faint" not in block and "animation" not in block and "transition" not in block
    assert ".cmd-said" in block and ".cmd-example" in block
