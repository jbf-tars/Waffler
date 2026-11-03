"""OpenAI retires both backup models (gpt-4.1-mini on 14 January 2027,
gpt-4o-mini-transcribe on 26 February 2027). The backup moves to the
replacements OpenAI names on its deprecations page:

  * clean-up:       gpt-6-luna      (a reasoning model: it refuses
                    ``max_tokens``, wants ``max_completion_tokens``, and is
                    told not to reason, which is also what lets it take a
                    ``temperature``; checked live 2026-11-03)
  * speech to text: gpt-transcribe  (same multipart request)

And a model OpenAI no longer serves is read the way Groq's and Cerebras's are:
a model someone pinned with OPENAI_STYLE_MODEL / OPENAI_WHISPER_MODEL that has
gone moves to the default, and a default that has gone pauses OpenAI for the
hour with a MODEL_UNAVAILABLE reason instead of a raw 404.

Offline: the clients are stubs.
"""
import importlib.util
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import style_openai as so  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
import user_messages as um  # noqa: E402
from style_openai import ChatError, OpenAIStyler  # noqa: E402

TEXT = ("ok so I wanted to talk about the budget for the project and what we "
        "can ship by the end of next month")
CLEAN = "Ok, so I wanted to talk about the budget for the project and what we can ship by the end of next month."
GONE = RuntimeError("Error code: 404 - {'error': {'message': 'The model `gpt-4.1-mini` does not exist "
                    "or you do not have access to it.', 'type': 'invalid_request_error', "
                    "'code': 'model_not_found'}}")
RATE = RuntimeError("Error code: 429 - Rate limit reached for gpt-6-luna on requests per min (RPM): "
                    "Limit 500, Used 500, Requested 1. Please try again in 120ms.")


def _answer(text=CLEAN):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text), finish_reason="stop")],
        usage=types.SimpleNamespace(prompt_tokens=50, completion_tokens=20))


