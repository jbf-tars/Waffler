"""The truncation guard vs. a spoken change of mind (v3.14.100).

Live bug, 2026-09-25: "Let's meet on Tuesday. No, wait, Wednesday. Actually,
Thursday at two." came back from Groq openai/gpt-oss-120b as "Thursday at two."
in 5 of 5 runs. The model deleted the lead-in "Let's meet on" along with the
wrong days. The truncation guard saw 3 of 11 words, refused the lossy answer and
pasted the lightly cleaned transcript, so the user got their uncorrected words.

That is still what 3.14.100 does, on purpose. Round 1 (f3c6322) rewrote the
SELF-CORRECTION section to keep the lead-in. It fixed that sentence, but the
model then deleted words the speaker meant in answers that keep more than half
the words, so the guard pasted them with no warning ("Let's meet on Thursday
at noon then." for "...Tuesday at noon then. Actually, Thursday works too.").
Round 2 re-measured with the prompt read as UTF-8 and tried three more
versions: each one lost words that the old prompt kept in all 5 of its runs,
and the loss came back when the case was run 5 more times. A failed
correction that pastes the speaker's words loses nothing; a silent deletion
is worse. So prompts/normal.txt ships the SELF-CORRECTION section it had in
3.14.99 (46b829c; the whole file is sha256 14e2868af8da... as the app reads
it, the version measured).

These tests need no network, keys, settings file or microphone. They pin:
  1. the guard on this bug class: the correct answer, which keeps the lead-in,
     is accepted unchanged; the bare final value is refused and the fallback
     keeps every word; the older truncation signals still fire; and inputs
     under 8 words are never guarded.
  2. the guard's blind spot: answers recorded in the round-1 review and the
     round-2 eval that lost or garbled words are pasted unchanged. Nothing
     after the model catches them, so the prompt must not produce them.
  3. the live harness (scripts/test_self_correction_corpus.py) scores every
     one of those recorded answers as LOSS or GARBLE and the right answers as
     clean, so a prompt that brings them back fails the live measurement. Round
     1 was committed partly because the harness then counted several as passes.
  4. the prompt: the SELF-CORRECTION section is the measured one, it keeps its
     worked examples and its not-a-correction list, and it carries none of the
     wording the rejected versions used to teach a cross-sentence correction.
"""
import hashlib
import importlib.util
import os
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from style_openai import OpenAIStyler  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "normal.txt"
HARNESS = ROOT / "scripts" / "test_self_correction_corpus.py"

# The exact transcripts from the live bug.
THURSDAY = "Let's meet on Tuesday. No, wait, Wednesday. Actually, Thursday at two."
ONE_SENTENCE = "Let's meet on Tuesday, no wait Wednesday, actually Thursday at 2."

MODEL_USAGE = {"input_tokens": 5500, "output_tokens": 40, "api_used": True,
               "provider": "groq", "finish_reason": "stop"}

DASH = "\u2014"   # Whisper's em-dash, as the harness transcripts carry it


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
    """What the user gets for the bug sentence in 3.14.100: their uncorrected
    words. The shipped prompt's model answered "Thursday at two." in 5 of 5
    runs when re-measured with the prompt read as UTF-8."""
    s = _styler_answering(monkeypatch, "Thursday at two.")
    pasted, usage = s.style(THURSDAY)
    assert pasted == THURSDAY
    assert usage["truncation_guard"] == "kept 3/11 words"
    assert "fallback_reason" not in usage


# ── 2. The guard's blind spot: answers that silently lost or garbled words ───
#
# Recorded pasted answers, verbatim. Each keeps at least half the words (or,
# once, is under the 8-word floor), so the guard lets it through and the user
# gets it with no warning. Where seen (runs with that answer):
#   rev  the round-1 review of f3c6322 (measured on the cp1252-garbled prompt)
#   R1   f3c6322 re-measured with the prompt read as UTF-8, 5 runs
#   ONE / GRD / R1R  round-2 candidates OLD+ONE, OLD+GUARDRAILS, R1-REPAIRED:
#        5 runs, and 5 more for a case where the damage was new
#   OLD  the prompt 3.14.100 ships (3.14.99's), 5 runs: its own known damage
# The last field is the kind of damage the live harness must report.

