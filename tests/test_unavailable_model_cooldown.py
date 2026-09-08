#!/usr/bin/env python3
"""A model that cannot be used must not be re-probed on every dictation.

app.log shows 458 occurrences of:

    404 - The model `llama-3.3-70b-versatile` does not exist or you do not
          have access to it.  (code: model_not_found)

most recently 2026-09-08 08:14:15, with the user's provider order set to
groq-first. The styler's except-chain sets a cooldown for rate limits (429),
connection/timeout errors and auth failures (401/403) — but a 404 falls
through to a bare `raise`, so `_groq_skip_until` is never set. Every single
dictation therefore pays a full round-trip to a model that cannot answer
before falling through to the next provider.

This is a latency tax on every recording, which is why it matters for
"trustworthy dictation" and not just tidiness.

Note on scope: an unavailable model is NOT silently swapped for another one
here. The message is ambiguous between "retired" and "your key lacks access",
and choosing a replacement default needs a benchmark on identical inputs plus
a check of the provider's current docs. This change only stops paying the
latency and makes the reason legible.

Offline: no keys, no network — the client is a stub that raises.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from style_openai import OpenAIStyler  # noqa: E402

GROQ_404 = ("Error code: 404 - {'error': {'message': 'The model "
            "`llama-3.3-70b-versatile` does not exist or you do not have "
            "access to it.', 'type': 'invalid_request_error', "
            "'code': 'model_not_found'}}")


class _Raiser:
    """Minimal stand-in for a provider client whose call always fails."""
    def __init__(self, msg):
        self._msg = msg
        self.chat = self
        self.completions = self

    def create(self, **kw):
        raise RuntimeError(self._msg)


def _styler(msg):
    s = object.__new__(OpenAIStyler)
    s._groq_client = _Raiser(msg)
    s._cerebras_client = _Raiser(msg)
    s._groq_skip_until = 0.0
    s._cerebras_skip_until = 0.0
    s._groq_model = "llama-3.3-70b-versatile"
    s._cerebras_model = "gpt-oss-120b"
    s._max_out_tokens = 2048
    s._last_raw = ""
    s._deadline_at = time.monotonic() + 30
    return s


def test_groq_404_sets_a_cooldown():
    """Without this the model is re-probed on every single dictation."""
    s = _styler(GROQ_404)
    with pytest.raises(Exception):
        s._style_groq("prompt", time.time())
    assert s._groq_skip_until > time.monotonic(), (
        "a 404 model_not_found must start a cooldown")


def test_groq_404_cooldown_is_substantial():
    """A few seconds would just re-probe constantly; this is not transient."""
    s = _styler(GROQ_404)
    with pytest.raises(Exception):
        s._style_groq("prompt", time.time())
    assert s._groq_skip_until - time.monotonic() >= 300


def test_groq_404_error_names_the_cause():
    """The failure reason must say the model is unavailable, so the log and
    any toast are actionable rather than a generic failure."""
    s = _styler(GROQ_404)
    with pytest.raises(Exception) as ei:
        s._style_groq("prompt", time.time())
    assert "MODEL_UNAVAILABLE" in str(ei.value)
    assert "llama-3.3-70b-versatile" in str(ei.value)


def test_cerebras_404_sets_a_cooldown():
    """Same gap existed on the Cerebras branch (a retired model there caused
    the same repeated-probe pattern historically)."""
    s = _styler("Error code: 404 - {'message': 'Model gpt-oss-120b does not "
                "exist or you do not have access to it.', 'code': 'model_not_found'}")
    with pytest.raises(Exception):
        s._style_cerebras("prompt", time.time())
    assert s._cerebras_skip_until > time.monotonic()


def test_rate_limit_still_takes_its_own_branch():
    """Regression guard: the 404 branch must not swallow 429 handling."""
    s = _styler("Error code: 429 - rate limit reached, tokens per minute (TPM)")
    with pytest.raises(Exception) as ei:
        s._style_groq("prompt", time.time())
    assert "RATE_LIMIT" in str(ei.value)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