class Client:
    """``outcomes`` by model; an exception is raised, anything else returned."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []
        self.chat = self.completions = self

    def create(self, **kw):
        self.calls.append(kw)
        out = self.outcomes.get(kw["model"], _answer())
        if isinstance(out, BaseException):
            raise out
        return out


def openai_only(client, monkeypatch, pinned=""):
    """A real OpenAIStyler with only an OpenAI key, its client swapped for a stub."""
    if pinned:
        monkeypatch.setenv("OPENAI_STYLE_MODEL", pinned)
    else:
        monkeypatch.delenv("OPENAI_STYLE_MODEL", raising=False)
    s = OpenAIStyler(api_key="sk-test-not-real", provider_order=["openai"])
    s._provider_order = ["openai"]
    s.client = client
    return s


def chat(s):
    return s._chat("SYS", "USER", max_tokens=2048, temperature=0.1,
                   deadline=time.monotonic() + 30, purpose="transform")


# ── the clean-up model ───────────────────────────────────────────────────────

def test_the_openai_clean_up_defaults_to_gpt_6_luna(monkeypatch):
    s = openai_only(Client({}), monkeypatch)
    assert so.OPENAI_STYLE_MODEL == "gpt-6-luna"
    assert s.model == "gpt-6-luna"


def test_gpt_6_luna_is_sent_max_completion_tokens_and_told_not_to_reason(monkeypatch):
    """Live, 2026-11-03: max_tokens is a 400 ("Use 'max_completion_tokens'
    instead"); with reasoning_effort none it takes temperature 0.1 and spends
    no reasoning tokens."""
    c = Client({})
    s = openai_only(c, monkeypatch)
    styled, usage = s.style(TEXT)
    assert styled == CLEAN and usage["provider"] == "openai"
    [sent] = c.calls
    assert sent["model"] == "gpt-6-luna"
    assert "max_tokens" not in sent
    assert sent["extra_body"]["max_completion_tokens"] >= 2048
    assert sent["extra_body"]["reasoning_effort"] == "none"
    assert sent["temperature"] == 0.1


def test_a_pinned_older_chat_model_is_not_sent_reasoning_flags(monkeypatch):
    c = Client({})
    s = openai_only(c, monkeypatch, pinned="gpt-4o")
    chat(s)
    [sent] = c.calls
    assert sent["model"] == "gpt-4o"
    assert sent["extra_body"] == {"max_completion_tokens": 2048}
    assert sent["temperature"] == 0.1


@pytest.mark.parametrize("model", ["o4-mini", "gpt-5.4-mini", "gpt-6-sol"])
def test_a_pinned_reasoning_model_is_not_sent_a_temperature(monkeypatch, model):
    """Reasoning models refuse a temperature unless told not to reason, and
    only gpt-6-luna is known (checked live) to accept 'none'."""
    c = Client({})
    s = openai_only(c, monkeypatch, pinned=model)
    chat(s)
    [sent] = c.calls
    assert "temperature" not in sent
    assert "reasoning_effort" not in sent["extra_body"]


# ── a model OpenAI no longer serves ─────────────────────────────────────────

def test_a_pinned_model_that_is_gone_moves_to_the_default(monkeypatch):
    c = Client({"gpt-4.1-mini": GONE})
    s = openai_only(c, monkeypatch, pinned="gpt-4.1-mini")
    styled, usage = s.style(TEXT)
    assert styled == CLEAN and usage["provider"] == "openai"
    assert [k["model"] for k in c.calls] == ["gpt-4.1-mini", "gpt-6-luna"]
    s.style(TEXT)
    assert c.calls[-1]["model"] == "gpt-6-luna", "the move lasts for the session"
    assert len(c.calls) == 3, "the gone model is not asked again"


def test_the_default_gone_pauses_openai_with_a_plain_reason(monkeypatch):
    c = Client({"gpt-6-luna": GONE})
    s = openai_only(c, monkeypatch)
    _text, usage = s.style(TEXT)
    reason = usage["fallback_reason"]
    assert usage["provider"] == "basic_clean"
    assert "MODEL_UNAVAILABLE" in reason and reason.startswith("OpenAI")
    heading, body = um.cleanup_skipped_message(reason)
    assert "no longer available" in body
    for jargon in ("MODEL_UNAVAILABLE", "404", "model_not_found"):
        assert jargon not in heading + body
    # Not re-probed on every dictation for the next hour.
    _text, usage = s.style(TEXT)
    assert len(c.calls) == 1
    assert "MODEL_UNAVAILABLE" in usage["fallback_reason"]


def test_the_shared_call_path_honours_the_pause_too(monkeypatch):
    c = Client({"gpt-6-luna": GONE})
    s = openai_only(c, monkeypatch)
    with pytest.raises(ChatError) as first:
        chat(s)
    assert "MODEL_UNAVAILABLE" in first.value.reason
    with pytest.raises(ChatError) as second:
        chat(s)
    assert "MODEL_UNAVAILABLE" in second.value.reason
    assert len(c.calls) == 1


def test_the_pause_ends_and_openai_is_asked_again(monkeypatch):
    c = Client({"gpt-6-luna": GONE})
    s = openai_only(c, monkeypatch)
    s.style(TEXT)
    s._openai_skip_until = time.monotonic() - 1
    c.outcomes = {}
    styled, usage = s.style(TEXT)
    assert styled == CLEAN and usage["provider"] == "openai"


def test_an_openai_rate_limit_is_not_read_as_a_gone_model(monkeypatch):
    c = Client({"gpt-6-luna": RATE})
    s = openai_only(c, monkeypatch)
    _text, usage = s.style(TEXT)
    assert "MODEL_UNAVAILABLE" not in usage["fallback_reason"]
    s.style(TEXT)
    assert len(c.calls) == 2, "a limit on OpenAI starts no hour-long pause"


# ── speech to text ───────────────────────────────────────────────────────────

class Audio:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []
        self.audio = self.transcriptions = self

    def create(self, **kw):
        kw = {k: v for k, v in kw.items() if k != "file"}
        self.calls.append(kw)
        out = self.outcomes.get(kw["model"], "hello there")
        if isinstance(out, BaseException):
            raise out
        return out


def transcriber(client, model):
    t = object.__new__(tw.WhisperTranscriber)
    t.model = model
    t.client = client
    return t


def test_openai_speech_to_text_defaults_to_gpt_transcribe(monkeypatch):
    monkeypatch.delenv("OPENAI_WHISPER_MODEL", raising=False)
    assert tw.OPENAI_TRANSCRIBE_MODEL == "gpt-transcribe"
    assert tw.WhisperTranscriber(api_key="sk-test-not-real").model == "gpt-transcribe"


def test_gpt_transcribe_gets_the_same_request_as_before():
    a = Audio({})
    t = transcriber(a, "gpt-transcribe")
    assert t._transcribe_api(b"RIFF....WAVE") == "hello there"
    [sent] = a.calls
    assert sent["model"] == "gpt-transcribe" and sent["response_format"] == "text"
    assert sent.get("language") == "en"


def test_a_pinned_transcription_model_that_is_gone_moves_to_the_default():
    a = Audio({"gpt-4o-mini-transcribe": GONE})
    t = transcriber(a, "gpt-4o-mini-transcribe")
    assert t._transcribe_api(b"RIFF....WAVE") == "hello there"
    assert [k["model"] for k in a.calls] == ["gpt-4o-mini-transcribe", "gpt-transcribe"]
    assert t.model == "gpt-transcribe"


def test_the_default_transcription_model_gone_is_raised_once():
    a = Audio({"gpt-transcribe": GONE})
    t = transcriber(a, "gpt-transcribe")
    with pytest.raises(RuntimeError):
        t._transcribe_api(b"RIFF....WAVE")
    assert len(a.calls) == 1


# ── prices ──────────────────────────────────────────────────────────────────

def _rates():
    spec = importlib.util.spec_from_file_location("recost_usage", ROOT / "scripts" / "recost_usage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, mod.load_from_app("MODEL_RATES", "_usage_cost")


def test_the_rate_table_prices_the_models_the_app_now_calls():
    _mod, p = _rates()
    gpt, whisper = p["MODEL_RATES"]["openai"]["gpt"], p["MODEL_RATES"]["openai"]["whisper"]
    assert gpt["model"] == so.OPENAI_STYLE_MODEL
    assert (gpt["in_per_1m"], gpt["out_per_1m"]) == (0.10, 0.50)
    assert whisper["model"] == tw.OPENAI_TRANSCRIBE_MODEL
    assert whisper["per_minute"] == 0.0045


def test_a_recost_leaves_rows_billed_as_another_model_alone():
    """A row says which model it was billed as. Repricing a gpt-4.1-mini row
    at gpt-6-luna's rate would rewrite history with the wrong model's price."""
    mod, p = _rates()
    old = {"type": "gpt", "provider": "openai", "input_tokens": 1000, "output_tokens": 500,
           "cost_usd": 0.0012, "model": "gpt-4.1-mini", "rate_verified": True}
    cost, rate = mod.cost_for(p["MODEL_RATES"], old, p["_usage_cost"])
    assert cost == pytest.approx(0.0012)
    assert rate["model"] == "gpt-4.1-mini"
    new = dict(old, model="gpt-6-luna")
    cost, _ = mod.cost_for(p["MODEL_RATES"], new, p["_usage_cost"])
    assert cost == pytest.approx(1000 / 1e6 * 0.10 + 500 / 1e6 * 0.50)