DAMAGED = [
    ("NEG-LEAD-5", "Let's meet on Tuesday at noon then. Actually, Thursday works too.",
     "Let's meet on Thursday at noon then.",
     "rev 2/2, R1 3/5: Tuesday deleted from an added option", "LOSS"),
    ("NEG-LEAD-4", "Let's meet on Tuesday, actually Thursday works too.",
     "Let's meet on Thursday works too.",
     "rev 2/2, R1 5/5, OLD 2/5: Tuesday deleted, broken grammar", "LOSS"),
    ("NEG-LEAD-1b", "Let's meet on Tuesday to go through the numbers. "
                    "Actually, Thursday works too if you're busy.",
     "Let's meet on Thursday to go through the numbers if you're busy.",
     "ONE 2/10, R1R 1/10: Tuesday deleted", "LOSS"),
    ("NEG-LEAD-6", "We could do the review on Tuesday at noon then. Actually, Thursday works too.",
     "We could do the review on Tuesday at noon. Thursday works too.",
     "GRD 5/10, R1R 2/10: 'then' dropped", "LOSS"),
    ("NEG-LEAD-6", "We could do the review on Tuesday at noon then. Actually, Thursday works too.",
     "We could do the review on Tuesday at noon. Actually, Thursday works too.",
     "R1R 2/10: 'then' dropped", "LOSS"),
    ("NEG-4", f"I thought the deploy would take an hour, but no, wait until you see this {DASH} "
              "it took six.",
     "I thought the deploy would take an hour, but until you see this, it took six.",
     "rev, R1 3/5, R1R 1/10: 'no, wait' dropped", "LOSS"),
    ("NEG-4", f"I thought the deploy would take an hour, but no, wait until you see this {DASH} "
              "it took six.",
     "I thought the deploy would take an hour, but wait until you see this, it took six.",
     "R1 1/5, GRD 3/10, R1R 2/10: 'no' dropped", "LOSS"),
    ("NEG-4", f"I thought the deploy would take an hour, but no, wait until you see this {DASH} "
              "it took six.",
     "I thought the deploy would take an hour, but it took six.",
     "GRD 2/10: the whole aside dropped", "LOSS"),
    ("NEG-4", f"I thought the deploy would take an hour, but no, wait until you see this {DASH} "
              "it took six.",
     "I thought the deploy would take an hour. It took six.",
     "GRD 1/10: 'but no, wait until you see this' dropped", "LOSS"),
    ("NEG-4b", "I thought the deploy would take an hour, but no, wait until you see this, "
               "it took six.",
     "I thought the deploy would take an hour, but wait until you see this, it took six.",
     "OLD 3/5, R1R 4/5: 'no' dropped", "LOSS"),
    ("NEG-2", "I actually really enjoyed working on this project, the whole team was great.",
     "I enjoyed working on this project, the whole team was great.",
     "ONE 3/10, GRD 5/10: 'really' dropped", "LOSS"),
    ("NEG-LEAD-2", "No, wait until you see the numbers.",
     "Wait until you see the numbers.",
     "GRD 8/10: the opening 'No' dropped (7 words: never guarded)", "LOSS"),
    ("MULTI-1", "Send it to John, sorry James, by Tuesday, no wait, Wednesday at three.",
     "Send it to James, Wednesday at three.",
     "rev, GRD 1/10: 'by' dropped", "LOSS"),
    ("MULTI-1", "Send it to John, sorry James, by Tuesday, no wait, Wednesday at three.",
     "Send it to James. Wednesday at three.",
     "R1 1/5: 'by' dropped", "LOSS"),
    ("LEAD-8", "Send it to John, sorry, James, by Wednesday at three.",
     "Send it to James, by Wednesday at three.",
     "round 1 13/13 (garbled prompt), R1 5/5: stray comma", "GARBLE"),
    ("EMAIL-1", "Hi Sam. Um, so, yeah, for the review, can we do Thursday at two? "
                "I'll bring the, uh, updated numbers. Cheers, James.",
     "Hi Sam,\n\nFor the review, can we do Thursday at two? "
     "I'll bring the, updated numbers.\n\nCheers,\nJames",
     "round 1 5/10 on the garbled prompt (R1 0/5): stray comma where 'uh' was",
     "GARBLE"),
    ("DAY-3", "Can we shift it to Tuesday, actually Monday works better.",
     "Can we shift it to Monday works better.",
     "rev 2/3, R1 4/5, ONE 5/5, OLD 1/5: lead-in glued onto a verb", "GARBLE"),
    ("DAY-3", "Can we shift it to Tuesday, actually Monday works better.",
     "Can we shift it to Tuesday, Monday works better.",
     "R1R 1/10: marker gone, retracted Tuesday kept", "LOSS"),
    ("VERB-2", "Let's meet on Tuesday. Actually, scratch that, let's just do a quick call.",
     "Let's meet on Tuesday. Let's just do a quick call.",
     "OLD 4/5, R1R 1/5: 'scratch that' gone, Tuesday kept", "LOSS"),
    ("STRUCT-1", f"Number one, ping John {DASH} sorry, I mean Jane {DASH} about the contract. "
                 "Number two, book the room for Tuesday, no Monday.",
     "1. Ping Jane about the contract.\n2. Book the room for Tuesday.",
     "OLD 5/5 (1 with a blank line), ONE 5/5: the correction to Monday lost", "LOSS"),
]

