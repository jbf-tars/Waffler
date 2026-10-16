#!/usr/bin/env python3
"""Groq clean-up robustness: a retired model, and a chain that really chains.

Three things this pins (S1 of the 2026-10 roadmap):

1. When Groq says the clean-up model is gone (a 404, ``model_not_found``,
   "decommissioned", "does not exist"), the styler moves to the next model in
   an ordered tuple and remembers it. The tuple is today's default, then an
   optional ``GROQ_STYLE_FALLBACK_MODEL``; nothing is added by default, so
   behaviour only changes for someone who sets the variable. When the tuple
   runs out, the old ``MODEL_UNAVAILABLE`` behaviour (and its cooldown) stands.

2. A retired model used to be reported as a rate limit. The cooldown after the
   404 said ``RATE_LIMIT|cooldown|3599s``, so a Groq-only user saw "Clean-up
   paused for about an hour" and the Journal banner about a limit they had
   never reached. It has its own tag and message now.

3. ``_style_openai`` caught its own exception and returned ``basic_clean``, so
   with OpenAI first in a user-chosen provider order, Groq and Cerebras were
   never tried. A failure now propagates and the chain carries on.

Offline: the clients are stubs.
"""

import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cleanup_pause  # noqa: E402
import user_messages as um  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402

PRIMARY = "openai/gpt-oss-120b"
BACKUP = "qwen/qwen3.8-27b"

TEXT = ("ok so I wanted to talk about the budget for the project and what we "
        "can ship by the end of next month")


def _gone(model):
    return RuntimeError(
        f"Error code: 404 - {{'error': {{'message': 'The model `{model}` does not exist "
        f"or you do not have access to it.', 'type': 'invalid_request_error', "
        f"'code': 'model_not_found'}}}}")


def _clean(tag):
    """A full-length clean-up of TEXT, so the truncation guard keeps it."""
    return f"Ok, so I wanted to talk about the budget for the project and what we can ship by the end of next month, {tag}."


def _answer(text="Done."):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text),
                                       finish_reason="stop")],
        usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=3))


