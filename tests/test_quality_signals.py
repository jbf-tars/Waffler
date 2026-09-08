#!/usr/bin/env python3
"""Per-recording quality signals.

The goal is to tell the user when a dictation is *probably* wrong, using
evidence the pipeline already has, computed locally and instantly.

Why not have an LLM review each transcript instead: judging a transcript from
its text cannot detect omission. If you speak twelve sentences and eight come
back, the eight read perfectly - nothing in the text says anything is missing.
A reviewer would approve it every time, which is worse than no check because it
manufactures confidence. The only ground truth is the audio, so these signals
are derived from MEASURED audio and from what the pipeline actually did.

Design rules pinned here:
  * flag, never block - the paste stays instant;
  * content-loss signals ("low") outrank advisory ones ("check");
  * a clean recording must stay "ok", or the flags become noise people ignore.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quality import assess  # noqa: E402


def _clean(**over):
    base = dict(
        speech_seconds=20.0, transcript_words=50, styled_words=48,
        asr_filtered=False, styling_provider="groq",
        styled_text="This is a normal dictation that ends properly.",
        retry_fired=False, deadline_fired=False,
    )
    base.update(over)
    return base


# ── A good recording must stay quiet ────────────────────────────────────────

def test_healthy_recording_is_ok_with_no_flags():
    r = assess(**_clean())
    assert r["level"] == "ok"
    assert r["flags"] == []


def test_short_utterance_is_not_flagged():
    """'Yes.' is a legitimate dictation, not a fault."""
    r = assess(**_clean(speech_seconds=1.2, transcript_words=1,
                        styled_words=1, styled_text="Yes."))
    assert r["level"] == "ok", r


# ── Content-loss signals -> "low" ───────────────────────────────────────────

def test_impossibly_low_word_rate_is_low():
    """57s of measured speech returning 18 words is the real 2026-07 failure."""
    r = assess(**_clean(speech_seconds=57.0, transcript_words=18, styled_words=18))
    assert r["level"] == "low"
    assert "low_word_rate" in r["flags"]


def test_styling_dropping_words_is_low():
    r = assess(**_clean(transcript_words=100, styled_words=60))
    assert r["level"] == "low"
    assert "styled_dropped_words" in r["flags"]


def test_cleanup_fallback_is_low():
    """basic_clean means the cleanup never ran; the user gets raw text."""
    r = assess(**_clean(styling_provider="basic_clean"))
    assert r["level"] == "low"
    assert "styling_fallback" in r["flags"]


# ── Advisory signals -> "check" ─────────────────────────────────────────────

def test_asr_filter_edit_is_advisory():
    r = assess(**_clean(asr_filtered=True))
    assert r["level"] == "check"
    assert "asr_filter_edited" in r["flags"]


def test_retry_is_advisory():
    r = assess(**_clean(retry_fired=True))
    assert r["level"] == "check"
    assert "retry_used" in r["flags"]


def test_deadline_is_advisory():
    r = assess(**_clean(deadline_fired=True))
    assert r["level"] == "check"
    assert "styling_deadline" in r["flags"]


def test_unterminated_ending_on_content_word_is_advisory():
    """No full stop, but ends on a content word - could be a legitimate title."""
    r = assess(**_clean(styled_text="Actual sites nailed down"))
    assert r["level"] == "check"
    assert "unterminated_ending" in r["flags"]


def test_dangling_function_word_is_content_loss():
    """Real examples from history: these are mid-clause cuts, not titles."""
    for tail in ["Then based on the", "I am also sure that",
                 "we just completely ignore that and just do our own and",
                 "how would we go about it with"]:
        r = assess(**_clean(styled_text=tail))
        assert r["level"] == "low", (tail, r)
        assert "truncated_midsentence" in r["flags"], (tail, r)


# ── Precedence and robustness ───────────────────────────────────────────────

def test_content_loss_outranks_advisory():
    r = assess(**_clean(speech_seconds=57.0, transcript_words=18,
                        styled_words=18, asr_filtered=True))
    assert r["level"] == "low"
    assert {"low_word_rate", "asr_filter_edited"} <= set(r["flags"])


def test_unknown_speech_duration_does_not_invent_a_rate_flag():
    """Never guess. With no measurement, the rate signal must stay silent."""
    r = assess(**_clean(speech_seconds=0.0, transcript_words=3, styled_words=3))
    assert "low_word_rate" not in r["flags"]


def test_empty_recording_is_not_a_fault():
    """Genuinely empty speech is a normal outcome, not a quality failure."""
    r = assess(**_clean(speech_seconds=0.3, transcript_words=0,
                        styled_words=0, styled_text=""))
    assert r["level"] == "ok"


def test_result_shape_is_stable():
    r = assess(**_clean())
    assert set(r) == {"level", "flags", "words_per_speech_second"}
    assert isinstance(r["flags"], list)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
