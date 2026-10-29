"""The usage record is appended to, never rewritten (S4).

record_usage used to read the whole usage.json, add a row and write it back,
twice per dictation, both times BEFORE the paste. That cost about 60 ms at
3,300 dictations and about 370 ms at 20,000, on the path between the user
letting go of the key and their words appearing. The benchmark below measures
the real record_usage from app.py against a data folder seeded with 20,000
dictations' worth of rows (two rows each) and asserts the cost of one more is
flat. The rest pins src/usage_log.py: what a line is, a torn last line, and
the one-time, repeatable move of an old usage.json.
"""
import ast
import json
import os
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import atomic_json  # noqa: E402
import privacy_data  # noqa: E402
import usage_log  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


def _app_defs(*names):
    nodes = [n for n in _TREE.body
             if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in names)
             or (isinstance(n, ast.FunctionDef) and n.name in names)]
    ns: dict = {}
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    for n in names:
        assert n in ns, f"{n} not found in app.py"
    return ns


def _row(i):
    return {"timestamp": f"2026-01-{1 + i % 28:02d}T10:{i % 60:02d}:{i % 60:02d}",
            "type": "whisper" if i % 2 == 0 else "gpt", "provider": "groq",
            "input_tokens": 5200, "output_tokens": 180, "cost_usd": 0.000842,
            "model": "whisper-large-v3", "rate_verified": True, "duration_seconds": 4.2}


def _app(tmp_path, logged=None):
    ns = _app_defs("MODEL_RATES", "_rate_for", "_usage_cost", "ensure_data_dir",
                   "load_usage", "save_usage", "record_usage", "record_usage_safely")
    logged = logged if logged is not None else []
    ns.update(json=json, os=os, datetime=datetime, threading=threading,
              DATA_DIR=tmp_path, USAGE_FILE=tmp_path / "usage.json",
              USAGE_LOG_FILE=tmp_path / usage_log.FILE, _usage_log=usage_log,
              _usage_lock=threading.Lock(), _log_to_file=logged.append)
    ns["logged"] = logged
    return ns


@pytest.fixture(autouse=True)
def _fresh_import_memory():
    usage_log._gave_up.clear()
    yield
    usage_log._gave_up.clear()


# ── the benchmark ────────────────────────────────────────────────────────────

DICTATIONS = 20_000


def _timed(record, n):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        record("whisper", duration_seconds=3.0, provider="groq")
        times.append(time.perf_counter() - t0)
    return times


def test_recording_usage_costs_the_same_at_20000_dictations_as_at_none(tmp_path):
    app = _app(tmp_path)
    record = app["record_usage"]
    small = statistics.median(_timed(record, 15))

    # A folder that has been used for years: 20,000 dictations, two rows each
    # (the speech and the clean-up). How it got there does not matter to what
    # one more row costs, which is what is measured.
    with open(tmp_path / usage_log.FILE, "ab") as f:
        for i in range(DICTATIONS * 2):
            f.write(usage_log._line(_row(i)))
    big = statistics.median(_timed(record, 15))

    # Flat means: not a function of how much is already recorded. The old
    # cost grew a hundredfold; a few times the empty-file cost, or 5 ms
    # (a slow Windows disk), is still flat.
    assert big <= max(small * 5, 0.005), (
        f"adding a row took {big * 1000:.1f} ms with {DICTATIONS * 2} rows already "
        f"recorded and {small * 1000:.2f} ms with none")
    assert len(usage_log.read(tmp_path / usage_log.FILE)) == DICTATIONS * 2 + 30


def test_the_old_file_is_moved_in_without_slowing_the_next_dictation_twice(tmp_path):
    """The one-off move of a big usage.json happens once; after it the cost
    is the same as ever."""
    # What a row costs on this machine with nothing recorded, the yardstick
    # (a fixed 5 ms failed on a slow Windows disk, where a row costs 5-7 ms).
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    small = statistics.median(_timed(_app(fresh)["record_usage"], 15))

    moved = tmp_path / "moved"
    moved.mkdir()
    app = _app(moved)
    (moved / "usage.json").write_text(
        json.dumps([_row(i) for i in range(DICTATIONS * 2)]), encoding="utf-8")
    app["record_usage"]("whisper", duration_seconds=3.0, provider="groq")   # the move
    after = statistics.median(_timed(app["record_usage"], 15))
    # Rewriting the old file each time cost hundreds of ms; a few times the
    # empty-folder cost, or 5 ms, is the same as ever.
    assert after <= max(small * 5, 0.005), (
        f"adding a row took {after * 1000:.1f} ms after the move and "
        f"{small * 1000:.2f} ms with nothing recorded")
    assert not (moved / "usage.json").exists()
    assert len(usage_log.read(moved / usage_log.FILE)) == DICTATIONS * 2 + 16


