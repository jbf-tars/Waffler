"""Vocabulary in 3.15's Atelier design: what each word sounds like, how often
it corrected something, and Try a sentence.

* "Sounds like": the spellings speech to text writes for an entry ("grok"
  for Groq). Saved beside vocab.json in vocab_sounds.json, so vocab.json
  stays the plain list older versions read. An entry with spellings is
  matched by them and by its own spelling, never loosely: naming what it
  hears stops the guessing. Entries without any match as before.
* The counts come from the corrections recorded with each Journal entry
  ("vocab_changes"), never from anything else.
* Try a sentence runs the same step a dictation runs, through the bridge.
"""
import ast
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import journal_data as jd  # noqa: E402
import transcribe_whisper as tw  # noqa: E402


@pytest.fixture
def files(tmp_path, monkeypatch):
    vocab, sounds = tmp_path / "vocab.json", tmp_path / "vocab_sounds.json"
    monkeypatch.setattr(tw, "VOCAB_FILE", vocab)
    monkeypatch.setattr(tw, "VOCAB_SOUNDS_FILE", sounds)
    return vocab, sounds


# ── storage ──────────────────────────────────────────────────────────────────

def test_spellings_are_tidied_and_kept_to_their_own_entry():
    words = ["Groq", "Waffler", "PostHog"]
    raw = {
        "groq": ["grok", "  grock  ", "Groq", "waffler", "grok", "", "x" * 61, "--", 7],
        "Waffler": ["grok", "waffle   her"],      # "grok" is Groq's already
        "Gone": ["gone"],                           # not in the list
        "PostHog": "post hog",                      # not a list
    }
    assert tw.clean_sounds_like(raw, words) == {
        "Groq": ["grok", "grock"],                  # keyed as the entry is written
        "Waffler": ["waffle her"],
    }
    assert tw.clean_sounds_like("nope", words) == {}
    many = {"Groq": [f"grok{i}" for i in range(20)]}
    assert len(tw.clean_sounds_like(many, words)["Groq"]) == tw.VOCAB_MAX_SOUNDS


def test_save_writes_both_files_and_vocab_json_stays_a_plain_list(files):
    vocab, sounds = files
    r = tw.save_vocab(["Groq", "Siobhán"], {"Groq": ["grok"], "Siobhán": ["Shavon"]})
    assert r["ok"] and r["sounds_like"] == {"Groq": ["grok"], "Siobhán": ["Shavon"]}
    assert json.loads(vocab.read_text(encoding="utf-8")) == ["Groq", "Siobhán"]
    assert "Shavon" in sounds.read_bytes().decode("utf-8")
    assert tw.load_sounds_like() == {"Groq": ["grok"], "Siobhán": ["Shavon"]}


def test_a_respelt_entry_keeps_its_spellings_and_a_removed_one_loses_them(files):
    tw.save_vocab(["PostHog", "Groq"], {"PostHog": ["post hog"], "Groq": ["grok"]})
    # The list alone, as the page saved it before 3.15: spellings follow.
    tw.save_vocab(["POSTHOG"])
    assert tw.load_sounds_like() == {"POSTHOG": ["post hog"]}
    # Put back with its spellings (the page's Undo sends both).
    tw.save_vocab(["POSTHOG", "Groq"], {"POSTHOG": ["post hog"], "Groq": ["grok"]})
    assert tw.load_sounds_like() == {"POSTHOG": ["post hog"], "Groq": ["grok"]}


def test_limits_are_refused_with_a_sentence(files):
    vocab, sounds = files
    long = tw.save_vocab(["Groq"], {"Groq": ["g" * (tw.VOCAB_MAX_ENTRY_LEN + 1)]})
    assert long["ok"] is False and "characters" in long["error"]
    many = tw.save_vocab(["Groq"], {"Groq": [f"g{i}" for i in range(tw.VOCAB_MAX_SOUNDS + 1)]})
    assert many["ok"] is False and str(tw.VOCAB_MAX_SOUNDS) in many["error"]
    assert tw.save_vocab(["Groq"], ["grok"])["ok"] is False
    assert not vocab.exists() and not sounds.exists()


def test_an_unreadable_spellings_file_is_no_spellings(files):
    vocab, sounds = files
    tw.save_vocab(["Groq"])
    sounds.write_text("{not json", encoding="utf-8")
    assert tw.load_sounds_like() == {}


# ── the matcher ──────────────────────────────────────────────────────────────

WORDS = ["Groq", "Waffler", "PostHog", "COBie", "Siobhán", "Tailscale", "Will", "GPT-4o"]
SOUNDS = {"Groq": ["grok", "grock"], "Waffler": ["waffle her"], "PostHog": ["post hog"],
          "COBie": ["cobby"], "Siobhán": ["Shavon"], "GPT-4o": ["gpt four oh"]}


def test_spellings_are_replaced_by_their_entry():
    text, changes = tw.apply_vocab_changes(
        "send the cobby file to shavon at post hog", WORDS, SOUNDS)
    assert text == "send the COBie file to Siobhán at PostHog"
    assert changes == [("cobby", "COBie"), ("shavon", "Siobhán"), ("post hog", "PostHog")]


