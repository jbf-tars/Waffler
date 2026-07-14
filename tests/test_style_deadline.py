#!/usr/bin/env python3
"""Tests for the overall styling budget (and the raw-paste promise it makes real).

The bug: style() only ever had a PER-PROVIDER timeout, never an aggregate one.
So when a provider hung or rate-limited, the chain just ground on to the next
one (30s each), stacking to 60-78s. The documented behaviour -- "if the cleanup
doesn't come through, your raw text is pasted" -- therefore almost never fired:
it only triggered when EVERY provider failed outright (9 times in 2347 real
recordings), while 42 recordings sat >10s and 24 sat >30s because a provider
eventually answered. Users waited instead of getting their words.

Now there is a wall-clock budget for the WHOLE styling step. When it's spent we
stop trying providers and paste the (lightly-cleaned) raw transcript.

The trap these tests also pin: the budget must NOT be a flat 10-12s, because a
genuinely long dictation can legitimately need ~30s to clean (max_out_tokens
scales to 8192). So the budget scales with input length -- short dictations bail
fast, long ones get room.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import style_openai as so  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402


# ── The budget scales with input length (don't clip long dictations) ─────────

def test_short_dictation_gets_the_floor():
    """A typical ~30-word dictation bails fast -- 12s, not 30."""
    assert so._style_deadline_for(30) == so._STYLE_DEADLINE_FLOOR_S == 12.0


def test_budget_grows_with_length():
    """A 300-word dictation gets more room than a 30-word one."""
    assert so._style_deadline_for(300) > so._style_deadline_for(30)
    assert so._style_deadline_for(300) == pytest.approx(17.0, abs=0.01)


def test_long_dictation_is_not_clipped():
    """THE TRAP: a very long transcript legitimately needs ~30s of generation.
    The budget must reach the cap, not cut it off at 12s."""
    assert so._style_deadline_for(2000) == so._STYLE_DEADLINE_CAP_S == 30.0
    assert so._style_deadline_for(5000) == so._STYLE_DEADLINE_CAP_S  # capped, never unbounded


def test_budget_is_monotonic_and_bounded():
    prev = 0.0
    for wc in (0, 1, 50, 200, 500, 1000, 5000):
        d = so._style_deadline_for(wc)
        assert so._STYLE_DEADLINE_FLOOR_S <= d <= so._STYLE_DEADLINE_CAP_S
        assert d >= prev
        prev = d


# ── Per-attempt timeout is clamped by what's left ────────────────────────────

def _bare_styler():
    return object.__new__(OpenAIStyler)


def test_attempt_timeout_unarmed_defaults_to_provider_cap():
    """No deadline armed (e.g. provider called directly) must NOT starve the call."""
    s = _bare_styler()
    assert s._attempt_timeout() == so._STYLE_TIMEOUT_S


def test_attempt_timeout_clamped_by_remaining_budget():
    s = _bare_styler()
    s._deadline_at = time.monotonic() + 3.0     # only 3s left
    t = s._attempt_timeout()
    assert 2.0 < t <= 3.0, t                     # clamped below the 15s cap


def test_attempt_timeout_never_exceeds_provider_cap():
    s = _bare_styler()
    s._deadline_at = time.monotonic() + 999.0    # huge budget
    assert s._attempt_timeout() == so._STYLE_TIMEOUT_S


def test_budget_left_zero_when_spent():
    s = _bare_styler()
    s._deadline_at = time.monotonic() - 5.0      # already blown
    assert s._budget_left() == 0.0


# ── The promise: budget exhausted -> raw text pasted, every word kept ────────

def test_exhausted_budget_pastes_raw_and_keeps_every_word(monkeypatch):
    """All providers stall. The user must get their words, not a 60s wait."""
    s = _bare_styler()
    s._provider_order = ["groq", "cerebras", "openai"]
    s._use_groq = True
    s._use_cerebras = True
    s.client = object()          # non-None so openai isn't skipped
    s._groq_skip_until = 0.0
    s._cerebras_skip_until = 0.0
    s._last_raw = ""
    s.prompt_template = "{transcript} {dialect_instruction}"

    transcript = " ".join(f"word{i}" for i in range(40))  # 40 words -> 12s budget

    # Every provider "hangs" past the budget.
    def _stall(*a, **k):
        raise AssertionError("provider should not be reached once budget is spent")

    calls = {"n": 0}

    def _slow_provider(*a, **k):
        calls["n"] += 1
        # Simulate a call that eats the entire budget, then fails.
        s._deadline_at = time.monotonic() - 1.0
        raise RuntimeError("provider hung")

    monkeypatch.setattr(OpenAIStyler, "_style_groq", _slow_provider)
    monkeypatch.setattr(OpenAIStyler, "_style_cerebras", _stall)
    monkeypatch.setattr(OpenAIStyler, "_style_openai", _stall)
    monkeypatch.setattr(OpenAIStyler, "_log_provider_failure", lambda *a, **k: None)

    styled, usage = s.style(transcript)

    # The first provider was tried; the rest were SKIPPED because the clock ran out.
    assert calls["n"] == 1
    assert usage["provider"] == "basic_clean"
    assert usage["api_used"] is False
    # Every dictated word survives -- that is the whole point of pasting raw.
    for w in ("word0", "word20", "word39"):
        assert w in styled


def test_deadline_reason_is_not_mistaken_for_a_rate_limit(monkeypatch):
    """The toast must say 'cleanup skipped', not blame a rate limit."""
    s = _bare_styler()
    s._provider_order = ["groq"]
    s._use_groq = True
    s._use_cerebras = False
    s.client = None
    s._groq_skip_until = 0.0
    s._cerebras_skip_until = 0.0
    s._last_raw = ""
    s.prompt_template = "{transcript} {dialect_instruction}"

    # Budget already blown before any provider runs.
    def _blow(self_, wc):
        return 0.0
    monkeypatch.setattr(so, "_style_deadline_for", lambda wc: 0.0)

    styled, usage = s.style(" ".join(["hello"] * 20))
    reason = usage.get("fallback_reason") or ""
    assert "TIMEOUT" in reason, reason
    assert not reason.startswith("RATE_LIMIT"), reason
    assert "hello" in styled


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
