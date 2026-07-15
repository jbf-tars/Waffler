#!/usr/bin/env python3
"""Measure how CONSISTENTLY the styler obeys the prompt.

Why this exists:
`auto_test_corpus.py` runs each case ONCE. But the styler is non-deterministic
(temperature=0.1), and the user's actual complaint is that formatting "hasn't
worked consistently" -- e.g. continuous speech being shattered into one
paragraph per sentence, but only *sometimes*. A bug that fires 1 time in 3 will
happily PASS a single-shot run and still ruin every third dictation.

So: run each case N times against a PINNED provider and report a FAILURE RATE.
A prompt change is only an improvement if it drives that rate down (and drives
no other case's rate up).

Pinning the provider matters just as much. Bugs here are provider-specific --
the over-paragraphing reproduces on groq/cerebras but NOT on openai -- and the
unpinned harness silently falls through when Groq hits its daily cap, so you get
a false PASS from a model you never actually use.

Usage:
    python scripts/flaky_check.py --provider groq --repeat 10 --filter OPL
    python scripts/flaky_check.py --provider cerebras --repeat 5
"""

import argparse
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from style_openai import OpenAIStyler  # noqa: E402
from auto_test_corpus import CORPUS, evaluate  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Prompt consistency / flakiness harness")
    ap.add_argument("--provider", required=True, choices=["groq", "cerebras", "openai"],
                    help="Pin the styler to ONE provider (no fallback).")
    ap.add_argument("--repeat", type=int, default=5, help="Runs per case (default 5)")
    ap.add_argument("--filter", type=str, default=None, help="Substring match on case label")
    ap.add_argument("--category", type=str, default=None, help="Substring match on category")
    ap.add_argument("--delay", type=float, default=0.6, help="Seconds between calls")
    args = ap.parse_args()

    styler = OpenAIStyler(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        cerebras_api_key=os.environ.get("CEREBRAS_API_KEY", ""),
        provider_order=[args.provider],
    )
    # _normalize_provider_order appends missing providers — override so the
    # pin is real and a fallback can't silently answer (or burn quota).
    styler._provider_order = [args.provider]

    def _match(c):
        if args.filter and args.filter.lower() not in c.label.lower():
            return False
        if args.category and args.category.lower() not in c.category.lower():
            return False
        return True

    cases = [c for c in CORPUS if _match(c)]
    if not cases:
        print(f"no cases matched filter={args.filter!r} category={args.category!r}")
        return 1

    print(f"\nProvider: {args.provider} (pinned, no fallback)")
    print(f"Cases: {len(cases)}  x  {args.repeat} runs = {len(cases) * args.repeat} calls\n")

    fail_counts = defaultdict(int)
    reasons = defaultdict(list)
    wrong_provider = 0
    errors = 0
    latencies = []

    for case in cases:
        for _ in range(args.repeat):
            try:
                t0 = time.time()
                styled, usage = styler.style(case.raw)
                latencies.append((time.time() - t0) * 1000)
            except Exception as e:
                errors += 1
                fail_counts[case.label] += 1
                reasons[case.label].append(f"EXCEPTION: {type(e).__name__}: {e}")
                time.sleep(args.delay)
                continue
            # A fallback to another provider (or basic_clean) means we did NOT
            # actually test the provider under test -- don't score it as a pass.
            used = (usage or {}).get("provider", "?")
            if used != args.provider:
                wrong_provider += 1
                time.sleep(args.delay)
                continue
            errs = evaluate(case, styled)
            if errs:
                fail_counts[case.label] += 1
                reasons[case.label].append("; ".join(errs)[:150])
            time.sleep(args.delay)

    width = max(len(c.label) for c in cases)
    print(f"{'CASE':<{width}}  FAILS/RUNS   RATE")
    print("-" * (width + 24))
    total_fail = 0
    flaky = []
    for c in cases:
        f = fail_counts[c.label]
        total_fail += f
        rate = f / args.repeat
        flag = "" if f == 0 else ("  <-- ALWAYS" if f == args.repeat else "  <-- FLAKY")
        if f:
            flaky.append((c.label, rate))
        print(f"{c.label:<{width}}  {f:>3}/{args.repeat:<7}  {rate:>5.0%}{flag}")

    runs = len(cases) * args.repeat
    print("-" * (width + 24))
    print(f"\nTOTAL failing runs: {total_fail}/{runs} ({total_fail / runs:.0%})")
    if latencies:
        print(f"styling latency: median {statistics.median(latencies):.0f}ms  "
              f"max {max(latencies):.0f}ms")
    if wrong_provider:
        print(f"WARNING: {wrong_provider} runs fell through to another provider "
              f"and were NOT scored (rate-limited?). Results are thinner than they look.")
    if errors:
        print(f"WARNING: {errors} runs raised.")

    if flaky:
        print("\n=== FAILURE REASONS ===")
        for label, rate in sorted(flaky, key=lambda x: -x[1]):
            print(f"\n[{label}]  {rate:.0%} failure")
            for r in reasons[label][:3]:
                print(f"   - {r}")

    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
