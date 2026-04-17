#!/usr/bin/env python3
"""Tests for _strip_hallucinations post-processor.

Each case is a real (transcript, bad_llm_output, expected_clean) tuple
pulled from ~/.waffler-hosted/history.json. These are the exact failure
modes the post-processor must fix.

Run: python tests/test_strip_hallucinations.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from style_openai import OpenAIStyler


def strip(transcript, llm_output):
    styler = OpenAIStyler.__new__(OpenAIStyler)
    return styler._strip_hallucinations(llm_output, transcript)


CASES = [
    # 1. Phantom Dear Team / Best regards — line 785
    (
        "Please look at my audit logs and let me know what's going on because I feel like some accounts are getting on more than the others.",
        "Dear Team,\n\nPlease look at my audit logs and let me know what's going on because I feel like some accounts are getting on more than the others.\n\nBest regards,\n[Your Name]",
        "Please look at my audit logs and let me know what's going on because I feel like some accounts are getting on more than the others.",
    ),
    # 2. Meta-preamble + Dear Team + Best + placeholder — line 1565
    (
        "Okay so number one for brand and positioning.",
        "Here is the cleaned text:\n\nDear Team,\n\nI'm working on brand and positioning.\n\nBest, \n[No signature provided]",
        "I'm working on brand and positioning.",
    ),
    # 3. Phantom Hi, / Best / [No signature provided] — line 2141
    (
        "Basically I want to create a gym app for my personal use.",
        "Hi,\n\nBasically I want to create a gym app for my personal use.\n\nBest,\n[No signature provided]",
        "Basically I want to create a gym app for my personal use.",
    ),
    # 4. Phantom Hi, / Best / [No Name] — line 2159
    (
        "Okay just a few things I'm noticing. Number one is on row 32 it's frozen.",
        "Hi,\n\nI'm noticing a few things.\n\nNumber one is on row 32, it's frozen.\n\nBest,\n[No Name]",
        "I'm noticing a few things.\n\nNumber one is on row 32, it's frozen.",
    ),
    # 5. Phantom Hi, / Best / [No signature] — line 2201
    (
        "And then I have just had a call with Ashcan and a few things, right?",
        "Hi,\n\nI just had a call with Ashcan and a few things came up.\n\nBest,\n[No signature]",
        "I just had a call with Ashcan and a few things came up.",
    ),
    # 6. NEGATIVE — real dictated "Hi Simon" must survive — line 389
    (
        "Hi Simon. Yeah, sounds good. Ideally we'd want to test it over a longer period of time.",
        "Hi Simon.\n\nYeah, sounds good. Ideally, we'd want to test it over a longer period of time.",
        "Hi Simon.\n\nYeah, sounds good. Ideally, we'd want to test it over a longer period of time.",
    ),
    # 7. NEGATIVE — real dictated "Hi Ashkan" must survive — line 413
    (
        "Hi Ashkan. Is there going to be a Kobe QC report this week?",
        "Hi Ashkan.\n\nIs there going to be a Kobe QC report this week?",
        "Hi Ashkan.\n\nIs there going to be a Kobe QC report this week?",
    ),
    # 8. Triple+ newline collapse — line 329 / 611
    (
        "Some speech here.",
        "Some speech here.\n\n\n\nAnother paragraph.",
        "Some speech here.\n\nAnother paragraph.",
    ),
    # 9. Meta-preamble only (no email wrapping)
    (
        "Just a simple sentence.",
        "Here's the cleaned text: Just a simple sentence.",
        "Just a simple sentence.",
    ),
    # 10. "Output:" preamble
    (
        "Test this thing.",
        "Output:\nTest this thing.",
        "Test this thing.",
    ),
    # 11. NEGATIVE — legitimate dictated sign-off (Kind regards, James) with NO placeholder must survive
    (
        "Thanks for the meeting. Kind regards, James.",
        "Thanks for the meeting.\n\nKind regards, James.",
        "Thanks for the meeting.\n\nKind regards, James.",
    ),
    # 12. Whitespace-only lines between paragraphs (from line 329 pattern: "\n\n \n")
    (
        "First sentence. Second sentence.",
        "First sentence.\n\n \nSecond sentence.",
        "First sentence.\n\nSecond sentence.",
    ),
]


def main():
    failed = 0
    for i, (transcript, bad, expected) in enumerate(CASES, 1):
        got = strip(transcript, bad)
        ok = got == expected
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] Case {i}")
        if not ok:
            failed += 1
            print(f"  transcript: {transcript!r}")
            print(f"  input:      {bad!r}")
            print(f"  expected:   {expected!r}")
            print(f"  got:        {got!r}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
