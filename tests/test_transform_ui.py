"""Settings, Transforms (beta) and the Journal's rewrites: what the window
decides, and how it is wired (S7).

The section is in the Atelier design (a side-nav entry, sheets of rows,
switches, a text box, the shared hotkey dialog), keyboard-reachable, and the
page holds no copy of the rules: the limits and sentences in ui/logic.js are
compared here with src/transforms.py and src/user_messages.py. Its small
decisions run in Node; the rest is read from the source, as the other window
tests do.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import transforms as tf  # noqa: E402
import user_messages as um  # noqa: E402
from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


def section():
    html = read("index.html")
    start = html.index('id="secTransforms"')
    return html[start:html.index("</section>", start)]


# ── the page and the app agree ───────────────────────────────────────────────

@needs_node
def test_the_rules_are_the_apps_in_the_apps_order_with_the_apps_defaults():
    rules = js("L.TRANSFORM_RULES")
    assert [r["key"] for r in rules] == list(tf.POLISH_RULE_KEYS)
    assert [r["label"] for r in rules] == [r.label for r in tf.POLISH_RULES]
    assert [r["on"] for r in rules] == [r.default for r in tf.POLISH_RULES]


@needs_node
def test_the_limits_are_the_apps():
    assert js("L.TRANSFORM_LIMITS") == {
        "custom": tf.MAX_CUSTOM, "name": tf.NAME_MAX, "prompt": tf.PROMPT_MAX,
        "rule": tf.RULE_MAX, "rules": tf.MAX_CUSTOM_RULES, "outline": tf.SKELETON_MAX}
    assert [b["label"] for b in js("L.TRANSFORM_BUILT_IN")] == [tf.BUILT_IN_NAMES[i] for i in tf.BUILT_IN]
    assert [b["value"] for b in js("L.TRANSFORM_BUILT_IN")] == list(tf.BUILT_IN)


@needs_node
def test_the_sentences_are_the_apps():
    text = js("L.TRANSFORM_TEXT")
    assert text == {
        "nameEmpty": um.TRANSFORM_NAME_EMPTY, "nameLong": um.TRANSFORM_NAME_LONG,
        "nameTaken": um.TRANSFORM_NAME_TAKEN, "promptEmpty": um.TRANSFORM_PROMPT_EMPTY,
        "promptLong": um.TRANSFORM_PROMPT_LONG, "tooMany": um.TRANSFORMS_TOO_MANY,
        "ruleEmpty": um.TRANSFORM_RULE_EMPTY, "ruleLong": um.TRANSFORM_RULE_LONG,
        "ruleTwice": um.TRANSFORM_RULE_TWICE, "rulesTooMany": um.TRANSFORM_RULES_TOO_MANY,
        "outlineLong": um.TRANSFORM_OUTLINE_LONG}


@needs_node
def test_the_page_refuses_what_the_app_refuses_before_asking():
    """Same inputs, same answer: the page's check and src/transforms.py agree."""
    cases = [
        ([], "Boss", "Be firm.", -1, None),
        ([], "  ", "Be firm.", -1, um.TRANSFORM_NAME_EMPTY),
        ([], "n" * 41, "Be firm.", -1, um.TRANSFORM_NAME_LONG),
        ([{"name": "Boss"}], " boss ", "Be firm.", -1, um.TRANSFORM_NAME_TAKEN),
        ([], "polish", "Be firm.", -1, um.TRANSFORM_NAME_TAKEN),
        ([], "Prompt   Engineer", "Be firm.", -1, um.TRANSFORM_NAME_TAKEN),
        ([], "Boss", " \n ", -1, um.TRANSFORM_PROMPT_EMPTY),
        ([], "Boss", "p" * 801, -1, um.TRANSFORM_PROMPT_LONG),
        ([{"name": f"T{i}"} for i in range(5)], "New", "Be firm.", -1, um.TRANSFORMS_TOO_MANY),
        ([{"name": "Boss"}], "Boss", "Firmer.", 0, None),          # changing it is not a second one
        ([{"name": "Boss"}, {"name": "Other"}], "other", "x", 0, um.TRANSFORM_NAME_TAKEN),
    ]
    for have, name, prompt, editing, error in cases:
        got = js(f"L.transformCheck({json.dumps(have)}, {json.dumps(name)}, {json.dumps(prompt)}, {editing})")
        assert (got["error"] if not got["ok"] else None) == error, (name, prompt, got)
        python = tf.settings_problem(
            {"transforms": {"custom": [dict(c, prompt="p") for c in have if True] +
                            ([{"name": name, "prompt": prompt}] if editing < 0 else [])}}, {}, platform="Darwin") \
            if editing < 0 else None
        if editing < 0 and error != um.TRANSFORMS_TOO_MANY:
            assert python == error, (name, prompt, python)
    ok = js("L.transformCheck([], '  Friendly   one ', ' Be warm.\\r\\n', -1)")
    assert ok == {"ok": True, "name": "Friendly one", "prompt": "Be warm."}


