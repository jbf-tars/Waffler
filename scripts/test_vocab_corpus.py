"""Vocabulary regression harness for Waffler.

Tests the fuzzy-match correction layer (apply_vocab_corrections) against
the user's actual ~/.waffler-hosted/vocab.json. Two pass families:

  POSITIVE — input that simulates a Whisper mishearing of a vocab word.
             Should be corrected to the canonical vocab spelling.
  NEGATIVE — input with NO vocab word said. Must NOT inject vocab words
             into unrelated text. This is the false-positive guard the
             user has hit before.

Pure local execution — no API calls, no rate limits, no Whisper. Tests
the correction logic in isolation (the Whisper prompt= bias is a
separate path that needs real audio to test).

Run:  python scripts/test_vocab_corpus.py
"""
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from transcribe_whisper import (  # noqa: E402
    apply_vocab_corrections,
    fuzzy_match_word,
    load_vocab,
)


# ── Load the user's actual vocab ────────────────────────────────────────────
USER_VOCAB = load_vocab()
print(f"Loaded vocab: {USER_VOCAB}\n")


@dataclass
class Case:
    label: str
    raw: str
    expect_corrected: Optional[str] = None   # exact expected output, or None
    must_contain: Optional[List[str]] = None
    must_not_contain: Optional[List[str]] = None
    note: str = ""


CORPUS: List[Case] = [
    # ─── POSITIVE — Ashkan single-token mishearings ───────────────────────
    Case("ASH-P1 exact match preserved",
         "Ashkan said the deploy is ready.",
         expect_corrected="Ashkan said the deploy is ready.",
         note="canonical form passes through unchanged"),
    Case("ASH-P2 lowercase corrected to canonical case",
         "ashkan said the deploy is ready.",
         must_contain=["Ashkan"],
         must_not_contain=["ashkan said"],
         note="case-insensitive match restores canonical capitalisation"),
    Case("ASH-P3 small typo (1 char)",
         "Ashkant said the deploy is ready.",
         must_contain=["Ashkan"],
         note="Ashkant → Ashkan (similarity 0.857, above 0.75)"),
    Case("ASH-P4 typo Ashken",
         "I had a chat with Ashken about the migration.",
         must_contain=["Ashkan"],
         note="Ashken → Ashkan (similarity 0.833)"),
    Case("ASH-P5 BIGRAM Nash can → Ashkan",
         "I had a chat with Nash can earlier about the project.",
         must_contain=["Ashkan"],
         must_not_contain=["Nash can"],
         note="classic Whisper splits 'Ashkan' into two words — bigram pass should glue + correct"),
    Case("ASH-P6 BIGRAM Ash can → Ashkan",
         "Ash can said the same thing in the meeting.",
         must_contain=["Ashkan"],
         must_not_contain=["Ash can"],
         note="another typical Whisper split"),

    # ─── POSITIVE — COBie / COBieQC mishearings ───────────────────────────
    Case("COB-P1 COBie exact",
         "Run the COBie checker on the file.",
         expect_corrected="Run the COBie checker on the file.",
         note="canonical COBie unchanged"),
    Case("COB-P2 COBieQC exact",
         "The COBieQC validation passed.",
         expect_corrected="The COBieQC validation passed.",
         note="canonical COBieQC unchanged"),
    Case("COB-P3 Cobe → COBie",
         "The Cobe checker is broken again.",
         must_contain=["COBie"],
         note="Cobe → COBie (similarity 0.8)"),
    Case("COB-P4 lowercase cobie",
         "Did the cobie pass?",
         must_contain=["COBie"],
         must_not_contain=["cobie"],
         note="lowercase cobie restored to COBie capitalisation"),
    Case("COB-P5 Cobby (borderline)",
         "Cobby is failing on row 200.",
         note="similarity 0.6 — below 0.75 threshold; Cobby won't match. "
              "This documents the threshold behaviour, not a bug."),

    # ─── NEGATIVE — common words that sound similar to vocab MUST NOT change ──
    Case("NEG-1 'cost' must stay 'cost'",
         "The cost of the project has gone up since the quarterly review.",
         must_contain=["cost"],
         must_not_contain=["COBie"],
         note="cost vs COBie: similarity 0.2 — well below 0.75. False-positive guard."),
    Case("NEG-2 'high' must stay 'high'",
         "Highest priority right now is the API rotation.",
         must_contain=["Highest"],
         must_not_contain=["Ashkan", "COBie"],
         note="'High' / 'Highest' must not match 'Ashkan' / 'COBie'"),
    Case("NEG-3 'ash' must stay 'ash'",
         "Ash falling from the fire was getting on the windowsill.",
         must_contain=["Ash"],
         must_not_contain=["Ashkan"],
         note="bare 'Ash' is short — len < 3 cutoff applies, plus similarity too low"),
    Case("NEG-4 'cobblestone' must stay",
         "The cobblestone road outside the office got resurfaced.",
         must_contain=["cobblestone"],
         must_not_contain=["COBie"],
         note="cobblestone shares prefix with COBie but is much longer — similarity well below 0.75"),
    Case("NEG-5 'cobalt' must stay",
         "We need cobalt blue for the brand palette.",
         must_contain=["cobalt"],
         must_not_contain=["COBie"],
         note="'cobalt' (6 chars) vs COBie (5): distance 4, similarity 0.33 — won't match"),
    Case("NEG-6 unrelated tech words must stay",
         "We tested with Postgres, Redis, Kafka, and Docker.",
         must_contain=["Postgres", "Redis", "Kafka", "Docker"],
         must_not_contain=["Ashkan", "COBie", "COBieQC"],
         note="totally unrelated tech terms — must not touch any"),
    Case("NEG-7 sentence with NO vocab anywhere",
         "Today I went to the shop and bought some bread, milk, and eggs for the kids' lunches.",
         must_not_contain=["Ashkan", "COBie", "COBieQC"],
         note="ordinary prose — vocab must not appear from thin air"),
    Case("NEG-8 'cobie' homophone with totally different meaning",
         # 'Coby' can be a real name. With threshold 0.75, "Coby" → "COBie"
         # WILL match (similarity 0.8). That's a known false-positive risk.
         "Coby Persin made a viral prank video last year.",
         must_contain=["Coby"],
         must_not_contain=["COBie"],
         note="EXPECTED FAILURE — documents that 'Coby' as a real name will currently be "
              "incorrectly corrected to 'COBie'. If this fails, threshold may need bumping "
              "or context-aware matching added."),

    # ─── NEGATIVE — Bigram pass false positives ───────────────────────────
    Case("NEG-9 'has can' must not become 'Ashkan'",
         "The team has can-do energy this sprint.",
         must_contain=["has can"],
         must_not_contain=["Ashkan"],
         note="'has can' bigram is far from 'Ashkan' (distance 4 of 6 — sim 0.33). False-positive guard."),
    Case("NEG-10 'task on' must not match 'Ashkan'",
         "I'll task on the migration tomorrow.",
         must_contain=["task on"],
         must_not_contain=["Ashkan"],
         note="phonetically similar bigram — distance 3 of 7 — sim 0.57, below 0.70 bigram threshold"),

    # ─── EDGE — multiple vocab words in one sentence ──────────────────────
    Case("MULTI-1 Ashkan and COBie in same sentence",
         "Ashkan said the COBie checker is broken — can he have a look?",
         must_contain=["Ashkan", "COBie"],
         note="both vocab words present, both kept"),
    Case("MULTI-2 mishearings of both",
         "Nash can said the cobe checker is broken.",
         must_contain=["Ashkan", "COBie"],
         must_not_contain=["Nash can", "cobe"],
         note="both should be corrected — bigram + unigram in one input"),

    # ─── EDGE — vocab in list / structured content ────────────────────────
    Case("STRUCT-1 vocab in numbered list",
         "Number one, ask Ashkan to review the COBieQC output. Number two, ship the dashboard refactor.",
         must_contain=["Ashkan", "COBieQC"],
         note="vocab survives numbered-list cue context"),
    Case("STRUCT-2 vocab in email greeting",
         "Hi Ashkan, can you take a look at the COBieQC errors when you get a chance?",
         must_contain=["Ashkan", "COBieQC"],
         note="vocab in greeting + body"),

    # ─── DIAGNOSTIC — what fuzzy_match_word actually finds ────────────────
    # This case doesn't run apply_vocab_corrections; it's a metadata probe.
]


