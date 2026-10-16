"""Vocabulary v2 in the window (S10): how it is wired and what it promises.

Search, order, select and star on the Vocabulary page; import and export of
the words and of the snippets; Edit text on a Journal card with the words it
suggests. The decisions are in ui/logic.js (tests/test_vocab_v2_logic.py); this
reads index.html, app.js and style.css for the rest: every control has a name,
the keyboard reaches it, nothing is added without a click, and the design's
rules (no hover-only controls on narrow windows, no em dashes, no network).
"""
import re
from pathlib import Path

from test_ui_logic import code_only

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def func(src, name):
    start = src.index(f"function {name}(")
    body = src[start:]
    return body[:body.index("\n}\n")]


def panel():
    html = read("index.html")
    return html[html.index('id="vocabularyPanel"'):html.index("<!-- Update dialog")]


APP = read("app.js")
CODE = code_only(APP)


# ── the page: names, labels, roles ───────────────────────────────────────────

def test_search_is_a_search_box_with_a_name_and_an_announced_result():
    p = panel()
    box = re.search(r'<input type="search" id="vocabSearch"[^>]*>', p).group(0)
    assert 'aria-label="Search your words and what they sound like"' in box
    assert 'aria-describedby="vocabSearchStatus"' in box
    assert 'oninput="onVocabSearch(this.value)"' in box and 'onkeydown="onVocabSearchKey(event)"' in box
    assert '<span class="sr-only" id="vocabSearchStatus" role="status" aria-live="polite"></span>' in p


def test_sort_has_a_visible_label_and_the_four_choices():
    p = panel()
    assert re.search(r'<label class="vt-sort"><span class="vt-sort-k">Sort</span>\s*<select id="vocabSort"', p)
    assert 'onchange="onVocabSort(this.value)"' in p


def test_select_is_a_toggle_whose_name_stays_the_same():
    p = panel()
    assert re.search(r'<button type="button" class="btn btn-sec btn-sm" id="vocabSelectBtn" aria-pressed="false" '
                     r'onclick="toggleVocabSelect\(\)">Select</button>', p)
    bar = p[p.index('id="vocabBulk"'):]
    bar = bar[:bar.index("</div>")]
    assert 'role="group" aria-label="Change the selected words"' in bar
    assert 'id="vocabBulkCount" aria-live="polite"' in bar
    for label in ("Select all shown", "Star", "Unstar", "Remove"):
        assert f">{label}</button>" in bar, label


def test_import_is_a_disclosure_with_a_labelled_box_a_file_choice_and_announced_errors():
    p = panel()
    assert 'id="vocabImportBtn" aria-expanded="false" aria-controls="vocabImport"' in p
    assert 'aria-labelledby="vocabImportTitle"' in p
    ta = re.search(r'<textarea id="vocabImportText"[^>]*>', p).group(0)
    assert 'aria-describedby="vocabImportHelp"' in ta and '<label class="vimport-lbl" for="vocabImportText">' in p
    assert re.search(r'id="vocabImportError" role="alert" hidden', p)
    assert 'id="vocabImportPreview" aria-live="polite" hidden' in p
    # The file input is not in the tab order or the page: the visible button opens it.
    assert re.search(r'<input type="file" id="vocabImportFile" hidden accept=', p)
    assert 'onclick="pickVocabFile()"' in p
    # Choose a file is optional: the pasted rows work without it.
    assert "pasting always works" in p or "Or paste rows here" in p


def test_export_and_import_buttons_are_named_by_their_words():
    p = panel()
    assert re.search(r'id="vocabExportBtn" onclick="exportVocab\(this\)"[^>]*><svg[^>]*><use href="#i-download"/></svg>'
                     r'<span>Export</span>', p)
    assert re.search(r'id="vocabImportBtn"[^>]*><svg[^>]*><use href="#i-upload"/></svg><span>Import</span>', p)


def test_snippets_have_the_same_import_and_a_format_choice_for_export():
    p = panel()
    assert 'id="snipImportBtn" aria-expanded="false" aria-controls="snipImport"' in p
    assert '<label class="sr-only" for="snipExportFormat">Export as</label>' in p
    assert re.search(r'<select id="snipExportFormat" class="select snip-fmt">\s*<option value="csv">CSV</option>\s*'
                     r'<option value="json">JSON</option>', p)
    assert 'onclick="exportSnippets(this)"' in p and 'onclick="applySnipImport()"' in p
    assert re.search(r'<input type="file" id="snipImportFile" hidden accept=', p)