def test_a_spelling_of_everyday_words_is_taken_because_you_said_so():
    # Without it, "waffle her" is two everyday words and stays.
    assert tw.apply_vocab_changes("ask waffle her", WORDS)[0] == "ask waffle her"
    assert tw.apply_vocab_changes("ask waffle her", WORDS, SOUNDS)[0] == "ask Waffler"
    # Written joined or hyphenated, it is the same spelling.
    assert tw.apply_vocab_changes("ask waffle-her and wafflEher", WORDS, SOUNDS)[0] == \
        "ask Waffler and Waffler"


def test_an_entry_with_spellings_stops_guessing():
    # Tailscale has none: a near miss is still fixed loosely.
    assert tw.apply_vocab_changes("try tailscail", WORDS, SOUNDS)[0] == "try Tailscale"
    # With a spelling, only that spelling and its own are taken.
    s = dict(SOUNDS, Tailscale=["tail scale"])
    assert tw.apply_vocab_changes("try tailscail", WORDS, s)[0] == "try tailscail"
    assert tw.apply_vocab_changes("try tail scale, tailscale", WORDS, s)[0] == \
        "try Tailscale, Tailscale"


def test_its_own_spelling_still_fixes_the_capitals_and_is_not_reported_when_right():
    text, changes = tw.apply_vocab_changes("groq and Groq", WORDS, SOUNDS)
    assert text == "Groq and Groq" and changes == [("groq", "Groq")]
    assert tw.apply_vocab_changes("Groq rocks", WORDS, SOUNDS) == ("Groq rocks", [])


def test_an_entry_with_digits_keeps_its_own_spelling_and_takes_its_spellings():
    assert tw.apply_vocab_changes("ask gpt four oh or gpt 4o", WORDS, SOUNDS)[0] == \
        "ask GPT-4o or GPT-4o"


def test_a_spelling_is_whole_words_only():
    assert tw.apply_vocab_changes("groks grokking agrok", WORDS, SOUNDS)[0] == "groks grokking agrok"


def test_a_replacement_is_not_matched_again():
    words = ["Groq", "Grog"]
    text, changes = tw.apply_vocab_changes("grok", words, {"Groq": ["grok"]})
    assert (text, changes) == ("Groq", [("grok", "Groq")])


def test_without_spellings_nothing_changes_from_before():
    for t in ["send the cobie file to siobhan", "try tailscail and gpt 4o", "Will will"]:
        assert tw.apply_vocab_changes(t, WORDS, {}) == tw.apply_vocab_changes(t, WORDS)
        assert tw.apply_vocab_changes(t, WORDS, None) == tw.apply_vocab_changes(t, WORDS)


def test_the_marks_are_where_the_changed_words_are():
    text, _changes, spans = tw.apply_vocab_marked(
        "send the cobby file to shavon, try tailscail", WORDS, SOUNDS)
    assert [text[a:b] for a, b in spans] == ["COBie", "Siobhán", "Tailscale"]


def test_the_log_form_takes_spellings_too():
    assert tw.apply_vocab_corrections("grok it", WORDS, SOUNDS) == ("Groq it", ["'grok' → 'Groq'"])


# ── the counts, from the Journal ─────────────────────────────────────────────

def h(ts, changes=None, failed=False):
    item = {"timestamp": ts, "text": "x", "styled": "x"}
    if changes is not None:
        item["vocab_changes"] = changes
    if failed:
        item["failed"] = True
    return item


HISTORY = [
    h("2026-09-20T10:00:00", [["grok", "Groq"]]),
    h("2026-10-04T18:05:00", [["post hog", "PostHog"], ["Shavon", "Siobhán"]]),
    h("2026-10-05T16:40:00", [["grok", "Groq"], ["grock", "Groq"]]),   # one dictation
    h("2026-10-05T17:00:00"),
    h("2026-10-05T18:00:00", [["bad"], "x", ["", "y"], [1, 2]]),
    h("2026-10-05T19:00:00", [["grok", "Groq"]], failed=True),
    h("2026-10-05T21:13:00", [["invisa line", "Invisalign"]]),
]


def test_each_word_counts_the_dictations_it_corrected_and_the_last_one():
    u = jd.vocab_usage(HISTORY)
    assert u["by_entry"] == {
        "groq": {"count": 2, "last": "2026-10-05T16:40:00"},
        "posthog": {"count": 1, "last": "2026-10-04T18:05:00"},
        "siobhán": {"count": 1, "last": "2026-10-04T18:05:00"},
        "invisalign": {"count": 1, "last": "2026-10-05T21:13:00"},
    }


def test_the_recent_corrections_are_newest_first():
    recent = jd.vocab_usage(HISTORY, recent=4)["recent"]
    assert [(r["heard"], r["used"]) for r in recent] == [
        ("invisa line", "Invisalign"), ("grok", "Groq"), ("grock", "Groq"), ("post hog", "PostHog")]
    assert recent[0]["timestamp"] == "2026-10-05T21:13:00"
    assert jd.vocab_usage([])["recent"] == []


