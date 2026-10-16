"""The page's own logic for Vocabulary v2 (S10), run in Node from ui/logic.js:
search, the four orders, selection, what the import preview says, how a file
becomes base64 for the bridge, and what the Journal shows for an edited
dictation and for a suggested word. Pure functions; no window.
"""
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
UI = ROOT / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")

NOW = "new Date(2026, 9, 16, 12, 0)"

WORDS = [
    {"word": "Groq", "sounds_like": ["grok", "grock"], "count": 5, "last": "2026-10-15T10:00:00"},
    {"word": "Sinéad", "sounds_like": ["shinade"], "count": 0, "last": "", "starred": True},
    {"word": "Morta", "sounds_like": ["mortar"], "count": 9, "last": "2026-10-01T10:00:00"},
    {"word": "PostHog", "sounds_like": ["post hog"], "count": 5, "last": "2026-10-16T09:00:00"},
    {"word": "cobie", "sounds_like": [], "count": 0, "last": "", "starred": True},
    {"word": "Álvaro", "sounds_like": [], "count": 2, "last": "2026-09-01T10:00:00"},
]


def js(expr):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def words(sort=None, query=None):
    opts = json.dumps({k: v for k, v in (("sort", sort), ("query", query)) if v is not None})
    return js(f"L.vocabRows({json.dumps(WORDS)}, {NOW}, {opts}).map((r) => r.word)")


# ── the four orders ──────────────────────────────────────────────────────────

@needs_node
def test_the_default_order_is_the_one_the_page_always_had():
    assert words() == ["Morta", "Groq", "PostHog", "Álvaro", "cobie", "Sinéad"]
    assert words("used") == words()
    assert js("L.VOCAB_SORT_DEFAULT") == "used"


@needs_node
def test_a_to_z_ignores_case_and_accents():
    assert words("az") == ["Álvaro", "cobie", "Groq", "Morta", "PostHog", "Sinéad"]


@needs_node
def test_starred_first_then_most_used_then_a_to_z():
    assert words("starred") == ["cobie", "Sinéad", "Morta", "Groq", "PostHog", "Álvaro"]


@needs_node
def test_recently_used_puts_the_newest_first_and_never_used_last():
    assert words("recent") == ["PostHog", "Groq", "Morta", "Álvaro", "cobie", "Sinéad"]


@needs_node
def test_an_unknown_order_is_the_default_and_nothing_is_lost():
    assert js("L.vocabSortId('nope')") == "used" and js("L.vocabSortId(null)") == "used"
    assert js("L.vocabSortId('az')") == "az"
    assert sorted(words("sideways")) == sorted(w["word"] for w in WORDS)


@needs_node
def test_every_order_is_the_same_each_time_and_does_not_change_the_list_it_was_given():
    for sort in ("used", "recent", "starred", "az"):
        assert words(sort) == words(sort)
    assert js(f"(() => {{ const a = {json.dumps(WORDS)}; L.vocabSortRows(a, 'az'); return a.map((e) => e.word); }})()") \
        == [w["word"] for w in WORDS]


@needs_node
def test_the_choices_have_plain_labels():
    assert js("L.VOCAB_SORTS") == [
        {"id": "used", "label": "Most used"}, {"id": "recent", "label": "Recently used"},
        {"id": "starred", "label": "Starred first"}, {"id": "az", "label": "A to Z"}]


@needs_node
def test_the_html_offers_exactly_those_choices():
    html = (UI / "index.html").read_text(encoding="utf-8")
    sel = html[html.index('id="vocabSort"'):]
    sel = sel[:sel.index("</select>")]
    assert re.findall(r'<option value="(\w+)">([^<]+)</option>', sel) == [
        (c["id"], c["label"]) for c in js("L.VOCAB_SORTS")]


# ── search ───────────────────────────────────────────────────────────────────

@needs_node
def test_search_finds_the_word_and_what_it_sounds_like_in_any_case_and_without_accents():
    assert words(query="sinead") == ["Sinéad"]
    assert words(query="ÁLVARO") == ["Álvaro"]
    assert words(query="mortar") == ["Morta"]
    assert words(query="post") == ["PostHog"]
    assert words(query="  grock ") == ["Groq"]


@needs_node
def test_every_word_typed_has_to_be_found():
    assert words(query="post hog") == ["PostHog"]
    assert words(query="groq mortar") == []
    assert words(query="o") == ["Morta", "Groq", "PostHog", "Álvaro", "cobie"]


@needs_node
def test_no_search_shows_everything_in_the_chosen_order():
    assert words("az", "") == words("az") and words("az", "   ") == words("az")


@needs_node
def test_search_and_order_work_together():
    assert words("az", "o") == ["Álvaro", "cobie", "Groq", "Morta", "PostHog"]


@needs_node
def test_the_bars_stay_against_the_busiest_word_of_the_whole_list():
    rows = js(f"L.vocabRows({json.dumps(WORDS)}, {NOW}, {{query: 'alvaro'}})")
    assert len(rows) == 1 and rows[0]["count"] == 2 and rows[0]["bar"] == round(2 / 9 * 100)


