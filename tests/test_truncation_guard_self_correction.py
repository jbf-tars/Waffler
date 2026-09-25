"""The truncation guard vs. a spoken change of mind (v3.14.100).

Live bug, 2026-09-25: "Let's meet on Tuesday. No, wait, Wednesday. Actually,
Thursday at two." came back from Groq openai/gpt-oss-120b as "Thursday at two."
in 5 of 5 runs. The model deleted the lead-in "Let's meet on" along with the
wrong days. The truncation guard saw 3 of 11 words, refused the lossy answer and
pasted the lightly cleaned transcript, so the user got their uncorrected words.

The guard was RIGHT, and it stays as it is: relaxing it would have pasted
"Thursday at two." and lost words the speaker said. The fix is in the prompt
(the SELF-CORRECTION section now says the lead-in is kept), measured live by
scripts/test_self_correction_corpus.py.

These tests need no network, keys, settings file or microphone. They pin:
  1. the guard's side of this bug class: the correct answer, which keeps the
     lead-in, is accepted unchanged; the bare final value is refused and the
     fallback keeps every word; the older truncation signals still fire; and
     inputs under 8 words are never guarded.
  2. the prompt's side: the SELF-CORRECTION section still tells the model to
     keep the lead-in, still treats a full stop between attempts like a comma,
     and still carries a cross-sentence example with its WRONG form, so the
     rule cannot be dropped silently.
"""
import os
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from style_openai import OpenAIStyler  # noqa: E402

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "normal.txt"

# The exact transcripts from the live bug.
THURSDAY = "Let's meet on Tuesday. No, wait, Wednesday. Actually, Thursday at two."
ONE_SENTENCE = "Let's meet on Tuesday, no wait Wednesday, actually Thursday at 2."

MODEL_USAGE = {"input_tokens": 5500, "output_tokens": 40, "api_used": True,
               "provider": "groq", "finish_reason": "stop"}


def _bare_styler():
    """A bare instance: the guard touches no network or settings state."""
    return object.__new__(OpenAIStyler)


def _guard(styled, transcript, **usage_overrides):
    usage = dict(MODEL_USAGE, **usage_overrides)
    return _bare_styler()._guard_truncation(styled, usage, transcript)


# ── 1. The guard on this bug class ───────────────────────────────────────────

def test_the_bug_transcripts_are_long_enough_to_be_guarded():
    """Both are 11 words, above the 8-word floor, so the ratio check applies."""
    assert len(THURSDAY.split()) == 11
    assert len(ONE_SENTENCE.split()) == 11


@pytest.mark.parametrize("raw, correct", [
    (THURSDAY, "Let's meet on Thursday at two."),
    (ONE_SENTENCE, "Let's meet on Thursday at 2."),
    ("Send it to John, sorry, James, by Wednesday at three.",
     "Send it to James by Wednesday at three."),
])
def test_correction_that_keeps_the_lead_in_is_accepted_unchanged(raw, correct):
    """6 of 11 words is a correct correction, not truncation: paste it as is."""
    out, usage = _guard(correct, raw)
    assert out == correct
    assert "truncation_guard" not in usage
    assert usage["provider"] == "groq"
    assert usage["api_used"] is True


@pytest.mark.parametrize("raw, lossy", [
    (THURSDAY, "Thursday at two."),
    (ONE_SENTENCE, "Thursday at 2."),
    (ONE_SENTENCE, "Actually Thursday at 2."),
])
def test_bare_final_value_is_refused_and_every_word_is_kept(raw, lossy):
    """The lead-in-less answer is refused; the fallback keeps every spoken word,
    in order, and does not raise the "styling failed" toast."""
    out, usage = _guard(lossy, raw)
    assert out.split() == raw.split()
    assert usage["truncation_guard"] == f"kept {len(lossy.split())}/11 words"
    assert usage["provider"] == "basic_clean"
    assert usage["api_used"] is False
    assert "fallback_reason" not in usage


def test_finish_reason_length_is_still_caught_even_when_most_words_survive():
    """The COBie/postcode bug: 18 of 20 words kept, but the model hit max_tokens."""
    raw = " ".join(f"word{i}" for i in range(20))
    cut = " ".join(f"word{i}" for i in range(18))
    out, usage = _guard(cut, raw, finish_reason="length")
    assert usage["truncation_guard"] == "finish_reason=length"
    assert out.split() == raw.split()


def test_long_dictation_cut_below_half_is_still_caught():
    """A 130-word dictation silently cut to 60 words is truncation."""
    sentences = [f"Point {i} is that the rollout needs another review pass."
                 for i in range(13)]
    raw = " ".join(sentences)
    assert len(raw.split()) == 130
    cut = " ".join(raw.split()[:60])
    out, usage = _guard(cut, raw)
    assert usage["truncation_guard"] == "kept 60/130 words"
    assert out.split() == raw.split()


