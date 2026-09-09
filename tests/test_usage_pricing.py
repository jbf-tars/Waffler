#!/usr/bin/env python3
"""The Usage panel was confidently wrong.

Rates were keyed by provider and had drifted from the models the app actually
calls, in both directions, and Cerebras had no branch at all so it was billed
at OpenAI's rates:

    Groq cleanup   priced as Llama 3.3 70B  $0.59/$0.79  actual $0.15/$0.60
    Groq whisper   priced at $0.168/hour                 actual $0.111/hour
    OpenAI cleanup priced as gpt-4o-mini    $0.15/$0.60  actual $0.40/$1.60
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
    """gpt-4.1-mini, not gpt-4o-mini: the app calls the former."""
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
