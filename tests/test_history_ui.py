"""The Journal's delete, undo and redo, and the export row (S4): the window's
decisions (ui/logic.js, run in Node) and how app.js and index.html use them.

The behaviour in a page, with a stubbed pywebview.api, was checked in the
Browser pane; these tests keep the pieces from drifting.
"""
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


def js(expr):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def func(src, name):
    start = src.index(f"function {name}(") if f"function {name}(" in src else src.index(f"async function {name}(")
    body = src[start:]
    return body[:body.index("\n}\n")]


def code_only(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ── finding an entry ─────────────────────────────────────────────────────────

@needs_node
def test_an_entry_is_found_by_its_id_or_for_an_old_one_by_its_time_and_words():
    assert js("L.entryRef({id: 7, timestamp: '2026-10-16T10:00:00', text: 'hi'})") == [7, "2026-10-16T10:00:00", "hi"]
    assert js("L.entryRef({timestamp: '2026-10-16T10:00:00', text: 'hi'})") == [None, "2026-10-16T10:00:00", "hi"]
    assert js("L.entryRef({id: '7', timestamp: 't'})") == [None, "t", ""]
    assert js("L.entryRef({id: 0, timestamp: 't', text: 'x'})") == [None, "t", "x"]
    assert js("L.entryRef({id: 1.5, timestamp: 't'})") == [None, "t", ""]
    assert js("L.entryRef(null)") == [None, "", ""]


@needs_node
def test_the_next_page_starts_after_the_oldest_entry_held_by_its_id():
    assert js("L.pageCursor([{id: 9}, {id: 8}, {id: 7}])") == {"beforeId": 7, "offset": 0}
    assert js("L.pageCursor([{id: 9}, {timestamp: 'old'}])") == {"beforeId": None, "offset": 2}
    assert js("L.pageCursor([])") == {"beforeId": None, "offset": 0}
    assert js("L.pageCursor(null)") == {"beforeId": None, "offset": 0}
    assert js("L.pageCursor([{id: 'x'}])") == {"beforeId": None, "offset": 1}


@needs_node
def test_the_window_and_the_recorder_agree_how_many_recordings_are_kept():
    import recent_audio
    assert js("L.RECENT_AUDIO_KEEP") == recent_audio.KEEP


@needs_node
def test_a_restored_entry_goes_where_its_time_says_in_a_newest_first_list():
    items = "[{timestamp: '2026-10-16T12:00:00'}, {timestamp: '2026-10-16T10:00:00'}, {timestamp: '2026-10-15T09:00:00'}]"
    assert js(f"L.insertIndex({items}, {{timestamp: '2026-10-16T11:00:00'}})") == 1
    assert js(f"L.insertIndex({items}, {{timestamp: '2026-10-17T00:00:00'}})") == 0
    assert js(f"L.insertIndex({items}, {{timestamp: '2026-01-01T00:00:00'}})") == 3
    assert js(f"L.insertIndex({items}, {{timestamp: '2026-10-16T10:00:00'}})") == 2, "after an equal time"
    assert js("L.insertIndex([], {timestamp: 't'})") == 0


# ── the words ────────────────────────────────────────────────────────────────

@needs_node
def test_the_undo_line_and_the_failure_messages_are_plain_sentences():
    assert js("L.undoLine('deleted')") == "Entry deleted."
    assert js("L.undoLine('replaced')") == "Entry redone."
    assert js("L.deleteFailedMessage('error')") == "Couldn't delete that entry. Try again."
    assert js("L.deleteFailedMessage('not_found')") == "That entry has already gone."
    assert js("L.undoFailedMessage('expired')") == "Too late to undo that."
    assert js("L.undoFailedMessage('error')") == "Couldn't put that entry back. Try again."


@needs_node
def test_every_reason_redo_can_fail_with_has_its_own_sentence_that_says_the_entry_is_unchanged():
    reasons = ["offline", "blocked", "timeout", "rate_limited", "empty", "missing", "not_found",
               "busy", "not_ready", "error", "not_saved", None]
    said = js(f"{json.dumps(reasons)}.map((r) => L.redoFailedMessage(r))")
    assert len(set(said)) >= 9
    for reason, text in zip(reasons, said):
        assert text.endswith("."), text
        if reason in ("offline", "blocked", "timeout", "rate_limited", "empty", None, "error", "not_saved"):
            assert "unchanged" in text, (reason, text)
    # The reasons Python gives are the keys the window knows.
    import unsent
    for known in (unsent.REASON_OFFLINE, unsent.REASON_BLOCKED, unsent.REASON_TIMEOUT,
                  unsent.REASON_RATE_LIMITED, unsent.REASON_EMPTY):
        assert js(f"L.redoFailedMessage({json.dumps(known)})") != js("L.redoFailedMessage('zzz')"), known


@needs_node
def test_no_new_sentence_has_a_dash_or_a_raw_error_in_it():
    texts = js("""[
      L.undoLine('deleted'), L.undoLine('replaced'), L.deleteFailedMessage('x'), L.undoFailedMessage('x'),
      ...['offline','blocked','timeout','rate_limited','empty','missing','not_found','busy','not_ready','x']
        .map((r) => L.redoFailedMessage(r)),
      L.exportFailed({reason: 'empty'}), L.exportFailed(null),
      L.exportDone({path: '/Users/a/Desktop/x.csv', count: 3}), L.exportDone({path: '/Users/a/x.csv', count: 1}),
      ...L.EXPORT_FORMATS.map((f) => f.label),
    ]""")
    for t in texts:
        assert "\u2014" not in t and "\u2013" not in t and " - " not in t, t
        assert not re.search(r"Error|Exception|Traceback|HTTP|\b[45]\d\d\b", t), t
        assert t[0].isupper() or t[0] in "JC", t


@needs_node
def test_the_export_formats_match_the_ones_python_writes_and_the_page_lists():
    import history_export
    fmts = js("L.EXPORT_FORMATS")
    assert [f["id"] for f in fmts] == list(history_export.FORMATS)
    assert {f["id"]: f["label"] for f in fmts} == history_export.LABELS
    html = read("index.html")
    sel = html[html.index('id="exportFormat"'):]
    sel = sel[:sel.index("</select>")]
    assert re.findall(r'<option value="(\w+)">([^<]+)</option>', sel) == [(f["id"], f["label"]) for f in fmts]
    assert js("L.exportFormat('csv')") == "csv" and js("L.exportFormat('xlsx')") == "json"
    assert js("L.exportFormat(null)") == "json"


@needs_node
def test_the_export_toast_says_how_many_and_where():
    assert js("L.exportDone({path: '/Users/a/Desktop/waffler-history.csv', count: 1234})") == "Saved 1,234 entries to your Desktop."
    assert js("L.exportDone({path: 'C:\\\\Users\\\\a\\\\Desktop\\\\h.json', count: 1})") == "Saved 1 entry to your Desktop."
    assert js("L.exportDone({path: '/Users/a/h.json', count: 2})") == "Saved 2 entries to your home folder."
    assert js("L.exportDone({path: '/Users/a/Desktop/h.json'})") == "Saved to your Desktop."
    assert js("L.exportDone(null)") == "Saved to your home folder."
    assert js("L.exportFailed({reason: 'empty'})") == "Your Journal is empty, so there is nothing to export."
    assert js("L.exportFailed({reason: 'error', error: 'raw'})") == "Couldn't save the file. Try again."


@needs_node
def test_the_new_not_sent_reason_has_its_card_sentence_and_try_again():
    import user_messages
    v = js("L.notSentView({failed: true, unsent_id: 'recording-2026-10-16T10-00-00.wav', "
           "not_sent_reason: 'interrupted', timestamp: '2026-10-16T10:00:00'})")
    assert v["text"] == user_messages.INTERRUPTED_MESSAGE
    assert v["canRetry"] and v["canReveal"] and v["canDelete"]
    assert v["next"] == "Click Try again to send it now."


# ── the Journal's cards ──────────────────────────────────────────────────────

def test_a_card_has_a_labelled_delete_and_redo_only_when_there_is_a_recording():
    app = read("app.js")
    card = func(app, "makeCard")
    assert "const redoButton = item.can_redo" in card
    assert 'class="btn btn-quiet btn-sm card-redo"' in card and "<span>Redo this take</span>" in card
    assert "aria-label=\"${escHtml(when ? `Redo this take from ${when}` : 'Redo this take')}\"" in card
    assert 'class="rbtn rbtn-sm card-del"' in card
    assert "aria-label=\"${escHtml(when ? `Delete the dictation from ${when}` : 'Delete this dictation')}\"" in card
    assert 'title="Delete"' in card and "WI.icon('trash')" in card
    assert "redo.addEventListener('click', () => redoEntry(item, div))" in card
    assert "deleteEntry(item, div)" in card
    # Not sent cards keep their own, confirmed delete.
    assert "card-del" not in func(app, "makeNotSentCard")


def test_delete_asks_the_backend_with_the_entrys_reference_and_leaves_an_undo_in_its_place():
    app = read("app.js")
    fn = func(app, "deleteEntry")
    assert "api.delete_history_item(...WL.entryRef(item))" in fn
    assert "history.splice(at, 1)" in fn
    assert "_makeUndoRow('deleted'" in fn and "card.replaceWith(row)" in fn
    assert "api.undo_history_change(r.undo)" in fn
    assert "history.splice(WL.insertIndex(history, res.item), 0, res.item)" in fn
    # A failed delete changes nothing on screen and says why.
    assert "WL.deleteFailedMessage(r && r.reason)" in fn and "b.disabled = false" in fn
    # Focus goes to Undo, so a keyboard user is not lost, and back to the card after.
    assert "row.querySelector('button').focus()" in fn
    assert "back.querySelector('.card-del')" in fn
    assert "confirm(" not in code_only(app)


def test_the_undo_row_is_a_status_with_a_labelled_button_that_goes_after_ten_seconds():
    app = read("app.js")
    assert "const UNDO_MS = 10000;" in app
    row = func(app, "_makeUndoRow")
    assert "row.setAttribute('role', 'status')" in row and "WL.undoLine(kind)" in row
    assert 'class="link-btn" aria-label="${escHtml(label)}">Undo</button>' in row
    assert "setTimeout(gone, UNDO_MS)" in row and "clearTimeout(timer)" in row
    # Gone also when the entry could not be put back.
    assert "if (!(await undo())) gone();" in row
    # When the time is up the day's heading goes with the last card, and an empty Journal draws its empty state.
    fn = func(app, "deleteEntry")
    assert "_removeCard(row)" in fn and "if (!history.length) drawFeed()" in fn


def test_redo_replaces_the_card_in_place_with_an_undo_above_it_and_says_why_when_it_cannot():
    app = read("app.js")
    fn = func(app, "redoEntry")
    assert "api.redo_history_item(...WL.entryRef(item))" in fn
    assert "Redoing…" in fn and "history[at] = r.item" in fn
    assert "card.replaceWith(fresh)" in fn and "fresh.before(row)" in fn
    assert "_makeUndoRow('replaced'" in fn and "api.undo_history_change(r.undo)" in fn
    assert "_cardSay(card, WL.redoFailedMessage(r && r.reason))" in fn
    # The card is left as it was on a failure: buttons come back.
    assert "b.disabled = false" in fn and "<span>Redo this take</span>" in fn
    assert re.search(r"\.j-status", read("style.css")) and 'class="j-status" role="status" aria-live="polite" hidden' in func(app, "makeCard")


def test_a_new_entry_can_be_redone_at_once_and_the_oldest_loses_its_button():
    app = read("app.js")
    refresh = app[app.index("window.waffler_refresh"):]
    refresh = refresh[:refresh.index("\n};")]
    assert "if (newItem.audio && !newItem.failed) newItem.can_redo = true;" in refresh
    assert refresh.index("_prependCard(newItem)") < refresh.index("_trimRedo();")
    trim = func(app, "_trimRedo")
    assert "WL.RECENT_AUDIO_KEEP" in trim and "delete h.can_redo" in trim and "b.remove()" in trim
    # A recording that rotated out since the page was drawn: the button goes with the message.
    redo = func(app, "redoEntry")
    assert "r.reason === 'missing'" in redo and "delete item.can_redo" in redo and "btn.remove()" in redo
    more = func(app, "loadMoreEntries")
    assert "WL.pageCursor(history)" in more and "at.offset, _searchText, at.beforeId" in more


def test_the_export_row_is_wired_to_the_backend():
    html = read("index.html")
    row = html[html.index('id="exportRow"'):]
    row = row[:row.index("</div>\n            <!--")]
    assert '<label for="exportFormat">Export history</label>' in row
    assert 'onclick="exportHistory(this)"' in row and "<span>Export</span>" in row
    assert 'id="exportTitle"' in row and 'aria-describedby="exportTitle"' in row
    assert 'class="select"' in row and 'class="btn btn-sec btn-sm"' in row
    fn = func(read("app.js"), "exportHistory")
    assert "api.export_history(fmt)" in fn and "WL.exportFormat(sel && sel.value)" in fn
    assert "WL.exportDone(r)" in fn and "WL.exportFailed(r)" in fn and "btn.disabled = false" in fn
    # It sits with what is kept, before the recordings row, in Privacy and data.
    priv = html[html.index('id="secPrivacy"'):]
    assert priv.index('id="exportRow"') < priv.index('id="recentAudioRow"')
    assert priv.index('id="historyRow"') < priv.index('id="exportRow"')


def test_privacy_says_what_each_entry_notes_and_what_the_export_is():
    html = read("index.html")
    assert "Each notes the app you were in, the language and which services handled it." in html
    assert "Each notes the app you were in, the language and which services handled it." in read("app.js")
    for text in re.findall(r'id="exportRow".*?</div>\s*</div>', html, flags=re.S):
        assert "\u2014" not in text and "\u2013" not in text


def test_the_new_styles_exist_and_use_the_tokens():
    css = read("style.css")
    for rule in (".j-undo {", ".j-status {", ".j-acts .card-redo"):
        assert rule in css, rule
    undo = css[css.index(".j-undo {"):]
    undo = undo[:undo.index("}")]
    assert "var(--text-2)" in undo and "#" not in undo
    status = css[css.index(".j-status {"):]
    assert "var(--danger)" in status[:status.index("}")]
    assert "@media" not in undo


def test_nothing_new_in_the_window_phones_home_or_uses_a_native_dialog():
    app = code_only(read("app.js"))
    for fn in ("deleteEntry", "redoEntry", "exportHistory", "_makeUndoRow"):
        body = func(app, fn)
        assert "http" not in body and "fetch(" not in body and "confirm(" not in body and "alert(" not in body