@needs_node
def test_the_page_refuses_a_bad_own_rule_in_the_apps_words():
    check = lambda have, text: js(f"L.transformRuleCheck({json.dumps(have)}, {json.dumps(text)})")
    assert check([], "Use British spelling") == {"ok": True, "rule": "Use British spelling"}
    assert check([], "   ")["error"] == um.TRANSFORM_RULE_EMPTY
    assert check([], "x" * 161)["error"] == um.TRANSFORM_RULE_LONG
    assert check(["Be kind"], "be   KIND")["error"] == um.TRANSFORM_RULE_TWICE
    assert check(["a", "b", "c", "d", "e"], "f")["error"] == um.TRANSFORM_RULES_TOO_MANY
    assert check([], "  two   spaces ")["rule"] == "two spaces"


# ── what the section shows ───────────────────────────────────────────────────

def settings(**over):
    t = {"enabled": False, "hotkey": [], "default": "polish",
         "polish": {"rules": {r.key: r.default for r in tf.POLISH_RULES}, "custom_rules": []},
         "prompt_engineer": {"skeleton": tf.DEFAULT_SKELETON}, "custom": [],
         "skeleton_default": tf.DEFAULT_SKELETON}
    t.update(over)
    return {"transforms": t}


@needs_node
def test_the_defaults_are_off_polish_and_the_default_rules():
    for given in ("{}", "null", "undefined", "5", "{transforms: 5}", "{transforms: null}"):
        v = js(f"L.transformView({given}, true)")
        assert v["enabled"] is False and v["hotkey"] == [] and v["defaultId"] == "polish", given
        assert [r["on"] for r in v["rules"]] == [r.default for r in tf.POLISH_RULES]
        assert v["custom"] == [] and v["ownRules"] == [] and v["canAddCustom"] and v["canAddRule"]
        assert [c["label"] for c in v["choices"]] == ["Polish", "Prompt Engineer"]


@needs_node
def test_the_saved_settings_are_drawn_as_they_are():
    custom = [{"id": "c1", "name": "Boss mode", "prompt": "Make it firm."}]
    s = settings(enabled=True, hotkey=["control", "option", "t"], default="c1", custom=custom,
                 polish={"rules": {"grammar": False, "concise": True}, "custom_rules": ["Use British spelling"]})
    v = js(f"L.transformView({json.dumps(s)}, true)")
    assert v["enabled"] is True and v["hotkey"] == ["control", "option", "t"]
    assert v["hotkeyShown"] == "Control + Option + T"
    assert v["defaultId"] == "c1" and [c["label"] for c in v["choices"]] == ["Polish", "Prompt Engineer", "Boss mode"]
    on = {r["key"]: r["on"] for r in v["rules"]}
    assert on["grammar"] is False and on["concise"] is True and on["clarity"] is True
    assert v["ownRules"] == ["Use British spelling"]
    assert v["custom"] == custom and v["outlineIsDefault"] is True and v["outline"] == tf.DEFAULT_SKELETON


@needs_node
def test_the_windows_shortcut_is_named_the_windows_way():
    s = settings(hotkey=["ctrl", "alt", "t"])
    assert js(f"L.transformView({json.dumps(s)}, false)")["hotkeyShown"] == "Ctrl + Alt + T"


@needs_node
def test_a_default_that_is_not_there_is_polish_and_junk_is_dropped():
    s = settings(default="gone", custom=[{"id": "c1"}, {"name": "x"}, None, 5, {"id": "c2", "name": "Ok", "prompt": "p"}],
                 polish={"rules": {"grammar": "no"}, "custom_rules": [1, "fine", None]})
    v = js(f"L.transformView({json.dumps(s)}, true)")
    assert v["defaultId"] == "polish"
    assert [c["id"] for c in v["custom"]] == ["c2"]
    assert v["ownRules"] == ["fine"]
    assert {r["key"]: r["on"] for r in v["rules"]}["grammar"] is True