@needs_node
def test_the_row_says_whether_it_is_starred():
    rows = js(f"L.vocabRows({json.dumps(WORDS)}, {NOW}, {{sort: 'starred'}})")
    assert [r["starred"] for r in rows][:2] == [True, True] and not any(r["starred"] for r in rows[2:])


@needs_node
def test_a_search_is_announced_in_words():
    assert js("[L.vocabSearchAnnouncement('', 3, 10), L.vocabSearchAnnouncement('x', 0, 10), "
              "L.vocabSearchAnnouncement('x', 1, 10), L.vocabSearchAnnouncement('x', 4, 10), "
              "L.vocabSearchAnnouncement('x', 10, 10), L.vocabSearchAnnouncement('x', 1200, 1500)]") == [
        "", "No words match.", "1 word matches.", "4 of 10 words match.", "10 words match.",
        "1,200 of 1,500 words match."]


@needs_node
def test_folding_text_for_matching():
    assert js("[L.foldText('Sinéad'), L.foldText('ÓBRIAIN'), L.foldText(null), L.foldText('Zoë')]") == [
        "sinead", "obriain", "", "zoe"]


# ── selection ────────────────────────────────────────────────────────────────

@needs_node
def test_only_words_still_shown_stay_selected():
    assert js("L.vocabKeepSelected(['a', 'b', 'c'], ['b', 'c', 'd'])") == ["b", "c"]
    assert js("L.vocabKeepSelected(['a'], [])") == []
    assert js("L.vocabKeepSelected(null, ['a'])") == [] and js("L.vocabKeepSelected(['a'], null)") == []


@needs_node
def test_the_selected_count_and_the_undo_lines():
    assert js("[L.vocabSelectedLabel(0), L.vocabSelectedLabel(1), L.vocabSelectedLabel(1200)]") == [
        "0 selected", "1 selected", "1,200 selected"]
    assert js("[L.vocabBulkDone('remove', ['Groq']), L.vocabBulkDone('remove', ['a', 'b', 'c']), "
              "L.vocabBulkDone('star', ['a', 'b']), L.vocabBulkDone('unstar', ['Groq'])]") == [
        "Removed Groq.", "Removed 3 words.", "Starred 2 words.", "Unstarred Groq."]


# ── what the import preview says ─────────────────────────────────────────────

def view(**counts):
    base = {"rows": 0, "new": 0, "updated": 0, "already": 0, "skipped": 0}
    base.update(counts)
    return json.dumps({"counts": base, "header": False})


@needs_node
def test_the_preview_names_what_would_change_and_offers_to_add_only_when_something_would():
    v = js(f"L.vocabImportView({view(rows=7, new=3, updated=1, already=2, skipped=1)})")
    assert [p["text"] for p in v["parts"]] == [
        "3 new words", "1 word gains a spelling or a star", "2 already there", "1 left out"]
    assert v["canApply"] is True and v["button"] == "Add to my Vocabulary"
    assert "Nothing is saved until you add it" in v["headline"]


@needs_node
def test_a_file_with_only_new_words_says_how_many_on_the_button():
    assert js(f"L.vocabImportView({view(rows=1, new=1)})")["button"] == "Add 1 word"
    assert js(f"L.vocabImportView({view(rows=4, new=4)})")["button"] == "Add 4 words"


@needs_node
def test_nothing_new_cannot_be_added():
    v = js(f"L.vocabImportView({view(rows=3, already=3)})")
    assert v["canApply"] is False and v["headline"] == "There is nothing new in that."
    v = js(f"L.vocabImportView({view(rows=2, skipped=2)})")
    assert v["canApply"] is False and [p["kind"] for p in v["parts"]] == ["skipped"]
    v = js(f"L.vocabImportView({view()})")
    assert v["canApply"] is False and v["headline"] == "Waffler found no rows in that." and v["parts"] == []


@needs_node
def test_the_done_lines():
    assert js(f"L.vocabImportDone({view(rows=3, new=2, updated=1)})") == "Added 2 words and changed 1 word."
    assert js(f"L.vocabImportDone({view(rows=3, new=1)})") == "Added 1 word."
    assert js(f"L.vocabImportDone({view(rows=3, updated=2)})") == "Changed 2 words."
    assert js(f"L.vocabImportDone({view(rows=3, already=3)})") == "Nothing new to add."
    assert js("L.snippetImportDone({counts: {new: 3}})") == "Added 3 snippets."
    assert js("L.snippetImportDone({counts: {new: 1}})") == "Added 1 snippet."
    assert js("L.snippetImportDone({counts: {new: 0}})") == "Nothing new to add."


@needs_node
def test_the_snippet_preview():
    v = js("L.snippetImportView({counts: {rows: 5, new: 3, already: 1, skipped: 1}, header: true})")
    assert [p["text"] for p in v["parts"]] == ["3 new snippets", "1 already there", "1 left out"]
    assert v["canApply"] is True and v["button"] == "Add 3 snippets" and v["header"] is True
    assert js("L.snippetImportView({counts: {rows: 2, already: 2}})")["canApply"] is False


