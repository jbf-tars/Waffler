"""Spoken commands in the Journal (S8): an entry of kind "command" is shown,
searched, exported, deleted and pruned like any entry, and is in none of the
numbers, lists or "Paste last" that are built from what the person dictated.
Redo is not offered for it. Spend on commands is kept apart from the cost of a
dictation.
"""
import ast
import json
import sys
import threading
import types
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import command_mode as cm  # noqa: E402
import history_export  # noqa: E402
import history_meta  # noqa: E402
import history_ops  # noqa: E402
import journal_data  # noqa: E402
import pasteboard  # noqa: E402
import privacy_data  # noqa: E402
import transforms as tf  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
TODAY = date(2026, 10, 16)


def dictation(ts, words=10, ident=1):
    return {"timestamp": ts, "text": " ".join(["w"] * words), "styled": " ".join(["W"] * words),
            "word_count": words, "id": ident}


def edit(ts="2026-10-16T10:00:00", before="a b c", after="A b c d e"):
    return cm.history_entry(cm.EDIT, "make this fancier", before=before, after=after, timestamp=ts,
                            provider="groq", took_s=2.0, fields={"app": "Notes", "paste": "pasted"})


def draft(ts="2026-10-16T10:00:00", after="Hi Sam, I'll be late."):
    return cm.history_entry(cm.DRAFT, "reply saying I'll be late", after=after, timestamp=ts, provider="groq")


def search(ts="2026-10-16T10:00:00", query="capital of Australia", engine="google"):
    return cm.history_entry(cm.ASK, "what is the capital of Australia", query=query, engine=engine,
                            timestamp=ts, provider="groq")


ALL_KINDS = [edit, draft, search]


# ── the numbers ──────────────────────────────────────────────────────────────

def test_a_command_is_in_the_journal_and_in_none_of_the_dictation_numbers():
    base = [dictation("2026-10-16T09:00:00", 10), dictation("2026-10-15T09:00:00", 20)]
    plain = journal_data.compute_stats(base, TODAY)
    withs = journal_data.compute_stats(
        base + [edit(), draft("2026-10-14T10:00:00", after="x " * 500), search("2026-10-13T10:00:00")], TODAY)
    for key in plain:
        if key in ("entries", "command_count"):
            continue
        assert withs[key] == plain[key], key
    assert plain["entries"] == 2 and plain["command_count"] == 0
    assert withs["entries"] == 5 and withs["command_count"] == 3 and withs["transform_count"] == 0


def test_commands_and_rewrites_are_counted_apart():
    rewrite = tf.history_entry(tf.spec_for({}), "a b c", "A b c", timestamp="2026-10-16T10:00:00")
    s = journal_data.compute_stats([dictation("2026-10-16T09:00:00"), rewrite, edit(), search()], TODAY)
    assert (s["entries"], s["transform_count"], s["command_count"]) == (4, 1, 2)
    assert s["today_count"] == 1 and s["today_words"] == 10


def test_a_day_with_only_a_command_does_not_extend_the_streak():
    s = journal_data.compute_stats([dictation("2026-10-15T09:00:00"), edit("2026-10-16T10:00:00")], TODAY)
    assert s["streak_days"] == 1 and s["today_count"] == 0 and s["today_words"] == 0
    only = journal_data.compute_stats([edit()], TODAY)
    assert only["streak_days"] == 0 and only["total_words"] == 0 and only["entries"] == 1
    gap = [dictation("2026-10-13T09:00:00"), search("2026-10-14T10:00:00"), dictation("2026-10-15T09:00:00")]
    assert journal_data.compute_stats(gap, TODAY)["longest_streak_days"] == 1


def test_the_cache_reports_the_same(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps([dictation("2026-10-16T09:00:00"), draft()]), encoding="utf-8")
    cache = journal_data.HistoryCache(path, lambda: json.loads(path.read_text(encoding="utf-8")))
    s = cache.stats(TODAY)
    assert s["entries"] == 2 and s["command_count"] == 1 and s["today_count"] == 1 and s["today_words"] == 10


def test_the_week_month_and_total_words_leave_commands_out():
    s = journal_data.compute_stats([dictation("2026-10-16T09:00:00", 10), edit(after="word " * 100)], TODAY)
    assert (s["week_words"], s["month_words"], s["total_words"], s["total_count"]) == (10, 10, 10, 1)


def test_the_kinds_that_are_not_dictations_are_one_list_everywhere():
    assert history_meta.NON_DICTATION_KINDS == ("transform", "command")
    assert pasteboard._NOT_A_DICTATION == history_meta.NON_DICTATION_KINDS
    assert history_meta.is_dictation(dictation("t")) and not history_meta.is_dictation(edit())
    assert not history_meta.is_dictation(None) and not history_meta.is_dictation("x")