_DAMAGED_IDS = [f"{cid}:{i}" for i, (cid, *_rest) in enumerate(DAMAGED)]


@pytest.mark.parametrize("case_id, raw, pasted, seen, kind", DAMAGED, ids=_DAMAGED_IDS)
def test_the_guard_lets_a_silently_damaged_answer_through(case_id, raw, pasted, seen, kind):
    """The blind spot, pinned: each of these is pasted exactly as the model
    wrote it (the guard only counts words, and the finish reason). So nothing
    after the model protects the user from them, and a prompt that produces
    them is worse than one that fails safe (section 1). If the guard ever
    learns to refuse one of these, move it to a test of that instead."""
    out, usage = _guard(pasted, raw)
    assert out == pasted, seen
    assert "truncation_guard" not in usage, seen
    assert usage["provider"] == "groq"


def test_style_pastes_an_answer_that_deleted_tuesday(monkeypatch):
    """End to end, as the user gets it: the sign-off and layout passes after
    the guard do not restore a deleted word either."""
    raw, pasted = DAMAGED[0][1], DAMAGED[0][2]
    s = _styler_answering(monkeypatch, pasted)
    out, usage = s.style(raw)
    assert out == pasted
    assert "Tuesday" not in out
    assert "truncation_guard" not in usage


# ── 3. The live harness counts every one of them as damage ───────────────────

