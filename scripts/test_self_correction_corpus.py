"""Self-correction regression harness for Waffler.

When the speaker corrects themselves mid-sentence, the wrong version
must be dropped from the output, not kept alongside the corrected one.

This covers many ways a speaker actually phrases corrections:
  - "X, sorry I mean Y"
  - "X, no wait, Y"
  - "X, actually Y"
  - "X — I meant Y"
  - "X, hmm no, Y"
  - implicit corrections without an explicit marker
  - multi-step corrections (X → Y → Z)
  - corrections inside lists / emails / questions

Drives the styler with text only — no Whisper involved. 30s delay
between calls so we don't trip Groq's per-minute limits if the
user wants to flip back to Groq once the rate limit clears.

Run:  python scripts/test_self_correction_corpus.py
      python scripts/test_self_correction_corpus.py --delay 5
"""
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler  # noqa: E402


@dataclass
class Case:
    label: str
    raw: str
    must_contain: List[str] = field(default_factory=list)
    must_not_contain: List[str] = field(default_factory=list)
    must_match: List[str] = field(default_factory=list)
    must_not_match: List[str] = field(default_factory=list)
    note: str = ""


CORPUS: List[Case] = [
    # ─── Day-of-week corrections (user's exact ask) ─────────────────────────
    Case("DAY-1 user's exact example",
         "Can we set up a meeting for Tuesday, sorry I mean Monday.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"],
         note="user reported this case isn't being corrected"),
    Case("DAY-2 'no wait' marker",
         "Let's do the standup at Tuesday, no wait, Monday.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"]),
    Case("DAY-3 'actually' marker (less explicit)",
         "Can we shift it to Tuesday, actually Monday works better.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"]),
    Case("DAY-4 em-dash + 'I meant'",
         "Send the doc on Tuesday — I meant Monday — before the standup.",
         must_contain=["Monday", "before the standup"],
         must_not_contain=["Tuesday"]),
    Case("DAY-5 'hmm no'",
         "Let's schedule it for Tuesday, hmm no, Monday morning.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"]),
    Case("DAY-6 abandoned without marker",
         "Tuesday — Monday at three would be better actually.",
         must_contain=["Monday", "three"],
         must_not_contain=["Tuesday"]),

    # ─── Name corrections ──────────────────────────────────────────────────
    Case("NAME-1 'sorry I mean'",
         "Send the report to John, sorry I mean Jane, by end of day.",
         must_contain=["Jane"],
         must_not_contain=["John"]),
    Case("NAME-2 multi-name correction",
         "Loop in Rohan and Sarah, no wait, Rohan and James.",
         must_contain=["Rohan", "James"],
         must_not_contain=["Sarah"]),
    Case("NAME-3 surname correction",
         "Ping James Smith, sorry, James Farrelly about the migration.",
         must_contain=["James Farrelly"],
         must_not_contain=["Smith"]),

    # ─── Number / time corrections ─────────────────────────────────────────
    Case("NUM-1 time correction",
         "The call is at three, no four, in the afternoon.",
         must_contain=["four"],
         must_not_contain=["at three"],
         note="'three' should be dropped, 'four' kept"),
    Case("NUM-2 price correction",
         "The quote was £500, sorry, £750 for the whole package.",
         must_contain=["£750"],
         must_not_contain=["£500"]),
    Case("NUM-3 count correction",
         "We've got about twenty users, no actually fifty users on the beta.",
         must_contain=["fifty"],
         must_not_contain=["twenty"]),

    # ─── Place / location corrections ──────────────────────────────────────
    Case("PLACE-1 location correction",
         "We're meeting in the boardroom, sorry I mean the cafeteria, at twelve.",
         must_contain=["cafeteria", "twelve"],
         must_not_contain=["boardroom"]),

    # ─── Multi-step corrections ────────────────────────────────────────────
    Case("MULTI-1 two corrections in one sentence",
         "Send it to John, sorry James, by Tuesday, no wait, Wednesday at three.",
         must_contain=["James", "Wednesday"],
         must_not_contain=["John", "Tuesday"],
         note="both the name AND day corrections must apply"),
    Case("MULTI-2 chain X→Y→Z",
         "Let's meet on Tuesday, no Wednesday, actually Thursday at two.",
         must_contain=["Thursday"],
         must_not_contain=["Tuesday", "on Wednesday"],
         note="only the final option in the chain survives"),

    # ─── Corrections inside structure ──────────────────────────────────────
    Case("STRUCT-1 correction inside numbered list",
         "Number one, ping John — sorry, I mean Jane — about the contract. Number two, book the room for Tuesday, no Monday.",
         must_contain=["Jane", "Monday"],
         must_not_contain=["John", "Tuesday"],
         must_match=[r"(?m)^\s*1\.\s*", r"(?m)^\s*2\.\s*"]),
    Case("STRUCT-2 correction inside email body",
         "Hi Rohan, can you sign off the budget by Tuesday, sorry I mean Monday. Cheers, James.",
         must_contain=["Monday", "Rohan"],
         must_not_contain=["Tuesday"],
         must_match=[r"^Hi Rohan,[ \t]*\n[ \t]*\n",
                     r"\n[ \t]*\n[ \t]*Cheers,[ \t]*\n[ \t]*James\b"]),
    Case("STRUCT-3 correction inside a question",
         "Are we meeting at three, sorry I mean four, this afternoon?",
         must_contain=["four", "?"],
         must_not_contain=["at three"]),

    # ─── Abandoned-phrase corrections (no explicit marker) ─────────────────
    Case("ABAND-1 abandoned start with em-dash",
         "We should ship — actually let me start over. The plan is to ship the dashboard refactor first.",
         must_contain=["dashboard refactor"],
         must_not_contain=["let me start over"]),
    Case("ABAND-2 partial then restart",
         "The thing about — what I'm trying to say is the migration is risky.",
         # Acceptable outputs: drop the abandoned start, OR fold the two halves
         # into smooth English. gpt-4.1-mini tends to preserve more text
         # (which is generally GOOD), so the assertion is just that the
         # meaning is preserved — migration + risky present in output.
         must_contain=["migration", "risky"],
         note="abandoned start; both 'drop entirely' and 'fold smoothly' are acceptable. "
              "The critical thing is content preservation."),

    # ─── NEGATIVE — 'I mean' as clarification, NOT a correction ────────────
    Case("NEG-1 'I mean' adds detail, doesn't replace",
         "Make sure the test is comprehensive — I mean cover the edge cases, the happy path, and the error states.",
         must_contain=["edge cases", "happy path", "error states", "comprehensive"],
         note="'I mean' here introduces a clarification, not a correction; nothing should be dropped"),
    Case("NEG-2 'actually' as emphasis not correction",
         "I actually really enjoyed working on this project, the whole team was great.",
         # 'actually' here is emphasis, not a correction. The prompt's
         # WHAT TO KEEP rule explicitly preserves "actually" when emphasising,
         # so the styler may legitimately keep it. The critical check is that
         # NOTHING gets dropped — "enjoyed" and "whole team" must survive.
         must_contain=["enjoyed", "whole team"],
         note="'actually' as emphasis is keep-able per prompt rule; the test is that no "
              "content gets dropped, not that 'actually' is removed"),
    Case("NEG-3 'sorry' as apology not correction",
         "Sorry for the late reply, the call ran over by twenty minutes.",
         must_contain=["late reply", "twenty minutes"],
         note="'Sorry' starting a sentence is an apology, not a correction marker — must not drop anything"),
    Case("NEG-4 'no wait' as suspense not correction",
         "I thought the deploy would take an hour, but no, wait until you see this — it took six.",
         must_contain=["six"],
         note="'no wait' as a rhetorical device, not a correction"),
]