def test_keeping_exactly_half_is_accepted_and_less_is_refused():
    """The line is "less than half", measured on an 8-word input (the floor)."""
    raw = "Book the room for Tuesday. Sorry, Monday, thanks."
    assert len(raw.split()) == 8
    half = "Book it for Monday."                # 4 of 8: exactly half, kept
    out, usage = _guard(half, raw)
    assert out == half and "truncation_guard" not in usage
    under = "For Monday, thanks."               # 3 of 8: under half, refused
    out, usage = _guard(under, raw)
    assert usage["truncation_guard"] == "kept 3/8 words"
    assert out.split() == raw.split()


@pytest.mark.parametrize("raw, styled", [
    ("Tuesday, no wait, Monday.", "Monday."),
    ("Book it for Tuesday. Sorry, Monday.", "Monday."),
    ("Let's do Tuesday. No, wait, Monday then.", "Monday then."),
])
def test_inputs_under_eight_words_are_never_guarded(raw, styled):
    assert len(raw.split()) < 8
    out, usage = _guard(styled, raw)
    assert out == styled
    assert "truncation_guard" not in usage


# ── The same two answers through style(), as the user would get them ─────────

def _styler_answering(monkeypatch, model_output):
    s = _bare_styler()
    s._provider_order = ["groq"]
    s._use_groq = True
    s._use_cerebras = False
    s.client = None
    s._groq_skip_until = 0.0
    s._cerebras_skip_until = 0.0
    s._last_raw = ""
    s.prompt_template = "{transcript} {dialect_instruction}"
    # style() reads the dialect from the user's settings file; keep that out.
    monkeypatch.setitem(sys.modules, "transcribe_whisper",
                        types.SimpleNamespace(load_settings=lambda: {}))
    monkeypatch.setattr(OpenAIStyler, "_style_groq",
                        lambda self, prompt, start: (model_output, dict(MODEL_USAGE)))
    return s


def test_style_pastes_the_correction_when_the_model_keeps_the_lead_in(monkeypatch):
    s = _styler_answering(monkeypatch, "Let's meet on Thursday at two.")
    pasted, usage = s.style(THURSDAY)
    assert pasted == "Let's meet on Thursday at two."
    assert usage["provider"] == "groq"


def test_style_pastes_the_raw_words_when_the_model_drops_the_lead_in(monkeypatch):
    """What the user saw before the prompt fix: their uncorrected words."""
    s = _styler_answering(monkeypatch, "Thursday at two.")
    pasted, usage = s.style(THURSDAY)
    assert pasted == THURSDAY
    assert usage["truncation_guard"] == "kept 3/11 words"
    assert "fallback_reason" not in usage


# ── 2. The prompt keeps the rule ─────────────────────────────────────────────

def _self_correction_section():
    text = PROMPT.read_text(encoding="utf-8")
    m = re.search(r"^SELF-CORRECTION[ \t]*\r?\n(.*?)^PRESERVE VERBATIM", text,
                  re.MULTILINE | re.DOTALL)
    assert m, "prompts/normal.txt has no SELF-CORRECTION section before PRESERVE VERBATIM"
    return m.group(1)


def test_prompt_still_formats():
    PROMPT.read_text(encoding="utf-8").format(transcript="x", dialect_instruction="y")


def test_self_correction_says_the_lead_in_is_kept():
    section = _self_correction_section()
    assert "never its lead-in" in section, section
    # The lead-in is named with the bug's own shape of words.
    assert "Let's meet on" in section, section


def test_self_correction_treats_a_full_stop_between_attempts_like_a_comma():
    """Whisper punctuates a spoken correction as its own sentence."""
    section = _self_correction_section()
    assert re.search(r"full stop or a dash between the attempts: treat it exactly as a comma",
                     section), section


def test_self_correction_has_a_cross_sentence_example_with_its_wrong_form():
    """A cross-sentence chain whose RIGHT form keeps the lead-in and whose WRONG
    form is the bare final value (the exact failure: "Thursday at two.").

    The example deliberately is NOT the bug transcript itself: a candidate that
    quoted it fixed that one sentence and got worse on corrections it had not
    seen, and quoting it would also stop the live harness's LEAD-1 case from
    measuring anything."""
    section = _self_correction_section()
    examples = re.findall(r'"([^"]+)" -> "([^"]+)" \(WRONG: "([^"]+)"\)', section)
    assert examples, "no example with a (WRONG: ...) form in SELF-CORRECTION"
    for raw, right, wrong in examples:
        assert len(re.findall(r"[.!?](?:\s|$)", raw)) >= 3, raw   # three sentences: X. No, Y. Z.
        assert right.endswith(wrong) and right != wrong, (right, wrong)
        lead_in = right[: -len(wrong)].split()
        assert lead_in and raw.split()[: len(lead_in)] == lead_in, (raw, right)


def test_self_correction_keeps_its_not_a_correction_guardrails():
    """The fix must not turn an added option or an opening "No, wait" into a
    correction (both were measured regressions of the first draft)."""
    section = _self_correction_section()
    assert "too / also / as well" in section, section
    assert "opens with No, wait" in section, section


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