@needs_node
def test_a_full_list_cannot_be_added_to():
    custom = [{"id": f"c{i}", "name": f"T{i}", "prompt": "p"} for i in range(5)]
    v = js(f"L.transformView({json.dumps(settings(custom=custom, polish={'rules': {}, 'custom_rules': list('abcde')}))}, true)")
    assert v["canAddCustom"] is False and v["canAddRule"] is False


@needs_node
def test_an_outline_of_your_own_is_not_the_default():
    s = settings(prompt_engineer={"skeleton": "# Ask\n(what)"})
    v = js(f"L.transformView({json.dumps(s)}, true)")
    assert v["outline"] == "# Ask\n(what)" and v["outlineIsDefault"] is False
    assert v["outlineDefault"] == tf.DEFAULT_SKELETON


@needs_node
def test_the_outline_counter_says_how_much_is_left_and_when_it_is_over():
    ok = js("L.transformOutlineView('# Title\\r\\n(x)  ')")
    assert ok["over"] is False and ok["text"] == "# Title\n(x)" and ok["counter"] == "11 of 1,200"
    over = js(f"L.transformOutlineView({json.dumps('x' * 1210)})")
    assert over["over"] is True and over["counter"] == "10 over the limit of 1,200"
    assert js("L.transformOutlineView(null)")["chars"] == 0


@needs_node
def test_the_sentences_are_plain():
    texts = [js("L.transformShortcutLine({hotkey: []})"), js("L.transformShortcutLine({hotkey: ['control','t']})"),
             js("L.transformSaved('enabled', true)"), js("L.transformSaved('enabled', false)"),
             js("L.transformSaved('hotkey', 'Control + Option + T')"), js("L.transformSaved('hotkey', '')"),
             js("L.transformSaved('default', 'Polish')"), js("L.transformSaved('added', 'Boss')"),
             js("L.transformSaved('removed', 'Boss')"), js("L.transformSaved('outline')"), js("L.transformSaved('x')"),
             js("L.hotkeyDialogText('transform')")["title"], js("L.hotkeyDialogText('transform')")["sub"],
             js("L.hotkeyDialogText('dictation')")["title"]]
    for t in texts:
        assert t and "—" not in t and "–" not in t, t
    assert js("L.transformSaved('hotkey', 'Control + Option + T')") == "Transform shortcut is now Control + Option + T."
    assert js("L.hotkeyDialogText('dictation')") == {"title": "Hold your new keys",
                                                      "sub": "Hold the combination you want, then let go. Esc cancels."}
    assert "two modifier keys" in js("L.hotkeyDialogText('transform')")["sub"]


# ── a rewrite in the Journal ─────────────────────────────────────────────────

BEFORE = "so yesterday i went to the shop and bought some milk and eggs and then i came home"
AFTER = "Yesterday I went to the shop and bought some milk and eggs. Then I came home."


@needs_node
def test_a_rewrite_shows_what_was_taken_out_and_what_was_put_in():
    item = {"id": 12, "kind": "transform", "transform_name": "Polish", "before": BEFORE, "after": AFTER,
            "paste": "pasted", "app": "Notes"}
    v = js(f"L.transformEntryView({json.dumps(item)})")
    assert v["id"] == 12 and v["name"] == "Polish" and v["before"] == BEFORE and v["after"] == AFTER
    struck = [p["text"].strip() for p in v["beforePieces"] if p["cut"]]
    assert struck == ["so", "and"], "'so' and the second 'and' were taken out"
    assert "".join(p["text"] for p in v["beforePieces"]) == BEFORE
    assert "".join(p["text"] for p in v["afterPieces"]) == AFTER
    assert v["changes"] == 2 and v["changesLabel"] == "2 changes"
    assert v["words"] == "16 words" and v["status"] == "Replaced your selection in Notes."


