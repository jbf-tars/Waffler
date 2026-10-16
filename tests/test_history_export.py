"""Export the Journal (S4): JSON, CSV and plain text, as a file on the Desktop.

src/history_export.py renders and writes; Api.export_history in app.py puts the
file where download_logs puts its zip and shows it in its folder.
"""
import ast
import csv
import io
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import history_export as X  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 16, 14, 30, 5)

ENTRIES = [
    {"timestamp": "2026-10-14T09:12:44", "text": "ok so ship it", "styled": "OK, so ship it.",
     "word_count": 4, "text_is": "asr_filtered", "id": 1, "duration_s": 3.2, "speech_s": 2.9,
     "lang": "en", "provider": {"stt": "groq", "cleanup": "groq"}, "paste": "pasted", "app": "Mail",
     "audio": "rec-20261014-091244.wav", "quality": {"level": "check", "flags": ["x"]}},
    {"timestamp": "2026-10-15T18:03:00", "text": "caf\u00e9 \u2014 \u65e5\u672c\u8a9e \U0001f600",
     "styled": "Caf\u00e9 \u2014 \u65e5\u672c\u8a9e \U0001f600", "word_count": 3, "id": 2, "lang": "fr", "app": "Notes"},
    {"timestamp": "2026-10-16T08:00:00", "text": "he said \"stop\", then left,\nand\r\nsmiled",
     "styled": "He said \"stop\", then left,\nand\r\nsmiled", "word_count": 7},
    {"timestamp": "2026-10-16T09:00:00", "text": "", "styled": "Not sent: this recording hasn't been turned into text yet.",
     "word_count": 0, "failed": True, "error": "Incorrect API key provided: gsk_ABCD***wxyz",
     "audio_path": "/Users/someone/.waffler-hosted/unsent/recording-2026-10-16T09-00-00.wav",
     "unsent_id": "recording-2026-10-16T09-00-00.wav", "not_sent_reason": "error", "id": 3},
]


def _rows(entries, fmt="csv"):
    raw = X.render(entries, fmt, NOW)
    text = raw.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))


# ── JSON ─────────────────────────────────────────────────────────────────────

def test_json_has_every_kept_field_oldest_first_and_reads_back():
    out = json.loads(X.render(ENTRIES, "json", NOW).decode("utf-8"))
    assert [e["timestamp"] for e in out] == [e["timestamp"] for e in ENTRIES]
    first = out[0]
    for field in ("id", "text", "styled", "word_count", "text_is", "duration_s", "speech_s", "lang",
                  "provider", "paste", "app", "audio", "quality"):
        assert first[field] == ENTRIES[0][field], field
    assert out[1]["text"] == ENTRIES[1]["text"], "unicode as written, not escaped away"


def test_json_leaves_out_what_is_about_this_computer_or_a_providers_message():
    raw = X.render(ENTRIES, "json", NOW).decode("utf-8")
    out = json.loads(raw)
    assert "audio_path" not in out[3] and "error" not in out[3]
    assert "someone" not in raw and "gsk_" not in raw
    assert out[3]["unsent_id"] and out[3]["not_sent_reason"] == "error"


def test_json_keeps_fields_it_does_not_know_about():
    out = json.loads(X.render([{"timestamp": "t", "text": "x", "future_field": [1, 2]}], "json", NOW))
    assert out == [{"timestamp": "t", "text": "x", "future_field": [1, 2]}]


def test_json_is_utf8_with_no_byte_order_mark_and_ends_with_a_line_break():
    raw = X.render(ENTRIES, "json", NOW)
    assert not raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"]\n")
    assert "\u65e5\u672c\u8a9e".encode("utf-8") in raw


# ── CSV ──────────────────────────────────────────────────────────────────────

def test_csv_has_the_five_columns_and_a_row_for_each_entry():
    rows = _rows(ENTRIES)
    assert rows[0] == ["timestamp", "app", "language", "words", "text"]
    assert len(rows) == 1 + len(ENTRIES)
    assert rows[1] == ["2026-10-14T09:12:44", "Mail", "en", "4", "OK, so ship it."]
    assert rows[2][:4] == ["2026-10-15T18:03:00", "Notes", "fr", "3"]
    assert rows[3][1:3] == ["", ""], "an entry that doesn't know its app or language leaves them empty"


