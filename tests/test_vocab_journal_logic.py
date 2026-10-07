"""The Atelier Vocabulary and Journal pages (3.15): the helpers in
ui/logic.js that turn real data into what the pages say, and the wiring
that keeps every figure on them real.

Vocabulary: the "last used" and recent times, the "sounds like" box, the
checks the page makes before saving, the table's order and bars, the
summary, and the marks Try a sentence draws. Journal: the day headings,
the week's bars from get_stats, the streak line, and the words the
Vocabulary put in, marked in the text.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
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


NOW = "new Date(2026, 9, 5, 22, 0)"     # Monday 5 October 2026, 22:00


# ── Vocabulary ───────────────────────────────────────────────────────────────

@needs_node
def test_last_used_in_words():
    stamps = ["2026-10-05T21:13:00", "2026-10-04T10:00:00", "2026-10-01T10:00:00",
              "2026-09-27T10:00:00", "2026-09-20T10:00:00", "2026-09-01T10:00:00",
              "2025-12-01T10:00:00", ""]
    assert js(f"{json.dumps(stamps)}.map((t) => L.vocabLastUsed(t, {NOW}))") == [
        "today", "yesterday", "4 days ago", "last week", "2 weeks ago",
        "in September", "in December 2025", "not yet"]


@needs_node
def test_recent_corrections_show_the_time_today_then_the_day_then_the_date():
    assert js(f"['2026-10-05T21:13:00', '2026-10-04T18:05:00', '2026-09-20T10:00:00', 'x']"
              f".map((t) => L.vocabRecentWhen(t, {NOW}))") == ["21:13", "Sun", "20 Sep", ""]


@needs_node
def test_the_sounds_like_box_takes_spellings_between_commas():
    assert js("L.vocabSoundsParse(' grok,  grock ;GROK,,waffle   her')") == ["grok", "grock", "waffle her"]
    assert js("L.vocabSoundsParse('')") == []
    assert js("L.vocabSoundsParse(null)") == []


@needs_node
def test_the_page_says_why_a_spelling_cannot_be_saved():
    entries = "[{word: 'Groq', sounds_like: ['grok']}, {word: 'Waffler', sounds_like: []}]"
    assert js(f"L.vocabSoundsCheck({entries}, 'Groq', ['grok', 'grock'])") == {"ok": True, "error": ""}
    assert js(f"L.vocabSoundsCheck({entries}, 'Waffler', ['Grok'])")["error"] == \
        '"Grok" already sounds like Groq.'
    assert "word of its own" in js(f"L.vocabSoundsCheck({entries}, 'Waffler', ['groq'])")["error"]
    assert "written already" in js(f"L.vocabSoundsCheck({entries}, 'Waffler', ['waffler'])")["error"]
    assert "60 characters" in js(f"L.vocabSoundsCheck({entries}, 'Waffler', ['x'.repeat(61)])")["error"]
    assert "Up to 8" in js(f"L.vocabSoundsCheck({entries}, 'Waffler', 'abcdefghi'.split(''))")["error"]
    assert "no letters" in js(f"L.vocabSoundsCheck({entries}, 'Waffler', ['--'])")["error"]


@needs_node
def test_the_limits_match_the_app():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    import transcribe_whisper as tw
    assert js("L.VOCAB_MAX_SOUNDS") == tw.VOCAB_MAX_SOUNDS


@needs_node
def test_the_table_is_busiest_first_then_a_to_z_with_bars_against_the_busiest():
    rows = js(f"L.vocabRows([{{word: 'b', count: 2}}, {{word: 'A', count: 2, last: '2026-10-05T09:00:00'}},"
              f" {{word: 'z', count: 4, sounds_like: ['x']}}, {{word: 'c', count: 0}}], {NOW})")
    assert [(r["word"], r["count"], r["bar"], r["last"]) for r in rows] == [
        ("z", 4, 100, "not yet"), ("A", 2, 50, "today"), ("b", 2, 50, "not yet"), ("c", 0, 0, "not yet")]
    assert rows[0]["sounds"] == ["x"]
    assert js("L.vocabRows([{word: 'a', count: 0}], new Date())")[0]["bar"] == 0


@needs_node
def test_the_summary_counts_words_and_only_real_corrections():
    assert js("L.vocabSummary(11, 181)") == {
        "words": "11 words", "corrections": "181", "correctionsLabel": "corrections in your Journal"}
    assert js("L.vocabSummary(1, 1)")["correctionsLabel"] == "correction in your Journal"
    assert js("L.vocabSummary(3, 0)") == {"words": "3 words", "corrections": "", "correctionsLabel": ""}


@needs_node
def test_try_a_sentence_marks_what_changed():
    assert js("L.markSegments('send the COBie file', [[9, 14]])") == [
        {"text": "send the ", "mark": False}, {"text": "COBie", "mark": True}, {"text": " file", "mark": False}]
    # Bad or overlapping marks are ignored, never thrown on.
    assert js("L.markSegments('abc', [[2, 9], [1, 0], 'x', [0, 1], [0, 2]])") == [
        {"text": "a", "mark": True}, {"text": "bc", "mark": False}]
    assert js("L.markSegments('', [])") == [{"text": "", "mark": False}]


# ── Journal ──────────────────────────────────────────────────────────────────

@needs_node
def test_the_journal_marks_the_words_the_vocabulary_put_in_as_whole_words():
    segs = js("L.markWords('Push PostHog and Siobhán, not PostHogs or xPostHog.', ['PostHog', 'Siobhán'])")
    assert [s["text"] for s in segs if s["mark"]] == ["PostHog", "Siobhán"]
    assert js("L.markWords('plain', [])") == [{"text": "plain", "mark": False}]
    # No look-behind in the pattern: older Mac web views lack it.
    src = read("logic.js")
    body = src[src.index("function markWords("):src.index("// ── Journal (3.15, Atelier)")]
    assert "(?<" not in body


@needs_node
def test_day_headings():
    assert js(f"['2026-10-05', '2026-10-04', '2026-10-01', 'x'].map((k) => L.dayHeading(k, {NOW}))") == [
        {"label": "Today", "date": "Monday 5 October"},
        {"label": "Yesterday", "date": "Sunday 4 October"},
        {"label": "Thursday", "date": "1 October"},
        {"label": "Earlier", "date": ""},
    ]


@needs_node
def test_the_week_is_the_last_seven_days_of_the_usage_counts():
    bars = js("L.weekBars([1,2,3,4,5,6,7,8,0,10], '2026-09-26')")
    assert [b["words"] for b in bars] == [4, 5, 6, 7, 8, 0, 10]
    assert "".join(b["initial"] for b in bars) == "TWTFSSM"      # ending Monday 5 October
    assert [b["today"] for b in bars] == [False] * 6 + [True]
    assert bars[-1]["height"] == 100 and bars[5]["height"] == 4
    assert [b["words"] for b in js("L.weekBars([], '')")] == [0] * 7


@needs_node
def test_the_streak_line_says_longest_yet_only_when_it_is():
    assert js("L.streakView(12, 12)") == {"title": "12 days in a row", "sub": "Your longest yet"}
    assert js("L.streakView(3, 20)") == {"title": "3 days in a row", "sub": "Your longest: 20 days"}
    assert js("L.streakView(1, 1)") == {"title": "1 day in a row", "sub": ""}
    assert js("L.streakView(0, 5)") == {"title": "", "sub": ""}


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_vocabulary_page_reads_real_counts_and_saves_the_spellings():
    app = read("app.js")
    load = app[app.index("async function loadVocabPage("):app.index("function renderVocab(")]
    assert "pywebview.api.get_vocab_book()" in load
    assert "pywebview.api.try_vocab(text)" in app
    add = app[app.index("async function addVocabWord()"):app.index("async function deleteVocabWord(")]
    assert "pywebview.api.set_vocab(p.words, p.sounds)" in add
    # A respelling is asked about, never made silently.
    assert "_vocabAsk = { existing: r.existing, word: r.word, sounds: fresh };" in add
    # Removing and respelling can be undone.
    assert app.count("_vocabShowUndo(") >= 4


def test_the_journal_draws_the_margin_from_get_stats_and_the_marks_from_vocab_changes():
    app = read("app.js")
    stats = app[app.index("function renderStats("):]
    stats = stats[:stats.index("\n}\n")]
    assert "WL.weekBars(stats.daily_words, stats.daily_start)" in stats
    assert "WL.streakView(stats.streak_days, stats.longest_streak_days)" in stats
    card = app[app.index("function makeCard("):app.index("function toggleRawHandler(")]
    assert "WL.vocabChanges(item)" in card and "WL.markWords(" in card
    # "Show what you said" is still offered whenever the words differ.
    assert "hasStyled ?" in card and "WL.hasOriginal(item)" in card
    html = read("index.html")
    assert 'id="journalRail"' in html and 'id="journalLive"' in html