@needs_node
def test_words_put_in_are_marked_even_when_most_of_the_text_is_new():
    """A rewrite is shown whole: the usual rule that shows no strikes when over
    half the words changed does not apply here."""
    item = {"before": "write a blog post", "after": "# Title\nBlog post about pricing\n\n## Role\nYou are a writer",
            "transform_name": "Prompt Engineer"}
    v = js(f"L.transformEntryView({json.dumps(item)})")
    put_in = " ".join(p["text"].strip() for p in v["afterPieces"] if p["cut"])
    assert "Role" in put_in and "pricing" in put_in and "writer" in put_in
    assert v["changes"] >= 1
    assert js("L.saidDiff('a b c d e f', 'x y z', 1)")[0]["cut"] is True, "all of it is marked when asked"
    assert js("L.saidDiff('a b c d e f', 'x y z')") == [{"text": "a b c d e f", "cut": False}], "as before by default"


@needs_node
def test_a_rewrite_that_only_changes_punctuation_says_so():
    v = js("L.transformEntryView({before: 'hello there friend', after: 'Hello there, friend.', transform_name: 'Polish'})")
    assert v["changes"] == 0 and v["changesLabel"] == "Punctuation and layout only"


@pytest.mark.parametrize("paste,fragment", [
    ("pasted", "Replaced your selection in Notes."),
    ("unverified", "Replaced your selection in Notes."),
    ("failed", "Couldn't paste in Notes. The rewrite is on your clipboard."),
    ("copied", "Copied to the clipboard, not pasted."),
    ("not_pasted", "Not pasted."),
    ("", ""), ("nonsense", ""), ("__proto__", ""), ("toString", ""),
])
@needs_node
def test_what_became_of_the_text_is_said_in_a_sentence(paste, fragment):
    v = js(f"L.transformEntryView({{before: 'a b', after: 'a c', app: 'Notes', paste: {json.dumps(paste)}}})")
    assert v["status"] == fragment


@needs_node
@pytest.mark.parametrize("item", ["null", "undefined", "5", "'x'", "{}", "{before: 5, after: null}", "{id: 'x', transform_name: 7}"])
def test_an_odd_entry_still_draws(item):
    v = js(f"L.transformEntryView({item})")
    assert v["name"] and v["changesLabel"] and v["status"] == ""
    assert v["id"] is None or isinstance(v["id"], int)


# ── the section is in the page, and wired ────────────────────────────────────

def test_transforms_is_a_section_of_settings_marked_beta():
    html = read("index.html")
    assert "data-sec=\"transforms\" onclick=\"showSettingsSection('transforms')\">Transforms (beta)</button>" in html
    assert 'id="secTransforms" data-sec="transforms" hidden' in html
    assert '<h1 class="ptitle">Transforms (beta)</h1>' in section()
    assert "const SETTINGS_SECTIONS = ['general', 'style', 'transforms'," in code_only(read("app.js"))


def test_every_control_has_a_name_and_one_tab_stop():
    sec = section()
    for ident in ["transformsToggle", "transformsRun", "peOutline"] + [f"polishRule-{k}" for k in tf.POLISH_RULE_KEYS]:
        assert f'<label for="{ident}">' in sec, ident
    assert 'aria-label="A rule of your own for Polish"' in sec
    assert 'aria-label="Name of the new transform"' in sec and 'aria-label="What the new transform should do"' in sec
    assert sec.count('class="tog" role="switch"') == 1 + len(tf.POLISH_RULE_KEYS)
    assert "tabindex" not in sec
    for button in re.findall(r"<button[^>]*>([^<]*)</button>", sec):
        assert button.strip(), "a button with no words has an aria-label instead"
    assert 'aria-describedby="transformsDesc"' in sec and 'aria-describedby="peCounter peHint"' in sec


def test_the_rule_switches_are_the_apps_rules_in_the_apps_order():
    sec = section()
    ids = re.findall(r'id="polishRule-(\w+)"', sec)
    assert ids == list(tf.POLISH_RULE_KEYS)
    for k in tf.POLISH_RULE_KEYS:
        assert f"setPolishRule('{k}', this.checked)" in sec


def test_the_section_says_where_the_text_goes_and_that_undo_works():
    sec = " ".join(re.sub(r"<[^>]+>", " ", section()).split())
    assert "the text you selected is read from the app in front, with Accessibility or by copying it" in sec
    assert "sent to the provider you chose" in sec
    assert "Nothing is read or sent at any other time" in sec
    assert "Your app's own Undo takes it back" in sec
    assert "Reset to default" in sec
    assert "up to 5" in sec or "You can keep up to 5" in sec