def test_csv_quoting_survives_commas_quotes_and_line_breaks():
    rows = _rows(ENTRIES)
    assert rows[3][4] == "He said \"stop\", then left,\nand\r\nsmiled"
    assert all(len(r) == 5 for r in rows)


def test_csv_unicode_comes_back_whole_and_a_byte_order_mark_tells_excel_it_is_utf8():
    raw = X.render(ENTRIES, "csv", NOW)
    assert raw.startswith(b"\xef\xbb\xbf")
    assert _rows(ENTRIES)[2][4] == ENTRIES[1]["styled"]


def test_csv_rows_end_with_a_carriage_return_and_line_feed():
    raw = X.render(ENTRIES[:1], "csv", NOW)
    assert raw.count(b"\r\n") == 2


@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_a_cell_that_starts_like_a_formula_is_defused(start):
    nasty = f"{start}HYPERLINK(\"http://evil.example\",\"x\")"
    entry = {"timestamp": "2026-10-16T10:00:00", "text": nasty, "styled": nasty, "app": nasty,
             "lang": nasty, "word_count": 1}
    row = _rows([entry])[1]
    assert row[1] == "'" + nasty and row[2] == "'" + nasty and row[4] == "'" + nasty
    for cell in row[1:]:
        assert not cell.startswith(("=", "+", "-", "@", "\t", "\r"))


def test_csv_harmless_cells_are_not_changed():
    entry = {"timestamp": "2026-10-16T10:00:00", "text": "Plan: 5 - 3 = 2", "styled": "Plan: 5 - 3 = 2",
             "app": "a=b", "lang": "en", "word_count": 5}
    assert _rows([entry])[1][1:] == ["a=b", "en", "5", "Plan: 5 - 3 = 2"]


def test_csv_a_list_dictated_with_dashes_is_made_safe_not_lost():
    entry = {"timestamp": "2026-10-16T10:00:00", "text": "- milk\n- eggs", "styled": "- milk\n- eggs", "word_count": 4}
    cell = _rows([entry])[1][4]
    assert cell == "'- milk\n- eggs"


def test_csv_counts_words_for_entries_that_never_stored_a_count():
    row = _rows([{"timestamp": "t", "text": "one two three", "styled": "One two three."}])[1]
    assert row[3] == "3"
    assert _rows([{"timestamp": "t", "failed": True, "styled": "Not sent"}])[1][3] == "0"
    assert _rows([{"timestamp": "t", "text": "x", "word_count": True}])[1][3] == "1"


def test_csv_drops_null_bytes():
    row = _rows([{"timestamp": "t", "text": "a\x00b", "styled": "a\x00b", "word_count": 1}])[1]
    assert row[4] == "ab"


# ── plain text ───────────────────────────────────────────────────────────────

def test_plain_text_is_dated_paragraphs_oldest_first():
    text = X.render(ENTRIES, "txt", NOW).decode("utf-8")
    lines = text.split("\n")
    assert lines[:3] == ["Waffler history", "Exported 2026-10-16 14:30", "4 entries, oldest first"]
    assert "2026-10-14 09:12 (Mail)" in lines and "OK, so ship it." in lines
    assert "2026-10-15 18:03 (Notes)" in lines and "2026-10-16 08:00" in lines
    assert text.index("2026-10-14") < text.index("2026-10-15") < text.index("2026-10-16 08:00")
    assert "Caf\u00e9 \u2014 \u65e5\u672c\u8a9e \U0001f600" in text, "the user's own characters are untouched"


def test_plain_text_has_no_dashes_of_its_own_and_no_byte_order_mark():
    text = X.render([{"timestamp": "2026-10-16T10:00:00", "styled": "hello", "app": "Mail"}], "txt", NOW)
    assert not text.startswith(b"\xef\xbb\xbf")
    own = text.decode("utf-8").replace("hello", "")
    assert "\u2014" not in own and "\u2013" not in own and "--" not in own


def test_plain_text_says_entry_in_the_singular():
    assert "1 entry, oldest first" in X.render(ENTRIES[:1], "txt", NOW).decode("utf-8")


def test_plain_text_copes_with_an_odd_time():
    text = X.render([{"timestamp": "yesterday-ish", "styled": "x"}, {"styled": "y"}], "txt", NOW).decode("utf-8")
    assert "yesterday-ish\nx" in text and "Unknown time\ny" in text