def evaluate(case: Case, corrected: str, applied: List[str]) -> List[str]:
    failures = []
    if case.expect_corrected is not None:
        if corrected.strip() != case.expect_corrected.strip():
            failures.append(f"expected exact: {case.expect_corrected!r}\n        got: {corrected!r}")
    if case.must_contain:
        for needle in case.must_contain:
            if needle not in corrected:
                failures.append(f"missing: {needle!r}")
    if case.must_not_contain:
        for needle in case.must_not_contain:
            if needle in corrected:
                failures.append(f"unexpected: {needle!r} appeared in output")
    return failures


def main():
    print(f"Running {len(CORPUS)} vocab cases against vocab={USER_VOCAB}\n")
    width = max(len(c.label) for c in CORPUS)
    print(f"{'#':<3} {'LABEL':<{width}} VERDICT")
    print("─" * (width + 30))

    failures_total = 0
    for i, case in enumerate(CORPUS, 1):
        corrected, applied = apply_vocab_corrections(case.raw, USER_VOCAB)
        fails = evaluate(case, corrected, applied)
        verdict = "PASS" if not fails else f"FAIL ({len(fails)})"
        applied_str = f" [{', '.join(applied)}]" if applied else ""
        print(f"{i:<3} {case.label:<{width}} {verdict}{applied_str}")
        if fails:
            failures_total += 1

    print(f"\n{'─' * (width + 30)}")
    print(f"PASSED {len(CORPUS) - failures_total}/{len(CORPUS)}")

    if failures_total:
        print("\n=== FAILURE DETAILS ===")
        for case in CORPUS:
            corrected, applied = apply_vocab_corrections(case.raw, USER_VOCAB)
            fails = evaluate(case, corrected, applied)
            if not fails:
                continue
            print(f"\n[{case.label}]  note: {case.note}")
            print(f"  raw:       {case.raw}")
            print(f"  corrected: {corrected}")
            if applied:
                print(f"  applied:   {applied}")
            for f in fails:
                print(f"  - {f}")


if __name__ == "__main__":
    main()