# ── search ───────────────────────────────────────────────────────────────────

def test_search_finds_a_command_by_what_was_said_what_it_made_and_the_question():
    items = [dictation("2026-10-16T09:00:00"),
             edit(before="milk and eggs", after="Milk, eggs"), draft(after="Hello Sam"), search(query="capital of Australia")]
    assert len(journal_data.page(items, query="fancier")) == 1                # the instruction
    assert journal_data.page(items, query="MILK AND")[0]["kind"] == "command"  # the text before
    assert len(journal_data.page(items, query="hello sam")) == 1               # the draft
    assert len(journal_data.page(items, query="australia")) == 1               # the question (and what was said)
    assert journal_data.page(items, query="cheese") == []
    assert [i.get("kind") for i in journal_data.page(items)] == ["command", "command", "command", None]


# ── Paste last ───────────────────────────────────────────────────────────────

def test_a_command_is_never_the_last_transcript():
    last = pasteboard.LastResult(history_loader=lambda: [dictation("2026-10-16T09:00:00"), edit(), draft(), search()])
    assert last.get() == " ".join(["W"] * 10)
    only = pasteboard.LastResult(history_loader=lambda: [edit(), draft(), search()])
    assert only.get() == "" and only.word_count() == 0


# ── words, export ────────────────────────────────────────────────────────────

def test_the_words_of_a_command_are_its_result_or_its_question():
    assert history_meta.words_of(edit(after="Done here")) == "Done here"
    assert history_meta.words_of(draft(after="Hi")) == "Hi"
    assert history_meta.words_of(search(query="capital of Australia")) == "capital of Australia"
    assert history_meta.words_of(dictation("t")) == " ".join(["W"] * 10)


def test_export_labels_each_command_and_writes_its_result():
    items = [dictation("2026-10-16T09:00:00"), edit(after="Done, here"), draft("2026-10-16T11:00:00"),
             search("2026-10-16T12:00:00", engine="duckduckgo")]
    items[1]["app"] = "Notes"
    txt = history_export.render_txt(items, now=datetime(2026, 10, 16, 13, 0))
    assert "2026-10-16 10:00 (Notes), edited by voice\nDone, here" in txt
    assert "2026-10-16 11:00, drafted by voice\nHi Sam, I'll be late." in txt
    assert "2026-10-16 12:00, searched DuckDuckGo\ncapital of Australia" in txt
    csv = history_export.render_csv(items).splitlines()
    assert len(csv) == 5 and "Done, here" in csv[2]
    data = json.loads(history_export.render_json(items))
    assert data[1]["kind"] == "command" and data[1]["action"] == "edit" and data[1]["instruction"] == "make this fancier"
    assert data[1]["before"] == "a b c" and data[1]["after"] == "Done, here"
    assert data[3]["query"] == "capital of Australia" and data[3]["engine"] == "duckduckgo"


def test_an_instruction_that_starts_like_a_formula_is_made_safe_in_a_spreadsheet():
    e = cm.history_entry(cm.DRAFT, "x", after="=HYPERLINK(\"http://evil\")", timestamp="2026-10-16T10:00:00")
    cell = history_export.render_csv([e]).splitlines()[1]
    assert "'=HYPERLINK" in cell


# ── delete, retention, redo ──────────────────────────────────────────────────

def test_one_command_is_found_by_its_id_and_removed_like_any_entry():
    items = [dictation("2026-10-16T09:00:00", ident=1), dict(edit(), id=2), dict(search(), id=3)]
    assert history_ops.find(items, 2) == 1 and history_ops.find(items, 3) == 2
    del items[history_ops.find(items, 2)]
    assert [i["id"] for i in items] == [1, 3]


def test_retention_prunes_commands_like_any_entry():
    old, new = edit("2026-01-01T10:00:00"), edit("2026-10-15T10:00:00")
    kept, removed = privacy_data.prune_history([old, new], 30, now=datetime(2026, 10, 16, 12, 0))
    assert kept == [new] and removed == 1


