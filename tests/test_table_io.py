"""The small CSV layer the Vocabulary and Snippets import and export share
(src/table_io.py): reading a file as people really save it (Excel, Numbers,
Google Sheets), writing one a spreadsheet will not run as a formula.

Pure: no files, no window, no network.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import table_io as T  # noqa: E402


# ── decoding ─────────────────────────────────────────────────────────────────

def test_a_utf8_file_with_a_byte_order_mark_loses_the_mark():
    assert T.decode(b"\xef\xbb\xbfword,sounds_like\r\ncaf\xc3\xa9,cafay") == "word,sounds_like\r\ncaf\xe9,cafay"


def test_plain_utf8_is_read_as_it_is():
    assert T.decode("Sinéad, Zoë".encode("utf-8")) == "Sinéad, Zoë"


def test_excel_unicode_text_is_utf16_with_a_mark_and_is_read():
    raw = "word\tsounds_like\nSinéad\tshinade".encode("utf-16")      # BOM included
    assert T.decode(raw) == "word\tsounds_like\nSinéad\tshinade"
    assert T.decode(b"\xfe\xff" + "a\tb".encode("utf-16-be")) == "a\tb"


def test_excel_on_windows_csv_is_windows_1252_and_is_read():
    raw = "Zoë;zoe".encode("cp1252")
    assert T.decode(raw) == "Zoë;zoe"


def test_bytes_that_are_nothing_readable_still_come_back_as_text():
    assert isinstance(T.decode(b"\x81\x8d\x8f\x90\x9d"), str)


def test_nul_characters_are_removed():
    assert T.decode(b"ab\x00c") == "abc"


def test_the_text_a_page_sends_may_be_base64():
    import base64
    raw = "word\r\nSinéad".encode("utf-8-sig")
    assert T.from_payload(base64.b64encode(raw).decode("ascii"), True) == "word\r\nSinéad"
    assert T.from_payload("word\nSinéad", False) == "word\nSinéad"


def test_a_payload_that_is_not_base64_or_too_big_is_refused_in_plain_words():
    with pytest.raises(T.TableError) as e:
        T.from_payload("***not base64***", True)
    assert "read" in str(e.value).lower()
    with pytest.raises(T.TableError) as e:
        T.from_payload("x" * (T.MAX_CHARS + 1), False)
    assert "too big" in str(e.value).lower()
    with pytest.raises(T.TableError):
        T.from_payload(None, False)


# ── the delimiter ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, want", [
    ("word,sounds_like\na,b", ","),
    ("word;sounds_like\na;b", ";"),
    ("word\tsounds_like\na\tb", "\t"),
    ("just one column\nanother", ","),
    ("", ","),
    ('"a, b";c\nx;y', ";"),             # a comma inside quotes is not a delimiter
    ("a,b;c\n", ","),                   # a tie goes to the comma
])
def test_the_delimiter_is_found_in_the_first_line(text, want):
    assert T.sniff_delimiter(text) == want


def test_excels_sep_line_names_the_delimiter_and_is_not_a_row():
    assert T.parse("sep=;\nword;sounds_like\nGroq;grok") == [["word", "sounds_like"], ["Groq", "grok"]]


# ── parsing ──────────────────────────────────────────────────────────────────

def test_rows_cells_are_trimmed_and_empty_rows_dropped():
    assert T.parse("word, sounds_like\n\n  Groq ,  grok  \n,,\n") == [["word", "sounds_like"], ["Groq", "grok"]]


def test_quotes_commas_and_doubled_quotes():
    text = 'trigger,expansion\n"my, address","12 ""Acacia"" Avenue"\n'
    assert T.parse(text) == [["trigger", "expansion"], ["my, address", '12 "Acacia" Avenue']]


def test_a_quoted_cell_may_hold_line_breaks_in_any_style():
    text = 'trigger,expansion\r\nsig,"Best,\r\nJames\nFarrelly"\r\nnext,x'
    got = T.parse(text)
    assert got[1] == ["sig", "Best,\r\nJames\nFarrelly"]
    assert got[2] == ["next", "x"]


def test_unicode_survives_parsing():
    assert T.parse("word\tsounds_like\nSinéad\tshinade; 신에드") == [["word", "sounds_like"], ["Sinéad", "shinade; 신에드"]]


def test_a_file_that_is_one_giant_cell_is_refused_in_plain_words():
    with pytest.raises(T.TableError) as e:
        T.parse('a,b\n"' + "x" * 200000 + '",c')
    assert "couldn't read" in str(e.value).lower()


def test_too_many_rows_are_refused():
    with pytest.raises(T.TableError) as e:
        T.parse("\n".join(f"w{i}" for i in range(T.MAX_ROWS + 1)))
    assert "rows" in str(e.value)


# ── spreadsheet safety, both ways ────────────────────────────────────────────

@pytest.mark.parametrize("cell", ["=1+1", "+44 20", "-5", "@SUM(A1)", "\t=x", "\r=x"])
def test_export_defuses_a_cell_a_spreadsheet_would_run(cell):
    out = T.defuse(cell)
    assert out == "'" + cell


@pytest.mark.parametrize("cell", ["Groq", "C++", ".NET", "", "a=b", "x@y"])
def test_export_leaves_other_cells_alone(cell):
    assert T.defuse(cell) == cell


def test_defuse_never_raises_for_none_or_numbers():
    assert T.defuse(None) == ""
    assert T.defuse(5) == "5"


@pytest.mark.parametrize("cell", ["=1+1", "+44 20", "-5", "@SUM(A1)"])
def test_import_takes_the_protecting_mark_off_again(cell):
    assert T.undefuse(T.defuse(cell)) == cell


@pytest.mark.parametrize("cell", ["'tis", "'", "'Groq", "Groq", "''=x"])
def test_import_only_takes_off_a_mark_that_export_put_there(cell):
    assert T.undefuse(cell) == cell


def test_render_writes_crlf_quotes_and_defuses():
    text = T.render([["word", "sounds_like"], ["=cmd", "a, b"], ["C++", 'say "hi"\nthere']])
    assert text == 'word,sounds_like\r\n\'=cmd,"a, b"\r\nC++,"say ""hi""\nthere"\r\n'


def test_a_rendered_table_reads_back_the_same_apart_from_the_protecting_marks():
    rows = [["trigger", "expansion"], ["sig", "Best,\nJames"], ["é", "ü"], ["=x", "-y"]]
    back = T.parse(T.render(rows))
    assert back[:3] == rows[:3]
    assert [T.undefuse(c) for c in back[3]] == ["=x", "-y"]


def test_encode_has_a_byte_order_mark_so_excel_shows_accents():
    data = T.encode("Sinéad\r\n")
    assert data.startswith(b"\xef\xbb\xbf")
    assert data.decode("utf-8-sig") == "Sinéad\r\n"


def test_it_agrees_with_the_history_export_about_what_is_defused():
    import history_export
    for cell in ["=1", "+1", "-1", "@a", "\tx", "\rx", "plain", "", "a=b"]:
        assert T.defuse(cell) == history_export.defuse(cell)


# ── headers ──────────────────────────────────────────────────────────────────

ALIASES = {"word": {"word", "words", "term"}, "sounds_like": {"sounds_like", "soundslike", "misspelling"}}


@pytest.mark.parametrize("header", [
    ["word", "sounds_like"], ["Word", "Sounds like"], ["WORD", "Sounds-Like"],
    ["﻿word", "SoundsLike"], [" Term ", "Misspelling"], ["words", "sounds_like"],
])
def test_header_variants_are_found(header):
    assert T.column_map(header, ALIASES) == {"word": 0, "sounds_like": 1}


def test_columns_may_come_in_any_order_with_extras():
    assert T.column_map(["notes", "sounds like", "Word"], ALIASES) == {"sounds_like": 1, "word": 2}


def test_a_row_of_data_is_not_a_header():
    assert T.column_map(["Postgres", "postgress"], ALIASES) is None
    assert T.column_map(["Groq"], ALIASES) is None


def test_a_header_with_only_the_second_kind_of_column_is_still_a_header():
    assert T.column_map(["sounds like", "x"], ALIASES) == {"sounds_like": 0}