def test_the_privacy_page_says_it_too():
    html = read("index.html")
    priv = html[html.index('id="secPrivacy"'):]
    priv = " ".join(re.sub(r"<[^>]+>", " ", priv[:priv.index("</section>")]).split())
    assert "Only when you use a transform, Waffler reads the text you selected" in priv
    assert "sends it to your provider on your own key" in priv
    assert "Delete all my data clears it" in priv


def test_sentence_case_no_dashes_in_the_section():
    sec = section()
    text = re.sub(r"<[^>]+>", " ", sec)
    assert "—" not in sec and "–" not in sec
    for h in re.findall(r"<h[123][^>]*>([^<]+)</h[123]>", sec):
        assert h[0].isupper(), h
        assert re.sub(r"\b(Waffler|Polish|Prompt|Engineer|Transforms|Your|beta)\b", "", h[1:]) == \
            re.sub(r"\b(Waffler|Polish|Prompt|Engineer|Transforms|Your|beta)\b", "", h[1:]).lower() or "(beta)" in h, h
    for label in re.findall(r"<label[^>]*>([^<]+)</label>", sec):
        assert label[0].isupper() and not label.isupper(), label
    assert "Transforms (beta)" in text


def test_the_handlers_exist_and_every_save_goes_through_one_path():
    app = code_only(read("app.js"))
    sec = section()
    for handler in re.findall(r'on(?:change|input|blur|click|submit)="(\w+)\(', sec):
        assert f"function {handler}(" in app or f"async function {handler}(" in app, handler
    block = app[app.index("let _txEditing"):app.index("function renderClipboardRow")]
    assert block.count("pywebview.api.save_settings(") == 1, "one place saves"
    assert "save_settings({ transforms: part })" in block
    # The app's own sentence is shown, not a made-up one.
    assert "(r && r.error) || \"Couldn't save that. Try again.\"" in block
    # A refused save puts the controls back to what is saved.
    assert "renderTransformsSection();\n    return false;" in block
    assert "confirm(" not in app


def test_the_shortcut_uses_the_shared_hotkey_dialog_and_its_own_call():
    app = code_only(read("app.js"))
    sec = section()
    assert "onclick=\"openHotkeyCapture('transform')\"" in sec
    open_fn = app[app.index("function openHotkeyCapture("):app.index("function closeHotkeyCapture")]
    assert "_captureTarget = target === 'transform' ? 'transform' : 'dictation';" in open_fn
    assert "presets.hidden = forTransform ||" in open_fn and "reset.hidden = forTransform" in open_fn
    save = app[app.index("async function saveHotkeyCapture"):app.index("async function refreshAll")]
    assert "if (_captureTarget === 'transform') { await saveTransformHotkey(keys, fail); return; }" in save
    assert "api.save_hotkey_config(" in save, "the dictation hotkey is saved as before"
    assert "api.save_transform_hotkey(JSON.stringify(keys))" in app
    close = app[app.index("function closeHotkeyCapture"):app.index("function _announceCaptured")]
    assert "_captureTarget = 'dictation'" in close, "the next time it opens it is the dictation hotkey's again"


def test_the_outline_is_saved_after_typing_stops_and_never_when_too_long():
    app = code_only(read("app.js"))
    assert "_outlineTimer = setTimeout(commitOutline, 800)" in app
    fn = app[app.index("async function commitOutline"):app.index("async function resetOutline")]
    assert "if (o.over || o.text === v.outline.trim()) return;" in fn
    assert "skeleton: ''" in app[app.index("async function resetOutline"):app.index("function onTransformPromptInput")], \
        "reset saves an empty outline, which the app reads as the built-in one"


def test_toasts_come_from_the_logic_file():
    app = code_only(read("app.js"))
    for call in ("WL.transformSaved('enabled'", "WL.transformSaved('hotkey'", "WL.transformSaved('default'",
                 "WL.transformSaved('added'", "WL.transformSaved('removed'", "WL.transformSaved('outline')"):
        assert call in app, call


def test_the_settings_are_drawn_once_loaded_and_after_every_save():
    app = code_only(read("app.js"))
    assert "renderTransformsSection();" in app[app.index("async function loadSettings()"):app.index("function renderCleanupRow")]
    refresh = app[app.index("async function refreshTransformSettings"):app.index("function renderTransformsSection")]
    assert "get_settings()" in refresh and "renderTransformsSection();" in refresh