class _Client:
    """A provider client: ``outcomes[model]`` is an exception (raised) or a
    response (returned); every call is recorded."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.calls.append(kw)
        out = self.outcomes[kw["model"]]
        if isinstance(out, Exception):
            raise out
        return out


def _styler(client, models=(PRIMARY,)):
    s = object.__new__(OpenAIStyler)
    s._groq_client = client
    s._groq_models = tuple(models)
    s._groq_model = models[0]
    s._groq_skip_until = 0.0
    s._cerebras_skip_until = 0.0
    s._max_out_tokens = 2048
    s._last_raw = TEXT
    s._deadline_at = time.monotonic() + 30
    return s


# ── the model tuple ──────────────────────────────────────────────────────────

def _real(monkeypatch, **env):
    for k in ("GROQ_STYLE_MODEL", "GROQ_STYLE_FALLBACK_MODEL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return OpenAIStyler(groq_api_key="gsk_" + "x" * 20)


def test_by_default_there_is_one_model_and_it_is_todays(monkeypatch):
    s = _real(monkeypatch)
    assert tuple(s._groq_models) == (PRIMARY,)
    assert s._groq_model == PRIMARY


def test_the_existing_override_stays_first(monkeypatch):
    s = _real(monkeypatch, GROQ_STYLE_MODEL="openai/gpt-oss-20b")
    assert tuple(s._groq_models) == ("openai/gpt-oss-20b",)
    assert s._groq_model == "openai/gpt-oss-20b"


def test_a_fallback_comes_only_from_its_own_variable(monkeypatch):
    s = _real(monkeypatch, GROQ_STYLE_FALLBACK_MODEL=BACKUP)
    assert tuple(s._groq_models) == (PRIMARY, BACKUP)
    assert s._groq_model == PRIMARY          # it starts on the first


def test_a_fallback_equal_to_the_primary_is_not_a_second_model(monkeypatch):
    s = _real(monkeypatch, GROQ_STYLE_FALLBACK_MODEL=PRIMARY)
    assert tuple(s._groq_models) == (PRIMARY,)


# ── moving on when a model is gone ───────────────────────────────────────────

def test_a_gone_model_moves_to_the_next_and_is_remembered():
    client = _Client({PRIMARY: _gone(PRIMARY), BACKUP: _answer("Hello there.")})
    s = _styler(client, (PRIMARY, BACKUP))
    styled, usage = s._style_groq("prompt", time.time())
    assert styled == "Hello there."
    assert usage["provider"] == "groq"
    assert [c["model"] for c in client.calls] == [PRIMARY, BACKUP]
    assert s._groq_model == BACKUP
    assert s._groq_skip_until == 0.0, "a model that works must not start a cooldown"
    # The next dictation goes straight to the model that works.
    s._style_groq("prompt", time.time())
    assert [c["model"] for c in client.calls] == [PRIMARY, BACKUP, BACKUP]


@pytest.mark.parametrize("message", [
    "Error code: 404 - Not Found",
    "Error code: 404 - {'error': {'message': 'The model `x` does not exist or you do not have access to it.', 'code': 'model_not_found'}}",
    "Error code: 400 - {'error': {'message': 'The model `x` has been decommissioned and is no longer supported. Please migrate to another model.', 'code': 'model_decommissioned'}}",
    "Error code: 400 - model_decommissioned",
    "The model `x` does not exist",
])
def test_every_way_of_saying_a_model_is_gone_is_recognised(message):
    client = _Client({PRIMARY: RuntimeError(message), BACKUP: _answer()})
    s = _styler(client, (PRIMARY, BACKUP))
    s._style_groq("prompt", time.time())
    assert s._groq_model == BACKUP, message


def test_a_rate_limit_that_mentions_404_does_not_move_the_model():
    limit = RuntimeError(
        "Error code: 429 - Rate limit reached for model `openai/gpt-oss-120b` on tokens "
        "per day (TPD): Limit 200000, Used 199404, Requested 5236. Please try again in 16m12s.")
    client = _Client({PRIMARY: limit, BACKUP: _answer()})
    s = _styler(client, (PRIMARY, BACKUP))
    with pytest.raises(RuntimeError) as ei:
        s._style_groq("prompt", time.time())
    assert str(ei.value).startswith("RATE_LIMIT|")
    assert s._groq_model == PRIMARY
    assert [c["model"] for c in client.calls] == [PRIMARY]


def test_auth_and_network_errors_do_not_move_the_model():
    for msg in ("Error code: 403 - Access denied", "Connection error."):
        client = _Client({PRIMARY: RuntimeError(msg), BACKUP: _answer()})
        s = _styler(client, (PRIMARY, BACKUP))
        with pytest.raises(RuntimeError):
            s._style_groq("prompt", time.time())
        assert s._groq_model == PRIMARY, msg


def test_when_every_model_is_gone_the_old_behaviour_stands():
    client = _Client({PRIMARY: _gone(PRIMARY), BACKUP: _gone(BACKUP)})
    s = _styler(client, (PRIMARY, BACKUP))
    with pytest.raises(RuntimeError) as ei:
        s._style_groq("prompt", time.time())
    assert "MODEL_UNAVAILABLE" in str(ei.value) and BACKUP in str(ei.value)
    assert s._groq_skip_until - time.monotonic() >= 3000       # the hour's cooldown
    # After the cooldown it starts again from the first model, in case access
    # to it came back: nothing is stuck on the last one for good.
    assert s._groq_model == PRIMARY


def test_one_model_gone_behaves_as_it_did(monkeypatch):
    """The pinned single-model case, built the way test_unavailable_model_cooldown
    builds it: no ``_groq_models`` at all."""
    s = object.__new__(OpenAIStyler)
    s._groq_client = _Client({"llama-3.3-70b-versatile": _gone("llama-3.3-70b-versatile")})
    s._groq_model = "llama-3.3-70b-versatile"
    s._groq_skip_until = 0.0
    s._max_out_tokens = 2048
    s._last_raw = ""
    s._deadline_at = time.monotonic() + 30
    with pytest.raises(RuntimeError) as ei:
        s._style_groq("prompt", time.time())
    assert "MODEL_UNAVAILABLE" in str(ei.value) and "llama-3.3-70b-versatile" in str(ei.value)
    assert s._groq_skip_until > time.monotonic()


def test_no_time_left_after_a_switch_ends_the_attempt_not_the_model():
    client = _Client({PRIMARY: _gone(PRIMARY), BACKUP: _answer()})
    s = _styler(client, (PRIMARY, BACKUP))
    s._deadline_at = time.monotonic() + 0.5          # under the minimum attempt
    with pytest.raises(RuntimeError) as ei:
        s._style_groq("prompt", time.time())
    assert str(ei.value).startswith("TIMEOUT|")
    assert s._groq_model == BACKUP                    # remembered for next time
    assert [c["model"] for c in client.calls] == [PRIMARY]


# ── what is sent with each model ─────────────────────────────────────────────

@pytest.mark.parametrize("model,extras", [
    ("openai/gpt-oss-120b", {"reasoning_effort": "low"}),
    ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
    ("qwen/qwen3.8-27b", {"reasoning_effort": "none"}),
    ("llama-3.3-70b-versatile", {}),
])
def test_reasoning_effort_follows_the_model(model, extras):
    assert OpenAIStyler._groq_request_extras(model) == extras
    client = _Client({model: _answer()})
    s = _styler(client, (model,))
    s._style_groq("prompt", time.time())
    sent = client.calls[0]
    assert sent.get("extra_body", {}) == extras


# ── BUG 1: a retired model is not a rate limit ───────────────────────────────

def _chain_styler(groq_client):
    s = _styler(groq_client)
    s._provider_order = ["groq"]
    s._use_groq = True
    s._use_cerebras = False
    s.client = None
    s.prompt_template = "{transcript} {dialect_instruction}"
    return s


def test_the_cooldown_after_a_404_is_not_reported_as_a_rate_limit():
    s = _chain_styler(_Client({PRIMARY: _gone(PRIMARY)}))
    _first, usage = s.style(TEXT)                 # the 404 itself
    assert "MODEL_UNAVAILABLE" in usage["fallback_reason"]
    _second, usage = s.style(TEXT)                # Groq is now cooling down
    reason = usage["fallback_reason"]
    assert "RATE_LIMIT" not in reason, reason
    assert "MODEL_UNAVAILABLE" in reason, reason
    assert usage["provider"] == "basic_clean"
    assert len(s._groq_client.calls) == 1, "the retired model must not be probed again"


def test_a_retired_model_is_said_plainly_and_is_not_a_pause():
    s = _chain_styler(_Client({PRIMARY: _gone(PRIMARY)}))
    for _ in range(3):                            # the 404, then two in the cooldown
        _t, usage = s.style(TEXT)
        reason = usage["fallback_reason"]
        heading, body = um.cleanup_skipped_message(reason)
        assert heading == "Pasted without the clean-up"
        assert "no longer available" in body
        assert "paused" not in heading.lower() and "limit" not in body.lower()
        assert chr(0x2014) not in body and chr(0x2013) not in body
        assert body.endswith(".")
        assert cleanup_pause.from_reason(reason, __import__("datetime").datetime.now()) is None


def test_the_message_for_a_retired_model_reads_well():
    heading, body = um.cleanup_skipped_message(
        "Groq: MODEL_UNAVAILABLE: Groq model 'x' is not available to this account - 404")
    assert heading == "Pasted without the clean-up" and len(heading) <= 38
    assert "clean-up model is no longer available" in body
    assert "updated" in body and "another key" in body
    assert "went in as you said them" in body
    for jargon in ("MODEL_UNAVAILABLE", "404", "provider", "raw", "styling", "Groq"):
        assert jargon not in body, jargon


def test_a_real_rate_limit_is_still_a_rate_limit():
    s = _chain_styler(_Client({PRIMARY: RuntimeError(
        "Error code: 429 - Rate limit reached on tokens per day (TPD): try again in 16m12s.")}))
    s.style(TEXT)
    _t, usage = s.style(TEXT)
    assert usage["fallback_reason"].startswith("RATE_LIMIT|")
    assert um.cleanup_skipped_message(usage["fallback_reason"])[0].startswith("Clean-up paused")


# ── BUG 2: OpenAI first must not stop the chain ──────────────────────────────

def _chain(order, *, openai_error=None, groq_outcomes=None):
    s = _styler(_Client(groq_outcomes or {PRIMARY: _answer(_clean("groq"))}))
    s._provider_order = list(order)
    s._use_groq = True
    s._use_cerebras = False
    s.prompt_template = "{transcript} {dialect_instruction}"
    s.model = "gpt-4.1-mini"
    s._openai_calls = 0

    class _OpenAIClient:
        chat = completions = None

        def __init__(self, owner):
            self.chat = self
            self.completions = self
            self.owner = owner

        def create(self, **kw):
            self.owner._openai_calls += 1
            if openai_error is not None:
                raise openai_error
            return _answer(_clean("openai"))

    s.client = _OpenAIClient(s)
    return s


def test_openai_failing_first_falls_through_to_groq():
    s = _chain(["openai", "groq", "cerebras"], openai_error=RuntimeError("Error code: 500"))
    styled, usage = s.style(TEXT)
    assert s._openai_calls == 1
    assert usage["provider"] == "groq", usage
    assert styled == _clean("groq")
    assert not usage.get("fallback_reason")


def test_openai_working_first_still_wins():
    s = _chain(["openai", "groq", "cerebras"])
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "openai" and styled == _clean("openai")
    assert s._groq_client.calls == []


def test_every_provider_failing_still_ends_in_basic_clean_with_the_best_reason():
    s = _chain(["openai", "groq", "cerebras"], openai_error=RuntimeError("Error code: 500"),
               groq_outcomes={PRIMARY: RuntimeError(
                   "Error code: 429 - Rate limit reached on tokens per day (TPD): "
                   "Please try again in 16m12s.")})
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "basic_clean" and usage["api_used"] is False
    assert usage["fallback_reason"].startswith("RATE_LIMIT|"), usage["fallback_reason"]
    assert "ship by the end of next month" in styled         # every word kept


def test_an_openai_only_failure_reaches_the_user_as_the_openai_reason():
    s = _chain(["openai"], openai_error=RuntimeError("Error code: 401 - invalid_api_key"))
    s._use_groq = False
    _styled, usage = s.style(TEXT)
    assert usage["provider"] == "basic_clean"
    assert usage["fallback_reason"].startswith("OpenAI: ")
    assert "OpenAI refused the connection" in um.cleanup_skipped_message(usage["fallback_reason"])[1]
