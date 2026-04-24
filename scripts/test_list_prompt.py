"""Probe the normal.txt prompt against the real Groq API.

Runs 10 transcripts covering numbered-list, bullet-list, and plain-prose
cases through the same OpenAIStyler the app uses. 30s pause between
calls so we stay well under Groq's per-minute request/token limits.
"""
import os
import sys
import time
from pathlib import Path

# Load the production key the app uses.
env_path = Path.home() / ".waffler-hosted" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler  # noqa: E402

groq_key = os.environ.get("GROQ_API_KEY", "")
openai_key = os.environ.get("OPENAI_API_KEY", "")
if not groq_key:
    print("ERROR: GROQ_API_KEY not found in ~/.waffler-hosted/.env")
    sys.exit(1)

styler = OpenAIStyler(api_key=openai_key, groq_api_key=groq_key)

TESTS = [
    ("Numbered list, 2 items (the original reproducer)",
     "Number one, check cards for Matchbook. Number two, check betting bot cards and see if I can add more.",
     "numbered"),
    ("Numbered list via first/second/third, 3 items",
     "First, we need to finalise the design document before Friday. Second, hand it to engineering for review. Third, schedule a kickoff meeting next week.",
     "numbered"),
    ("Numbered list with natural filler between items",
     "Number one, um, check the database and make sure it looks healthy. Number two, uh, verify the backups are running every night.",
     "numbered"),
    ("Mixed: prose lead-in, then numbered items",
     "Here's what we need to do this sprint. Number one, hire another backend engineer. Number two, onboard them and pair them with Rohan for the first week.",
     "numbered"),
    ("Bullet list — natural parallel sequence, no counting",
     "For the grocery run I need eggs, milk, bread, and a bag of apples for the kids' lunches.",
     "bullet-or-prose"),
    ("Bullet list — explicit 'the three things are' cue",
     "The three things we optimise for in this product are speed, accuracy, and cost to the end user.",
     "bullet-or-prose"),
    ("Plain prose — NO list should appear",
     "So today I finally got round to cleaning out the garage, and honestly it took way longer than I thought it would because there was so much rubbish in there.",
     "prose"),
    ("Plain prose with commas — should stay prose, not bullets",
     "The meeting went well overall, but the product team pushed back pretty hard on the timeline, so we agreed to revisit it next Tuesday.",
     "prose"),
    ("Numbered items with longer sentences each",
     "Number one, I need to review the quarterly reports this afternoon and flag anything that looks off before the board meeting on Thursday. Number two, I want to follow up with the accounts team about the budget variance we spotted last week.",
     "numbered"),
    ("Self-correction inside a numbered list",
     "Number one, contact John — sorry, I mean Jane — about the contract. Number two, send the signed copy back to legal by end of day.",
     "numbered"),
]


def render(out):
    # Small indent so formatting (newlines / list markers) is easy to see.
    return "\n    " + out.replace("\n", "\n    ")


for i, (label, transcript, expected_kind) in enumerate(TESTS, 1):
    print(f"\n=== TEST {i}/{len(TESTS)}: {label} ===")
    print(f"expected: {expected_kind}")
    print(f"input:   {transcript!r}")
    t0 = time.time()
    styled, usage = styler.style(transcript)
    elapsed = (time.time() - t0) * 1000
    provider = usage.get("provider", "?")
    fallback = usage.get("fallback_reason", "")
    print(f"provider: {provider} ({elapsed:.0f}ms){'  FALLBACK: ' + fallback if fallback else ''}")
    print(f"output:{render(styled)}")

    if i < len(TESTS):
        print(f"-- sleeping 30s before test {i+1} --", flush=True)
        time.sleep(30)

print("\n=== done ===")
