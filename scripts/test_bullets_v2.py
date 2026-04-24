"""Re-test bullet cases after softening HARD RULE and strengthening FORMATTING rule."""
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
    ("B1 grocery list — MUST bullet",
     "For the grocery run I need eggs, milk, bread, and a bag of apples for the kids' lunches.",
     "bullet"),
    ("B2 three things are — MUST bullet",
     "The three things we optimise for in this product are speed, accuracy, and cost to the end user.",
     "bullet"),
    ("B3 platforms list — MUST bullet",
     "The desktop app runs on Mac, Windows, and Linux, although Linux support is still experimental.",
     "bullet"),
    ("P1 conversational commas — MUST stay prose",
     "The meeting went well overall, but the product team pushed back pretty hard on the timeline, so we agreed to revisit it next Tuesday.",
     "prose"),
    ("N1 numbered reproducer — regression check",
     "Number one, check cards for Matchbook. Number two, check betting bot cards and see if I can add more.",
     "numbered"),
]


def verdict(expected, styled):
    has_bullet = "\n- " in styled or styled.lstrip().startswith("- ")
    has_numbered = any(f"\n{n}." in styled or styled.lstrip().startswith(f"{n}.") for n in range(1, 10))
    if expected == "bullet":
        return "PASS — bulleted" if has_bullet else "FAIL — still prose"
    if expected == "prose":
        return "PASS — prose kept" if not has_bullet and not has_numbered else f"FAIL — listified (bullet={has_bullet}, numbered={has_numbered})"
    if expected == "numbered":
        return "PASS — numbered" if has_numbered else "FAIL — not numbered"
    return "?"


for i, (label, transcript, expected) in enumerate(CASES, 1):
    print(f"\n=== {i}/{len(CASES)}  {label} ===")
    print(f"  input:   {transcript!r}")
    styled, usage = styler.style(transcript)
    print(f"  provider: {usage.get('provider', '?')}")
    print(f"  output:\n    " + styled.replace("\n", "\n    "))
    print(f"  verdict: {verdict(expected, styled)}")
    if i < len(CASES):
        print(f"  -- sleep 30s --", flush=True)
        time.sleep(30)
print("\n=== done ===")
