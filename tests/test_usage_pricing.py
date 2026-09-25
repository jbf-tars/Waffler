#!/usr/bin/env python3
"""The Usage panel was confidently wrong.

Rates were keyed by provider and had drifted from the models the app actually
calls, in both directions, and Cerebras had no branch at all so it was billed
at OpenAI's rates:

    Groq cleanup   priced as Llama 3.3 70B  $0.59/$0.79  actual $0.15/$0.60
    Groq whisper   priced at $0.168/hour                 actual $0.111/hour
    OpenAI cleanup priced as gpt-4o-mini    $0.15/$0.60  actual $0.40/$1.60  # doc-drift-ok (records the wrong rate deliberately)
    OpenAI whisper priced as whisper-1      $0.006/min   actual $0.003/min
    Cerebras       no rate at all, fell through to OpenAI's

Rates verified 2026-09-09 against console.groq.com/docs/models and
developers.openai.com/api/docs/pricing.

app.py imports pywebview and audio hardware at module scope, so the table is
read out of the source rather than imported.
"""

import ast
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _rates():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "MODEL_RATES":
            ns: dict = {}
            exec(compile(ast.Module([node], []), "<rates>", "exec"), ns)
            return ns["MODEL_RATES"]
    raise AssertionError("MODEL_RATES not found in app.py")


RATES = _rates()


# ── The published rates, as verified ────────────────────────────────────────

def test_groq_cleanup_rate_matches_published():
    r = RATES["groq"]["gpt"]
    assert r["model"] == "openai/gpt-oss-120b"
    assert (r["in_per_1m"], r["out_per_1m"]) == (0.15, 0.60)
    assert r["verified"] is True


def test_groq_whisper_rate_matches_published():
    r = RATES["groq"]["whisper"]
    assert r["model"] == "whisper-large-v3"
    assert r["per_hour"] == 0.111
    assert r["verified"] is True


def test_openai_cleanup_rate_matches_published():
    """gpt-4.1-mini, not gpt-4o-mini: the app calls the former."""  # doc-drift-ok (names the wrong model deliberately)
    r = RATES["openai"]["gpt"]
    assert r["model"] == "gpt-4.1-mini"
    assert (r["in_per_1m"], r["out_per_1m"]) == (0.40, 1.60)


def test_openai_whisper_rate_matches_published():
    """gpt-4o-mini-transcribe at $0.003/min, not whisper-1 at $0.006."""
    r = RATES["openai"]["whisper"]
    assert r["model"] == "gpt-4o-mini-transcribe"
    assert r["per_minute"] == 0.003


# ── Cerebras must not be billed as OpenAI ───────────────────────────────────

def test_cerebras_has_its_own_rate():
    assert "cerebras" in RATES and "gpt" in RATES["cerebras"], \
        "Cerebras with no rate falls through to OpenAI's prices"


def test_cerebras_rate_is_flagged_unverified():
    """Cerebras publishes no per-token rate, so the figure is an estimate and
    must say so rather than implying a precision we do not have."""
    assert RATES["cerebras"]["gpt"]["verified"] is False


def test_every_rate_records_its_source():
    for provider, kinds in RATES.items():
        for kind, r in kinds.items():
            assert r.get("source"), f"{provider}/{kind} has no source recorded"
            assert r.get("model"), f"{provider}/{kind} has no model recorded"


# ── The models priced are the models actually called ────────────────────────

def test_priced_models_match_the_code_that_calls_them():
    style = (ROOT / "src/style_openai.py").read_text(encoding="utf-8")
    trans = (ROOT / "src/transcribe_whisper.py").read_text(encoding="utf-8")
    assert RATES["groq"]["gpt"]["model"] in style, \
        "Groq cleanup is priced as a model the styler does not call"
    assert RATES["groq"]["whisper"]["model"] in trans, \
        "Groq transcription is priced as a model the transcriber does not call"
    assert RATES["openai"]["whisper"]["model"] in trans, \
        "OpenAI transcription is priced as a model the transcriber does not call"


# ── Arithmetic ──────────────────────────────────────────────────────────────

def _cost(provider, kind, dur=0.0, tin=0, tout=0):
    r = RATES.get(provider, {}).get(kind) or RATES["openai"][kind]
    if kind == "whisper":
        return dur / 3600 * r["per_hour"] if "per_hour" in r else dur / 60 * r["per_minute"]
    return tin / 1e6 * r["in_per_1m"] + tout / 1e6 * r["out_per_1m"]


def test_an_hour_of_groq_audio_costs_the_hourly_rate():
    assert _cost("groq", "whisper", dur=3600) == pytest.approx(0.111)


def test_a_million_groq_input_tokens_costs_the_input_rate():
    assert _cost("groq", "gpt", tin=1_000_000) == pytest.approx(0.15)


def test_typical_dictation_is_a_fraction_of_a_cent():
    """~29s of audio and ~5.4k prompt tokens, measured from real usage."""
    total = _cost("groq", "whisper", dur=29) + _cost("groq", "gpt", tin=5400, tout=200)
    assert 0.0005 < total < 0.005, f"${total:.5f} is outside the plausible range"


# ── Groq bills at least 10 seconds per transcription request ────────────────
# console.groq.com/docs/speech-to-text: "Minimum Billed Length: 10 seconds.
# If you submit a request less than this, you will still be billed for 10
# seconds." The panel priced the raw duration, so every short dictation was
# under-reported. These run app.py's own pricing code, lifted out of the
# source the same way as MODEL_RATES above.