def test_the_suggest_switch_is_a_real_switch_with_a_name_and_a_description():
    p = panel()
    assert re.search(r'<input type="checkbox" class="tog" role="switch" id="learnToggle" aria-describedby="learnDesc" '
                     r'onchange="setLearnFromEdits\(this.checked\)">', p)
    assert '<label class="vlearn-t" for="learnToggle">Suggest words from my edits</label>' in p
    assert "It never adds a word by itself." in p


def test_every_button_in_the_new_markup_has_a_type_so_none_submits_a_form():
    p = panel()
    for tag in re.findall(r"<button\b[^>]*>", p):
        assert "type=" in tag, tag


def test_the_new_icons_exist_and_are_the_ones_the_markup_uses():
    icons = read("icons.js")
    for name in ("star", "upload", "download", "pencil", "file", "search"):
        assert re.search(rf"\b{name}: '<", icons) or re.search(rf"'{name}': '<", icons), name
    used = set(re.findall(r'href="#i-([a-z-]+)"', panel()))
    assert {"upload", "download", "file", "search"} <= used


# ── the table ────────────────────────────────────────────────────────────────

def test_each_row_has_a_star_that_keeps_one_name_and_says_whether_it_is_pressed():
    render = func(APP, "renderVocab")
    assert 'aria-pressed="${r.starred ? \'true\' : \'false\'}" aria-label="Star ${w}"' in render
    assert 'aria-label="Select ${w}"' in render
    assert "WL.vocabRows(entries, new Date(), { sort: _vocabSort, query: _vocabQuery })" in render
    assert "WL.vocabKeepSelected(" in render and "WL.vocabSearchAnnouncement(" in render


def test_the_checkbox_column_is_only_there_while_selecting():
    render = func(APP, "renderVocab")
    assert "const check = pick" in render and "class=\"vt${pick ? ' is-select' : ''}\"" in render
    css = read("style.css")
    assert re.search(r"\.vt\.is-select \.vt-h, \.vt\.is-select \.vt-r:not\(\.vt-ask\):not\(\.vt-edit\) \{\s*"
                     r"grid-template-columns: 22px", css)


def test_no_matches_says_so_and_offers_to_clear():
    render = func(APP, "renderVocab")
    assert "No words match &ldquo;" in render and 'onclick="clearVocabSearch()"' in render


def test_starring_and_removing_send_the_stars_so_undo_brings_them_back():
    save = func(APP, "_vocabSave")
    assert "pywebview.api.set_vocab(p.words, p.sounds, p.starred)" in save
    assert "starred: entries.filter((e) => e.starred).map((e) => e.word)" in func(APP, "_vocabPayload")
    # The page of 3.15.1 sent the words and spellings only; the one-word add and remove still do.
    assert "pywebview.api.set_vocab(p.words, p.sounds)" in func(APP, "addVocabWord")


def test_bulk_remove_is_undoable_and_keeps_focus_in_the_list():
    fn = func(APP, "bulkRemoveVocab")
    assert "_vocabShowUndo(WL.vocabBulkDone('remove', words)" in fn
    assert "WL.focusAfterRemove(at, left.length)" in fn
    assert "await _vocabSave(before)" in fn
    # Only the words that are shown and selected are removed.
    assert "_vocabBook.entries.map((e) => e.word).filter((w) => _vocabSelected.has(w))" in fn


def test_bulk_star_is_undoable_too():
    fn = func(APP, "bulkStarVocab")
    assert "_vocabShowUndo(WL.vocabBulkDone(on ? 'star' : 'unstar', words)" in fn and "_vocabSave(before)" in fn


def test_the_keyboard_can_do_all_of_it():
    assert "e.key === 'Escape'" in func(APP, "onVocabSearchKey") and "ArrowDown" in func(APP, "onVocabSearchKey")
    assert "document.addEventListener('keydown'" in CODE and "toggleVocabSelect();" in CODE
    ip = func(APP, "makeImportPanel")
    assert "e.key === 'Escape'" in ip and "e.key === 'Enter' && (e.ctrlKey || e.metaKey)" in ip
    # Opening an import puts the cursor in the paste box; closing returns it to the button.
    assert "if (t) t.focus();" in ip and "if (refocus) b.focus();" in ip


