"""Confirm 'first of all / second / third' now produces a numbered list,
and that prose 'first of all' on its own stays as prose (no false positive)."""
import os, sys, time
from pathlib import Path
env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler

styler = OpenAIStyler(
    api_key=os.environ.get("OPENAI_API_KEY", ""),
    groq_api_key=os.environ.get("GROQ_API_KEY", ""),
)

CASES = [
    ("FIX: 'first of all / second / third' — MUST be numbered",
     "Right, so first of all I need to finish the AI Foundry write-up before Friday. Second, hand it off to engineering for a review. Third, book in a kickoff with the team next week.",
     "numbered"),
    ("CONTROL: bare 'first of all' with no second/third — stays prose",
     "First of all, can I just say I really appreciate what you did last week. It meant a lot and I don't think I said it at the time.",
     "prose"),
    ("CONTROL: 'firstly / secondly' British variant — MUST be numbered",
     "Okay, firstly we need to sort out the API key rotation. Secondly, make sure logging is switched on in production. And thirdly, get the runbook updated before we roll out.",
     "numbered"),
]


def verdict(expected, styled):
    has_bullet = "\n- " in styled or styled.lstrip().startswith("- ")
    has_numbered = any(f"\n{n}." in styled or styled.lstrip().startswith(f"{n}.") for n in range(1, 10))
    if expected == "numbered":
        return "PASS — numbered" if has_numbered else "FAIL — still prose"
    if expected == "prose":
        return "PASS — prose kept" if not has_bullet and not has_numbered else "FAIL — unexpectedly listified"
    return "?"


for i, (label, transcript, expected) in enumerate(CASES, 1):
    print(f"\n=== {i}/{len(CASES)}  {label} ===")
    print(f"  in:      {transcript}")
    styled, usage = styler.style(transcript)
    print(f"  provider: {usage.get('provider', '?')}")
    print(f"  out:\n    " + styled.replace("\n", "\n    "))
    print(f"  verdict: {verdict(expected, styled)}")
    if i < len(CASES):
        print(f"  -- sleep 30s --", flush=True)
        time.sleep(30)
print("\n=== done ===")
