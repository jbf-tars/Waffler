"""Snippets as a file: export as CSV or JSON, import with a preview
(src/snippets_io.py).

An import only adds. A trigger that is already a snippet (in any capitals)
keeps its existing text and is listed as left out; the limits are the
editor's (60 characters for the phrase, 4,000 for the text, 200 snippets).
Pure: no files, no window.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import snippets as S  # noqa: E402
import snippets_io as IO  # noqa: E402
import table_io  # noqa: E402


def sn(*pairs):
    return [{"trigger": t, "expansion": e} for t, e in pairs]


def plan(text, existing=()):
    return IO.plan_import(IO.parse(text), sn(*existing))


def reasons(p):
    return [(s["row"], s["reason"]) for s in p["skipped"]]


# ── reading ──────────────────────────────────────────────────────────────────

def test_a_csv_with_a_header_is_read_by_its_columns_in_any_order():
    p = IO.parse("Expansion,Trigger\r\n12 Acacia Avenue,my address\r\n")
    assert p["header"] is True
    assert p["rows"] == [{"row": 2, "trigger": "my address", "expansion": "12 Acacia Avenue"}]


@pytest.mark.parametrize("header", [
    "trigger,expansion", "Trigger,Expansion", "phrase,text", "Shortcut,Replacement",
    "When you say,Waffler pastes", "abbreviation,body", "TRIGGER;EXPANSION".replace(";", ","),
])
def test_header_variants(header):
    p = IO.parse(header + "\nbrb,be right back\n")
    assert p["header"] is True and p["rows"][0]["trigger"] == "brb"


def test_no_header_means_trigger_then_text():
    p = IO.parse("brb,be right back\nomw,on my way\n")
    assert p["header"] is False
    assert [r["trigger"] for r in p["rows"]] == ["brb", "omw"]


@pytest.mark.parametrize("sep", [",", ";", "\t"])
def test_any_of_the_three_delimiters(sep):
    p = IO.parse(f"trigger{sep}expansion\nbrb{sep}be right back\n")
    assert p["rows"][0]["expansion"] == "be right back"


def test_a_quoted_text_may_hold_commas_quotes_and_line_breaks():
    text = 'trigger,expansion\nsign off,"Best wishes,\r\nJane ""JJ"" Doe\n\n07700 900123"\nnext,x\n'
    p = IO.parse(text)
    assert p["rows"][0]["expansion"] == 'Best wishes,\nJane "JJ" Doe\n\n07700 900123'
    assert p["rows"][1]["trigger"] == "next"


def test_a_byte_order_mark_and_unicode_are_fine():
    text = table_io.decode("﻿phrase\ttext\ncafé\tnaïve — résumé\n".encode("utf-8"))
    assert IO.parse(text)["rows"][0] == {"row": 2, "trigger": "café", "expansion": "naïve — résumé"}


def test_the_marks_export_put_before_formula_characters_come_off():
    p = IO.parse("trigger,expansion\n'=sum,'-5 degrees\n")
    assert p["rows"][0] == {"row": 2, "trigger": "=sum", "expansion": "-5 degrees"}


def test_a_json_file_of_trigger_and_expansion_is_read_too():
    text = json.dumps(sn(("brb", "be right back"), ("é", "ü")), ensure_ascii=False)
    p = IO.parse("﻿" + text)
    assert [(r["trigger"], r["expansion"]) for r in p["rows"]] == [("brb", "be right back"), ("é", "ü")]


def test_json_that_is_not_a_list_of_snippets_is_refused_in_plain_words():
    with pytest.raises(table_io.TableError):
        IO.parse("[1, 2, 3]")
    with pytest.raises(table_io.TableError):
        IO.parse('[{"trigger": "a"')


# ── writing ──────────────────────────────────────────────────────────────────

def test_csv_export():
    text = IO.render_csv(sn(("my address", "12 Acacia Avenue,\nLondon"), ("brb", "be right back")))
    assert text == 'trigger,expansion\r\nmy address,"12 Acacia Avenue,\nLondon"\r\nbrb,be right back\r\n'


def test_csv_export_is_safe_in_a_spreadsheet():
    rows = table_io.parse(IO.render_csv(sn(("=cmd", "+1 555"), ("ok", "@home"), ("-x", "plain"))))
    assert rows[1] == ["'=cmd", "'+1 555"]
    assert rows[2] == ["ok", "'@home"]
    assert rows[3] == ["'-x", "plain"]


def test_json_export_is_the_same_shape_snippets_json_has_so_an_older_waffler_reads_it():
    text = IO.render_json(sn(("café", "naïve\nline two")))
    assert json.loads(text) == sn(("café", "naïve\nline two"))
    assert "café" in text and text.endswith("\n")


def test_json_export_is_not_defused_because_it_is_not_opened_in_a_spreadsheet():
    assert json.loads(IO.render_json(sn(("=x", "-y")))) == sn(("=x", "-y"))


def test_export_bytes_per_format():
    assert IO.export_bytes(sn(("é", "ü")), "csv").startswith(b"\xef\xbb\xbf")
    assert not IO.export_bytes(sn(("é", "ü")), "json").startswith(b"\xef\xbb\xbf")
    assert json.loads(IO.export_bytes(sn(("é", "ü")), "json").decode("utf-8")) == sn(("é", "ü"))
    with pytest.raises(ValueError):
        IO.export_bytes([], "xml")


def test_file_names():
    assert IO.FORMATS == ("csv", "json")


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_what_is_exported_imports_back_the_same(fmt):
    items = sn(("my address", "12 Acacia Avenue,\r\nLondon\n\nUK"), ("=x", "-y"), ("é", "ü \"quoted\""),
               ("emoji", "ok 👍"), ("sig", "Best,\nJames"))
    want = sn(("my address", "12 Acacia Avenue,\nLondon\n\nUK"), ("=x", "-y"), ("é", "ü \"quoted\""),
              ("emoji", "ok 👍"), ("sig", "Best,\nJames"))
    p = IO.plan_import(IO.parse(IO.export_bytes(items, fmt).decode("utf-8-sig")), [])
    assert p["snippets"] == want
    assert p["counts"]["skipped"] == 0 and p["counts"]["new"] == 5


# ── what an import does ──────────────────────────────────────────────────────

def test_new_snippets_are_added_after_the_old_ones():
    p = plan("trigger,expansion\nbrb,be right back\nomw,on my way\n", existing=[("my address", "12 Acacia")])
    assert [s["trigger"] for s in p["snippets"]] == ["my address", "brb", "omw"]
    assert p["counts"] == {"rows": 2, "new": 2, "already": 0, "skipped": 0}
    assert p["new"] == ["brb", "omw"]


def test_text_and_trigger_are_tidied_as_the_editor_does():
    p = plan("trigger,expansion\n\"  my   address \",\"  12 Acacia\r\nLondon \n\"\n")
    assert p["snippets"] == sn(("my address", "12 Acacia\nLondon"))


def test_an_existing_trigger_in_any_capitals_keeps_the_existing_text_and_is_listed():
    p = plan("trigger,expansion\nMY ADDRESS,somewhere else\nbrb,be right back\n",
             existing=[("my address", "12 Acacia")])
    assert p["snippets"] == sn(("my address", "12 Acacia"), ("brb", "be right back"))
    assert reasons(p) == [(2, "exists")]
    assert "my address" in p["skipped"][0]["message"]
    assert p["counts"] == {"rows": 2, "new": 1, "already": 0, "skipped": 1}


def test_the_same_trigger_and_text_again_is_already_there_not_a_problem():
    p = plan("trigger,expansion\nMy Address,12 Acacia\n", existing=[("my address", "12 Acacia")])
    assert not p["skipped"] and p["counts"]["already"] == 1 and p["already"] == ["my address"]


def test_a_trigger_twice_in_the_file_keeps_the_first():
    p = plan("trigger,expansion\nbrb,first\nBRB,second\n")
    assert p["snippets"] == sn(("brb", "first"))
    assert reasons(p) == [(3, "repeat")]


def test_rows_over_the_limits_are_left_out_with_the_row_and_the_reason():
    text = ('trigger,expansion\n' + 'x' * 61 + ',text\nlong,"' + 'y' * 4001 + '"\n,orphan\nno text,\nfine,ok\n')
    p = plan(text)
    assert p["snippets"] == sn(("fine", "ok"))
    assert reasons(p) == [(2, "trigger_long"), (3, "text_long"), (4, "empty"), (5, "no_text")]
    assert all(s["message"] and "—" not in s["message"] for s in p["skipped"])


def test_the_limits_are_exactly_the_editors():
    ok = plan("trigger,expansion\n" + "x" * 60 + ",\"" + "y" * 4000 + "\"\n")
    assert ok["counts"]["new"] == 1
    assert (IO.MAX_TRIGGER, IO.MAX_EXPANSION, IO.MAX_SNIPPETS) == (S.MAX_TRIGGER, S.MAX_EXPANSION, S.MAX_SNIPPETS)


def test_the_cap_of_two_hundred_stops_new_snippets_and_says_so():
    existing = [(f"t{i:03d}", "x") for i in range(199)]
    p = plan("trigger,expansion\na1,one\na2,two\na3,three\n", existing=existing)
    assert len(p["snippets"]) == 200
    assert p["new"] == ["a1"]
    assert reasons(p) == [(3, "full"), (4, "full")]


def test_nothing_is_ever_taken_away_or_reordered():
    existing = [("b", "B"), ("a", "A"), ("c", "C")]
    p = plan("trigger,expansion\nd,D\n", existing=existing)
    assert p["snippets"][:3] == sn(*existing)


def test_the_input_list_is_not_changed():
    existing = sn(("a", "A"))
    IO.plan_import(IO.parse("trigger,expansion\nb,B\n"), existing)
    assert existing == sn(("a", "A"))


def test_what_the_plan_gives_is_what_the_editors_clean_would_keep():
    text = "trigger,expansion\nMy  Address,12 Acacia\nbrb,be right back\nBRB,x\n,y\nz,\n"
    p = plan(text, existing=[("old", "OLD")])
    cleaned, error = S.clean(p["snippets"])
    assert error is None and cleaned == p["snippets"]


def test_an_empty_file_plans_nothing():
    p = plan("")
    assert p["counts"] == {"rows": 0, "new": 0, "already": 0, "skipped": 0}
    p = plan("trigger,expansion\n")
    assert p["counts"]["rows"] == 0 and p["header"] is True


def test_bytes_from_the_page_go_through_the_same_door():
    import base64
    raw = base64.b64encode("trigger;expansion\r\ncafé;ü\r\n".encode("utf-16")).decode("ascii")
    p = IO.plan_from_payload(raw, True, [])
    assert p["snippets"] == sn(("café", "ü"))
