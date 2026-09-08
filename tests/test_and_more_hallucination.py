"""Verify the v3.14.39 fix for the "and more" Whisper hallucination.

Complementary to v3.14.38, which fixed the styling-prompt layer (telling
the LLM not to generate filler-tail phrases). This fix is at the
transcription layer — it catches the case where Whisper ITSELF emits
"and more." on near-silent <1 s clips, where local pass-through styling
never gets a chance to filter it.

User log (14 May 2026) showed 28 instances of "Done: and more." with
`styling (local): 0ms` — every one was a Whisper-layer hallucination.

These tests assert:
1. The exact-string hallucination check rejects "and more" variants.
2. The trailing-pattern strip removes "and more" tails from real content.
3. Real recordings that legitimately mention "more" mid-sentence survive.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.transcribe_whisper import (
    _is_whisper_hallucination,
    _strip_hallucinations,
)


def test_exact_and_more_variants_rejected():
    """Every observed form of the bare hallucination should match."""
    cases = [
        "and more",
        "and more.",
        "And more.",
        "AND MORE!",
        "and more...",
        "  and more.  ",
    ]
    for s in cases:
        assert _is_whisper_hallucination(s), f"should reject {s!r}"
    print(f"  ✓ all {len(cases)} bare 'and more' variants rejected")


def test_trailing_and_more_stripped_on_near_silent_clip():
    """Strip an "and more" tail when the AUDIO says the clip was near-silent.

    v3.14.86 note — expectation refined, cases preserved. This test previously
    called the filter with no audio evidence and asserted the tail was always
    stripped. That is what this module's own header describes as the target
    case ("Whisper ITSELF emits 'and more.' on near-silent <1 s clips"), but
    end-of-string matching alone cannot tell that clip apart from a real
    enumeration: "The pack covers onboarding, payroll, benefits and more."
    is the speaker's own words, and deleting "and more" there drops the
    substantive signal that the list was open-ended.

    The filter now takes measured speech seconds, so the original intent is
    expressed directly instead of by proxy. All five inputs are unchanged.
    """
    cases = [
        ("This is a real sentence and more.", "This is a real sentence"),
        ("Buy our gadgets, gizmos, accessories, and more!", "Buy our gadgets, gizmos, accessories"),
        ("Our available products and services with more.", "Our available products and services"),
        ("A list of recipes from around the world plus many more.", "A list of recipes from around the world"),
        ("The tools we offer all developers and much more!", "The tools we offer all developers"),
    ]
    for inp, expected in cases:
        out = _strip_hallucinations(inp, speech_seconds=0.4)
        assert out == expected, f"strip({inp!r}) = {out!r}, expected {expected!r}"


def test_trailing_and_more_kept_when_clip_had_real_speech():
    """The complement, and the reason the expectation above is conditional:
    on a clip with real speech the same tails are the speaker's content and
    must survive."""
    for inp, _stripped in [
        ("This is a real sentence and more.", None),
        ("Buy our gadgets, gizmos, accessories, and more!", None),
        ("The tools we offer all developers and much more!", None),
    ]:
        assert _strip_hallucinations(inp, speech_seconds=11.0) == inp


def test_real_sentence_mentioning_more_left_alone():
    """A real utterance where 'more' isn't a trailing outro must survive."""
    cases = [
        "There is more work to do.",
        "I'd like more coffee please.",
        "We need more time on this project.",
        # 'more' surrounded by other words — not a YouTube outro shape.
        "And more importantly, we should ship it.",
    ]
    for s in cases:
        assert not _is_whisper_hallucination(s), f"false-positive on {s!r}"
        out = _strip_hallucinations(s)
        assert "more" in out.lower(), f"strip ate real content from {s!r} → {out!r}"
    print(f"  ✓ all {len(cases)} real-mention sentences preserved")


def test_short_filler_hallucinations_rejected():
    """The full set of new short-clip hallucinations all reject."""
    cases = [
        "bye", "Bye.", "BYE!", "bye-bye", "goodbye",
        "thank you", "Thank you.", "thanks",
        "okay", "OK.", "yeah", "uh", "um", "hmm", "mhm",
    ]
    for s in cases:
        assert _is_whisper_hallucination(s), f"should reject {s!r}"
    print(f"  ✓ all {len(cases)} short-filler hallucinations rejected")


def test_existing_hallucinations_still_caught():
    """Regression: don't break the existing YouTube-outro filter."""
    cases = [
        "Thanks for watching!",
        "thanks for watching.",
        "please subscribe",
        "Like and subscribe",
        "[music]",
        ".",
    ]
    for s in cases:
        assert _is_whisper_hallucination(s), f"regression on existing case {s!r}"
    print(f"  ✓ all {len(cases)} pre-existing hallucinations still caught")


def main():
    tests = [
        test_exact_and_more_variants_rejected,
        test_trailing_and_more_stripped_from_real_content,
        test_real_sentence_mentioning_more_left_alone,
        test_short_filler_hallucinations_rejected,
        test_existing_hallucinations_still_caught,
    ]
    print("v3.14.39 — Whisper-layer 'and more' hallucination filter tests")
    print("=" * 70)
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {fn.__name__}: {e}")
    print("=" * 70)
    if failed:
        print(f"FAILED: {failed}/{len(tests)}")
        sys.exit(1)
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    main()