def test_the_order_is_remembered_in_a_way_that_cannot_break_the_page():
    assert "window.localStorage.getItem('waffler.vocabSort')" in APP
    assert re.search(r"try \{ window\.localStorage\.setItem\('waffler\.vocabSort', _vocabSort\); \} catch \(_\)", APP)
    assert re.search(r"try \{ _vocabSort = WL\.vocabSortId\(window\.localStorage\.getItem\('waffler\.vocabSort'\)\); \} "
                     r"catch \(_\) \{ _vocabSort = 'used'; \}", APP)


# ── import ───────────────────────────────────────────────────────────────────

def test_a_file_is_read_as_bytes_and_sent_as_base64_so_its_encoding_is_kept():
    ip = func(APP, "makeImportPanel")
    assert "arrayBuffer" in ip and "WL.bytesToBase64(new Uint8Array(buf))" in ip
    assert "payload = { content: WL.bytesToBase64(new Uint8Array(buf)), base64: true };" in ip
    assert "IMPORT_MAX_BYTES" in ip and "f.size > IMPORT_MAX_BYTES" in ip


def test_the_preview_is_shown_before_anything_is_saved_and_add_is_a_second_step():
    ip = func(APP, "makeImportPanel")
    prev = ip[ip.index("async function preview()"):ip.index("async function apply()")]
    app_ = ip[ip.index("async function apply()"):ip.index("function open()")]
    assert "api[c.previewApi]" in prev and "api[c.applyApi]" not in prev
    assert "api[c.applyApi]" in app_ and "if (!ready || !payload) return;" in app_
    # The Add button starts disabled, and only a preview with something to add enables it.
    assert 'id="vocabImportGo" onclick="applyVocabImport()" disabled' in read("index.html")
    assert "go.disabled = !out.view.canApply;" in prev


def test_an_import_can_be_undone_by_putting_the_old_lists_back_through_set_vocab():
    fn = func(APP, "_vocabImported")
    assert "pywebview.api.set_vocab(before.words, before.sounds_like, before.starred)" in fn
    assert "_vocabShowUndo(WL.vocabImportDone(r)" in fn
    snip = func(APP, "_snipImported")
    assert "_snipShowUndo(WL.snippetImportDone(r)" in snip and "_snipSave(before)" in snip


def test_the_import_wiring_names_the_real_bridge_calls():
    assert "previewApi: 'preview_vocab_import', applyApi: 'import_vocabulary'" in APP
    assert "previewApi: 'preview_snippets_import', applyApi: 'import_snippets'" in APP
    assert "api.export_vocabulary()" in func(APP, "exportVocab")
    assert "api.export_snippets(fmt)" in func(APP, "exportSnippets")


def test_the_previews_words_are_escaped_and_long_ones_are_cut():
    fn = func(APP, "_importPreviewHtml")
    assert fn.count("escHtml") >= 6 and "map(escHtml)" in fn
    assert "t.slice(0, 40)" in fn
    assert "innerHTML = out.html" in func(APP, "makeImportPanel")        # built only from escaped pieces


def test_each_visit_starts_with_the_panels_closed_and_empty():
    assert "_vocabImport.close(false);" in func(APP, "loadVocabPage")
    assert "_snipImport.close(false);" in func(APP, "loadSnippets")


# ── Edit text, and what it suggests ──────────────────────────────────────────

def test_the_card_shows_the_edited_text_and_copies_it():
    card = func(APP, "makeCard")
    assert "const displayText = WL.finalText(item);" in card
    assert "copyItem(textEl.classList.contains('raw') ? rawText : displayText, this)" in card
    assert "WL.isEdited(item)" in card and "Edited by you. Copy and Paste last use your text." in card


def test_edit_text_is_a_button_with_the_time_in_its_name_and_opens_a_labelled_box():
    card = func(APP, "makeCard")
    assert "`Edit the text of the dictation from ${when}`" in card and "<span>Edit text</span>" in card
    fn = func(APP, "beginEdit")
    assert 'aria-label="Text of this dictation"' in fn and "form.setAttribute('aria-label'" in fn
    assert "Esc cancels" in fn and "Cmd+Enter" in fn and "Ctrl+Enter" in fn
    assert "ta.focus();" in fn