# ── empty, odd and large ─────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", X.FORMATS)
def test_an_empty_journal_renders_as_an_empty_file_of_that_format(fmt):
    raw = X.render([], fmt, NOW)
    if fmt == "json":
        assert json.loads(raw) == []
    elif fmt == "csv":
        assert _rows([]) == [["timestamp", "app", "language", "words", "text"]]
    else:
        assert raw.decode("utf-8").startswith("Waffler history\n")
    assert X.render(None, fmt, NOW) == raw


@pytest.mark.parametrize("fmt", X.FORMATS)
def test_things_in_the_list_that_are_not_entries_are_skipped(fmt):
    X.render([ENTRIES[0], "junk", None, 5, [1], ENTRIES[1]], fmt, NOW)
    only = X.render([ENTRIES[0], ENTRIES[1]], fmt, NOW)
    assert X.render([ENTRIES[0], "junk", None, 5, [1], ENTRIES[1]], fmt, NOW) == only


def test_an_unknown_format_is_refused():
    for bad in ("xlsx", "", None, "JSON", "../x"):
        with pytest.raises(ValueError):
            X.render(ENTRIES, bad, NOW)


def test_twenty_thousand_entries_export_in_a_moment_in_every_format():
    import time
    big = [{"timestamp": f"2026-01-{1 + i % 28:02d}T10:{i % 60:02d}:00", "text": "word " * 45,
            "styled": "Word " * 45, "word_count": 45, "id": i + 1, "app": "Mail", "lang": "en"}
           for i in range(20000)]
    t0 = time.perf_counter()
    for fmt in X.FORMATS:
        X.render(big, fmt, NOW)
    assert time.perf_counter() - t0 < 5


# ── the file ─────────────────────────────────────────────────────────────────

def test_the_file_is_written_whole_under_a_dated_name(tmp_path):
    path = X.write_file(ENTRIES, "csv", tmp_path, NOW)
    assert path.parent == tmp_path and path.name == "waffler-history-2026-10-16T14-30-05.csv"
    assert path.read_bytes() == X.render(ENTRIES, "csv", NOW)
    assert not list(tmp_path.glob("*.tmp"))


def test_a_second_export_in_the_same_second_does_not_overwrite_the_first(tmp_path):
    a = X.write_file(ENTRIES, "json", tmp_path, NOW)
    b = X.write_file(ENTRIES[:1], "json", tmp_path, NOW)
    assert a != b and b.name.endswith("-2.json")
    assert len(json.loads(a.read_text(encoding="utf-8"))) == 4 and len(json.loads(b.read_text(encoding="utf-8"))) == 1


def test_a_failed_write_leaves_nothing_behind(tmp_path, monkeypatch):
    def locked(src, dst, **k):
        raise PermissionError("[WinError 5] Access is denied")
    monkeypatch.setattr(X, "replace_with_retry", locked)
    with pytest.raises(PermissionError):
        X.write_file(ENTRIES, "txt", tmp_path, NOW)
    assert list(tmp_path.iterdir()) == []


def test_the_desktop_is_used_when_there_is_one_and_home_when_there_is_not(tmp_path):
    assert X.desktop_folder(tmp_path) == tmp_path
    (tmp_path / "Desktop").mkdir()
    assert X.desktop_folder(tmp_path) == tmp_path / "Desktop"


@pytest.mark.parametrize("system,expected", [
    ("Darwin", ["open", "-R"]), ("Windows", ["explorer", "/select,"])])
def test_the_file_is_shown_in_its_folder(monkeypatch, system, expected):
    calls = []
    monkeypatch.setattr(X.subprocess, "Popen", lambda args, **k: calls.append(args))
    assert X.reveal_in_folder("/x/waffler-history.csv", system) is True
    assert calls == [expected + ["/x/waffler-history.csv"]]


def test_showing_the_file_never_raises(monkeypatch):
    def nope(*a, **k):
        raise FileNotFoundError("no open")
    monkeypatch.setattr(X.subprocess, "Popen", nope)
    assert X.reveal_in_folder("/x/y.csv", "Darwin") is False
    assert X.reveal_in_folder("/x/y.csv", "Linux") is False


# ── Api.export_history ───────────────────────────────────────────────────────

