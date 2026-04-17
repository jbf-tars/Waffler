#!/usr/bin/env python3
"""Head-to-head benchmark of Groq models on real transcripts.

For each candidate model, run 6 representative cases and measure:
  - Latency (ms)
  - Output quality (shown side-by-side for manual judgment)
"""

import os
import sys
import json
import time
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

env = Path.home() / ".waffler-hosted" / ".env"
load_dotenv(str(env), override=True)

from style_openai import OpenAIStyler

HISTORY = json.loads(Path.home().joinpath(".waffler-hosted/history.json").read_text())
by_ts = {e["timestamp"]: e for e in HISTORY}

MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",  # current
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
]

# 6 representative cases — mix of short/long and known-failure patterns
CASES = [
    "2026-04-14T16:21:08",  # "like" preservation test
    "2026-04-15T17:12:09",  # long, needs grammar smoothing
    "2026-04-15T21:17:56",  # tests email-hallucination resistance
    "2026-04-16T22:12:07",  # worst case: long + number-one dictation
    "2026-04-17T14:45:38",  # H-E-V-Y letter spelling preservation
    "2026-04-17T15:15:28",  # "number one" dictation, multi-point
]


def bench_model(model_id: str):
    styler = OpenAIStyler(
        api_key="",
        groq_api_key=os.getenv("GROQ_API_KEY"),
        prompt_style="normal",
    )
    styler._groq_model = model_id
    results = []
    for ts in CASES:
        raw = by_ts[ts]["text"]
        t0 = time.perf_counter()
        try:
            out, _ = styler.style(raw)
            err = None
        except Exception as e:
            out = ""
            err = str(e)[:120]
        ms = (time.perf_counter() - t0) * 1000
        results.append((ts, raw, out, ms, err))
    return results


def main():
    all_results = {}
    print("Running benchmarks...")
    for model in MODELS:
        print(f"  {model}")
        try:
            all_results[model] = bench_model(model)
        except Exception as e:
            print(f"    FAILED: {e}")
            all_results[model] = None

    # Summary — avg latency per model
    print("\n" + "=" * 78)
    print("LATENCY SUMMARY (avg ms over 6 cases)")
    print("=" * 78)
    for model, results in all_results.items():
        if results is None:
            print(f"  {model:60s}  FAILED")
            continue
        lats = [r[3] for r in results if r[4] is None]
        if lats:
            print(f"  {model:60s}  avg={sum(lats)/len(lats):6.0f}ms  min={min(lats):4.0f}  max={max(lats):4.0f}")
        else:
            print(f"  {model:60s}  all errored")

    # Per-case side-by-side output
    for i, ts in enumerate(CASES):
        print("\n" + "=" * 78)
        raw = by_ts[ts]["text"]
        print(f"CASE {i+1}: {ts} ({by_ts[ts]['word_count']} words)")
        print("-" * 78)
        print(f"RAW: {raw}")
        for model, results in all_results.items():
            if results is None:
                continue
            _, _, out, ms, err = results[i]
            short = model.split("/")[-1]
            print(f"\n[{short}] {ms:.0f}ms")
            print(out if not err else f"ERROR: {err}")


if __name__ == "__main__":
    main()