def test_escape_cancels_and_focus_returns_to_edit_text():
    fn = func(APP, "beginEdit")
    assert "e.key === 'Escape'" in fn and "endEdit(card, true)" in fn
    end = func(APP, "endEdit")
    assert "btn.focus()" in end and "classList.remove('is-editing')" in end


def test_saving_goes_through_the_bridge_and_never_touches_the_vocabulary():
    fn = func(APP, "saveEdit")
    assert "api.edit_history_text(...WL.entryRef(item), value)" in fn
    assert "set_vocab" not in fn and "addSuggestedWord" not in fn
    # What the app says about the edit wins over what the card thought.
    assert "if (!r.item.edited_text) delete merged.edited_text;" in fn


def test_suggestions_are_shown_but_nothing_is_added_until_add_is_pressed():
    show = func(APP, "showSuggestions") + func(APP, "_suggestionRow")
    assert "set_vocab" not in show and "api.set_vocab" not in show
    assert 'data-act="add"' in show and 'data-act="dismiss"' in show
    assert "addSuggestedWord(card, box, row, v)" in show
    add = func(APP, "addSuggestedWord")
    assert "api.set_vocab(p.words, p.sounds)" in add


def test_add_goes_the_pages_own_way_with_its_checks_and_an_undo():
    add = func(APP, "addSuggestedWord")
    assert "WL.vocabAdd(_vocabWords, v.word, WL.VOCAB_MAX_ENTRY_LEN)" in add
    assert "WL.vocabSoundsCheck(entries, r.word, v.sounds, WL.VOCAB_MAX_ENTRY_LEN)" in add
    assert "entries.length >= 500" in add
    done = func(APP, "_suggestionAdded")
    assert "set_vocab(before.words, before.sounds, before.starred)" in done
    assert "setTimeout(() => _dropSuggestion(card, box, done), UNDO_MS)" in done
    assert "role" in done and "'status'" in done


def test_the_suggestions_group_is_named_and_each_button_says_which_word():
    show = func(APP, "showSuggestions")
    assert "box.setAttribute('aria-label', 'Words to add to your Vocabulary')" in show
    row = func(APP, "_suggestionRow")
    assert "`Add ${v.word} to your Vocabulary`" in row and "`Don't add ${v.word}`" in row


def test_the_dictation_form_is_wide_enough_and_the_actions_get_out_of_its_way():
    css = read("style.css")
    assert ".transcript-card.j-ent.is-editing { grid-template-columns: 76px minmax(0, 1fr); }" in css
    assert ".j-ent.is-editing .j-acts { display: none; }" in css


# ── the look ─────────────────────────────────────────────────────────────────

def test_a_star_is_the_dark_gold_ink_not_the_light_honey():
    css = read("style.css")
    assert re.search(r'\.vt-star\[aria-pressed="true"\] \{ color: var\(--accent-ink\); \}', css)
    assert '.vt-star[aria-pressed="true"] .ic { fill: currentColor; }' in css


def test_nothing_is_hidden_until_hover_on_a_narrow_window():
    css = read("style.css")
    narrow = css[css.index("@media (max-width: 640px) {\n  .vtools .j-search-input"):]
    narrow = narrow[:narrow.index("\n}\n")]
    assert ".vt-star { opacity: 1; }" in narrow
    assert ".vt-x { grid-column: 2; grid-row: 1; opacity: 1; }" in css


def test_the_new_styles_use_the_themes_tokens_not_colours_of_their_own():
    css = read("style.css")
    block = css[css.index("/* Vocabulary v2 (S10)"):css.index("/* Snippets: under the words.")]
    assert not re.search(r"#[0-9A-Fa-f]{3,6}\b", block), "a hard-coded colour"
    assert "var(--accent-ink)" in block and "var(--honey-wash)" in block


def test_no_em_dash_in_what_the_window_says():
    for text in (panel(), func(APP, "beginEdit"), func(APP, "_importPreviewHtml"), func(APP, "addSuggestedWord")):
        assert "—" not in text and "–" not in text


def test_nothing_new_asks_the_network_for_anything():
    for text in (panel(), read("style.css")):
        assert not re.search(r'(?:src|href)="https?://', text)
        assert "url(http" not in text
