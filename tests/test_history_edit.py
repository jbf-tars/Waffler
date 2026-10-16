"""Editing a dictation's text in the Journal (S10; src/history_edit.py).

The corrected text is stored beside the entry as ``edited_text``. The words
that were pasted (``styled``) and the words the speech service wrote
(``text``) are never touched: the first is the record of what Waffler did, the
second is what the Vocabulary suggestions are worked out from. Everything that
shows or reuses the entry's words ("Copy", "Paste last", the Journal search,
CSV and text exports) uses the edited text when there is one, through
``history_meta.final_text``. Deleting, retention and the JSON export see an
ordinary entry with one more field.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import history_edit as E  # noqa: E402
import history_export  # noqa: E402
import history_meta as M  # noqa: E402
import journal_data  # noqa: E402
import pasteboard  # noqa: E402
import privacy_data  # noqa: E402


def entry(**kw):
    base = {"id": 7, "timestamp": "2026-10-16T09:00:00", "text": "use waffle daily",
            "styled": "Use waffle daily.", "word_count": 3, "text_is": "asr_filtered"}
    base.update(kw)
    return base


# ── final_text ───────────────────────────────────────────────────────────────

def test_the_final_text_is_the_edit_when_there_is_one_else_what_was_pasted_else_what_was_heard():
    assert M.final_text(entry(edited_text="Use Waffler daily.")) == "Use Waffler daily."
    assert M.final_text(entry()) == "Use waffle daily."
    assert M.final_text({"text": "only heard"}) == "only heard"


def test_an_empty_or_odd_edit_is_not_one():
    for bad in ("", "   ", None, 5, ["x"]):
        assert M.final_text(entry(edited_text=bad)) == "Use waffle daily."


def test_a_rewrite_or_a_command_has_no_edit_and_keeps_its_own_words():
    assert M.final_text({"kind": "transform", "after": "A", "edited_text": "B"}) == "A"
    assert M.final_text({"kind": "command", "action": "ask", "query": "Q", "edited_text": "B"}) == "Q"


def test_words_of_is_still_what_was_pasted():
    assert M.words_of(entry(edited_text="Use Waffler daily.")) == "Use waffle daily."


# ── what may be edited ───────────────────────────────────────────────────────

def test_a_dictation_can_be_edited_but_not_a_not_sent_card_or_a_rewrite():
    assert E.is_editable(entry()) is True
    assert E.is_editable({"failed": True, "error": "x", "timestamp": "t"}) is False
    assert E.is_editable({"kind": "transform", "before": "a", "after": "b"}) is False
    assert E.is_editable({"kind": "command", "action": "ask", "query": "q"}) is False
    assert E.is_editable("junk") is False
    assert E.is_editable({"timestamp": "t"}) is False        # no words at all


# ── cleaning what was typed ──────────────────────────────────────────────────

def test_line_ends_are_made_one_kind_and_the_outside_trimmed():
    assert E.clean("  Use Waffler\r\ndaily.\r\n\r\nThanks  \n") == ("Use Waffler\ndaily.\n\nThanks", "")


def test_inside_spaces_and_blank_lines_are_left_as_typed():
    assert E.clean("a  b\n\n\nc") == ("a  b\n\n\nc", "")


def test_nothing_typed_is_refused():
    assert E.clean("   \n ") == ("", "empty")
    assert E.clean(None) == ("", "empty")
    assert E.clean(5) == ("", "empty")


def test_a_very_long_edit_is_refused():
    assert E.clean("x" * E.MAX_EDIT_CHARS)[1] == ""
    assert E.clean("x" * (E.MAX_EDIT_CHARS + 1)) == ("", "too_long")


def test_control_characters_are_dropped_but_tabs_and_line_breaks_stay():
    assert E.clean("a\x00b\x07c\td\ne")[0] == "abc\td\ne"


# ── applying it ──────────────────────────────────────────────────────────────

def test_an_edit_adds_edited_text_and_changes_nothing_else():
    old = entry(vocab_changes=[["x", "y"]], quality={"level": "low"}, app="Mail", audio="rec.wav")
    before = json.loads(json.dumps(old))
    new, state = E.apply(old, "Use Waffler daily.")
    assert state == "edited"
    assert new["edited_text"] == "Use Waffler daily."
    assert {k: v for k, v in new.items() if k != "edited_text"} == before
    assert old == before                                   # the original dict is not changed


def test_saving_what_was_pasted_takes_the_edit_off():
    old = entry(edited_text="Use Waffler daily.")
    new, state = E.apply(old, "Use waffle daily.")
    assert state == "cleared" and "edited_text" not in new
    new, state = E.apply(entry(), E.clean("  Use waffle daily.\n")[0])
    assert state == "unchanged" and "edited_text" not in new


def test_a_second_edit_replaces_the_first():
    new, state = E.apply(entry(edited_text="One."), "Two.")
    assert state == "edited" and new["edited_text"] == "Two."


def test_unchanged_when_the_same_edit_is_saved_again():
    new, state = E.apply(entry(edited_text="Use Waffler daily."), "Use Waffler daily.")
    assert state == "unchanged" and new["edited_text"] == "Use Waffler daily."


# ── what uses the final text ─────────────────────────────────────────────────

def test_the_journal_search_finds_the_edited_text_and_still_the_original():
    e = entry(edited_text="Use Waffler daily.")
    assert journal_data.matches(e, "waffler") is True
    assert journal_data.matches(e, "waffle") is True        # what was pasted and heard
    assert journal_data.matches(entry(), "waffler") is False


def test_paste_last_after_a_restart_is_the_edited_text():
    hist = [entry(id=1, styled="Older.", text="older"),
            entry(id=2, edited_text="Use Waffler daily.")]
    last = pasteboard.LastResult(history_loader=lambda: hist)
    assert last.get() == "Use Waffler daily."
    assert last.word_count() == 3


def test_paste_last_skips_an_empty_edit_and_other_kinds():
    hist = [entry(id=1, styled="Real one."),
            {"kind": "transform", "after": "rewrite", "edited_text": "no"},
            entry(id=3, edited_text="   ")]
    assert pasteboard.LastResult(history_loader=lambda: hist).get() == "Use waffle daily."


def test_the_last_result_swaps_the_old_final_text_for_the_new():
    last = pasteboard.LastResult()
    last.set("Use waffle daily.")
    last.swap(M.final_text(entry()), M.final_text(entry(edited_text="Use Waffler daily.")))
    assert last.get() == "Use Waffler daily."


def test_pasteboard_and_history_meta_agree_about_what_is_not_a_dictation():
    assert tuple(pasteboard._NOT_A_DICTATION) == tuple(M.NON_DICTATION_KINDS)


def test_csv_and_text_exports_carry_the_edited_text():
    hist = [entry(edited_text="Use Waffler daily."), entry(id=8, timestamp="2026-10-16T10:00:00")]
    csv_text = history_export.render_csv(hist)
    assert "Use Waffler daily." in csv_text and "Use waffle daily." in csv_text
    assert csv_text.count("Use waffle daily.") == 1             # the edited entry shows its edit only
    txt = history_export.render_txt(hist)
    assert "Use Waffler daily." in txt


def test_the_json_export_keeps_both_texts_as_they_are():
    hist = [entry(edited_text="Use Waffler daily.")]
    out = json.loads(history_export.render_json(hist))
    assert out[0]["edited_text"] == "Use Waffler daily." and out[0]["styled"] == "Use waffle daily."


def test_retention_removes_an_edited_entry_with_its_edit():
    old = entry(timestamp="2020-01-01T09:00:00", edited_text="Use Waffler daily.")
    kept, removed = privacy_data.prune_history([old], 30)
    assert removed == 1 and kept == []


def test_the_stats_still_count_the_words_that_were_dictated():
    hist = [entry(edited_text="A much longer text than was ever said out loud by anyone here.")]
    stats = journal_data.compute_stats(hist, journal_data._day("2026-10-16"))
    assert stats["total_words"] == 3


def test_redoing_a_take_starts_again_from_the_recording_so_the_edit_is_not_carried_over():
    old = entry(edited_text="Use Waffler daily.", audio="rec-1.wav", app="Mail", duration_s=3.0)
    new = M.redone_entry(old, transcript="use waffle every day", styled="Use waffle every day.")
    assert "edited_text" not in new and "edited_text" not in M.KEPT_ON_REDO
    assert new["id"] == 7 and new["audio"] == "rec-1.wav" and new["app"] == "Mail"


def test_the_window_and_the_app_read_the_same_final_text():
    """logic.js finalText and history_meta.final_text are the one rule."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("needs Node.js")
    cases = [entry(), entry(edited_text="Use Waffler daily."), entry(edited_text="  "), {"text": "heard"},
             {"kind": "transform", "after": "B", "edited_text": "C", "styled": "A."}, entry(edited_text=5)]
    code = (f"const L = require({json.dumps(str(ROOT / 'ui' / 'logic.js'))});"
            f"process.stdout.write(JSON.stringify({json.dumps(cases)}.map((c) => L.finalText(c))));")
    out = subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # A rewrite shows its result (``after``) in the app; the window's finalText is for dictations
    # and leaves a rewrite's own card to its own view.
    for case, js_text in zip(cases, got):
        if case.get("kind") == "transform":
            continue
        assert js_text == M.final_text(case), case