# ── a line, a torn line ──────────────────────────────────────────────────────

def test_a_row_is_one_ascii_line_and_reads_back_equal(tmp_path):
    p = tmp_path / "u.jsonl"
    row = {"timestamp": "2026-10-16T09:00:00", "type": "gpt", "provider": "groq",
           "note": "café   line\nbreak", "cost_usd": 0.1}
    usage_log.append(p, row)
    raw = p.read_bytes()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1 and raw.isascii()
    assert usage_log.read(p) == [row]


def test_appending_to_nothing_creates_the_file_and_to_a_file_adds_a_line(tmp_path):
    p = tmp_path / "u.jsonl"
    assert usage_log.read(p) == []
    usage_log.append(p, {"a": 1})
    usage_log.append(p, {"a": 2})
    assert [r["a"] for r in usage_log.read(p)] == [1, 2]


def test_a_torn_last_line_is_skipped_and_the_next_row_starts_on_its_own_line(tmp_path):
    p = tmp_path / "u.jsonl"
    usage_log.append(p, {"a": 1})
    with open(p, "ab") as f:
        f.write(b'{"a": 2, "ty')                      # power cut mid-write
    assert usage_log.read(p) == [{"a": 1}]
    usage_log.append(p, {"a": 3})
    assert usage_log.read(p) == [{"a": 1}, {"a": 3}]
    assert usage_log.read(p) and p.read_bytes().count(b"\n") == 3


def test_blank_lines_and_lines_that_are_not_rows_are_skipped(tmp_path):
    p = tmp_path / "u.jsonl"
    p.write_bytes(b'\xef\xbb\xbf{"a": 1}\n\n[1, 2]\n"text"\n{"a": 2}\r\n   \nnot json\n')
    assert usage_log.read(p) == [{"a": 1}, {"a": 2}]


def test_write_all_replaces_the_record(tmp_path):
    p = tmp_path / "u.jsonl"
    usage_log.append(p, {"a": 1})
    usage_log.write_all(p, [{"a": 2}, {"a": 3}])
    assert usage_log.read(p) == [{"a": 2}, {"a": 3}]
    usage_log.write_all(p, [])
    assert usage_log.read(p) == [] and p.exists()
    assert not list(tmp_path.glob("*.tmp"))


# ── moving an old usage.json in ──────────────────────────────────────────────

def _legacy(tmp_path, rows, name="usage.json"):
    p = tmp_path / name
    p.write_text(json.dumps(rows), encoding="utf-8")
    return p


