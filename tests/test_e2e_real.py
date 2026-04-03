#!/usr/bin/env python3
"""End-to-end test using real Groq API against the actual bad-output
transcripts from ~/.waffler-hosted/history.json.

Prints a side-by-side: RAW | OLD (what was saved) | NEW (new prompt + guardrail).
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    load_dotenv(str(env), override=True)

from style_openai import OpenAIStyler

groq_key = os.getenv("GROQ_API_KEY")
assert groq_key, "GROQ_API_KEY not in env"

styler = OpenAIStyler(
    api_key="",
    groq_api_key=groq_key,
    prompt_style="normal",
)

HISTORY = json.loads(Path.home().joinpath(".waffler-hosted/history.json").read_text())
by_ts = {e["timestamp"]: e for e in HISTORY}

# Hand-picked cases — the worst/longest/most-complex failures from history
CASE_TIMESTAMPS = [
    "2026-04-14T16:21:08",  # dropped "like" as filler when meaningful
    "2026-04-15T16:37:56",  # long, paraphrased "I have to be honest"
    "2026-04-15T17:12:09",  # long about Site Audit Pro, triple-newline injection
    "2026-04-15T20:23:28",  # huge chargebacks monologue, got bulleted
    "2026-04-15T20:28:50",  # "Wait it seems there was a pause" hallucination
    "2026-04-15T21:17:56",  # "Dear Team / Best regards / [Your Name]"
    "2026-04-16T22:12:07",  # worst — preamble + email wrap + bullets + bad sign-off
    "2026-04-17T14:47:46",  # frozen row — "Hi," + "Best,[No Name]"
    "2026-04-17T14:45:38",  # gym app — "Hi," + "Best,[No signature]"
    "2026-04-17T15:15:28",  # call with Ashcan — "Hi," + bullets + "Best,[No signature]"
]


def main():
    for ts in CASE_TIMESTAMPS:
        if ts not in by_ts:
            print(f"! missing: {ts}")
            continue
        e = by_ts[ts]
        raw = e["text"]
        old = e["styled"]
        try:
            new, _ = styler.style(raw)
        except Exception as ex:
            new = f"<ERROR: {ex}>"

        print("=" * 78)
        print(f"TIMESTAMP: {ts}   ({e['word_count']} words)")
        print("-" * 78)
        print("RAW:")
        print(raw)
        print("-" * 78)
        print("OLD (what got saved):")
        print(old)
        print("-" * 78)
        print("NEW (new prompt + guardrail):")
        print(new)
        print()


if __name__ == "__main__":
    main()