@needs_node
def test_every_sentence_is_plain_with_no_em_dash():
    text = json.dumps([
        js(f"L.vocabImportView({view(rows=7, new=3, updated=1, already=2, skipped=1)})"),
        js("L.snippetImportView({counts: {rows: 5, new: 3, already: 1, skipped: 1}})"),
        js("[L.vocabBulkDone('remove', ['a', 'b']), L.learnSaved(true), L.learnSaved(false), "
           "L.editSavedLine('edited'), L.editSavedLine('cleared'), L.editFailedMessage('x', ''), "
           "L.editFailedMessage('not_found', '')]"),
        js("L.suggestionView({word: 'Waffler', sounds_like: ['waffle']})"),
    ], ensure_ascii=False)
    assert "\u2014" not in text and "\u2013" not in text and " - " not in text


@needs_node
def test_a_toast_after_an_export_says_where_and_how_many():
    assert js("L.listExportDone({path: '/Users/j/Desktop/w.csv', count: 12}, 'words')") == "Saved 12 words to your Desktop."
    assert js("L.listExportDone({path: 'C:\\\\Users\\\\j\\\\Desktop\\\\w.csv', count: 1}, 'words')") == "Saved 1 word to your Desktop."
    assert js("L.listExportDone({path: '/Users/j/w.json', count: 2}, 'snippets')") == "Saved 2 snippets to your home folder."
    assert js("L.listExportDone(null, 'words')") == "Saved to your home folder."


# ── a file read in the page ──────────────────────────────────────────────────

@needs_node
@pytest.mark.parametrize("raw", [b"", b"abc", "word,sounds_like\r\nSinéad,shinade\r\n".encode("utf-8-sig"),
                                 "word\tsounds\n".encode("utf-16"), bytes(range(256)) * 300])
def test_bytes_become_the_base64_the_app_decodes(raw):
    got = js(f"L.bytesToBase64(new Uint8Array({json.dumps(list(raw))}))")
    assert base64.b64decode(got) == raw


# ── the Journal's edited text and suggestions ────────────────────────────────

@needs_node
def test_the_words_shown_are_the_edit_when_there_is_one():
    assert js("L.finalText({styled: 'Use waffle daily.', text: 'use waffle daily', edited_text: 'Use Waffler daily.'})") \
        == "Use Waffler daily."
    assert js("L.finalText({styled: 'Use waffle daily.', text: 'x'})") == "Use waffle daily."
    assert js("L.finalText({text: 'only heard'})") == "only heard"
    assert js("L.finalText({styled: 'A.', edited_text: '   '})") == "A."
    assert js("L.finalText({kind: 'transform', after: 'B', styled: 'A.', edited_text: 'C'})") == "A."
    assert js("L.finalText(null)") == ""
    assert js("[L.isEdited({edited_text: 'x'}), L.isEdited({edited_text: ' '}), L.isEdited({}), L.isEdited(null)]") == [
        True, False, False, False]


@needs_node
def test_what_you_said_is_still_offered_for_an_edited_dictation():
    item = {"asr_text": "use waffle daily", "text": "use waffle daily", "styled": "Use waffle daily.",
            "edited_text": "Use Waffler daily."}
    assert js(f"L.hasOriginal({json.dumps(item)})") is True
    # Edited back to exactly what was said: nothing left to show.
    item = {"text": "Thanks.", "styled": "Thanks.", "edited_text": "Thanks."}
    assert js(f"L.hasOriginal({json.dumps(item)})") is False


@needs_node
def test_a_suggestion_says_what_to_add_and_what_speech_to_text_wrote():
    v = js("L.suggestionView({word: 'Waffler', sounds_like: ['waffle']})")
    assert v["question"] == 'Add "Waffler" to your Vocabulary?'
    assert v["detail"] == 'Speech to text wrote "waffle".'
    assert v["added"] == 'Added "Waffler".' and v["sounds"] == ["waffle"]
    v = js("L.suggestionView({word: 'Waffler', sounds_like: ['waffle', 'wafler', 'waffler']})")
    assert v["detail"] == 'Speech to text wrote "waffle", "wafler" and "waffler".'
    v = js("L.suggestionView({word: 'PostHog', sounds_like: []})")
    assert v["detail"] == "Its capitals will be kept." and v["sounds"] == []
    assert js("L.suggestionView(null).word") == ""


@needs_node
def test_edit_failures_are_said_in_words_and_the_apps_own_sentence_wins():
    assert js("L.editFailedMessage('error', '')") == "Couldn't save your change. Try again."
    assert js("L.editFailedMessage('not_found', '')") == "That dictation isn't in your Journal any more."
    assert js("L.editFailedMessage('empty', '')").startswith("Type the text first")
    assert js("L.editFailedMessage('too_long', 'Keep it to 20,000 characters or fewer.')") == \
        "Keep it to 20,000 characters or fewer."


@needs_node
def test_the_limits_in_the_page_are_the_apps():
    import transcribe_whisper as tw
    assert js("L.VOCAB_MAX_ENTRY_LEN") == tw.VOCAB_MAX_ENTRY_LEN
    assert js("L.VOCAB_MAX_SOUNDS") == tw.VOCAB_MAX_SOUNDS
