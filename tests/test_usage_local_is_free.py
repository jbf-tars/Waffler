"""A dictation done on this computer costs nothing, and the usage record says so.

Before: record_usage priced provider "local" (speech turned into text on this
computer) at OpenAI's published rate, because _rate_for fell back to OpenAI
for any provider it did not know. A 12-second dictation was logged as
$0.00097 of "gpt-transcribe", and Settings, Usage added it to the money
spent, so it looked as if the person's key had been used. Now a local row
costs 0 and its model reads "on this computer"; rows logged before the fix
with a made-up cost are left out of every total too.
"""
import ast
import threading
import types
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _usage_ns():
    ns = {}
    nodes = [n for n in _TREE.body if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in
                                       ("MODEL_RATES", "_RATES_CHECKED"))
             or (isinstance(n, ast.FunctionDef) and n.name in ("_rate_for", "_usage_cost", "record_usage"))]
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    saved = []
    ns.update(datetime=datetime, USAGE_FILE=None, USAGE_LOG_FILE=None, ensure_data_dir=lambda: None,
              _usage_lock=threading.Lock(), _log_to_file=lambda m: None,
              _usage_log=types.SimpleNamespace(try_import_legacy=lambda *a: None,
                                               append=lambda path, row: saved.append(row)))
    return ns, saved


def test_a_dictation_on_this_computer_costs_nothing():
    ns, saved = _usage_ns()
    for provider in ("local", "ondevice"):
        speech = ns["record_usage"]("whisper", duration_seconds=12.3, provider=provider)
        writing = ns["record_usage"]("gpt", input_tokens=5600, output_tokens=40, provider=provider)
        for row in (speech, writing):
            assert row["cost_usd"] == 0, row
            assert row["model"] == "on this computer" and row["rate_verified"] is True, row
    assert ns["record_usage"]("whisper", duration_seconds=12.3, provider="groq")["cost_usd"] > 0
    assert ns["record_usage"]("whisper", duration_seconds=12.3, provider="openai")["cost_usd"] > 0


def test_the_totals_leave_out_on_device_rows_even_ones_logged_with_a_made_up_cost():
    ns = {}
    method = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "get_usage_stats")
    exec(compile(ast.Module([method], []), "<app.py>", "exec"), ns)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows = [
        {"timestamp": now, "type": "whisper", "provider": "groq", "cost_usd": 0.01, "duration_seconds": 5},
        # logged before the fix: "local", priced as OpenAI's gpt-transcribe
        {"timestamp": now, "type": "whisper", "provider": "local", "cost_usd": 0.00097,
         "duration_seconds": 12.3, "model": "gpt-transcribe"},
        {"timestamp": now, "type": "whisper", "provider": "local", "cost_usd": 0.0,
         "duration_seconds": 8, "model": "on this computer"},
    ]
    ns.update(datetime=datetime, load_usage=lambda: rows, _entry_rate_is_estimate=lambda e: False)
    stats = ns["get_usage_stats"](None)
    for total in ("total_cost_usd", "month_cost_usd", "week_cost_usd", "today_cost_usd"):
        assert stats[total] == pytest.approx(0.01), total
    assert stats["by_provider"]["local"]["cost_usd"] == 0
    assert stats["by_provider"]["groq"]["cost_usd"] == pytest.approx(0.01)
    assert stats["transcription_count"] == 3                  # still dictations, just free ones
    assert stats["avg_cost_per_transcription"] == pytest.approx(round(0.01 / 3, 4))
