"""Vocabulary as a CSV: the file Waffler writes, the files it reads, and what an
import does to the list (src/dictionary.py).

An import only ever adds: new words, extra "sounds like" spellings for words
already there, stars. It never removes or rewrites anything, applies the limits
the Vocabulary page applies when a word is typed in, and says what it left out
and why. Pure: no files, no window.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import dictionary as D  # noqa: E402
import table_io  # noqa: E402
import transcribe_whisper as tw  # noqa: E402


def plan(text, words=(), sounds=None, starred=()):
    return D.plan_import(D.parse_vocab(text), list(words), dict(sounds or {}), list(starred))


def reasons(p):
    return [(s["row"], s["reason"]) for s in p["skipped"]]


# ── one word, by the page's rules ────────────────────────────────────────────

@pytest.mark.parametrize("raw, word, reason", [
    ("  Groq  ", "Groq", ""),
    ("James   Farrelly", "James Farrelly", ""),
    ("Sinéad", "Sinéad", ""),
    ("GPT-4o", "GPT-4o", ""),
    ("C++", "C++", ""),
    (".NET", ".NET", ""),
    ("می‌خواهم", "می‌خواهم", ""),               # a zero-width non-joiner belongs in Persian
    ("", "", "empty"),
    ("   ", "", "empty"),
    ("a" * 61, "a" * 61, "too_long"),
    ("a" * 60, "a" * 60, ""),
    ("bad\x07word", "bad\x07word", "odd"),
    ("hid‮den", "hid‮den", "odd"),
    ("soft­hyphen", "soft­hyphen", "odd"),
    ("2024", "2024", "no_letters"),
    ("---", "---", "no_letters"),
])
def test_check_word(raw, word, reason):
    assert D.check_word(raw) == (word, reason)


def test_check_word_collapses_line_breaks_and_tabs_into_spaces():
    assert D.check_word("two\nlines\tand tab") == ("two lines and tab", "")


def test_check_word_never_raises_for_non_text():
    assert D.check_word(None) == ("", "empty")
    assert D.check_word(5) == ("", "empty")


def test_the_limits_are_the_matchers_own():
    assert D.MAX_WORDS == tw.VOCAB_MAX_ENTRIES == 500
    assert D.MAX_WORD_LEN == tw.VOCAB_MAX_ENTRY_LEN == 60
    assert D.MAX_SPELLINGS == tw.VOCAB_MAX_SOUNDS == 8


# ── reading a file ───────────────────────────────────────────────────────────

def test_a_file_with_a_header_in_any_style_is_read_by_its_columns():
    p = D.parse_vocab("Sounds Like,Word,Starred\r\n\"grok; grock\",Groq,yes\r\n,Morta,no\r\n")
    assert p["header"] is True
    assert p["rows"] == [
        {"row": 2, "word": "Groq", "sounds_like": ["grok", "grock"], "starred": True},
        {"row": 3, "word": "Morta", "sounds_like": [], "starred": False},
    ]


def test_a_file_with_no_header_is_word_then_sounds_like_then_star():
    p = D.parse_vocab("Postgres,postgress;post gress\nZoë\n")
    assert p["header"] is False
    assert p["rows"][0] == {"row": 1, "word": "Postgres", "sounds_like": ["postgress", "post gress"], "starred": None}
    assert p["rows"][1]["word"] == "Zoë" and p["rows"][1]["sounds_like"] == []


def test_a_pasted_list_of_words_one_to_a_line():
    p = D.parse_vocab("Waffler\nPostHog\n\nSinéad\n")
    assert [r["word"] for r in p["rows"]] == ["Waffler", "PostHog", "Sinéad"]


@pytest.mark.parametrize("sep", [",", ";", "\t"])
def test_comma_semicolon_and_tab_files_read_the_same(sep):
    text = sep.join(["word", "sounds_like"]) + "\n" + sep.join(["Groq", "grok"]) + "\n"
    assert D.parse_vocab(text)["rows"] == [{"row": 2, "word": "Groq", "sounds_like": ["grok"], "starred": None}]


def test_the_sounds_like_cell_may_be_split_by_semicolons_commas_bars_or_lines():
    p = D.parse_vocab('word,sounds_like\nGroq,"grok, grock | groq\nGROK;gruck"\n')
    assert p["rows"][0]["sounds_like"] == ["grok", "grock", "groq", "GROK", "gruck"]


@pytest.mark.parametrize("cell, want", [
    ("yes", True), ("Y", True), ("TRUE", True), ("1", True), ("x", True), ("starred", True), ("★", True),
    ("no", False), ("", None), ("0", False), ("false", False), ("N", False),
    ("maybe", None),
])
def test_the_star_column_is_read_loosely(cell, want):
    p = D.parse_vocab(f"word,starred\nGroq,{cell}\n")
    assert p["rows"][0]["starred"] is want


def test_an_exported_mark_in_front_of_a_formula_character_comes_off():
    p = D.parse_vocab("word,sounds_like\n'+Plus,'-minus\n")
    assert p["rows"][0]["word"] == "+Plus" and p["rows"][0]["sounds_like"] == ["-minus"]


def test_a_byte_order_mark_and_windows_line_ends_are_fine():
    text = table_io.decode("﻿word,sounds_like\r\nGroq,grok\r\n".encode("utf-8"))
    assert D.parse_vocab(text)["rows"][0]["word"] == "Groq"


def test_a_header_without_a_word_column_takes_the_first_other_column_as_the_word():
    p = D.parse_vocab("notes,sounds like\nGroq,grok\n")
    assert p["rows"] == [{"row": 2, "word": "Groq", "sounds_like": ["grok"], "starred": None}]


# ── writing a file ───────────────────────────────────────────────────────────

def test_export_columns_and_values():
    text = D.render_csv(["Groq", "Morta", "Sinéad"], {"Groq": ["grok", "grock"]}, ["Morta"])
    assert text.splitlines() == [
        "word,sounds_like,starred",
        "Groq,grok; grock,no",
        "Morta,,yes",
        "Sinéad,,no",
    ]
    assert text.endswith("\r\n")


def test_export_defuses_formula_cells_in_words_and_spellings():
    text = D.render_csv(["=cmd", "@home"], {"@home": ["+at home"]}, [])
    rows = table_io.parse(text)
    assert rows[1][0] == "'=cmd"
    assert rows[2][0] == "'@home" and rows[2][1] == "'+at home"


def test_export_file_has_a_byte_order_mark_and_keeps_accents():
    data = D.export_bytes(["Sinéad"], {}, [])
    assert data.startswith(b"\xef\xbb\xbf")
    assert "Sinéad" in data.decode("utf-8-sig")


def test_a_semicolon_in_a_spelling_becomes_a_space_so_the_cell_keeps_one_spelling_per_piece():
    text = D.render_csv(["Groq"], {"Groq": ["gr;ok", "grok"]}, [])
    assert D.parse_vocab(text)["rows"][0]["sounds_like"] == ["gr ok", "grok"]


def test_what_is_exported_imports_back_the_same():
    words = ["Groq", "=cmd", "Sinéad", "C++", "Morta"]
    sounds = {"Groq": ["grok", "grock"], "Sinéad": ["shinade"], "=cmd": ["see em dee"]}
    stars = ["Sinéad", "Morta"]
    p = plan(D.render_csv(words, sounds, stars))
    assert p["words"] == words
    assert p["sounds"] == sounds
    assert p["starred"] == stars
    assert p["counts"]["skipped"] == 0 and p["counts"]["new"] == 5


def test_a_big_export_round_trips_with_the_cap():
    words = [f"Word{i:03d}" for i in range(500)]
    p = plan(D.render_csv(words, {}, []))
    assert len(p["words"]) == 500 and p["counts"]["new"] == 500


# ── what an import does to the list ──────────────────────────────────────────

def test_new_words_are_added_in_file_order_after_the_old_ones():
    p = plan("word\nZed\nAbe\n", words=["Groq"])
    assert p["words"] == ["Groq", "Zed", "Abe"]
    assert p["counts"] == {"rows": 2, "new": 2, "updated": 0, "already": 0, "skipped": 0}
    assert p["new"] == ["Zed", "Abe"]


def test_a_word_already_there_in_any_capitals_is_not_added_again_or_respelt():
    p = plan("word\nGROQ\nmorta\n", words=["Groq", "Morta"])
    assert p["words"] == ["Groq", "Morta"]
    assert p["counts"]["already"] == 2 and p["counts"]["new"] == 0
    assert p["already"] == ["Groq", "Morta"]


def test_spellings_are_added_to_a_word_already_there_and_none_are_taken_away():
    p = plan("word,sounds_like\nGroq,grock\n", words=["Groq"], sounds={"Groq": ["grok"]})
    assert p["sounds"] == {"Groq": ["grok", "grock"]}
    assert p["counts"]["updated"] == 1 and p["updated"] == ["Groq"]


def test_a_spelling_already_there_changes_nothing():
    p = plan("word,sounds_like\nGroq,GROK\n", words=["Groq"], sounds={"Groq": ["grok"]})
    assert p["sounds"] == {"Groq": ["grok"]}
    assert p["counts"]["already"] == 1 and p["counts"]["updated"] == 0


def test_a_star_is_added_never_taken_off():
    p = plan("word,starred\nGroq,yes\nMorta,no\n", words=["Groq", "Morta"], starred=["Morta"])
    assert p["starred"] == ["Groq", "Morta"]
    assert p["counts"]["updated"] == 1


def test_a_word_listed_twice_in_the_file_is_one_entry_with_both_rows_spellings():
    p = plan("word,sounds_like\nGroq,grok\nGroq,grock\ngroq,gruck\n")
    assert p["words"] == ["Groq"]
    assert p["sounds"] == {"Groq": ["grok", "grock", "gruck"]}
    assert p["counts"]["new"] == 1 and p["counts"]["skipped"] == 0


def test_existing_words_keep_their_place_and_spelling():
    words = ["Zed", "abe", "Morta"]
    p = plan("word\nAbe\nNova\n", words=words)
    assert p["words"] == ["Zed", "abe", "Morta", "Nova"]


def test_the_input_lists_are_not_changed():
    words, sounds, stars = ["Groq"], {"Groq": ["grok"]}, ["Groq"]
    D.plan_import(D.parse_vocab("word,sounds_like,starred\nGroq,grock,yes\nNova,x,yes\n"), words, sounds, stars)
    assert words == ["Groq"] and sounds == {"Groq": ["grok"]} and stars == ["Groq"]


# ── what is left out, and why ────────────────────────────────────────────────

def test_rows_that_fail_the_pages_rules_are_skipped_with_the_row_and_a_reason():
    text = "word\nQuill\n" + "x" * 61 + "\n2024\nbad\x07\n,grok\n"
    p = plan(text)
    assert p["words"] == ["Quill"]
    assert reasons(p) == [(3, "too_long"), (4, "no_letters"), (5, "odd"), (6, "empty")]
    assert all(s["message"] and "—" not in s["message"] for s in p["skipped"])


def test_an_everyday_word_is_left_out_unless_it_has_a_sounds_like():
    p = plan("word,sounds_like\nwill\nthe\nWill,wil\nGroq\n")
    assert p["words"] == ["Will", "Groq"]
    assert reasons(p) == [(2, "everyday"), (3, "everyday")]
    assert p["sounds"] == {"Will": ["wil"]}


def test_an_everyday_word_that_is_already_listed_is_not_an_error():
    p = plan("word\nWill\n", words=["Will"])
    assert p["counts"]["already"] == 1 and not p["skipped"]


def test_the_list_cap_stops_new_words_and_says_so_for_each():
    words = [f"Word{i:03d}" for i in range(498)]
    p = plan("word\nZorro\nQuill\nNova\n", words=words)
    assert len(p["words"]) == 500
    assert p["new"] == ["Zorro", "Quill"]
    assert reasons(p) == [(4, "full")]
    assert "500" in p["skipped"][0]["message"]


def test_existing_words_still_merge_when_the_list_is_full():
    words = [f"Word{i:03d}" for i in range(500)]
    p = plan("word,sounds_like\nWord001,wurd\n", words=words)
    assert p["sounds"] == {"Word001": ["wurd"]} and p["counts"]["updated"] == 1


def test_a_word_that_is_already_how_another_word_sounds_is_left_out():
    p = plan("word\ngrok\n", words=["Groq"], sounds={"Groq": ["grok"]})
    assert reasons(p) == [(2, "is_spelling")]
    assert "Groq" in p["skipped"][0]["message"]
    assert p["sounds"] == {"Groq": ["grok"]}


def test_spellings_are_held_to_the_rules_of_the_sounds_like_box_and_each_one_left_out_is_listed():
    long_ = "s" * 61
    text = f'word,sounds_like\nGroq,"groq; {long_}; ---; grok; Morta; owned; grok"\n'
    p = plan(text, words=["Morta", "Other"], sounds={"Other": ["owned"]})
    assert p["sounds"] == {"Other": ["owned"], "Groq": ["grok"]}
    kinds = sorted(n["reason"] for n in p["notes"])
    assert kinds == ["spelling_long", "spelling_none", "spelling_same", "spelling_taken", "spelling_word"]
    assert all(n["row"] == 2 and n["word"] == "Groq" for n in p["notes"])
    assert p["words"] == ["Morta", "Other", "Groq"]


def test_a_word_can_have_at_most_eight_spellings():
    spellings = "; ".join(f"sp{i}" for i in range(12))
    p = plan(f'word,sounds_like\nGroq,"{spellings}"\n')
    assert len(p["sounds"]["Groq"]) == 8
    assert sum(1 for n in p["notes"] if n["reason"] == "spelling_max") == 4


def test_the_result_is_always_something_the_matcher_would_keep_as_it_is():
    text = ("word,sounds_like,starred\nGroq,grok,yes\nGROQ,grock,\nMorta,morta,\nSinéad,\"shinade; Groq\",y\n"
            "will,,\nNew,\"grok; new\",\n")
    p = plan(text, words=["Old"], sounds={"Old": ["ould"]}, starred=["Old"])
    assert tw.clean_vocab(p["words"]) == p["words"]
    assert tw.clean_sounds_like(p["sounds"], p["words"]) == p["sounds"]
    assert tw.check_sounds_like(p["sounds"]) == ""
    assert set(p["starred"]) <= set(p["words"])


def test_an_empty_file_plans_nothing():
    p = plan("")
    assert p["counts"] == {"rows": 0, "new": 0, "updated": 0, "already": 0, "skipped": 0}
    assert p["words"] == []


def test_only_a_header_plans_nothing():
    p = plan("word,sounds_like,starred\n")
    assert p["counts"]["rows"] == 0 and p["header"] is True


def test_bytes_from_the_page_go_through_the_same_door():
    import base64
    raw = base64.b64encode("﻿word;sounds_like\r\nSinéad;shinade\r\n".encode("utf-8")).decode("ascii")
    p = D.plan_from_payload(raw, True, [], {}, [])
    assert p["words"] == ["Sinéad"] and p["sounds"] == {"Sinéad": ["shinade"]}