def test_the_cache_answers_copies(tmp_path):
    p = tmp_path / "history.json"
    p.write_text(json.dumps(HISTORY), encoding="utf-8")
    cache = jd.HistoryCache(p, lambda: json.loads(p.read_text(encoding="utf-8")))
    first = cache.vocab_usage()
    first["by_entry"]["groq"]["count"] = 99
    first["recent"].clear()
    again = cache.vocab_usage()
    assert again["by_entry"]["groq"]["count"] == 2 and again["recent"]
    assert cache.loads == 1


def test_the_longest_streak_beside_the_current_one():
    days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-10", "2026-10-04", "2026-10-05"]
    s = jd.compute_stats([h(d + "T09:00:00") for d in days], date(2026, 10, 5))
    assert s["streak_days"] == 2 and s["longest_streak_days"] == 3
    assert jd.compute_stats([], date(2026, 10, 5))["longest_streak_days"] == 0


# ── the bridge ───────────────────────────────────────────────────────────────

def _lift(*names):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    nodes = [n for n in api.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    const = next(n for n in tree.body if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], "id", "") == "TRY_VOCAB_MAX_CHARS")
    return ast.Module([const] + nodes, [])


def _api(cache):
    logged = []
    ns = {"_history_cache": cache, "_log_to_file": logged.append}
    exec(compile(_lift("get_vocab_book", "try_vocab", "set_vocab"), "<app.py>", "exec"), ns)
    return ns, logged


def test_the_page_gets_each_word_with_its_spellings_and_counts(files, tmp_path):
    p = tmp_path / "history.json"
    p.write_text(json.dumps(HISTORY), encoding="utf-8")
    ns, _ = _api(jd.HistoryCache(p, lambda: json.loads(p.read_text(encoding="utf-8"))))
    saved = ns["set_vocab"](None, ["GROQ", "PostHog", "Ó Briain"], {"GROQ": ["grok"]})
    assert saved["ok"] and saved["sounds_like"] == {"GROQ": ["grok"]}
    book = ns["get_vocab_book"](None)
    assert book["ok"] and book["total"] == 3          # the rows add up
    assert book["entries"] == [
        {"word": "GROQ", "sounds_like": ["grok"], "count": 2, "last": "2026-10-05T16:40:00"},
        {"word": "PostHog", "sounds_like": [], "count": 1, "last": "2026-10-04T18:05:00"},
        {"word": "Ó Briain", "sounds_like": [], "count": 0, "last": ""},
    ]
    assert book["recent"][0]["used"] == "Invisalign"
    # The list alone (as before 3.15) still answers with the spellings.
    again = ns["set_vocab"](None, ["GROQ", "PostHog"])
    assert again["sounds_like"] == {"GROQ": ["grok"]}


def test_try_a_sentence_runs_the_saved_vocabulary(files, tmp_path):
    ns, _ = _api(jd.HistoryCache(tmp_path / "none.json", list))
    tw.save_vocab(WORDS, SOUNDS)
    r = ns["try_vocab"](None, "send the cobby file to shavon at post hog")
    assert r == {"ok": True, "text": "send the COBie file to Siobhán at PostHog",
                 "changes": [["cobby", "COBie"], ["shavon", "Siobhán"], ["post hog", "PostHog"]],
                 "marks": [[9, 14], [23, 30], [34, 41]]}
    assert ns["try_vocab"](None, None)["text"] == ""
    assert len(ns["try_vocab"](None, "a" * 5000)["text"]) == ns["TRY_VOCAB_MAX_CHARS"]


def test_a_failure_is_an_answer(files, tmp_path):
    class Broken:
        def vocab_usage(self):
            raise OSError("locked")
    ns, logged = _api(Broken())
    assert ns["get_vocab_book"](None) == {"ok": False, "entries": [], "total": 0, "recent": []}
    assert logged and "locked" in logged[0]


# ── in a dictation ───────────────────────────────────────────────────────────

def test_a_dictation_uses_the_spellings_and_records_the_change(files, tmp_path, monkeypatch):
    from _pipeline_harness import FakeTranscriber, fast_limits, history, make_pipeline, run_process
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=5.0, TRANSCRIBE_DEADLINE_BASE_S=5.0,
                TRANSCRIBE_DEADLINE_MAX_S=5.0, STYLE_DEADLINE_S=5.0)
    tw.save_vocab(["Waffler"], {"Waffler": ["waffle her"]})
    p = make_pipeline(tmp_path / "data", transcriber=FakeTranscriber(text="I use waffle her daily"))
    (tmp_path / "data").mkdir(exist_ok=True)
    worker = run_process(p)
    worker.join(5)
    assert not worker.is_alive()
    entry = history(p)[-1]
    assert entry["vocab_changes"] == [["waffle her", "Waffler"]]
    assert "Waffler" in entry["text"]