def test_the_journal_draws_a_rewrite_with_its_own_card():
    app = code_only(read("app.js"))
    assert "if (item && item.kind === 'transform') return makeTransformCard(item, isNew);" in app
    card = app[app.index("function makeTransformCard"):app.index("window.waffler_show_transform")]
    # Nothing from the entry reaches the page unescaped.
    assert "escHtml(p.text)" in card and "escHtml(v.name)" in card and "escHtml(v.status)" in card
    assert "innerHTML" in card and "${v.before}" not in card and "${v.after}" not in card
    # Copy for either text, Delete through the shared path.
    assert "copyItem(v.after, this)" in card and "copyItem(v.before, this)" in card
    assert "deleteEntry(item, div)" in card and 'class="rbtn rbtn-sm card-del"' in card
    assert "data-idle=\"Copy result\"" in card and "data-idle=\"Copy original\"" in card
    assert "const idle = btnEl.dataset.idle || 'Copy';" in app


def test_a_new_rewrite_joins_the_journal_without_a_second_message():
    app = code_only(read("app.js"))
    refresh = app[app.index("window.waffler_refresh = "):]
    refresh = refresh[:refresh.index("\n};")]
    assert "newItem.kind !== 'transform'" in refresh and "'Added to your Journal.'" in refresh
    assert "newItem.kind === 'transform'" in refresh and "newItem.after" in refresh, "search finds it"


def test_show_in_journal_finds_the_entry_even_if_the_page_has_not_loaded_it():
    app = code_only(read("app.js"))
    fn = app[app.index("window.waffler_show_transform = "):]
    fn = fn[:fn.index("\n};")]
    assert "showPage('home')" in fn and "await renderFeed()" in fn
    assert ".tx-card[data-id=" in fn and "scrollIntoView" in fn
    assert "_searchText = ''" in fn, "a search that hides it is cleared"


def test_the_transforms_css_uses_tokens_only():
    css = read("style.css")
    start = css.index("/* Settings, Transforms (beta)")
    block = css[start:css.index("/* ── Keys and providers", start)]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), "colours come from tokens"
    assert "--faint" not in block, "never for readable text"
    assert "animation" not in block and "transition" not in block
    assert ".tx-text del" in block and ".tx-text ins" in block
    narrow = css[css.index("@media (max-width: 560px) {"):]
    assert ".tx-add { flex-wrap: wrap; }" in narrow[:narrow.index("\n}\n")]


def test_the_menu_item_and_the_page_use_the_same_name():
    app_py = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "Transform selected text" in app_py and "Transform selected text" in read("logic.js")


def test_a_deleted_transform_can_be_brought_back_and_is_found_by_its_id():
    """Found in review: the list is drawn again after every save, so a second
    click on the same place meant the next row. The click names the transform,
    and a line under the list undoes the removal."""
    app = code_only(read("app.js"))
    sec = section()
    assert 'id="transformsUndo" role="status" aria-live="polite" hidden' in sec
    assert 'id="transformsUndoBtn">Undo</button>' in sec
    assert "deleteTransform(b.getAttribute('data-id'))" in app
    fn = app[app.index("async function deleteTransform"):app.index("function renderClipboardRow")]
    assert "before.find((c) => c.id === id)" in fn and "_txShowUndo(WL.transformSaved('removed', gone.name)" in fn
    assert "saveTransforms({ custom: before }" in fn, "Undo saves the whole list as it was, ids and all"
    assert "setTimeout(_txHideUndo, 10000)" in app


def test_a_refused_edit_keeps_what_was_typed_and_says_why_in_the_form():
    app = code_only(read("app.js"))
    fn = app[app.index("async function saveTransformEdit"):app.index("function _txShowUndo")]
    assert "const draft = { i, name, prompt };" in fn and "_txDraft = draft;" in fn
    assert "shown.textContent = said.textContent;" in fn
    render = app[app.index("function renderCustomTransforms"):app.index("async function setTransformsEnabled")]
    assert "d ? d.name : c.name" in render and "d ? d.prompt : c.prompt" in render


def test_the_edit_form_is_drawn_from_the_saved_transform_otherwise():
    app = code_only(read("app.js"))
    open_fn = app[app.index("function openTransformEdit"):app.index("function closeTransformEdit")]
    assert "_txDraft = null;" in open_fn