def evaluate(case: Case, styled: str) -> List[str]:
    failures = []
    s_lower = styled.lower()
    for needle in case.must_contain:
        if needle.lower() not in s_lower:
            failures.append(f"missing: {needle!r}")
    for needle in case.must_not_contain:
        if needle.lower() in s_lower:
            failures.append(f"contains forbidden: {needle!r}")
    for pat in case.must_match:
        if not re.search(pat, styled, re.MULTILINE):
            failures.append(f"missing pattern: {pat!r}")
    for pat in case.must_not_match:
        if re.search(pat, styled, re.MULTILINE):
            failures.append(f"forbidden pattern matched: {pat!r}")
    return failures


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--filter", type=str, default=None)
    args = ap.parse_args()

    styler = OpenAIStyler(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
    )

    cases = [c for c in CORPUS if (not args.filter or args.filter.lower() in c.label.lower())]
    width = max(len(c.label) for c in cases)
    print(f"\nRunning {len(cases)} self-correction cases  delay={args.delay}s")
    print(f"{'#':<3} {'LABEL':<{width}} VERDICT")
    print("─" * (width + 50))

    failures_count = 0
    results = []
    for i, case in enumerate(cases, 1):
        try:
            t0 = time.time()
            styled, usage = styler.style(case.raw)
            elapsed = (time.time() - t0) * 1000
        except Exception as e:
            print(f"{i:<3} {case.label:<{width}} ERROR: {e!s:.80}")
            results.append((case, "", [f"exception: {e}"]))
            failures_count += 1
            continue
        fails = evaluate(case, styled)
        verdict = "PASS" if not fails else f"FAIL ({len(fails)})"
        provider = usage.get("provider", "?")
        print(f"{i:<3} {case.label:<{width}} {verdict:<12} ({elapsed:.0f}ms via {provider})")
        results.append((case, styled, fails))
        if fails:
            failures_count += 1
        if i < len(cases):
            time.sleep(args.delay)

    print(f"\n{'─' * (width + 50)}")
    print(f"PASSED {len(cases) - failures_count}/{len(cases)}")

    if failures_count:
        print("\n=== FAILURE DETAILS ===")
        for case, styled, fails in results:
            if not fails:
                continue
            print(f"\n[{case.label}]  {case.note}")
            print(f"  raw:    {case.raw}")
            print(f"  styled: {styled}")
            for f in fails:
                print(f"  - {f}")


if __name__ == "__main__":
    main()