def _export(tmp_path, monkeypatch, history, log=None):
    api = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "export_history")
    desktop = tmp_path / "home" / "Desktop"
    desktop.mkdir(parents=True)
    shown = []
    logged = log if log is not None else []
    ns = {"load_history": lambda: history, "_history_export": X, "_log_to_file": logged.append,
          "_platform": types.SimpleNamespace(system=lambda: "Darwin")}
    monkeypatch.setattr(X.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(X, "reveal_in_folder", lambda path, system: shown.append((str(path), system)) or True)
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    return (lambda *a: ns["export_history"](None, *a)), desktop, shown, logged


@pytest.mark.parametrize("fmt,ext", [("json", "json"), ("csv", "csv"), ("txt", "txt")])
def test_export_writes_the_file_to_the_desktop_and_shows_it(tmp_path, monkeypatch, fmt, ext):
    export, desktop, shown, logged = _export(tmp_path, monkeypatch, ENTRIES)
    r = export(fmt)
    assert r["ok"] is True and r["format"] == fmt and r["count"] == 4
    path = Path(r["path"])
    assert path.parent == desktop and path.suffix == f".{ext}" and path.is_file()
    assert shown == [(str(path), "Darwin")]
    assert any("[export] wrote" in m for m in logged)
    assert not any("caf" in m.lower() or "ship it" in m for m in logged), "no words in the log"


def test_export_of_an_empty_journal_says_so_in_a_plain_sentence(tmp_path, monkeypatch):
    export, desktop, shown, _ = _export(tmp_path, monkeypatch, [])
    r = export("csv")
    assert r["ok"] is False and r["reason"] == "empty"
    assert r["error"] == "Your Journal is empty, so there is nothing to export."
    assert list(desktop.iterdir()) == [] and shown == []


def test_export_refuses_a_format_it_does_not_have(tmp_path, monkeypatch):
    export, desktop, _, _ = _export(tmp_path, monkeypatch, ENTRIES)
    r = export("xlsx")
    assert r["ok"] is False and r["reason"] == "format" and list(desktop.iterdir()) == []


def test_export_that_cannot_write_says_so_without_the_raw_error(tmp_path, monkeypatch):
    export, desktop, shown, logged = _export(tmp_path, monkeypatch, ENTRIES)
    monkeypatch.setattr(X, "replace_with_retry", lambda *a, **k: (_ for _ in ()).throw(
        PermissionError("[WinError 5] Access is denied: 'C:\\\\Users\\\\x'")))
    r = export("json")
    assert r == {"ok": False, "reason": "error", "error": "Couldn't save the file. Try again."}
    assert shown == [] and list(desktop.iterdir()) == []
    assert any("[export] failed (PermissionError" in m for m in logged)


def test_export_with_no_format_answers_as_it_did_before(tmp_path, monkeypatch):
    export, desktop, shown, _ = _export(tmp_path, monkeypatch, ENTRIES)
    r = export()
    assert r["ok"] is True and r["count"] == 4 and "OK, so ship it." in r["content"]
    assert list(desktop.iterdir()) == [] and shown == []


def test_an_export_never_holds_a_key(tmp_path, monkeypatch):
    """Exports are made from history.json alone: not the environment, not .env."""
    secret = "not-a-real-key-" + "z" * 24            # built here, so no key-shaped text sits in the file
    monkeypatch.setenv("GROQ_API_KEY", secret)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + secret)
    (tmp_path / ".env").write_text(f"GROQ_API_KEY={secret}\n", encoding="utf-8")
    export, desktop, _, _ = _export(tmp_path, monkeypatch, ENTRIES)
    for fmt in X.FORMATS:
        assert secret.encode() not in Path(export(fmt)["path"]).read_bytes()
    src = (ROOT / "src" / "history_export.py").read_text(encoding="utf-8")
    for source in ("environ", "getenv", ".env", "settings.json", "config.json", "api_key"):
        assert source not in src.replace("PRIVATE_FIELDS", ""), source


def test_the_export_is_the_users_own_copy_so_delete_my_data_does_not_chase_it():
    import privacy_data
    assert not any(n.startswith("waffler-history") for n in privacy_data.DATA_FILES)
    assert not any("waffler-history" in g for g in privacy_data.DATA_GLOBS)
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    row = html[html.index('id="exportRow"'):]
    row = row[:row.index("</div>\n            <!--")]
    assert "It never includes your keys." in row
    assert "Delete all my data leaves it alone." in row


def test_the_file_is_written_with_an_encoding_and_shown_like_the_logs_zip():
    src = (ROOT / "src" / "history_export.py").read_text(encoding="utf-8")
    assert '"open", "-R"' in src and '"explorer", "/select,"' in src
    assert "Desktop" in src