def test_delete_all_my_data_takes_the_commands_with_the_history(tmp_path):
    from atomic_json import write_json_atomic
    write_json_atomic(tmp_path / "history.json", [dictation("2026-10-16T09:00:00"), edit(), search()])
    assert privacy_data.delete_my_data(tmp_path)["ok"] is True
    assert json.loads((tmp_path / "history.json").read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("make", ALL_KINDS)
def test_redo_is_not_offered_for_a_command(tmp_path, make):
    p = H.make_pipeline(tmp_path)
    entry = dict(make(), id=7)
    (tmp_path / "history.json").write_text(json.dumps([entry]), encoding="utf-8")
    assert "audio" not in entry, "no recording is kept, so the Journal never shows Redo"
    assert p.redo_take(7) == {"ok": False, "reason": "not_found"}
    assert p.transcriber.calls == 0


def test_redo_refuses_a_command_even_if_one_somehow_has_a_recording_name(tmp_path):
    p = H.make_pipeline(tmp_path)
    entry = dict(edit(), id=7, audio="recording-2026-10-16T10-00-00.wav")
    (tmp_path / "history.json").write_text(json.dumps([entry]), encoding="utf-8")
    assert p.redo_take(7)["ok"] is False and p.transcriber.calls == 0


# ── the usage record ─────────────────────────────────────────────────────────

def _usage_ns():
    ns = {}
    nodes = [n for n in _TREE.body if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in
                                       ("MODEL_RATES", "_RATES_CHECKED"))
             or (isinstance(n, ast.FunctionDef) and n.name in ("_rate_for", "_usage_cost", "_entry_rate_is_estimate",
                                                               "record_usage"))]
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    saved = []
    ns.update(datetime=datetime, USAGE_FILE=None, USAGE_LOG_FILE=None, ensure_data_dir=lambda: None,
              _usage_lock=threading.Lock(), _log_to_file=lambda m: None,
              _usage_log=types.SimpleNamespace(try_import_legacy=lambda *a: None,
                                               append=lambda path, row: saved.append(row)))
    return ns, saved


def test_a_commands_usage_rows_are_priced_like_any_call_and_tagged():
    ns, saved = _usage_ns()
    plain = ns["record_usage"]("gpt", input_tokens=1000, output_tokens=500, provider="groq")
    tagged = ns["record_usage"]("gpt", input_tokens=1000, output_tokens=500, provider="groq", purpose="command")
    speech = ns["record_usage"]("whisper", duration_seconds=4.0, provider="groq", purpose="command")
    assert "purpose" not in plain and tagged["purpose"] == "command" and speech["purpose"] == "command"
    assert tagged["cost_usd"] == plain["cost_usd"] > 0


def test_the_cost_per_dictation_leaves_out_what_commands_cost_but_every_total_keeps_it():
    ns = {}
    method = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "get_usage_stats")
    exec(compile(ast.Module([method], []), "<app.py>", "exec"), ns)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows = [
        {"timestamp": now, "type": "whisper", "provider": "groq", "cost_usd": 0.01, "duration_seconds": 5},
        {"timestamp": now, "type": "gpt", "provider": "groq", "cost_usd": 0.01, "input_tokens": 10, "output_tokens": 5},
        {"timestamp": now, "type": "whisper", "provider": "groq", "cost_usd": 0.02, "duration_seconds": 3,
         "purpose": "command"},
        {"timestamp": now, "type": "gpt", "provider": "groq", "cost_usd": 0.05, "purpose": "command",
         "input_tokens": 90, "output_tokens": 50},
        {"timestamp": now, "type": "gpt", "provider": "groq", "cost_usd": 0.04, "purpose": "transform",
         "input_tokens": 90, "output_tokens": 50},
    ]
    ns.update(datetime=datetime, load_usage=lambda: rows, _entry_rate_is_estimate=lambda e: False)
    stats = ns["get_usage_stats"](None)
    assert stats["total_cost_usd"] == pytest.approx(0.13)
    assert stats["today_cost_usd"] == pytest.approx(0.13)
    assert stats["avg_cost_per_transcription"] == pytest.approx(0.02), "(0.13 - 0.07 - 0.04) / 1 dictation"
    assert stats["transcription_count"] == 1, "a command's speech is not a transcription"
    assert stats["total_duration_seconds"] == 5
    assert stats["command_count"] == 1 and stats["command_cost_usd"] == pytest.approx(0.07)
    assert stats["transform_count"] == 1 and stats["transform_cost_usd"] == pytest.approx(0.04)
    assert stats["by_provider"]["groq"]["cost_usd"] == pytest.approx(0.13)


def test_the_logs_copy_of_the_settings_carries_no_wording_from_command_mode():
    raw = json.dumps({"command_mode": {"enabled": True, "hotkey": ["control", "option", "c"], "web_engine": "google"}})
    out = json.loads(privacy_data.settings_for_logs(raw))
    assert out["command_mode"] == {"enabled": True, "hotkey": ["control", "option", "c"], "web_engine": "google"}
