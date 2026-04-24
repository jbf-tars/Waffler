"""Targeted re-run of the two cases that matter after the prompt tweak:
the original reproducer (#1) and the prose-lead-in regression (#4).
30s between calls to stay clear of Groq rate limits."""
import os, sys, time
from pathlib import Path

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler  # noqa: E402

styler = OpenAIStyler(
    api_key=os.environ.get("OPENAI_API_KEY", ""),
    groq_api_key=os.environ.get("GROQ_API_KEY", ""),
)

CASES = [
    ("#1 reproducer — should stay a clean numbered list",
     "Number one, check cards for Matchbook. Number two, check betting bot cards and see if I can add more."),
    ("#4 regression — prose lead-in MUST be kept",
     "Here's what we need to do this sprint. Number one, hire another backend engineer. Number two, onboard them and pair them with Rohan for the first week."),
]

for i, (label, transcript) in enumerate(CASES, 1):
    print(f"\n=== {label} ===")
    print(f"input:  {transcript!r}")
    styled, usage = styler.style(transcript)
    print(f"provider: {usage.get('provider', '?')}")
    print("output:\n    " + styled.replace("\n", "\n    "))
    if i < len(CASES):
        print("-- sleep 30s --", flush=True)
        time.sleep(30)
print("\n=== done ===")