def _app_defs(*names, cls=None):
    """Execute the named top-level definitions of app.py (or, with ``cls``,
    methods of that class) in one fresh namespace."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    body = tree.body
    if cls:
        body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    nodes = [n for n in body
             if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in names)
             or (isinstance(n, ast.FunctionDef) and n.name in names)]
    ns: dict = {}
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    for n in names:
        assert n in ns, f"{n} not found in app.py"
    return ns


_P = _app_defs("MODEL_RATES", "_rate_for", "_usage_cost", "_entry_rate_is_estimate",
               "record_usage")
_usage_cost = _P["_usage_cost"]
GROQ_WHISPER = RATES["groq"]["whisper"]
OPENAI_WHISPER = RATES["openai"]["whisper"]


def _record(provider, dur):
    """Run the real record_usage with storage stubbed out; return the entry."""
    from datetime import datetime
    saved = []
    _P.update(datetime=datetime, load_usage=lambda: [],
              save_usage=lambda rows: saved.extend(rows))
    entry = _P["record_usage"]("whisper", duration_seconds=dur, provider=provider)
    assert saved == [entry]
    return entry


def test_groq_whisper_records_the_published_minimum():
    assert GROQ_WHISPER["min_billed_seconds"] == 10.0
    assert "speech-to-text" in GROQ_WHISPER["min_billed_source"]


def test_short_groq_clip_is_billed_as_ten_seconds():
    ten = 10 / 3600 * 0.111
    assert _usage_cost(GROQ_WHISPER, "whisper", 3.0) == pytest.approx(ten)
    assert _usage_cost(GROQ_WHISPER, "whisper", 0.4) == pytest.approx(ten)
    assert _usage_cost(GROQ_WHISPER, "whisper", 10.0) == pytest.approx(ten)


def test_groq_clip_over_ten_seconds_is_billed_as_is():
    assert _usage_cost(GROQ_WHISPER, "whisper", 29.0) == pytest.approx(29 / 3600 * 0.111)
    assert _usage_cost(GROQ_WHISPER, "whisper", 3600.0) == pytest.approx(0.111)


def test_openai_transcription_has_no_minimum():
    """OpenAI documents no minimum billed length, so none is applied."""
    assert "min_billed_seconds" not in OPENAI_WHISPER
    assert _usage_cost(OPENAI_WHISPER, "whisper", 3.0) == pytest.approx(3 / 60 * 0.003)


def test_no_audio_costs_nothing():
    assert _usage_cost(GROQ_WHISPER, "whisper", 0.0) == 0.0
    assert _usage_cost(GROQ_WHISPER, "whisper", None) == 0.0


def test_cleanup_costs_are_untouched_by_the_minimum():
    assert _usage_cost(RATES["groq"]["gpt"], "gpt", None, 5400, 200) == pytest.approx(
        5400 / 1e6 * 0.15 + 200 / 1e6 * 0.60)


def test_record_usage_bills_the_minimum_but_keeps_the_real_duration():
    entry = _record("groq", 3.0)
    assert entry["duration_seconds"] == 3.0, "the stored duration must stay the real length"
    assert entry["cost_usd"] == round(10 / 3600 * 0.111, 6)
    assert entry["model"] == "whisper-large-v3"


def test_record_usage_bills_a_short_openai_clip_as_is():
    entry = _record("openai", 3.0)
    assert entry["duration_seconds"] == 3.0
    assert entry["cost_usd"] == round(3 / 60 * 0.003, 6)


def test_recost_script_applies_the_same_minimum():
    """scripts/recost_usage.py must price history the way the app does, or a
    recost would quietly undo the minimum."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "recost_usage", ROOT / "scripts" / "recost_usage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pricing = mod.load_from_app("MODEL_RATES", "_usage_cost")
    row = {"type": "whisper", "provider": "groq", "duration_seconds": 3.0}
    cost, _ = mod.cost_for(pricing["MODEL_RATES"], row, pricing["_usage_cost"])
    assert cost == pytest.approx(10 / 3600 * 0.111)


# ── Estimated rates are labelled in the Usage panel ─────────────────────────

def test_cerebras_entries_count_as_estimates():
    est = _P["_entry_rate_is_estimate"]
    assert est({"provider": "cerebras", "type": "gpt", "rate_verified": False})
    # Rows from before 3.14.95 carry no flag; Cerebras is still an estimate.
    assert est({"provider": "cerebras", "type": "gpt"})
    assert not est({"provider": "groq", "type": "gpt", "rate_verified": True})
    assert not est({"provider": "groq", "type": "whisper"})
    assert not est({"provider": "openai", "type": "gpt"})


def test_usage_stats_report_estimated_calls_per_provider():
    stats_ns = _app_defs("get_usage_stats", cls="Api")
    from datetime import datetime
    rows = [
        {"timestamp": "2026-09-25T10:00:00", "type": "gpt", "provider": "cerebras",
         "cost_usd": 0.001, "rate_verified": False},
        {"timestamp": "2026-09-25T10:00:01", "type": "gpt", "provider": "cerebras",
         "cost_usd": 0.001},
        {"timestamp": "2026-09-25T10:00:02", "type": "gpt", "provider": "groq",
         "cost_usd": 0.001, "rate_verified": True},
    ]
    stats_ns.update(datetime=datetime, load_usage=lambda: rows,
                    _entry_rate_is_estimate=_P["_entry_rate_is_estimate"])
    by = stats_ns["get_usage_stats"](None)["by_provider"]
    assert by["cerebras"]["estimated_count"] == 2
    assert by["groq"]["estimated_count"] == 0


def test_usage_panel_marks_estimated_costs():
    js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    render = js[js.index("async function loadUsageStats"):js.index("async function loadAppVersion")]
    assert "estimated_count" in render, "the panel ignores which costs are estimates"
    assert ">estimate</span>" in render
    css = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")
    assert ".usage-provider-est" in css


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