def test_import_moves_every_row_and_sets_the_old_file_aside(tmp_path):
    rows = [_row(i) for i in range(7)]
    legacy = _legacy(tmp_path, rows)
    new = tmp_path / "usage.jsonl"
    assert usage_log.import_legacy(legacy, new) == 7
    assert usage_log.read(new) == rows
    assert not legacy.exists()
    backups = list(tmp_path.glob("usage.backup-*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == rows
    # Run again: nothing left to do.
    assert usage_log.import_legacy(legacy, new) is None
    assert usage_log.read(new) == rows


def test_import_with_nothing_to_move(tmp_path):
    assert usage_log.import_legacy(tmp_path / "usage.json", tmp_path / "usage.jsonl") is None
    legacy = _legacy(tmp_path, [])
    assert usage_log.import_legacy(legacy, tmp_path / "usage.jsonl") == 0
    assert not legacy.exists() and not (tmp_path / "usage.jsonl").exists()
    assert not list(tmp_path.glob("usage.backup-*"))


def test_import_stopped_before_the_old_file_was_set_aside_adds_nothing_twice(tmp_path, monkeypatch):
    rows = [_row(i) for i in range(5)]
    legacy = _legacy(tmp_path, rows)
    new = tmp_path / "usage.jsonl"

    def crash(src, dst):                    # the process dies at the last step
        raise KeyboardInterrupt
    real = usage_log.replace_with_retry
    calls = []

    def replace(src, dst, **kw):
        calls.append(dst)
        if Path(dst) == new:
            return real(src, dst, **kw)
        return crash(src, dst)
    monkeypatch.setattr(usage_log, "replace_with_retry", replace)
    with pytest.raises(KeyboardInterrupt):
        usage_log.import_legacy(legacy, new)
    assert legacy.exists() and usage_log.read(new) == rows

    monkeypatch.setattr(usage_log, "replace_with_retry", real)
    assert usage_log.import_legacy(legacy, new) == 0           # all held already
    assert usage_log.read(new) == rows
    assert not legacy.exists()


def test_import_stopped_part_way_through_a_big_add_finishes_it(tmp_path):
    rows = [_row(i) for i in range(6)]
    legacy = _legacy(tmp_path, rows)
    new = tmp_path / "usage.jsonl"
    for r in rows[:2]:
        usage_log.append(new, r)
    with open(new, "ab") as f:
        f.write(b'{"timestamp": "2026-01-01T1')           # torn
    assert usage_log.import_legacy(legacy, new) == 4
    assert usage_log.read(new) == rows


def test_rows_recorded_while_an_older_version_ran_are_kept(tmp_path):
    """Back to 3.15 and on again: its usage.json has rows the new file does
    not, and the new file has rows it does not."""
    old = [_row(i) for i in range(3)]
    new = tmp_path / "usage.jsonl"
    for r in old:
        usage_log.append(new, r)
    usage_log.append(new, _row(10))
    legacy = _legacy(tmp_path, old + [_row(20), _row(21)])
    assert usage_log.import_legacy(legacy, new) == 2
    assert usage_log.read(new) == old + [_row(10), _row(20), _row(21)]


def test_two_real_rows_that_look_alike_are_both_kept(tmp_path):
    same = _row(1)
    legacy = _legacy(tmp_path, [same, dict(same), dict(same)])
    new = tmp_path / "usage.jsonl"
    usage_log.append(new, same)
    assert usage_log.import_legacy(legacy, new) == 2
    assert len(usage_log.read(new)) == 3


def test_an_unreadable_old_file_is_kept_aside_as_it_always_was(tmp_path):
    (tmp_path / "usage.json").write_bytes(b"not json")
    logged = []
    assert usage_log.import_legacy(tmp_path / "usage.json", tmp_path / "usage.jsonl", logged.append) == 0
    kept = list(tmp_path.glob("usage.unreadable-*.json"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"not json"
    assert not (tmp_path / "usage.json").exists()
    assert any("could not be read" in m for m in logged)


def test_a_bom_and_stray_items_in_the_old_file_are_tolerated(tmp_path):
    rows = [_row(1)]
    (tmp_path / "usage.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(rows + ["junk", 3, None]).encode("utf-8"))
    assert usage_log.import_legacy(tmp_path / "usage.json", tmp_path / "usage.jsonl") == 1
    assert usage_log.read(tmp_path / "usage.jsonl") == rows


def test_the_dictation_path_import_never_raises_and_gives_up_for_the_run(tmp_path, monkeypatch):
    legacy = _legacy(tmp_path, [_row(1)])
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise PermissionError("locked")
    monkeypatch.setattr(usage_log, "import_legacy", boom)
    logged = []
    assert usage_log.try_import_legacy(legacy, tmp_path / "u.jsonl", logged.append) is None
    assert usage_log.try_import_legacy(legacy, tmp_path / "u.jsonl", logged.append) is None
    assert len(calls) == 1 and len(logged) == 1


# ── app.py: the writers and readers ──────────────────────────────────────────

def test_record_usage_adds_a_row_and_load_usage_returns_every_row(tmp_path):
    app = _app(tmp_path)
    first = app["record_usage"]("whisper", duration_seconds=3.0, provider="groq")
    app["record_usage"]("gpt", input_tokens=100, output_tokens=20, provider="groq")
    rows = app["load_usage"]()
    assert [r["type"] for r in rows] == ["whisper", "gpt"] and rows[0] == first
    assert not (tmp_path / "usage.json").exists()


def test_the_first_dictation_after_an_update_moves_the_old_file_and_loses_nothing(tmp_path):
    app = _app(tmp_path)
    old = [_row(i) for i in range(4)]
    _legacy(tmp_path, old)
    assert app["load_usage"]() == old                        # Settings opened first
    assert (tmp_path / "usage.jsonl").exists()
    app["record_usage"]("whisper", duration_seconds=3.0, provider="groq")
    assert app["load_usage"]()[:4] == old and len(app["load_usage"]()) == 5


def test_record_usage_still_never_raises_into_the_dictation(tmp_path, monkeypatch):
    app = _app(tmp_path)

    def locked(*a, **k):
        raise PermissionError("[WinError 5] Access is denied")
    monkeypatch.setattr(usage_log, "append", locked)
    assert app["record_usage_safely"]("whisper", duration_seconds=3.0, provider="groq") is None
    assert any("[usage] not recorded" in m and "PermissionError" in m for m in app["logged"])


def test_usage_written_from_four_threads_keeps_every_row(tmp_path):
    app = _app(tmp_path)

    def work():
        for _ in range(25):
            app["record_usage"]("whisper", duration_seconds=1.0, provider="groq")

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(app["load_usage"]()) == 100


def test_resetting_usage_empties_it_and_an_old_file_cannot_bring_it_back(tmp_path):
    app = _app(tmp_path)
    _legacy(tmp_path, [_row(1), _row(2)])
    app["save_usage"]([])
    assert app["load_usage"]() == []
    assert not (tmp_path / "usage.json").exists()


def test_record_usage_holds_the_usage_lock_and_appends():
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "record_usage")
    text = ast.unparse(fn)
    withs = [w for w in ast.walk(fn) if isinstance(w, ast.With)]
    assert any("_usage_lock" in ast.unparse(w.items[0].context_expr) for w in withs)
    assert "_usage_log.append(" in text
    assert "read_json_for_update" not in text and "save_usage" not in text   # no rewrite


# ── the privacy lists ────────────────────────────────────────────────────────

def test_usage_jsonl_is_deleted_with_my_data_and_its_backup_too(tmp_path):
    assert usage_log.FILE in privacy_data.DATA_FILES
    assert usage_log.FILE not in privacy_data.KEPT_FILES
    usage_log.append(tmp_path / usage_log.FILE, _row(1))
    (tmp_path / "usage.backup-20261016-100000.json").write_text("[]", encoding="utf-8")
    (tmp_path / "history.json").write_text("[]", encoding="utf-8")
    assert privacy_data.delete_my_data(tmp_path)["ok"] is True
    assert not (tmp_path / usage_log.FILE).exists()
    assert not list(tmp_path.glob("usage.backup-*.json"))


def test_the_logs_zip_leaves_usage_out_and_says_so():
    fn = next(n for n in ast.walk(_TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "download_logs")
    listed = [c.value for c in ast.walk(fn) if isinstance(c, ast.Constant)
              and isinstance(c.value, str) and c.value.endswith((".json", ".jsonl"))]
    assert "usage.jsonl" not in listed and "usage.json" not in listed
    assert "usage.jsonl" in ast.get_docstring(fn)


# ── scripts/recost_usage.py works on the new file ────────────────────────────

def _recost_module(tmp_path, monkeypatch, argv=()):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "recost_usage_s4", ROOT / "scripts" / "recost_usage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "USAGE_LOG", tmp_path / "usage.jsonl")
    monkeypatch.setattr(mod, "USAGE", tmp_path / "usage.json")
    monkeypatch.setattr(sys, "argv", ["recost_usage.py", *argv])
    return mod


def test_recost_reprices_usage_jsonl_in_place_and_keeps_a_backup(tmp_path, monkeypatch, capsys):
    mod = _recost_module(tmp_path, monkeypatch)
    wrong = {"timestamp": "2026-10-01T10:00:00", "type": "whisper", "provider": "groq",
             "duration_seconds": 3.0, "cost_usd": 99.0}
    usage_log.append(tmp_path / "usage.jsonl", wrong)
    assert mod.main() == 0
    rows = usage_log.read(tmp_path / "usage.jsonl")
    assert len(rows) == 1 and rows[0]["cost_usd"] == pytest.approx(10 / 3600 * 0.111, abs=1e-6)
    assert len(list(tmp_path.glob("usage.backup-*.json"))) == 1
    assert "usage.jsonl updated" in capsys.readouterr().out


def test_recost_dry_run_writes_nothing(tmp_path, monkeypatch):
    mod = _recost_module(tmp_path, monkeypatch, argv=["--dry-run"])
    usage_log.append(tmp_path / "usage.jsonl", {"type": "whisper", "provider": "groq",
                                                "duration_seconds": 3.0, "cost_usd": 99.0})
    before = (tmp_path / "usage.jsonl").read_bytes()
    assert mod.main() == 0
    assert (tmp_path / "usage.jsonl").read_bytes() == before
    assert not list(tmp_path.glob("usage.backup-*.json"))


def test_recost_still_reads_an_old_usage_json(tmp_path, monkeypatch):
    mod = _recost_module(tmp_path, monkeypatch)
    (tmp_path / "usage.json").write_text(json.dumps([
        {"type": "whisper", "provider": "groq", "duration_seconds": 3.0, "cost_usd": 99.0}]),
        encoding="utf-8")
    assert mod.main() == 0
    rows = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert rows[0]["cost_usd"] == pytest.approx(10 / 3600 * 0.111, abs=1e-6)