@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """scripts/test_self_correction_corpus.py as a module. It loads
    ~/.waffler-hosted/.env when imported, so home points at an empty folder:
    no key is read."""
    home = tmp_path_factory.mktemp("no-keys-home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(home))          # Path.home() on macOS / Linux
        mp.setenv("USERPROFILE", str(home))   # Path.home() on Windows
        spec = importlib.util.spec_from_file_location("_selfcorr_harness", HARNESS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def _case(harness, case_id):
    matches = [c for c in harness.CORPUS if harness.case_id(c) == case_id]
    assert len(matches) == 1, f"the harness has no single case {case_id}"
    return matches[0]


@pytest.mark.parametrize("case_id, raw, pasted, seen, kind", DAMAGED, ids=_DAMAGED_IDS)
def test_the_harness_scores_each_damaged_answer_as_damage(harness, case_id, raw, pasted,
                                                          seen, kind):
    case = _case(harness, case_id)
    assert case.raw == raw, "the recorded transcript no longer matches the harness case"
    failures = harness.evaluate(case, pasted)
    assert any(f.startswith(kind + ":") for f in failures), (seen, failures)


RIGHT = [
    ("LEAD-1", "Let's meet on Thursday at two."),
    ("LEAD-8", "Send it to James by Wednesday at three."),
    ("MULTI-1", "Send it to James by Wednesday at three."),
    ("NEG-LEAD-5", "Let's meet on Tuesday at noon then. Actually, Thursday works too."),
    ("NEG-LEAD-5", "Let's meet on Tuesday at noon then. Thursday works too."),
    ("NEG-LEAD-4", "Let's meet on Tuesday, actually Thursday works too."),
    ("NEG-LEAD-1b", "Let's meet on Tuesday to go through the numbers. "
                    "Actually, Thursday works too if you're busy."),
    ("NEG-LEAD-6", "We could do the review on Tuesday at noon then. Actually, Thursday works too."),
    ("NEG-4", "I thought the deploy would take an hour, but no, wait until you see this, "
              "it took six."),
    ("NEG-4b", "I thought the deploy would take an hour, but no, wait until you see this, "
               "it took six."),
    ("NEG-2", "I actually really enjoyed working on this project, the whole team was great."),
    ("NEG-2", "I really enjoyed working on this project, the whole team was great."),
    ("NEG-LEAD-2", "No, wait until you see the numbers."),
    ("VERB-2", "Let's just do a quick call."),
    ("STRUCT-1", "1. Ping Jane about the contract.\n2. Book the room for Monday."),
    ("EMAIL-1", "Hi Sam,\n\nFor the review, can we do Thursday at two? "
                "I'll bring the updated numbers.\n\nCheers,\nJames"),
]


@pytest.mark.parametrize("case_id, answer", RIGHT,
                         ids=[f"{c}:{i}" for i, (c, _a) in enumerate(RIGHT)])
def test_the_harness_passes_the_right_answers(harness, case_id, answer):
    """The damage checks discriminate: the answers a correct prompt gives for
    the same transcripts pass every check."""
    assert harness.evaluate(_case(harness, case_id), answer) == []


def test_the_harness_never_counts_the_speakers_own_words_as_damage(harness):
    """Pasting the transcript, or the guard's lightly cleaned copy of it, is
    the safe failure: no case may score it as LOSS or GARBLE."""
    assert harness.self_check(harness.CORPUS) == []


def test_the_harness_reads_a_candidate_prompt_as_the_app_does(harness, monkeypatch, tmp_path):
    """Round 1 was measured on a cp1252-garbled prompt. load_prompt_file must
    decode a --prompt-file as UTF-8 even where the default is cp1252."""
    import builtins
    real_open = builtins.open

    def windows_default_open(file, mode="r", buffering=-1, encoding=None, *args, **kwargs):
        if "b" not in mode and encoding is None:
            encoding = "cp1252"
        return real_open(file, mode, buffering, encoding, *args, **kwargs)

    monkeypatch.setattr(harness, "open", windows_default_open, raising=False)
    candidate = tmp_path / "candidate.txt"
    candidate.write_bytes(PROMPT.read_bytes())
    expected = PROMPT.read_text(encoding="utf-8")
    assert DASH in expected
    assert harness.load_prompt_file(str(candidate)) == expected


# ── 4. The prompt: the SELF-CORRECTION section that was measured ─────────────

# sha256 of the section as _section_text() normalises it: the 3.14.99 text.
SECTION_SHA256 = "c3648041c6f33963775a55a6a7e136a3aa217a575b7958c637d92ae06e7d6187"


def _self_correction_section():
    text = PROMPT.read_text(encoding="utf-8")
    m = re.search(r"^SELF-CORRECTION[ \t]*\r?\n(.*?)^PRESERVE VERBATIM", text,
                  re.MULTILINE | re.DOTALL)
    assert m, "prompts/normal.txt has no SELF-CORRECTION section before PRESERVE VERBATIM"
    return m.group(1)


def _section_text():
    return "\n".join(line.rstrip() for line in _self_correction_section().strip().splitlines())


def test_prompt_still_formats():
    PROMPT.read_text(encoding="utf-8").format(transcript="x", dialect_instruction="y")


def test_self_correction_section_is_the_measured_one():
    """Any edit to this section changes what the model deletes, and the damage
    it can do gets past the guard (section 2). Before changing the expected
    hash, measure the new text against this one with
    scripts/test_self_correction_corpus.py --prompt-file NEW --runs 5
    --provider groq (and again with --force-llm), and re-run 5 more times any
    case where NEW pastes a LOSS or GARBLE this version did not. A loss seen
    in 2 or more of those 10 runs is a regression, however many more
    corrections NEW gets right."""
    digest = hashlib.sha256(_section_text().encode("utf-8")).hexdigest()
    assert digest == SECTION_SHA256, (
        "The SELF-CORRECTION section of prompts/normal.txt changed (sha256 "
        f"{digest}). See this test's docstring for the measurement to run first.")


def test_self_correction_keeps_its_worked_examples():
    """An in-sentence chain whose answer keeps the lead-in, and two corrections
    in one sentence whose answer keeps "by". Round 1 deleted the second
    example, and that case then kept "John, sorry James" or lost "by"."""
    section = _self_correction_section()
    assert ('"Let\'s meet on Tuesday, no Wednesday, actually Thursday at two." -> '
            '"Let\'s meet on Thursday at two."') in section, section
    assert ('"Send it to John, sorry James, by Tuesday, no wait, Wednesday at three." -> '
            '"Send it to James by Wednesday at three."') in section, section


def test_self_correction_keeps_its_not_a_correction_list():
    """What must never be read as a correction, so no word of it is dropped."""
    section = _self_correction_section()
    for needle in ("NOT corrections, keep everything",
                   "clarifying I mean",
                   "Sorry opening an apology",
                   'rhetorical "but no, wait until you see this"',
                   '"Thanks again, James" (a SIGN-OFF)'):
        assert needle in section, needle


@pytest.mark.parametrize("pattern, why", [
    (r"(?i)full\s+stop",
     "a correction that spans a full stop: all four rejected versions (f3c6322, "
     "OLD+ONE, OLD+GUARDRAILS, R1-REPAIRED) said so, and each lost words the "
     "shipped prompt kept"),
    (r"(?i)lead-in",
     "a keep-the-lead-in rule: all four rejected versions had one"),
    (r"(?i)retracted value is not content|LENGTH do not protect",
     "round 1's licence to delete: the review named it as one of three behind "
     "the lost Tuesday"),
    (r"(?i)short (?:noun )?phrase",
     "round 1's slot definition, which let a whole phrase count as the retracted "
     "value: the review named it too"),
], ids=["full-stop", "lead-in", "round-1-licence", "short-phrase"])
def test_self_correction_carries_none_of_the_rejected_wording(pattern, why):
    """A gate, not a style rule: these words are how every rejected version
    taught the cross-sentence correction, and each version lost words. A
    later fix may need them; it gets them by passing the measurement in
    test_self_correction_section_is_the_measured_one, then updating this."""
    section = _self_correction_section()
    m = re.search(pattern, section)
    assert m is None, f"SELF-CORRECTION contains {m.group(0)!r}: {why}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
